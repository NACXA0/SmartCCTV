"""向 SmartCenter 上报事件（HMAC）。同类事件合并。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import httpx

from server.bus import BusMessage

log = logging.getLogger("smartcctv.center")

EXPORT_TYPES = {
    "face_known",
    "face_unknown",
    "zone_intrude",
    "loiter",
    "line_cross",
    "crowd",
    "door_request_manual",
}


def sign(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    payload = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{digest}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@dataclass
class CenterExporter:
    enabled: bool = False
    url: str = ""
    hmac_secret: str = ""
    merge_sec: float = 2.0
    timeout_sec: float = 3.0
    _last: dict[str, float] = field(default_factory=dict)

    def submit(self, message: BusMessage) -> None:
        if not self.enabled or not self.url or not self.hmac_secret:
            return
        if message.type not in EXPORT_TYPES:
            return
        key = f"{message.type}:{message.cam_id}:{message.payload.get('person_id', '')}"
        now = time.time()
        if now - self._last.get(key, 0) < self.merge_sec:
            return
        self._last[key] = now
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._post(message))

    async def _post(self, message: BusMessage) -> None:
        body = json.dumps(message.to_dict(), ensure_ascii=False).encode()
        from urllib.parse import urlparse

        path = urlparse(self.url).path or "/api/lobn_office/events"
        ts = str(int(time.time()))
        nonce = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "X-Lobn-Timestamp": ts,
            "X-Lobn-Nonce": nonce,
            "X-Lobn-Signature": sign(self.hmac_secret, ts, nonce, "POST", path, body),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(self.url, content=body, headers=headers)
            if resp.status_code >= 300:
                log.warning("上报总控失败 HTTP %s", resp.status_code)
        except httpx.HTTPError as exc:
            log.warning("上报总控异常: %s", exc)
