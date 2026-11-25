"""开门通行窗口：采集 sighting，归并成一条 DoorPassage 摘要。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from server.bus import BusMessage, EventBus
from server.db import DoorPassage

log = logging.getLogger("smartcctv.passages")

PASSAGE_CAP_SEC = 120.0
# 通行窗口内陌生人短去重，避免同一人刷计数
_STRANGER_DEDUP_SEC = 2.0


@dataclass
class _ActivePassage:
    command_id: str
    started_at: float
    duration: float
    truncated_cap: bool
    known_ids: set[int] = field(default_factory=set)
    stranger_count: int = 0
    first_snapshot_path: str = ""
    _stranger_last: float = 0.0
    row_id: int | None = None


def passage_summary(row: DoorPassage) -> dict:
    return {
        "id": row.id,
        "command_id": row.command_id,
        "people_count": row.people_count,
        "stranger_count": row.stranger_count,
        "first_snapshot_path": row.first_snapshot_path,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "truncated": bool(row.truncated),
    }


class PassageCollector:
    def __init__(self, db, bus: EventBus | None = None) -> None:
        self.db = db
        self.bus = bus
        self._active: dict[str, _ActivePassage] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def observe(self, person_id: int | None, snapshot_path: str = "") -> None:
        """识别循环在通行窗口内上报一张脸（已知或陌生人）。"""
        if not self._active:
            return
        now = time.time()
        snap = snapshot_path or ""
        for state in self._active.values():
            if snap and not state.first_snapshot_path:
                state.first_snapshot_path = snap
            if person_id is not None:
                state.known_ids.add(int(person_id))
            else:
                if now - state._stranger_last >= _STRANGER_DEDUP_SEC:
                    state._stranger_last = now
                    state.stranger_count += 1

    async def start(self, command_id: str, seconds: float) -> dict:
        command_id = (command_id or "").strip()
        if not command_id:
            raise ValueError("缺少 command_id")
        requested = max(0.1, float(seconds))
        duration = min(requested, PASSAGE_CAP_SEC)
        truncated_cap = requested > PASSAGE_CAP_SEC
        now = time.time()

        async with self._lock:
            await self._finalize_locked(command_id, early=True)
            state = _ActivePassage(
                command_id=command_id,
                started_at=now,
                duration=duration,
                truncated_cap=truncated_cap,
            )
            self._active[command_id] = state
            task = asyncio.create_task(self._run_window(command_id, duration))
            self._tasks[command_id] = task

        log.info(
            "通行采集开始 command_id=%s duration=%.1fs truncated_cap=%s",
            command_id,
            duration,
            truncated_cap,
        )
        return {
            "ok": True,
            "command_id": command_id,
            "seconds": duration,
            "requested_seconds": requested,
            "truncated_cap": truncated_cap,
            "started_at": now,
        }

    async def stop(self, command_id: str) -> dict | None:
        command_id = (command_id or "").strip()
        async with self._lock:
            row = await self._finalize_locked(command_id, early=True)
        return passage_summary(row) if row else None

    async def _run_window(self, command_id: str, duration: float) -> None:
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return
        async with self._lock:
            await self._finalize_locked(command_id, early=False)

    async def _finalize_locked(self, command_id: str, *, early: bool) -> DoorPassage | None:
        task = self._tasks.pop(command_id, None)
        if task and not task.done() and early:
            task.cancel()
        state = self._active.pop(command_id, None)
        if state is None:
            return None
        if state.row_id is not None:
            async def read(session):
                return await session.get(DoorPassage, state.row_id)

            return await self.db.read(read)

        ended_at = time.time()
        people_count = len(state.known_ids) + state.stranger_count
        truncated = 1 if state.truncated_cap and not early else 0
        if truncated:
            log.warning(
                "通行采集截断 command_id=%s：已达 %ss 上限仍视为门开，停止继续写入",
                command_id,
                int(PASSAGE_CAP_SEC),
            )
            if self.bus:
                self.bus.publish(
                    BusMessage(
                        type="passage_truncated",
                        level="warning",
                        payload={
                            "command_id": command_id,
                            "cap_seconds": PASSAGE_CAP_SEC,
                            "people_count": people_count,
                            "stranger_count": state.stranger_count,
                        },
                    )
                )

        async def write(session):
            row = DoorPassage(
                command_id=command_id,
                people_count=people_count,
                stranger_count=state.stranger_count,
                first_snapshot_path=state.first_snapshot_path,
                started_at=state.started_at,
                ended_at=ended_at,
                truncated=truncated,
            )
            session.add(row)
            await session.flush()
            state.row_id = row.id
            return row

        row = await self.db.write(write)
        log.info(
            "通行归并完成 command_id=%s people=%s strangers=%s truncated=%s",
            command_id,
            people_count,
            state.stranger_count,
            bool(truncated),
        )
        return row

    async def list_passages(self, limit: int = 50) -> list[dict]:
        limit = min(200, max(1, limit))

        async def read(session):
            result = await session.execute(
                select(DoorPassage).order_by(DoorPassage.started_at.desc()).limit(limit)
            )
            return list(result.scalars())

        rows = await self.db.read(read)
        return [passage_summary(r) for r in rows]

    async def get_by_command(self, command_id: str) -> dict | None:
        command_id = (command_id or "").strip()
        if not command_id:
            return None
        # 仍在采集中：返回实时摘要
        state = self._active.get(command_id)
        if state is not None:
            people_count = len(state.known_ids) + state.stranger_count
            return {
                "id": None,
                "command_id": command_id,
                "people_count": people_count,
                "stranger_count": state.stranger_count,
                "first_snapshot_path": state.first_snapshot_path,
                "started_at": state.started_at,
                "ended_at": 0,
                "truncated": False,
                "active": True,
            }

        async def read(session):
            result = await session.execute(
                select(DoorPassage)
                .where(DoorPassage.command_id == command_id)
                .order_by(DoorPassage.started_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

        row = await self.db.read(read)
        if row is None:
            return None
        out = passage_summary(row)
        out["active"] = False
        return out
