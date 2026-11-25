from pathlib import Path

import pytest

from server.config import load_config, validate_source


def test_validate_source():
    assert validate_source("rtsp://a/b").startswith("rtsp://")
    assert validate_source("file:///tmp/a.mp4").startswith("file://")
    with pytest.raises(ValueError):
        validate_source("http://evil")


def test_load_example(tmp_path: Path):
    src = Path("config.example.yaml")
    cfg = load_config(src)
    assert cfg.port == 8300
    assert cfg.auth.max_sessions == 20
