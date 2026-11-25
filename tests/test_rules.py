from server.config import RuleConfig
from server.rules import RuleEngine, TrackView, cross_direction, point_in_polygon


def test_point_in_polygon():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(0.5, 0.5, square)
    assert not point_in_polygon(1.5, 0.5, square)


def test_cross_direction():
    assert cross_direction((0.2, 0.4), (0.8, 0.4), (0.5, 0.0), (0.5, 1.0)) != 0
    assert cross_direction((0.2, 0.4), (0.3, 0.4), (0.5, 0.0), (0.5, 1.0)) == 0


def test_zone_intrude_confirm():
    engine = RuleEngine()
    rule = RuleConfig(
        id="z1",
        camera_id="cam",
        kind="zone",
        points=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        confirm_frames=2,
        cooldown_sec=0,
    )
    track = TrackView(1, (0.2, 0.2, 0.3, 0.5))
    assert engine.update("cam", [track], [rule], now=1000) == []
    alerts = engine.update("cam", [track], [rule], now=1001)
    assert any(a.type == "zone_intrude" for a in alerts)
