"""进程内事件总线。schema 只增字段、不改已有字段。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from server.db import BUS_SCHEMA


@dataclass
class BusMessage:
    type: str
    level: str = "info"
    cam_id: str | None = None
    payload: dict = field(default_factory=dict)
    schema: str = BUS_SCHEMA
    ts: float = field(default_factory=time.time)
    mode: str = "day"

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "type": self.type,
            "level": self.level,
            "cam_id": self.cam_id,
            "payload": self.payload,
            "ts": self.ts,
            "mode": self.mode,
        }


class EventBus:
    def __init__(self, queue_size: int = 200) -> None:
        self.queue_size = queue_size
        self._subscribers: list[asyncio.Queue] = []
        self._handlers: list[Callable[[BusMessage], None]] = []
        self.dropped = 0

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def add_handler(self, handler: Callable[[BusMessage], None]) -> None:
        self._handlers.append(handler)

    def publish(self, message: BusMessage) -> None:
        for handler in list(self._handlers):
            try:
                handler(message)
            except Exception:
                continue
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self.dropped += 1
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.dropped += 1
