#!/usr/bin/env python3
"""下载 YOLO26n 与 buffalo_l（需出网）。"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {url} -> {dest}")
    urlretrieve(url, dest)


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    yolo = MODELS / "yolo26n.pt"
    if not yolo.exists():
        # Ultralytics 会在首次 YOLO() 时自动拉取；这里尝试常见权重 URL，失败则提示
        try:
            from ultralytics import YOLO

            print("通过 ultralytics 拉取 yolo26n.pt ...")
            model = YOLO("yolo26n.pt")
            # ultralytics 可能下载到 cwd；尽量复制到 models/
            for candidate in [Path("yolo26n.pt"), Path.home() / ".cache"]:
                pass
            if Path("yolo26n.pt").exists():
                Path("yolo26n.pt").replace(yolo)
            else:
                print("请手动将 yolo26n.pt 放到 models/")
            _ = model
        except Exception as exc:
            print("YOLO 下载失败:", exc)
    else:
        print("已有", yolo)

    buffalo = MODELS / "buffalo_l"
    zip_path = MODELS / "buffalo_l.zip"
    if not (buffalo / "det_10g.onnx").exists():
        url = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
        try:
            download(url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(buffalo)
            print("buffalo_l 就绪")
        except Exception as exc:
            print("buffalo_l 下载失败（可手动放置 det_10g.onnx / w600k_r50.onnx）:", exc)
            return 1
    else:
        print("已有 buffalo_l")
    return 0


if __name__ == "__main__":
    sys.exit(main())
