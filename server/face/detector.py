"""SCRFD ONNX 检测包装。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from server.face.scrfd import decode_scrfd


class ScrfdDetector:
    def __init__(self, model_path: Path, providers: list[str] | None = None) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=providers or ["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.input_size = (
            int(shape[2]) if isinstance(shape[2], int) else 640,
            int(shape[3]) if isinstance(shape[3], int) else 640,
        )

    def detect(self, image: np.ndarray, score_thresh: float = 0.5) -> list[dict]:
        h0, w0 = image.shape[:2]
        th, tw = self.input_size
        scale = min(th / h0, tw / w0)
        nh, nw = int(h0 * scale), int(w0 * scale)
        resized = cv2.resize(image, (nw, nh))
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        canvas[:nh, :nw] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (tw, th), (127.5, 127.5, 127.5), swapRB=True)
        outputs = self.session.run(None, {self.input_name: blob})
        faces = decode_scrfd(outputs, input_size=self.input_size, score_thresh=score_thresh)
        for face in faces:
            box = face["bbox"]
            face["bbox"] = [box[0] / scale, box[1] / scale, box[2] / scale, box[3] / scale]
            if "kps" in face:
                face["kps"] = [[p[0] / scale, p[1] / scale] for p in face["kps"]]
        return faces
