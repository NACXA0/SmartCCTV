import numpy as np

from server.face.match import best_match, cosine, l2_normalize


def test_l2_and_cosine():
    a = l2_normalize(np.array([3.0, 4.0]))
    assert abs(np.linalg.norm(a) - 1.0) < 1e-5
    assert cosine(a, a) > 0.99


def test_best_match_threshold():
    gallery = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    pid, score = best_match(np.array([0.9, 0.1]), gallery, [10, 20], threshold=0.5)
    assert pid == 10
    assert score > 0.5
    pid2, _ = best_match(np.array([0.1, 0.1]), gallery, [10, 20], threshold=0.99)
    assert pid2 is None
