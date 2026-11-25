"""is_prod=false 时会话视为通过；配置默认为开发态。"""

from __future__ import annotations

from pathlib import Path

from server.config import AppConfig, load_config


def test_default_is_prod_false():
    cfg = AppConfig()
    assert cfg.is_prod is False


def test_example_config_is_dev(tmp_path: Path):
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = load_config(example)
    assert cfg.is_prod is False


def test_env_overrides_is_prod(monkeypatch):
    monkeypatch.setenv("SMARTCCTV_IS_PROD", "true")
    cfg = AppConfig(is_prod=False)
    cfg.apply_env()
    assert cfg.is_prod is True
    monkeypatch.setenv("SMARTCCTV_IS_PROD", "0")
    cfg2 = AppConfig(is_prod=True)
    cfg2.apply_env()
    assert cfg2.is_prod is False
