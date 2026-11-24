"""门禁请求转发：不再调用继电器。软件开门只由 SmartCenter 执行。"""

from __future__ import annotations

import logging
import time
import uuid

from server.bus import BusMessage, EventBus
from server.config import DoorConfig
from server.db import DOOR_SCHEMA, DoorLog

log = logging.getLogger("smartcctv.door")


class DoorService:
    """把开门意图变成上报事件；本地永不 open() 继电器。"""

    def __init__(self, config: DoorConfig, bus: EventBus, db) -> None:
        self.config = config
        self.bus = bus
        self.db = db
        self._cooldown: dict[str, float] = {}
        bus.add_handler(self._on_bus)

    def _on_bus(self, message: BusMessage) -> None:
        # 兼容旧 door.open：改写为请求事件，不执行硬件
        if message.type != "door.open":
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._forward_request(message))

    async def _forward_request(self, message: BusMessage) -> None:
        reason = message.payload.get("reason", "face_match")
        person_id = message.payload.get("person_id")
        person_name = message.payload.get("person_name", "")
        key = f"{person_id or person_name}:{reason}"
        now = time.time()
        if now - self._cooldown.get(key, 0) < 5:
            return
        self._cooldown[key] = now
        command_id = message.payload.get("command_id") or str(uuid.uuid4())

        if reason == "manual":
            out_type = "door_request_manual"
        elif reason == "face_match":
            out_type = "face_known"
        else:
            out_type = "door_request_manual"

        payload = {
            **message.payload,
            "command_id": command_id,
            "person_id": person_id,
            "person_name": person_name,
            "reason": reason,
            "forwarded": True,
            "note": "监控不执行开门，已转交总控",
        }
        self.bus.publish(
            BusMessage(
                type=out_type,
                level="info",
                cam_id=message.cam_id,
                payload=payload,
                schema=message.schema,
                mode=message.mode,
            )
        )
        await self._log(
            person_id,
            person_name or "",
            message.payload.get("similarity"),
            "request",
            reason,
            command_id,
            message.payload.get("snapshot_path", ""),
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
                "token": None,
                "qr": None,
            },
        )
        await self._forward_request(message)
        return {"command_id": command_id, "ok": True, "forwarded_to_center": True}

    async def startup_close(self) -> None:
        # 不再校正继电器；总控负责上锁
        log.info("门禁本地执行已禁用，跳过 startup_close")

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
            "adapter": "center_only",
            "camera_id": self.config.camera_id,
            "open_seconds": self.config.open_seconds,
            "executes_locally": False,
        }
