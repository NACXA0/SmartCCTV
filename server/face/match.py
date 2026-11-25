"""人脸向量归一化与余弦匹配（纯函数，便于单测）。"""

from __future__ import annotations

import numpy as np


def l2_normalize(vec: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm < eps:
        return arr
    return arr / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    va = l2_normalize(a)
    vb = l2_normalize(b)
    return float(np.dot(va, vb))


def best_match(
    query: np.ndarray,
    gallery: np.ndarray,
    ids: list[int],
    threshold: float = 0.45,
) -> tuple[int | None, float]:
    """gallery 形状 (N, D)。返回 (person_id, score)；未达阈值时 person_id 为 None。"""
    if gallery.size == 0 or not ids:
        return None, 0.0
    mat = np.asarray(gallery, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    q = l2_normalize(query)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    mat_n = mat / norms
    scores = mat_n @ q
    index = int(np.argmax(scores))
    score = float(scores[index])
    if score < threshold:
        return None, score
    return ids[index], score
