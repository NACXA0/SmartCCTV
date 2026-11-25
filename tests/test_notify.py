from server.bus import BusMessage
from server.notify.dispatcher import cooled, level_allows


def test_level_and_cooldown():
    assert level_allows("warning", "warning")
    assert not level_allows("info", "warning")
    assert cooled(0, 100, 60)
    assert not cooled(80, 100, 60)


def test_render_contains_title():
    from server.notify.dispatcher import Notifier

    class Dummy:
        min_level = "info"
        cooldown_sec = 1
        gotify_url = ""
        gotify_token = ""
        wecom_webhook = ""

    n = Notifier(Dummy(), db=None)
    text = n.render(BusMessage(type="zone_intrude", level="warning", cam_id="door", payload={"x": 1}))
    assert "区域入侵" in text
