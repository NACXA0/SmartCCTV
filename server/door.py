"""门禁执行末端：只消费事件，验证后执行。开门路径不经过 AI 同步调用。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod

import httpx

from server.bus import BusMessage, EventBus
from server.config import DoorConfig
from server.db import DOOR_SCHEMA, DoorLog

log = logging.getLogger("smartcctv.door")


class DoorAdapter(ABC):
    @abstractmethod
    async def open(self, seconds: float, command_id: str) -> bool: ...

    @abstractmethod
    async def close(self) -> bool: ...

    @abstractmethod
    async def state(self) -> str: ...


class MockDoorAdapter(DoorAdapter):
    def __init__(self) -> None:
        self._open_until = 0.0
        self.calls: list[dict] = []

    async def open(self, seconds: float, command_id: str) -> bool:
        self._open_until = time.time() + seconds
        self.calls.append({"action": "open", "seconds": seconds, "command_id": command_id, "ts": time.time()})
        log.info("mock door open %.1fs id=%s", seconds, command_id)
        return True

    async def close(self) -> bool:
        self._open_until = 0
        self.calls.append({"action": "close", "ts": time.time()})
        return True

    async def state(self) -> str:
        return "open" if time.time() < self._open_until else "closed"


class HttpRelayAdapter(DoorAdapter):
    def __init__(self, config: DoorConfig) -> None:
        self.config = config

    async def open(self, seconds: float, command_id: str) -> bool:
        url = self.config.open_url.replace("{seconds}", str(int(seconds))).replace("{command_id}", command_id)
        return await self._hit(url)

    async def close(self) -> bool:
        if not self.config.close_url:
            return True
        return await self._hit(self.config.close_url)

    async def state(self) -> str:
        return "unknown"

    async def _hit(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                if self.config.method == "POST":
                    response = await client.post(url)
                else:
                    response = await client.get(url)
            return response.status_code < 300
        except httpx.HTTPError as exc:
            log.warning("door http failed: %s", exc)
            return False


def build_adapter(config: DoorConfig) -> DoorAdapter:
    if config.adapter == "http" and config.open_url:
        return HttpRelayAdapter(config)
    return MockDoorAdapter()


class DoorService:
    """订阅 door.open 事件并执行；暴露手动开门。"""

    def __init__(self, config: DoorConfig, bus: EventBus, db) -> None:
        self.config = config
        self.bus = bus
        self.db = db
        self.adapter = build_adapter(config)
        self._cooldown: dict[str, float] = {}
        bus.add_handler(self._on_bus)

    def _on_bus(self, message: BusMessage) -> None:
        if message.type != "door.open":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._handle_open(message))

    async def _handle_open(self, message: BusMessage) -> None:
        reason = message.payload.get("reason", "face_match")
        person_id = message.payload.get("person_id")
        person_name = message.payload.get("person_name", "")
        similarity = message.payload.get("similarity")
        snapshot = message.payload.get("snapshot_path", "")
        key = f"{person_id or person_name}:{reason}"
        now = time.time()
        # 冷却由人脸侧也做一份；这里再兜底
        if now - self._cooldown.get(key, 0) < 5:
            return
        self._cooldown[key] = now
        command_id = message.payload.get("command_id") or str(uuid.uuid4())
        ok = await self.adapter.open(self.config.open_seconds, command_id)
        action = "open" if ok else "unknown"
        await self._log(person_id, person_name, similarity, action, reason, command_id, snapshot)
        if not ok:
            self.bus.publish(
                BusMessage(
                    type="door_unknown",
                    level="warning",
                    cam_id=message.cam_id,
                    payload={"command_id": command_id, "reason": reason},
                    schema=DOOR_SCHEMA,
                )
            )
        else:
            self.bus.publish(
                BusMessage(
                    type="door_open",
                    level="info",
                    cam_id=message.cam_id,
                    payload={
                        "command_id": command_id,
                        "person_id": person_id,
                        "person_name": person_name,
                        "reason": reason,
                    },
                    schema=DOOR_SCHEMA,
                )
            )

    async def manual_open(self, *, snapshot_path: str = "") -> dict:
        command_id = str(uuid.uuid4())
        message = BusMessage(
            type="door.open",
            level="info",
            payload={
                "reason": "manual",
                "command_id": command_id,
                "person_name": "manual",
                "snapshot_path": snapshot_path,
                "schema": DOOR_SCHEMA,
                # v2 预留
                "token": None,
                "qr": None,
            },
        )
        await self._handle_open(message)
        return {"command_id": command_id, "ok": True}

    async def startup_close(self) -> None:
        try:
            await self.adapter.close()
        except Exception:
            log.exception("door startup close failed")

    async def _log(
        self,
        person_id,
        person_name: str,
        similarity,
        action: str,
        reason: str,
        command_id: str,
        snapshot: str,
    ) -> None:
        async def write(session):
            session.add(
                DoorLog(
                    ts=time.time(),
                    person_id=person_id,
                    person_name=person_name or "",
                    similarity=similarity,
                    action=action,
                    reason=reason,
                    command_id=command_id,
                    snapshot_path=snapshot or "",
                )
            )

        await self.db.write(write)

    def status(self) -> dict:
        return {
            "id": self.config.id,
            "adapter": self.config.adapter,
            "camera_id": self.config.camera_id,
            "open_seconds": self.config.open_seconds,
        }
