"""轻量多目标关联。Ultralytics ByteTrack 可用时优先，否则用两阶段 IoU。"""

from __future__ import annotations

from dataclasses import dataclass, field


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


@dataclass
class Det:
    bbox: tuple[float, float, float, float]
    score: float
    cls: str = "person"


@dataclass
class Tracked:
    id: int
    bbox: tuple[float, float, float, float]
    score: float
    cls: str
    age: int = 0


@dataclass
class _Slot:
    id: int
    bbox: tuple[float, float, float, float]
    score: float
    cls: str
    missed: int = 0
    hits: int = 1


class IoUTracker:
    """ByteTrack 风格：先匹配高分框，再把低分框补给未匹配轨迹。"""

    def __init__(self, high: float = 0.5, low: float = 0.1, match_iou: float = 0.3, max_missed: int = 15) -> None:
        self.high = high
        self.low = low
        self.match_iou = match_iou
        self.max_missed = max_missed
        self._next = 1
        self._slots: list[_Slot] = []

    def update(self, dets: list[Det]) -> list[Tracked]:
        high = [det for det in dets if det.score >= self.high]
        low = [det for det in dets if self.low <= det.score < self.high]
        matched: set[int] = set()
        used_high = self._assign(self._slots, high, matched)
        unmatched = [slot for slot in self._slots if id(slot) not in matched]
        self._assign(unmatched, low, matched)
        for det_index, det in enumerate(high):
            if det_index not in used_high:
                self._slots.append(_Slot(self._next, det.bbox, det.score, det.cls))
                self._next += 1
                matched.add(id(self._slots[-1]))
        output: list[Tracked] = []
        alive: list[_Slot] = []
        for slot in self._slots:
            if id(slot) in matched:
                slot.missed = 0
                slot.hits += 1
            else:
                slot.missed += 1
            if slot.missed == 0:
                output.append(Tracked(slot.id, slot.bbox, slot.score, slot.cls))
            if slot.missed <= self.max_missed:
                alive.append(slot)
        self._slots = alive
        return output

    def _assign(self, pool: list[_Slot], dets: list[Det], matched: set[int]) -> set[int]:
        used: set[int] = set()
        pairs: list[tuple[float, int, int]] = []
        for si, slot in enumerate(pool):
            for di, det in enumerate(dets):
                score = iou(slot.bbox, det.bbox)
                if score >= self.match_iou:
                    pairs.append((score, si, di))
        pairs.sort(reverse=True)
        for _score, si, di in pairs:
            slot = pool[si]
            if id(slot) in matched or di in used:
                continue
            det = dets[di]
            slot.bbox = det.bbox
            slot.score = det.score
            slot.cls = det.cls
            matched.add(id(slot))
            used.add(di)
        return used


@dataclass
class CameraTrackers:
    trackers: dict[str, IoUTracker] = field(default_factory=dict)

    def update(self, cam_id: str, dets: list[Det]) -> list[Tracked]:
        tracker = self.trackers.get(cam_id)
        if tracker is None:
            tracker = IoUTracker()
            self.trackers[cam_id] = tracker
        return tracker.update(dets)
