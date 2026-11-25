"""SCRFD 检测头后处理。输入为 onnxruntime 的输出列表，不依赖模型文件。"""

from __future__ import annotations

import numpy as np

from server.tracking import iou


def _squeeze(array: np.ndarray) -> np.ndarray:
    array = np.asarray(array)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim == 4 and array.shape[0] == 1:
        array = array[0]
    return array


def distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, 0] + distance[:, i])
        preds.append(points[:, 1] + distance[:, i + 1])
    return np.stack(preds, axis=-1)


def nms(boxes: np.ndarray, scores: np.ndarray, thresh: float = 0.4) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        ious = np.array([iou(tuple(boxes[i]), tuple(boxes[j])) for j in rest])
        order = rest[ious < thresh]
    return keep


def decode_scrfd(
    outputs: list[np.ndarray],
    input_size: tuple[int, int] = (640, 640),
    strides: tuple[int, ...] = (8, 16, 32),
    num_anchors: int = 2,
    score_thresh: float = 0.5,
    nms_thresh: float = 0.4,
) -> list[dict]:
    """按特征图尺寸把 score/bbox/kps 分组。兼容 (N,1)/(N,4)/(N,10) 或带 batch 维。"""
    tensors = [_squeeze(item) for item in outputs]
    grouped: dict[int, dict[str, np.ndarray]] = {}
    for tensor in tensors:
        flat = tensor.reshape(-1, tensor.shape[-1]) if tensor.ndim >= 2 else tensor.reshape(-1, 1)
        count, width = flat.shape[0], flat.shape[-1]
        if width not in {1, 4, 10}:
            continue
        kind = {1: "score", 4: "bbox", 10: "kps"}[width]
        grouped.setdefault(count, {})[kind] = flat.astype(np.float32)

    height, width = input_size
    boxes_all = []
    scores_all = []
    kps_all = []
    for stride in strides:
        grid_h = height // stride
        grid_w = width // stride
        count = grid_h * grid_w * num_anchors
        pack = grouped.get(count)
        if not pack or "score" not in pack or "bbox" not in pack:
            continue
        scores = pack["score"].reshape(-1)
        if scores.max(initial=0) > 1.0 or scores.min(initial=0) < 0.0:
            scores = 1 / (1 + np.exp(-scores))
        centers = []
        for gy in range(grid_h):
            for gx in range(grid_w):
                for _ in range(num_anchors):
                    centers.append(((gx + 0.5) * stride, (gy + 0.5) * stride))
        points = np.asarray(centers, dtype=np.float32)
        bbox = pack["bbox"]
        if bbox.max() < 8:
            bbox = bbox * stride
        decoded = distance2bbox(points, bbox)
        keep = scores >= score_thresh
        if not np.any(keep):
            continue
        boxes_all.append(decoded[keep])
        scores_all.append(scores[keep])
        if "kps" in pack:
            kps = pack["kps"]
            if kps.max() < 8:
                kps = kps * stride
            kps_all.append(distance2kps(points, kps)[keep])

    if not boxes_all:
        return []
    boxes = np.concatenate(boxes_all, axis=0)
    scores = np.concatenate(scores_all, axis=0)
    kps = np.concatenate(kps_all, axis=0) if kps_all else None
    keep_idx = nms(boxes, scores, nms_thresh)
    faces = []
    for index in keep_idx:
        item = {"bbox": boxes[index].tolist(), "score": float(scores[index])}
        if kps is not None:
            item["kps"] = kps[index].reshape(-1, 2).tolist()
        faces.append(item)
    faces.sort(key=lambda item: item["score"], reverse=True)
    return faces
