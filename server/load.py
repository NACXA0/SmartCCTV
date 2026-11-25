"""CPU 负载画像，供降频和谷期调度共用。"""

from __future__ import annotations

import time
from collections import deque


def read_cpu_times() -> tuple[int, int]:
    try:
        line = open("/proc/stat", encoding="utf-8").readline()
        parts = [int(item) for item in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts), idle
    except OSError:
        return 0, 0


def read_mem_percent() -> float:
    try:
        info = {}
        for line in open("/proc/meminfo", encoding="utf-8"):
            key, value = line.split(":", 1)
            info[key] = float(value.strip().split()[0])
        total = info.get("MemTotal", 1)
        avail = info.get("MemAvailable", total)
        return max(0.0, min(100.0, (1 - avail / total) * 100))
    except (OSError, ValueError):
        return 0.0


class LoadGauge:
    def __init__(self) -> None:
        self._prev = read_cpu_times()
        self._prev_at = time.monotonic()
        self.cpu = 0.0
        self.mem = 0.0
        self.history: deque[tuple[float, float]] = deque(maxlen=360)

    def sample(self) -> float:
        now = time.monotonic()
        current = read_cpu_times()
        total = current[0] - self._prev[0]
        idle = current[1] - self._prev[1]
        self._prev = current
        if total > 0 and now > self._prev_at:
            self.cpu = max(0.0, min(100.0, (1 - idle / total) * 100))
        self._prev_at = now
        self.mem = read_mem_percent()
        self.history.append((time.time(), self.cpu))
        return self.cpu

    def idle_for(self, seconds: float, threshold: float) -> bool:
        if not self.history:
            return False
        cutoff = time.time() - seconds
        window = [cpu for ts, cpu in self.history if ts >= cutoff]
        if len(window) < 2:
            return self.cpu < threshold
        return max(window) < threshold

    def peaks(self) -> dict:
        values = [cpu for _ts, cpu in self.history]
        if not values:
            return {"current": self.cpu, "max": 0, "p95": 0}
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return {"current": round(self.cpu, 1), "max": round(max(values), 1), "p95": round(ordered[index], 1)}
