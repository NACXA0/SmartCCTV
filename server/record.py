"""门口连续分段录像 + 留存清理。"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path

from server.bus import BusMessage, EventBus
from server.cameras import resolve_source
from server.config import AppConfig, CameraConfig
from server.db import Recording
from server.diskmon import DiskMon
from server.paths import ensure_dir

log = logging.getLogger("smartcctv.record")


def build_record_cmd(url: str, out_pattern: str, segment: int = 600) -> list[str]:
    source = resolve_source(url)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    lower = url.lower()
    if lower.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp", "-rw_timeout", "5000000", "-stimeout", "5000000"]
    elif Path(source).exists() or lower.startswith("file://"):
        cmd += ["-re", "-stream_loop", "-1"]
    cmd += [
        "-i",
        source,
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(segment),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-movflags",
        "+faststart",
        out_pattern,
    ]
    return cmd


class RecordWorker:
    def __init__(self, cam: CameraConfig, out_dir: Path, diskmon: DiskMon) -> None:
        self.cam = cam
        self.out_dir = ensure_dir(out_dir)
        self.diskmon = diskmon
        self._proc: subprocess.Popen | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.alive = False
        self.last_error = ""

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    async def _loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            sample = self.diskmon.sample()
            if sample["state"] == "CRITICAL":
                self.alive = False
                self.last_error = "disk CRITICAL"
                await asyncio.sleep(5)
                continue
            pattern = str(self.out_dir / f"{self.cam.id}_%Y%m%d_%H%M%S.mp4")
            cmd = build_record_cmd(self.cam.url, pattern)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.alive = True
                self.last_error = ""
                await self._proc.wait()
                self.alive = False
                self.last_error = "ffmpeg exited"
            except Exception as exc:
                self.alive = False
                self.last_error = str(exc)
                log.warning("record %s: %s", self.cam.id, exc)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 1.5)


class RecordService:
    def __init__(self, config: AppConfig, db, storage, diskmon: DiskMon, bus: EventBus, root: Path) -> None:
        self.config = config
        self.db = db
        self.storage = storage
        self.diskmon = diskmon
        self.bus = bus
        self.root = root
        self.workers: dict[str, RecordWorker] = {}
        self._index_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.heartbeat = time.time()

    async def start(self) -> None:
        for cam in self.config.cameras:
            if cam.enabled and cam.continuous():
                out = self.storage.subdir("recordings", cam.id)
                worker = RecordWorker(cam, out, self.diskmon)
                self.workers[cam.id] = worker
                await worker.start()
        self._stop.clear()
        self._index_task = asyncio.create_task(self._index_loop())

    async def stop(self) -> None:
        self._stop.set()
        for worker in self.workers.values():
            await worker.stop()
        if self._index_task:
            self._index_task.cancel()
            try:
                await self._index_task
            except asyncio.CancelledError:
                pass

    async def _index_loop(self) -> None:
        while not self._stop.is_set():
            self.heartbeat = time.time()
            await self.reindex()
            await self.cleanup()
            sample = self.diskmon.sample()
            if sample.get("changed"):
                self.bus.publish(
                    BusMessage(
                        type="disk_state",
                        level="critical" if sample["state"] == "CRITICAL" else "warning",
                        payload=sample,
                    )
                )
            await asyncio.sleep(60)

    async def reindex(self) -> int:
        added = 0
        root = self.storage.recordings
        if not root.exists():
            return 0
        existing = set()

        async def read(session):
            from sqlalchemy import select

            result = await session.execute(select(Recording.path))
            return {row[0] for row in result.all()}

        existing = await self.db.read(read)
        for path in root.rglob("*.mp4"):
            rel = str(path.relative_to(self.storage.root))
            if rel in existing:
                continue
            cam_id = path.name.split("_")[0]
            mtime = path.stat().st_mtime
            start = mtime - 600

            async def write(session, rel=rel, cam_id=cam_id, start=start, end=mtime):
                session.add(
                    Recording(
                        cam_id=cam_id,
                        path=rel,
                        start_ts=start,
                        end_ts=end,
                        kind="continuous",
                        storage="local",
                    )
                )

            await self.db.write(write)
            added += 1
        return added

    async def cleanup(self) -> int:
        now = time.time()
        removed = 0

        async def read(session):
            from sqlalchemy import select

            result = await session.execute(select(Recording))
            return list(result.scalars())

        rows = await self.db.read(read)
        for row in rows:
            days = self.config.retention.continuous_days if row.kind == "continuous" else self.config.retention.event_days
            if now - row.end_ts <= days * 86400:
                continue
            try:
                path = self.storage.open_local(row.path)
                if path.exists():
                    path.unlink()
            except Exception:
                pass

            async def write(session, row_id=row.id):
                obj = await session.get(Recording, row_id)
                if obj:
                    await session.delete(obj)

            await self.db.write(write)
            removed += 1
        return removed

    def status(self) -> list[dict]:
        return [
            {"cam_id": cam_id, "alive": worker.alive, "error": worker.last_error}
            for cam_id, worker in self.workers.items()
        ]
