"""ffmpeg 子进程读帧 + latest-frame 总线 + MJPEG。"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from server.config import AppConfig, CameraConfig
from server.night import DayNightState, ensure_bgr

log = logging.getLogger("smartcctv.cameras")


@dataclass
class FrameSlot:
    frame: np.ndarray | None = None
    seq: int = 0
    ts: float = 0
    width: int = 0
    height: int = 0
    mode: str = "day"
    fps_in: float = 0
    dropped: int = 0
    restarts: int = 0
    alive: bool = False
    last_error: str = ""
    overlays: list[dict] = field(default_factory=list)
    event: threading.Event = field(default_factory=threading.Event)


class ReconnectLimiter:
    def __init__(self, per_sec: float = 2.0) -> None:
        self.per_sec = per_sec
        self._times: list[float] = []
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.time()
            self._times = [t for t in self._times if now - t < 1.0]
            if len(self._times) >= self.per_sec:
                sleep_for = 1.0 - (now - self._times[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self._times.append(time.time())


def resolve_source(url: str) -> str:
    if url.startswith("file://"):
        return url[7:]
    return url


def build_analyze_cmd(url: str, width: int | None = None, height: int | None = None) -> list[str]:
    source = resolve_source(url)
    lower = url.lower()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if lower.startswith(("rtsp://", "rtsps://")):
        cmd += [
            "-rtsp_transport",
            "tcp",
            "-rw_timeout",
            "5000000",
            "-stimeout",
            "5000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
    elif Path(source).exists() or lower.startswith("file://"):
        cmd += ["-re", "-stream_loop", "-1"]
    cmd += ["-i", source, "-an", "-f", "rawvideo", "-pix_fmt", "bgr24"]
    if width and height:
        cmd += ["-s", f"{width}x{height}"]
    cmd += ["-"]
    return cmd


def probe_size(url: str, timeout: float = 8.0) -> tuple[int, int]:
    source = resolve_source(url)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        source,
    ]
    try:
        out = subprocess.check_output(cmd, timeout=timeout, stderr=subprocess.DEVNULL)
        text = out.decode().strip()
        w, h = text.split("x")
        return int(w), int(h)
    except Exception:
        return 640, 480


class CameraWorker:
    def __init__(self, cam: CameraConfig, slot: FrameSlot, limiter: ReconnectLimiter) -> None:
        self.cam = cam
        self.slot = slot
        self.limiter = limiter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self.daynight = DayNightState()
        self.width = 0
        self.height = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.cam.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout=3)

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self.limiter.wait()
            try:
                if not self.width:
                    self.width, self.height = probe_size(self.cam.url)
                    self.slot.width = self.width
                    self.slot.height = self.height
                cmd = build_analyze_cmd(self.cam.url, self.width, self.height)
                log.info("camera %s start: %s", self.cam.id, " ".join(shlex.quote(c) for c in cmd[:8]))
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
                self.slot.alive = True
                self.slot.last_error = ""
                frame_bytes = self.width * self.height * 3
                assert self._proc.stdout is not None
                last = time.time()
                frames = 0
                while not self._stop.is_set():
                    raw = self._proc.stdout.read(frame_bytes)
                    if not raw or len(raw) < frame_bytes:
                        raise RuntimeError("ffmpeg 无帧或提前退出")
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()
                    frame = ensure_bgr(frame)
                    mode = self.daynight.update(frame)
                    self.slot.frame = frame
                    self.slot.seq += 1
                    self.slot.ts = time.time()
                    self.slot.mode = mode
                    self.slot.event.set()
                    frames += 1
                    now = time.time()
                    if now - last >= 1.0:
                        self.slot.fps_in = frames / (now - last)
                        frames = 0
                        last = now
                    backoff = 1.0
                break
            except Exception as exc:
                self.slot.alive = False
                self.slot.last_error = str(exc)
                self.slot.restarts += 1
                log.warning("camera %s restart: %s", self.cam.id, exc)
                self._kill()
                time.sleep(backoff + (0.1 * (hash(self.cam.id) % 10)))
                backoff = min(30.0, backoff * 1.8)
        self.slot.alive = False
        self._kill()


class CameraHub:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.slots: dict[str, FrameSlot] = {}
        self.workers: dict[str, CameraWorker] = {}
        self.viewers: dict[str, int] = {}
        self._limiter = ReconnectLimiter(2)
        self._lock = threading.Lock()

    def start(self) -> None:
        for cam in self.config.cameras:
            if not cam.enabled:
                continue
            slot = FrameSlot()
            self.slots[cam.id] = slot
            worker = CameraWorker(cam, slot, self._limiter)
            self.workers[cam.id] = worker
            worker.start()

    def stop(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        self.workers.clear()

    def get(self, cam_id: str) -> FrameSlot | None:
        return self.slots.get(cam_id)

    def set_overlays(self, cam_id: str, overlays: list[dict]) -> None:
        slot = self.slots.get(cam_id)
        if slot:
            slot.overlays = overlays

    def acquire_viewer(self, cam_id: str) -> bool:
        with self._lock:
            total = sum(self.viewers.values())
            if total >= self.config.watch.max_viewers:
                return False
            current = self.viewers.get(cam_id, 0)
            if current >= self.config.watch.fanout_per_camera:
                return False
            self.viewers[cam_id] = current + 1
            return True

    def release_viewer(self, cam_id: str) -> None:
        with self._lock:
            current = self.viewers.get(cam_id, 0)
            self.viewers[cam_id] = max(0, current - 1)

    def status(self) -> list[dict]:
        rows = []
        for cam in self.config.cameras:
            slot = self.slots.get(cam.id)
            rows.append(
                {
                    "id": cam.id,
                    "name": cam.name,
                    "role": cam.role,
                    "enabled": cam.enabled,
                    "alive": bool(slot and slot.alive),
                    "width": slot.width if slot else 0,
                    "height": slot.height if slot else 0,
                    "mode": slot.mode if slot else "day",
                    "fps_in": round(slot.fps_in, 1) if slot else 0,
                    "restarts": slot.restarts if slot else 0,
                    "error": slot.last_error if slot else "",
                    "viewers": self.viewers.get(cam.id, 0),
                }
            )
        return rows


def draw_overlays(frame: np.ndarray, overlays: list[dict]) -> np.ndarray:
    out = frame.copy()
    for item in overlays:
        x1, y1, x2, y2 = [int(v) for v in item.get("bbox", (0, 0, 0, 0))]
        color = tuple(item.get("color", (0, 200, 80)))
        label = str(item.get("label", ""))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        if label:
            cv2.putText(out, label, (x1, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


async def mjpeg_generator(hub: CameraHub, cam_id: str, quality: int, width: int):
    slot = hub.get(cam_id)
    if slot is None:
        return
    if not hub.acquire_viewer(cam_id):
        yield b"--frame\r\nContent-Type: text/plain\r\n\r\nviewer limit\r\n"
        return
    last_seq = -1
    try:
        while True:
            if not slot.event.wait(timeout=2):
                await asyncio.sleep(0.05)
                continue
            slot.event.clear()
            if slot.seq == last_seq or slot.frame is None:
                await asyncio.sleep(0.02)
                continue
            last_seq = slot.seq
            frame = draw_overlays(slot.frame, slot.overlays)
            h, w = frame.shape[:2]
            if w > width > 0:
                nh = int(h * (width / w))
                frame = cv2.resize(frame, (width, nh), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            payload = buf.tobytes()
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
                + b"\r\n"
            )
            await asyncio.sleep(0.05)
    finally:
        hub.release_viewer(cam_id)
