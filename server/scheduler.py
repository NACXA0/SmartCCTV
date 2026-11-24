"""削峰填谷：谷期跑重活，峰期让路。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from server.db import JobRow

log = logging.getLogger("smartcctv.scheduler")


def in_valley(now: datetime, start: str, end: str) -> bool:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    minutes = now.hour * 60 + now.minute
    a = sh * 60 + sm
    b = eh * 60 + em
    if a <= b:
        return a <= minutes < b
    return minutes >= a or minutes < b


class Scheduler:
    def __init__(self, config, db, load, record, storage) -> None:
        self.config = config
        self.db = db
        self.load = load
        self.record = record
        self.storage = storage
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.running = 0
        self.heartbeat = time.time()

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

    def valley_now(self) -> bool:
        cfg = self.config.scheduler
        local = datetime.now(ZoneInfo(self.config.timezone))
        by_time = in_valley(local, cfg.valley_start, cfg.valley_end)
        by_load = self.load.idle_for(cfg.idle_minutes * 60, cfg.idle_cpu)
        return by_time or by_load

    async def _loop(self) -> None:
        while not self._stop.is_set():
            self.heartbeat = time.time()
            if self.valley_now() and self.running < self.config.scheduler.max_concurrent:
                await self._run_job("checkpoint", self._checkpoint)
                await self._run_job("reindex", self.record.reindex)
            await asyncio.sleep(30)

    async def _run_job(self, name: str, fn) -> None:
        self.running += 1
        try:
            await self._mark(name, "running")
            await fn()
            await self._mark(name, "done")
        except Exception as exc:
            log.warning("job %s failed: %s", name, exc)
            await self._mark(name, "failed")
        finally:
            self.running = max(0, self.running - 1)

    async def _checkpoint(self) -> None:
        await self.db.checkpoint()

    async def _mark(self, name: str, status: str) -> None:
        async def write(session):
            from sqlalchemy import select

            result = await session.execute(select(JobRow).where(JobRow.name == name))
            row = result.scalar_one_or_none()
            now = time.time()
            if row is None:
                session.add(JobRow(name=name, status=status, updated_at=now))
            else:
                row.status = status
                row.updated_at = now

        await self.db.write(write)
