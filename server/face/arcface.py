"""ArcFace ONNX 推理与人脸对齐。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from server.face.match import l2_normalize

# InsightFace 常用 5 点模板（112x112）
_ARC_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def align_face(image: np.ndarray, kps: np.ndarray, size: int = 112) -> np.ndarray:
    src = np.asarray(kps, dtype=np.float32).reshape(5, 2)
    dst = _ARC_DST * (size / 112.0)
    matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if matrix is None:
        x1, y1 = src.min(axis=0).astype(int)
        x2, y2 = src.max(axis=0).astype(int)
        crop = image[max(0, y1) : max(y1 + 1, y2), max(0, x1) : max(x1 + 1, x2)]
        return cv2.resize(crop, (size, size))
    return cv2.warpAffine(image, matrix, (size, size), borderValue=0)


class ArcFace:
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
        self.size = int(shape[2]) if isinstance(shape[2], int) else 112

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        img = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        img = (img - 127.5) / 127.5
        blob = np.transpose(img, (2, 0, 1))[None, ...]
        out = self.session.run(None, {self.input_name: blob})[0]
        return l2_normalize(out.reshape(-1))
