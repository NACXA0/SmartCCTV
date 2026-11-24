"""磁盘水位。临界停录，恢复后由录像器续录。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path


class DiskMon:
    def __init__(self, path: Path, config) -> None:
        self.path = path
        self.config = config
        self.state = "OK"
        self.last: dict = {}

    def sample(self) -> dict:
        usage = shutil.disk_usage(self.path)
        free_ratio = usage.free / usage.total if usage.total else 0
        reserve = max(self.config.reserve_gb * (1024**3), usage.total * self.config.reserve_ratio)
        pool = max(0, usage.total - reserve)
        previous = self.state
        if free_ratio < self.config.critical_free_ratio:
            self.state = "CRITICAL"
        elif self.state == "CRITICAL" and free_ratio < self.config.critical_free_ratio + self.config.resume_hysteresis:
            self.state = "CRITICAL"
        elif free_ratio < self.config.warn_free_ratio:
            self.state = "WARN"
        else:
            self.state = "OK"
        self.last = {
            "state": self.state,
            "changed": previous != self.state,
            "previous": previous,
            "total": usage.total,
            "free": usage.free,
            "free_ratio": round(free_ratio, 4),
            "pool": pool,
            "used": usage.used,
            "ts": time.time(),
        }
        return self.last
