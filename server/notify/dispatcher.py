"""极简告警：级别过滤、冷却、Gotify、可选企业微信、熔断与磁盘溢写。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass

import httpx

from server.bus import BusMessage

LEVELS = {"info": 0, "warning": 1, "critical": 2}


def level_allows(level: str, minimum: str) -> bool:
    return LEVELS.get(level, 0) >= LEVELS.get(minimum, 1)


def cooled(last_sent: float, now: float, cooldown: float) -> bool:
    return now - last_sent >= cooldown


@dataclass
class Breaker:
    failures: int = 0
    opened_until: float = 0
    threshold: int = 5
    pause: float = 60

    def allow(self, now: float) -> bool:
        return now >= self.opened_until

    def ok(self) -> None:
        self.failures = 0
        self.opened_until = 0

    def fail(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_until = now + self.pause
            self.failures = 0


class Notifier:
    def __init__(self, config, db, clock=time.time) -> None:
        self.config = config
        self.db = db
        self.clock = clock
        self._last: dict[str, float] = {}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.gotify_breaker = Breaker()
        self.wecom_breaker = Breaker()
        self.sent = 0
        self.spilled = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def submit(self, message: BusMessage) -> None:
        now = self.clock()
        if not level_allows(message.level, self.config.min_level):
            return
        key = f"{message.type}:{message.cam_id or ''}"
        if message.level != "critical" and not cooled(self._last.get(key, 0), now, self.config.cooldown_sec):
            return
        self._last[key] = now
        item = {"level": message.level, "text": self.render(message), "ts": message.ts, "image": message.payload.get("snapshot_path", "")}
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.spilled += 1
            asyncio.create_task(self._spill(item))

    def render(self, message: BusMessage) -> str:
        title = {
            "zone_intrude": "区域入侵",
            "line_cross": "越线",
            "loiter": "徘徊",
            "crowd": "人员聚集",
            "face_unknown": "陌生人",
            "door_open": "开门",
            "door_unknown": "开门结果未知",
            "stream_down": "摄像头断流",
            "stream_stutter": "摄像头卡顿",
            "disk_state": "磁盘水位",
            "scene_abnormal": "画面异常",
        }.get(message.type, message.type)
        cam = message.cam_id or "系统"
        return f"[SmartCCTV] {title} · {cam} · {message.level}\n{json.dumps(message.payload, ensure_ascii=False)[:500]}"

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1)
            except TimeoutError:
                await self.flush_spill()
                continue
            delivered = await self._deliver(item)
            if not delivered:
                await self._spill(item)
            await asyncio.sleep(3)

    async def _deliver(self, item: dict) -> bool:
        now = self.clock()
        ok = False
        if self.config.gotify_url and self.config.gotify_token and self.gotify_breaker.allow(now):
            ok = await self._gotify(item) or ok
            if ok:
                self.gotify_breaker.ok()
            else:
                self.gotify_breaker.fail(now)
        if self.config.wecom_webhook and self.wecom_breaker.allow(now):
            sent = await self._wecom(item)
            ok = sent or ok
            if sent:
                self.wecom_breaker.ok()
            else:
                self.wecom_breaker.fail(now)
        if not self.config.gotify_url and not self.config.wecom_webhook:
            return True
        if ok:
            self.sent += 1
        return ok

    async def _gotify(self, item: dict) -> bool:
        url = self.config.gotify_url.rstrip("/") + "/message"
        priority = {"info": 2, "warning": 5, "critical": 8}.get(item["level"], 5)
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.post(url, params={"token": self.config.gotify_token}, json={
                    "title": "SmartCCTV",
                    "message": item["text"],
                    "priority": priority,
                })
            return response.status_code < 300
        except httpx.HTTPError:
            return False

    async def _wecom(self, item: dict) -> bool:
        body: dict = {"msgtype": "text", "text": {"content": item["text"][:2000]}}
        image = item.get("image") or ""
        if image:
            try:
                raw = open(image, "rb").read()
                if len(raw) < 2_000_000:
                    import base64

                    body = {"msgtype": "image", "image": {"base64": base64.b64encode(raw).decode(), "md5": hashlib.md5(raw).hexdigest()}}
            except OSError:
                pass
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.post(self.config.wecom_webhook, json=body)
            data = response.json()
            return response.status_code < 300 and data.get("errcode", 0) == 0
        except (httpx.HTTPError, ValueError):
            return False

    async def _spill(self, item: dict) -> None:
        from server.db import NotifySpill

        self.spilled += 1

        async def write(session):
            session.add(NotifySpill(level=item["level"], body=json.dumps(item, ensure_ascii=False), created_at=item["ts"]))

        await self.db.write(write)

    async def flush_spill(self) -> None:
        from sqlalchemy import select

        from server.db import NotifySpill

        async def read(session):
            result = await session.execute(select(NotifySpill).where(NotifySpill.sent_at.is_(None)).limit(5))
            return list(result.scalars())

        rows = await self.db.read(read)
        now = self.clock()
        for row in rows:
            age = now - row.created_at
            if row.level != "critical" and age > 7200:
                await self._mark(row.id, now)
                continue
            item = json.loads(row.body)
            if await self._deliver(item):
                await self._mark(row.id, now)

    async def _mark(self, row_id: int, now: float) -> None:
        from server.db import NotifySpill

        async def write(session):
            row = await session.get(NotifySpill, row_id)
            if row:
                row.sent_at = now

        await self.db.write(write)

    async def test(self) -> bool:
        return await self._deliver({"level": "warning", "text": "[SmartCCTV] 测试通知", "ts": self.clock(), "image": ""})
