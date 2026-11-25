"""认证捕捉辅助与通行采集分离。"""

from __future__ import annotations

from types import SimpleNamespace

from server.face.pipeline import FaceService


class _Hub:
    def __init__(self, frame=None):
        self._frame = frame

    def get(self, cam_id):
        if self._frame is None:
            return SimpleNamespace(frame=None, mode="day")
        return SimpleNamespace(frame=self._frame, mode="day")


def _svc(hub):
    cfg = SimpleNamespace(
        door=SimpleNamespace(camera_id="door1"),
        cameras=[SimpleNamespace(id="door1", role="door")],
    )
    svc = FaceService.__new__(FaceService)
    svc.config = cfg
    svc.hub = hub
    svc.assist_until = 0.0
    svc.assist_source = ""
    svc.assist_frames = 0
    svc._task = None
    return svc


def test_assist_no_frames():
    svc = _svc(_Hub(frame=None))
    out = svc.start_assist("pir", 5)
    assert out["ok"] is True
    assert out["frames"] == 0
    assert out["source"] == "pir"
    assert svc.assist_active()


def test_assist_with_frame():
    svc = _svc(_Hub(frame=object()))
    out = svc.start_assist("button", 8)
    assert out["frames"] == 1
    # 延长窗口不双开
    out2 = svc.start_assist("button", 3)
    assert out2["until"] >= out["until"]
