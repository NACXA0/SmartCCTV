"""数据目录与路径穿越防护。"""

from __future__ import annotations

import os
import re
from pathlib import Path

_UNSAFE = re.compile(r"[\x00-\x1f]")


def chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def resolve_inside(root: Path, relative: str) -> Path:
    """把相对路径解析到 root 内。拒绝绝对路径、空字节与跳出根目录。"""
    if not relative or relative.startswith(("/", "\\")) or _UNSAFE.search(relative):
        raise ValueError("非法路径")
    root_r = root.resolve()
    candidate = (root_r / relative).resolve()
    if candidate != root_r and root_r not in candidate.parents:
        raise ValueError("路径越界")
    return candidate


def mode_is_private(path: Path) -> bool:
    try:
        return (path.stat().st_mode & 0o077) == 0
    except OSError:
        return False
