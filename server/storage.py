"""本地热层与可选 S3 冷层。未启用 S3 时只管理本地文件。"""

from __future__ import annotations

import time
from pathlib import Path

from server.paths import ensure_dir, resolve_inside


def expired(ts: float, now: float, days: int) -> bool:
    return now - ts > days * 86400


class Storage:
    def __init__(self, root: Path, config) -> None:
        self.root = ensure_dir(root)
        self.snapshots = ensure_dir(root / "snapshots")
        self.recordings = ensure_dir(root / "recordings")
        self.thumbs = ensure_dir(root / "thumbs")
        self.config = config
        self._client = None

    def subdir(self, kind: str, cam_id: str, now: float | None = None) -> Path:
        stamp = time.localtime(now or time.time())
        path = self.root / kind / cam_id / time.strftime("%Y/%m/%d", stamp)
        return ensure_dir(path)

    def save_jpeg(self, cam_id: str, data: bytes, now: float | None = None) -> str:
        folder = self.subdir("snapshots", cam_id, now)
        name = f"{cam_id}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now or time.time()))}_{int((now or time.time()) * 1000) % 1000:03d}.jpg"
        path = folder / name
        path.write_bytes(data)
        return str(path.relative_to(self.root))

    def open_local(self, relative: str) -> Path:
        return resolve_inside(self.root, relative)

    def s3_ready(self) -> bool:
        cfg = self.config
        return bool(cfg.s3_enabled and cfg.endpoint and cfg.access_key and cfg.secret_key and cfg.bucket)

    def client(self):
        if self._client is None and self.s3_ready():
            import boto3
            from botocore.client import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

    def upload(self, relative: str) -> str:
        path = self.open_local(relative)
        key = relative.replace("\\", "/")
        self.client().upload_file(str(path), self.config.bucket, key)
        return key

    def open_stream(self, relative: str, storage: str, s3_key: str):
        if storage == "s3" and s3_key and self.s3_ready():
            obj = self.client().get_object(Bucket=self.config.bucket, Key=s3_key)
            return obj["Body"]
        return self.open_local(relative).open("rb")
