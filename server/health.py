"""系统自检。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path


async def collect_health(runtime) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str = "", level: str = "ok") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": "ok" if ok else level})

    # DB
    try:
        integrity = await runtime.db.integrity()
        add("database", integrity == "ok", integrity)
    except Exception as exc:
        add("database", False, str(exc), "fail")

    # models
    detect_ok = runtime.detector.available
    add("yolo", detect_ok, runtime.detector.last_error or "loaded", "warn")
    face_ok = runtime.face.available
    add("face", face_ok, runtime.face.last_error or "loaded", "warn")

    # ffmpeg
    add("ffmpeg", shutil.which("ffmpeg") is not None, shutil.which("ffmpeg") or "missing", "fail")

    # cameras
    for cam in runtime.hub.status():
        add(f"camera:{cam['id']}", cam["alive"], cam.get("error") or "alive", "warn")

    # disk
    disk = runtime.diskmon.sample()
    add("disk", disk["state"] != "CRITICAL", f"{disk['state']} free={disk['free_ratio']}", "fail" if disk["state"] == "CRITICAL" else "warn")

    # time sync hint
    add("time", True, time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime()))

    # S3
    if runtime.config.storage.s3_enabled:
        add("s3", runtime.storage.s3_ready(), "configured" if runtime.storage.s3_ready() else "incomplete", "warn")
    else:
        add("s3", True, "disabled")

    # notify
    note = runtime.config.notify
    configured = bool(note.gotify_url or note.wecom_webhook)
    add("notify", True, "configured" if configured else "panel-only")

    # load
    peaks = runtime.load.peaks()
    add("load", peaks["current"] < 95, str(peaks), "warn")

    overall = "ok"
    if any(c["level"] == "fail" for c in checks):
        overall = "fail"
    elif any(not c["ok"] for c in checks):
        overall = "warn"

    return {
        "status": overall,
        "checks": checks,
        "ts": time.time(),
        "models_dir": str(Path(runtime.config.models_dir)),
    }
