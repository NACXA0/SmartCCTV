"""配置加载、校验与写回。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator

_RTSP_SECRET = re.compile(r"(rtsp://[^:/\s]+):([^@/\s]+)@", re.IGNORECASE)
_SECRET_KEYS = ("gotify_token", "wecom_webhook", "access_key", "secret_key", "open_url")


class AuthConfig(BaseModel):
    session_days: int = 7
    max_sessions: int = 20
    max_sessions_per_ip: int = 5
    login_max_fails: int = 5
    login_lock_seconds: int = 30


class NightProfile(BaseModel):
    analysis_fps: float = 1.0
    face_mode: Literal["stricter", "alert_only"] = "stricter"
    face_threshold_delta: float = 0.08
    face_confirm_frames_delta: int = 2
    loiter_factor: float = 0.7


class CameraConfig(BaseModel):
    id: str
    name: str
    url: str
    role: Literal["door", "indoor"] = "indoor"
    enabled: bool = True
    analysis_fps: float = 2.0
    record_continuous: bool | None = None
    night_profile: NightProfile = Field(default_factory=NightProfile)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", value):
            raise ValueError("摄像头 id 只能包含字母数字、下划线和短横线")
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return validate_source(value)

    def continuous(self) -> bool:
        if self.record_continuous is None:
            return self.role == "door"
        return self.record_continuous


class RuleConfig(BaseModel):
    id: str
    camera_id: str
    kind: Literal["zone", "line"]
    name: str = ""
    points: list[tuple[float, float]]
    loiter_seconds: float = 30
    crowd_count: int = 3
    confirm_frames: int = 3
    armed: Literal["always", "schedule", "off"] = "always"
    schedule: list[tuple[str, str]] = Field(default_factory=list)
    cooldown_sec: float = 30

    @field_validator("points")
    @classmethod
    def _points(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(value) < 2 or len(value) > 32:
            raise ValueError("规则点数须在 2 到 32 之间")
        for x, y in value:
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("规则坐标须在 0 到 1 之间")
        return value


class FaceConfig(BaseModel):
    threshold: float = 0.45
    confirm_frames: int = 3
    cooldown_sec: int = 120
    min_det_score: float = 0.5
    min_box_px: int = 48
    det_model: str = "models/buffalo_l/det_10g.onnx"
    rec_model: str = "models/buffalo_l/w600k_r50.onnx"


class DoorConfig(BaseModel):
    id: str = "main"
    adapter: Literal["mock", "http"] = "mock"
    camera_id: str = ""
    open_seconds: float = 5
    open_url: str = ""
    close_url: str = ""
    method: Literal["GET", "POST"] = "GET"
    timeout_sec: float = 3


class NotifyConfig(BaseModel):
    min_level: Literal["info", "warning", "critical"] = "warning"
    cooldown_sec: float = 60
    gotify_url: str = ""
    gotify_token: str = ""
    wecom_webhook: str = ""


class StorageConfig(BaseModel):
    s3_enabled: bool = False
    endpoint: str = "http://127.0.0.1:9000"
    access_key: str = ""
    secret_key: str = ""
    bucket: str = "smartcctv"
    region: str = "us-east-1"
    hot_event_days: int = 7
    hot_continuous_days: int = 14


class RetentionConfig(BaseModel):
    continuous_days: int = 14
    event_days: int = 30
    snapshot_days: int = 30


class DiskConfig(BaseModel):
    reserve_gb: float = 20
    reserve_ratio: float = 0.10
    warn_free_ratio: float = 0.20
    critical_free_ratio: float = 0.05
    resume_hysteresis: float = 0.05


class SchedulerConfig(BaseModel):
    valley_start: str = "03:00"
    valley_end: str = "06:00"
    idle_cpu: float = 30
    idle_minutes: float = 5
    max_concurrent: int = 2


class DetectConfig(BaseModel):
    model: str = "models/yolo26n.pt"
    imgsz: int = 640
    conf: float = 0.35
    max_boxes: int = 50
    activity_gate: float = 1.2
    force_interval_sec: float = 2
    torch_threads: int = 2


class WatchConfig(BaseModel):
    max_viewers: int = 4
    fanout_per_camera: int = 3
    jpeg_quality: int = 70
    display_width: int = 960


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8300
    timezone: str = "Asia/Shanghai"
    data_dir: str = "data"
    models_dir: str = "models"
    auth: AuthConfig = Field(default_factory=AuthConfig)
    cameras: list[CameraConfig] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
    face: FaceConfig = Field(default_factory=FaceConfig)
    door: DoorConfig = Field(default_factory=DoorConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    disk: DiskConfig = Field(default_factory=DiskConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    detect: DetectConfig = Field(default_factory=DetectConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)

    def data_path(self, root: Path | None = None) -> Path:
        path = Path(self.data_dir)
        if not path.is_absolute():
            path = (root or Path.cwd()) / path
        return path

    def apply_env(self) -> None:
        if token := os.environ.get("SMARTCCTV_GOTIFY_TOKEN"):
            self.notify.gotify_token = token
        if url := os.environ.get("SMARTCCTV_GOTIFY_URL"):
            self.notify.gotify_url = url
        if hook := os.environ.get("SMARTCCTV_WECOM_WEBHOOK"):
            self.notify.wecom_webhook = hook
        if os.environ.get("SMARTCCTV_PORT"):
            self.port = int(os.environ["SMARTCCTV_PORT"])
        if os.environ.get("SMARTCCTV_S3_SECRET"):
            self.storage.secret_key = os.environ["SMARTCCTV_S3_SECRET"]
        if os.environ.get("SMARTCCTV_S3_ACCESS_KEY"):
            self.storage.access_key = os.environ["SMARTCCTV_S3_ACCESS_KEY"]


def validate_source(url: str) -> str:
    if not url or re.search(r"[\x00-\x1f]", url):
        raise ValueError("视频源包含非法字符")
    lower = url.lower()
    if lower.startswith(("rtsp://", "rtsps://")):
        return url
    if lower.startswith("file://"):
        return url
    path = Path(url)
    if path.is_absolute():
        return str(path)
    raise ValueError("视频源仅支持 rtsp、rtsps、file:// 或绝对路径")


def validate_http_url(url: str, *, allow_empty: bool = True) -> str:
    if not url:
        if allow_empty:
            return url
        raise ValueError("URL 为空")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("仅允许 http(s) URL")
    return url


def redact_text(text: str) -> str:
    redacted = _RTSP_SECRET.sub(r"\1:***@", text)
    redacted = re.sub(r"(key=)[^&\s]+", r"\1***", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"(token=)[^&\s]+", r"\1***", redacted, flags=re.IGNORECASE)
    return redacted


def redact_url(url: str) -> str:
    return redact_text(url)


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("配置文件必须是映射")
    cfg = AppConfig.model_validate(raw)
    cfg.apply_env()
    _validate_graph(cfg)
    return cfg


def dump_config(path: Path, cfg: AppConfig) -> None:
    payload = cfg.model_dump(mode="json")
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _validate_graph(cfg: AppConfig) -> None:
    ids = [cam.id for cam in cfg.cameras]
    if len(ids) != len(set(ids)):
        raise ValueError("摄像头 id 重复")
    urls = [cam.url for cam in cfg.cameras]
    if len(urls) != len(set(urls)):
        raise ValueError("摄像头 URL 重复")
    known = set(ids)
    for rule in cfg.rules:
        if rule.camera_id not in known:
            raise ValueError(f"规则 {rule.id} 指向未知摄像头 {rule.camera_id}")
        if rule.kind == "zone" and len(rule.points) < 3:
            raise ValueError(f"区域规则 {rule.id} 至少需要 3 个点")
        if rule.kind == "line" and len(rule.points) != 2:
            raise ValueError(f"越线规则 {rule.id} 需要恰好 2 个点")
    validate_http_url(cfg.notify.gotify_url)
    if cfg.notify.wecom_webhook:
        validate_http_url(cfg.notify.wecom_webhook, allow_empty=False)
        if "qyapi.weixin.qq.com" not in urlparse(cfg.notify.wecom_webhook).netloc:
            raise ValueError("企业微信 webhook 域名必须是 qyapi.weixin.qq.com")
    validate_http_url(cfg.door.open_url)
    validate_http_url(cfg.door.close_url)
    if cfg.storage.s3_enabled:
        validate_http_url(cfg.storage.endpoint, allow_empty=False)


def public_config(cfg: AppConfig) -> dict:
    data = cfg.model_dump(mode="json")
    for cam in data["cameras"]:
        cam["url"] = redact_url(cam["url"])
    note = data["notify"]
    if note.get("gotify_token"):
        note["gotify_token"] = "***"
    if note.get("wecom_webhook"):
        note["wecom_webhook"] = redact_text(note["wecom_webhook"])
    storage = data["storage"]
    if storage.get("access_key"):
        storage["access_key"] = "***"
    if storage.get("secret_key"):
        storage["secret_key"] = "***"
    if data["door"].get("open_url"):
        data["door"]["open_url"] = redact_text(data["door"]["open_url"])
    if data["door"].get("close_url"):
        data["door"]["close_url"] = redact_text(data["door"]["close_url"])
    return data
