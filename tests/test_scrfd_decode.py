import numpy as np

from server.face.scrfd import decode_scrfd, distance2bbox


def test_distance2bbox():
    points = np.array([[10.0, 10.0]], dtype=np.float32)
    dist = np.array([[2.0, 3.0, 4.0, 5.0]], dtype=np.float32)
    box = distance2bbox(points, dist)[0]
    assert list(box) == [8.0, 7.0, 14.0, 15.0]


def test_decode_empty():
    assert decode_scrfd([]) == []
