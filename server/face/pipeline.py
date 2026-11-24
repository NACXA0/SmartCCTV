"""人脸识别管线：检测 → 对齐 → embedding → FaceDB 匹配 → 门禁事件。"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from server.bus import BusMessage, EventBus
from server.cameras import CameraHub
from server.config import AppConfig
from server.face.arcface import ArcFace, align_face
from server.face.detector import ScrfdDetector
from server.face.match import best_match, l2_normalize
from server.secrets import SecretStore

log = logging.getLogger("smartcctv.face")


class FaceDB:
    def __init__(self, secrets: SecretStore) -> None:
        self.secrets = secrets
        self.ids: list[int] = []
        self.names: dict[int, str] = {}
        self.notes: dict[int, str] = {}
        self.matrix = np.zeros((0, 512), dtype=np.float32)

    def reload(self, rows: list[tuple[int, str, bytes]] | list[tuple[int, str, str, bytes]]) -> None:
        ids: list[int] = []
        names: dict[int, str] = {}
        notes: dict[int, str] = {}
        vectors = []
        for row in rows:
            if len(row) == 4:
                person_id, name, note, blob = row  # type: ignore[misc]
            else:
                person_id, name, blob = row  # type: ignore[misc]
                note = ""
            try:
                vec = np.frombuffer(self.secrets.decrypt(blob), dtype=np.float32)
            except Exception:
                continue
            ids.append(person_id)
            names[person_id] = name
            notes[person_id] = note or ""
            vectors.append(l2_normalize(vec))
        self.ids = ids
        self.names = names
        self.notes = notes
        self.matrix = np.vstack(vectors) if vectors else np.zeros((0, 512), dtype=np.float32)

    def match(self, embedding: np.ndarray, threshold: float) -> tuple[int | None, float, str]:
        pid, score = best_match(embedding, self.matrix, self.ids, threshold)
        return pid, score, self.names.get(pid or -1, "")

    def note_of(self, person_id: int) -> str:
        return self.notes.get(person_id, "")


class FaceService:
    def __init__(
        self,
        config: AppConfig,
        hub: CameraHub,
        bus: EventBus,
        db,
        secrets: SecretStore,
        storage,
        root: Path,
    ) -> None:
        self.config = config
        self.hub = hub
        self.bus = bus
        self.db = db
        self.secrets = secrets
        self.storage = storage
        self.root = root
        self.facedb = FaceDB(secrets)
        self.detector: ScrfdDetector | None = None
        self.recognizer: ArcFace | None = None
        self.available = False
        self.last_error = ""
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._confirm: dict[str, dict] = {}
        self._cooldown: dict[int, float] = {}
        self._unknown_cooldown: dict[str, float] = {}
        self.heartbeat = time.time()

    def try_load(self) -> bool:
        det = Path(self.config.face.det_model)
        rec = Path(self.config.face.rec_model)
        if not det.is_absolute():
            det = self.root / det
        if not rec.is_absolute():
            rec = self.root / rec
        if not det.exists() or not rec.exists():
            self.available = False
            self.last_error = f"人脸模型缺失: {det} / {rec}"
            log.warning(self.last_error)
            return False
        try:
            self.detector = ScrfdDetector(det)
            self.recognizer = ArcFace(rec)
            self.available = True
            self.last_error = ""
            return True
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            log.exception("face model load failed")
            return False

    async def reload_gallery(self) -> None:
        from sqlalchemy import select

        from server.db import FaceEmbedding, Person

        async def read(session):
            result = await session.execute(
                select(FaceEmbedding.person_id, Person.name, Person.note, FaceEmbedding.embedding)
                .join(Person, Person.id == FaceEmbedding.person_id)
                .where(Person.enabled == 1, Person.deleted_at.is_(None))
            )
            return list(result.all())

        rows = await self.db.read(read)
        self.facedb.reload([(r[0], r[1], r[2] or "", r[3]) for r in rows])

    async def start(self) -> None:
        self.try_load()
        await self.reload_gallery()
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
            door_cam = self.config.door.camera_id
            targets = [door_cam] if door_cam else [c.id for c in self.config.cameras if c.role == "door"]
            for cam_id in targets:
                if cam_id:
                    await self._process(cam_id)
            await asyncio.sleep(0.2)

    async def _process(self, cam_id: str) -> None:
        if not self.available or not self.detector or not self.recognizer:
            return
        slot = self.hub.get(cam_id)
        if not slot or slot.frame is None:
            return
        cam = next((c for c in self.config.cameras if c.id == cam_id), None)
        if cam is None:
            return
        night = slot.mode == "night"
        threshold = self.config.face.threshold
        confirm_need = self.config.face.confirm_frames
        if night:
            if cam.night_profile.face_mode == "alert_only":
                # 夜间仅记录，不开门
                pass
            threshold += cam.night_profile.face_threshold_delta
            confirm_need += cam.night_profile.face_confirm_frames_delta
        frame = slot.frame
        faces = await asyncio.to_thread(self.detector.detect, frame, self.config.face.min_det_score)
        if not faces:
            return
        # 多人时取最靠下（最靠近门）的脸
        faces.sort(key=lambda f: f["bbox"][3], reverse=True)
        face = faces[0]
        box = face["bbox"]
        if (box[2] - box[0]) < self.config.face.min_box_px or (box[3] - box[1]) < self.config.face.min_box_px:
            return
        kps = np.asarray(face.get("kps") or [], dtype=np.float32)
        if kps.size != 10:
            return
        aligned = align_face(frame, kps)
        emb = await asyncio.to_thread(self.recognizer.embed, aligned)
        pid, score, name = self.facedb.match(emb, threshold)
        now = time.time()
        key = cam_id
        state = self._confirm.setdefault(key, {"pid": None, "count": 0})
        if pid is None:
            state["pid"] = None
            state["count"] = 0
            if score < threshold:
                # 归并：同摄像头 30s 内只上报一次陌生人，避免刷爆总控
                ukey = cam_id
                if now - self._unknown_cooldown.get(ukey, 0) >= 30:
                    self._unknown_cooldown[ukey] = now
                    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    snap = self.storage.save_jpeg(cam_id, buf.tobytes(), now) if ok else ""
                    self.bus.publish(
                        BusMessage(
                            type="face_unknown",
                            level="warning",
                            cam_id=cam_id,
                            payload={
                                "similarity": round(float(score), 3),
                                "snapshot_path": snap,
                                "authorized": False,
                            },
                            mode=slot.mode,
                        )
                    )
            return
        if state["pid"] == pid:
            state["count"] += 1
        else:
            state["pid"] = pid
            state["count"] = 1
        if state["count"] < confirm_need:
            return
        if now - self._cooldown.get(pid, 0) < self.config.face.cooldown_sec:
            return
        if night and cam.night_profile.face_mode == "alert_only":
            self.bus.publish(
                BusMessage(
                    type="face_known",
                    level="info",
                    cam_id=cam_id,
                    payload={"person_id": pid, "person_name": name, "similarity": score, "night_alert_only": True},
                    mode=slot.mode,
                )
            )
            return
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        snapshot = self.storage.save_jpeg(cam_id, buf.tobytes(), now) if ok else ""
        self._cooldown[pid] = now
        # 只上报事实；开门由 SmartCenter 决策。person.note 约定为云端 user_id
        self.bus.publish(
            BusMessage(
                type="face_known",
                level="info",
                cam_id=cam_id,
                payload={
                    "person_id": pid,
                    "person_name": name,
                    "user_id": self.facedb.note_of(pid),
                    "similarity": score,
                    "snapshot_path": snapshot,
                },
                mode=slot.mode,
            )
        )

    def embed_image(self, image: np.ndarray) -> np.ndarray | None:
        if not self.detector or not self.recognizer:
            return None
        faces = self.detector.detect(image, self.config.face.min_det_score)
        if not faces:
            return None
        face = max(faces, key=lambda f: f["score"])
        kps = np.asarray(face.get("kps") or [], dtype=np.float32)
        if kps.size != 10:
            return None
        return self.recognizer.embed(align_face(image, kps))
