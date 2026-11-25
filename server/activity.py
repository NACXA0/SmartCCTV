"""帧差活动闸门：静止画面跳过重推理。"""

from __future__ import annotations

import cv2
import numpy as np


class ActivityGate:
    def __init__(self, threshold: float = 1.2) -> None:
        self.threshold = threshold
        self._prev: np.ndarray | None = None

    def active(self, frame: np.ndarray) -> tuple[bool, float]:
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = gray.astype(np.float32)
        if self._prev is None:
            self._prev = gray
            return True, 999.0
        score = float(np.mean(np.abs(gray - self._prev)))
        self._prev = gray
        return score >= self.threshold, score
