"""按帧亮度判定昼夜；灰度帧统一转 BGR。"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np


def ensure_bgr(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 1:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


def mean_luma(frame: np.ndarray) -> float:
    bgr = ensure_bgr(frame)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return float(np.mean(gray))


class DayNightState:
    """滑动平均亮度判定。阈值可配；带滞回防抖。"""

    def __init__(
        self,
        night_below: float = 45.0,
        day_above: float = 55.0,
        window: int = 15,
    ) -> None:
        self.night_below = night_below
        self.day_above = day_above
        self._values: deque[float] = deque(maxlen=window)
        self.mode = "day"
        self.changed = False

    def update(self, frame: np.ndarray) -> str:
        self._values.append(mean_luma(frame))
        avg = sum(self._values) / len(self._values)
        previous = self.mode
        if self.mode == "day" and avg < self.night_below:
            self.mode = "night"
        elif self.mode == "night" and avg > self.day_above:
            self.mode = "day"
        self.changed = previous != self.mode
        return self.mode
