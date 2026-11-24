"""区域入侵、越线、徘徊、聚集。几何部分是纯函数，便于单测。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from server.config import RuleConfig


def bottom_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, _y1, x2, y2 = bbox
    return ((x1 + x2) / 2, y2)


def point_in_polygon(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    count = len(points)
    j = count - 1
    for i in range(count):
        xi, yi = points[i]
        xj, yj = points[j]
        intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def cross_direction(
    p0: tuple[float, float],
    p1: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> int:
    """质心从 p0 到 p1 是否穿过有向线段 a→b。返回 1、−1 或 0。"""

    def side(p: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    s0 = side(p0)
    s1 = side(p1)
    if s0 == 0 or s1 == 0 or s0 * s1 > 0:
        return 0

    def side_ab(p: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> float:
        return (d[0] - c[0]) * (p[1] - c[1]) - (d[1] - c[1]) * (p[0] - c[0])

    t0 = side_ab(a, p0, p1)
    t1 = side_ab(b, p0, p1)
    if t0 * t1 > 0:
        return 0
    return 1 if s0 < 0 < s1 or (s0 < 0 and s1 > 0) else -1


def in_schedule(schedule: list[tuple[str, str]], now: datetime) -> bool:
    if not schedule:
        return True
    minutes = now.hour * 60 + now.minute
    for start, end in schedule:
        sh, sm = (int(part) for part in start.split(":"))
        eh, em = (int(part) for part in end.split(":"))
        a = sh * 60 + sm
        b = eh * 60 + em
        if a <= b:
            if a <= minutes < b:
                return True
        elif minutes >= a or minutes < b:
            return True
    return False


@dataclass
class TrackView:
    id: int
    bbox: tuple[float, float, float, float]
    cls: str = "person"


@dataclass
class Alert:
    rule_id: str
    cam_id: str
    type: str
    level: str
    track_id: int | None
    detail: dict


@dataclass
class _Mem:
    inside_frames: int = 0
    loiter_since: float | None = None
    last_inside: float = 0
    prev: tuple[float, float] | None = None
    last_alert: dict[str, float] = field(default_factory=dict)


class RuleEngine:
    def __init__(self, grace_sec: float = 2.0) -> None:
        self.grace_sec = grace_sec
        self._mem: dict[tuple[str, int], _Mem] = {}
        self._crowd_alert: dict[str, float] = {}

    def update(
        self,
        cam_id: str,
        tracks: list[TrackView],
        rules: list[RuleConfig],
        now: float | None = None,
        *,
        night: bool = False,
        timezone: str = "Asia/Shanghai",
    ) -> list[Alert]:
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, tz=ZoneInfo(timezone))
        alerts: list[Alert] = []
        persons = [track for track in tracks if track.cls == "person"]
        seen: set[tuple[str, int]] = set()
        for rule in rules:
            if rule.camera_id != cam_id or not self._armed(rule, local):
                continue
            confirm = rule.confirm_frames
            loiter_need = rule.loiter_seconds
            if night:
                confirm = max(1, confirm - 1)
                loiter_need = max(5.0, loiter_need * 0.7)
            if rule.kind == "zone":
                inside = []
                for track in persons:
                    point = bottom_center(track.bbox)
                    mem = self._memory(rule.id, track.id)
                    seen.add((rule.id, track.id))
                    mem.prev = point
                    if point_in_polygon(point[0], point[1], rule.points):
                        inside.append(track)
                        mem.inside_frames += 1
                        mem.last_inside = now
                        if mem.loiter_since is None:
                            mem.loiter_since = now
                        if mem.inside_frames == confirm:
                            alerts.extend(self._emit(rule, cam_id, "zone_intrude", track.id, now, {"frames": confirm}))
                        if mem.loiter_since and now - mem.loiter_since >= loiter_need:
                            alerts.extend(
                                self._emit(rule, cam_id, "loiter", track.id, now, {"seconds": round(now - mem.loiter_since, 1)})
                            )
                    else:
                        mem.inside_frames = 0
                        if mem.loiter_since and now - mem.last_inside > self.grace_sec:
                            mem.loiter_since = None
                if len(inside) >= rule.crowd_count:
                    last = self._crowd_alert.get(rule.id, 0)
                    if now - last >= rule.cooldown_sec:
                        self._crowd_alert[rule.id] = now
                        alerts.append(
                            Alert(rule.id, cam_id, "crowd", "warning", None, {"count": len(inside)})
                        )
            elif rule.kind == "line" and len(rule.points) == 2:
                segment = (rule.points[0], rule.points[1])
                for track in persons:
                    center = ((track.bbox[0] + track.bbox[2]) / 2, (track.bbox[1] + track.bbox[3]) / 2)
                    mem = self._memory(rule.id, track.id)
                    seen.add((rule.id, track.id))
                    if mem.prev is not None:
                        direction = cross_direction(mem.prev, center, segment[0], segment[1])
                        if direction:
                            alerts.extend(
                                self._emit(
                                    rule,
                                    cam_id,
                                    "line_cross",
                                    track.id,
                                    now,
                                    {"direction": "forward" if direction > 0 else "backward"},
                                )
                            )
                    mem.prev = center
        stale = [key for key in self._mem if key[0] in {rule.id for rule in rules} and key not in seen]
        for key in stale:
            mem = self._mem[key]
            if now - mem.last_inside > 15 and (mem.prev is None or now - mem.last_alert.get("_seen", mem.last_inside) > 15):
                self._mem.pop(key, None)
        return alerts

    def _armed(self, rule: RuleConfig, local: datetime) -> bool:
        if rule.armed == "off":
            return False
        if rule.armed == "schedule":
            return in_schedule(rule.schedule, local)
        return True

    def _memory(self, rule_id: str, track_id: int) -> _Mem:
        key = (rule_id, track_id)
        mem = self._mem.get(key)
        if mem is None:
            mem = _Mem()
            self._mem[key] = mem
        mem.last_alert.setdefault("_seen", time.time())
        return mem

    def _emit(self, rule: RuleConfig, cam_id: str, kind: str, track_id: int, now: float, detail: dict) -> list[Alert]:
        mem = self._memory(rule.id, track_id)
        last = mem.last_alert.get(kind, 0)
        mem.last_alert["_seen"] = now
        if now - last < rule.cooldown_sec:
            return []
        mem.last_alert[kind] = now
        level = "critical" if kind in {"zone_intrude", "loiter"} else "warning"
        return [Alert(rule.id, cam_id, kind, level, track_id, detail)]
