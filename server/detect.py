"""YOLO26 检测 + 跟踪 + 规则引擎驱动。模型缺失时进入纯观看模式。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import numpy as np

from server.activity import ActivityGate
from server.bus import BusMessage, EventBus
from server.cameras import CameraHub
from server.config import AppConfig
from server.db import DetectionStat, EventRow
from server.load import LoadGauge
from server.rules import RuleEngine, TrackView
from server.tracking import CameraTrackers, Det

log = logging.getLogger("smartcctv.detect")


class DetectorService:
    def __init__(
        self,
        config: AppConfig,
        hub: CameraHub,
        bus: EventBus,
        db,
        load: LoadGauge,
        root: Path,
    ) -> None:
        self.config = config
        self.hub = hub
        self.bus = bus
        self.db = db
        self.load = load
        self.root = root
        self.rules = RuleEngine()
        self.trackers = CameraTrackers()
        self.gates = {cam.id: ActivityGate(config.detect.activity_gate) for cam in config.cameras}
        self.model = None
        self.available = False
        self.last_error = ""
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_force: dict[str, float] = {}
        self._stats_bucket: dict[str, dict] = {}
        self.heartbeat = time.time()

    def try_load(self) -> bool:
        model_path = Path(self.config.detect.model)
        if not model_path.is_absolute():
            model_path = self.root / model_path
        if not model_path.exists():
            self.available = False
            self.last_error = f"模型缺失: {model_path}"
            log.warning(self.last_error)
            return False
        try:
            import torch
            from ultralytics import YOLO

            torch.set_num_threads(max(1, self.config.detect.torch_threads))
            self.model = YOLO(str(model_path))
            self.available = True
            self.last_error = ""
            log.info("YOLO loaded: %s", model_path)
            return True
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            log.exception("YOLO load failed")
            return False

    async def start(self) -> None:
        self.try_load()
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
            self.heartbeat = time.time()
            cpu = self.load.sample()
            scale = 1.0
            if cpu > 85:
                scale = 0.4
            elif cpu > 70:
                scale = 0.7
            for cam in self.config.cameras:
                if not cam.enabled:
                    continue
                await self._process_cam(cam.id, cam.analysis_fps * scale, night=False)
            await self._flush_stats()
            await asyncio.sleep(0.05)

    async def _process_cam(self, cam_id: str, fps: float, night: bool) -> None:
        slot = self.hub.get(cam_id)
        if not slot or slot.frame is None:
            return
        cam = next(c for c in self.config.cameras if c.id == cam_id)
        night = slot.mode == "night"
        target_fps = cam.night_profile.analysis_fps if night else fps
        target_fps = max(0.2, target_fps)
        now = time.time()
        last = self._last_force.get(cam_id, 0)
        interval = 1.0 / target_fps
        force = now - last >= self.config.detect.force_interval_sec
        if now - last < interval and not force:
            return
        frame = slot.frame
        gate = self.gates.setdefault(cam_id, ActivityGate(self.config.detect.activity_gate))
        active, _score = gate.active(frame)
        if not active and not force:
            self.hub.set_overlays(cam_id, [])
            return
        self._last_force[cam_id] = now
        if not self.available or self.model is None:
            self.hub.set_overlays(cam_id, [])
            return
        dets = await asyncio.to_thread(self._infer, frame)
        tracks = self.trackers.update(cam_id, dets)
        overlays = [
            {
                "bbox": [int(t.bbox[0]), int(t.bbox[1]), int(t.bbox[2]), int(t.bbox[3])],
                "label": f"{t.cls}#{t.id}",
                "color": (40, 200, 80) if t.cls == "person" else (80, 160, 255),
            }
            for t in tracks
        ]
        self.hub.set_overlays(cam_id, overlays)
        h, w = frame.shape[:2]
        views = [
            TrackView(
                t.id,
                (t.bbox[0] / w, t.bbox[1] / h, t.bbox[2] / w, t.bbox[3] / h),
                t.cls,
            )
            for t in tracks
        ]
        alerts = self.rules.update(
            cam_id,
            views,
            self.config.rules,
            now=now,
            night=night,
            timezone=self.config.timezone,
        )
        person_count = sum(1 for t in tracks if t.cls == "person")
        self._accumulate_stats(cam_id, person_count, len(alerts), now)
        for alert in alerts:
            await self._emit_alert(alert, slot.mode)

    def _infer(self, frame: np.ndarray) -> list[Det]:
        assert self.model is not None
        results = self.model.predict(
            frame,
            imgsz=self.config.detect.imgsz,
            conf=self.config.detect.conf,
            verbose=False,
            device="cpu",
        )
        dets: list[Det] = []
        if not results:
            return dets
        result = results[0]
        names = result.names or {}
        boxes = result.boxes
        if boxes is None:
            return dets
        for box in boxes:
            cls_id = int(box.cls.item())
            name = names.get(cls_id, str(cls_id))
            if name not in {"person"}:
                continue
            xyxy = box.xyxy.cpu().numpy().reshape(-1)
            dets.append(Det((float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])), float(box.conf.item()), name))
        dets.sort(key=lambda d: d.score, reverse=True)
        return dets[: self.config.detect.max_boxes]

    async def _emit_alert(self, alert, mode: str) -> None:
        payload = {"rule_id": alert.rule_id, "track_id": alert.track_id, **alert.detail}

        async def write(session):
            row = EventRow(
                cam_id=alert.cam_id,
                type=alert.type,
                level=alert.level,
                payload=json.dumps(payload, ensure_ascii=False),
                mode=mode,
                ts=time.time(),
            )
            session.add(row)
            await session.flush()
            return row.id

        event_id = await self.db.write(write)
        payload["event_id"] = event_id
        self.bus.publish(
            BusMessage(type=alert.type, level=alert.level, cam_id=alert.cam_id, payload=payload, mode=mode)
        )

    def _accumulate_stats(self, cam_id: str, persons: int, events: int, now: float) -> None:
        bucket = int(now // 60) * 60
        key = f"{cam_id}:{bucket}"
        item = self._stats_bucket.get(key)
        if item is None:
            item = {
                "bucket_start": bucket,
                "cam_id": cam_id,
                "person_max": persons,
                "person_sum": persons,
                "samples": 1,
                "events": events,
                "cpu_sum": self.load.cpu,
                "mem_sum": self.load.mem,
            }
            self._stats_bucket[key] = item
        else:
            item["person_max"] = max(item["person_max"], persons)
            item["person_sum"] += persons
            item["samples"] += 1
            item["events"] += events
            item["cpu_sum"] += self.load.cpu
            item["mem_sum"] += self.load.mem

    async def _flush_stats(self) -> None:
        if not self._stats_bucket:
            return
        now = time.time()
        ready = [key for key, item in self._stats_bucket.items() if now - item["bucket_start"] >= 60]
        for key in ready:
            item = self._stats_bucket.pop(key)

            async def write(session, data=item):
                from sqlalchemy import select

                result = await session.execute(
                    select(DetectionStat).where(
                        DetectionStat.bucket_start == data["bucket_start"],
                        DetectionStat.cam_id == data["cam_id"],
                    )
                )
                row = result.scalar_one_or_none()
                samples = max(1, data["samples"])
                if row is None:
                    session.add(
                        DetectionStat(
                            bucket_start=data["bucket_start"],
                            cam_id=data["cam_id"],
                            person_max=data["person_max"],
                            person_sum=data["person_sum"],
                            samples=data["samples"],
                            events=data["events"],
                            cpu_avg=data["cpu_sum"] / samples,
                            mem_avg=data["mem_sum"] / samples,
                        )
                    )
                else:
                    row.person_max = max(row.person_max, data["person_max"])
                    row.person_sum += data["person_sum"]
                    row.samples += data["samples"]
                    row.events += data["events"]

            await self.db.write(write)
