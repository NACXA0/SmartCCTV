# 05 · Python 3.14 关键依赖 wheel 核实（一手数据）

> 核实时间：2026-11-24。方法：直接抓取 PyPI 项目页 / JSON API（`pypi.org/project/<pkg>`、`pypi.org/pypi/<pkg>/json`），逐条核对 wheel 文件名与标签。**全部为当次一手验证，非记忆**。

---

## 1. 结论表

| 包 | 最新版（当时） | py3.14 可用性 | 关键证据（wheel 文件名） |
|----|----------------|----------------|--------------------------|
| torch | **2.15.0**（2026-10-28） | ✅ | `torch-2.15.0-cp314-cp314-manylinux_2_28_x86_64.whl`（539.2MB；连 free-threaded `cp314t` 都有；亦覆盖 cp311–cp315） |
| onnxruntime | **1.31.1**（2026-10-26） | ✅ | `onnxruntime-1.31.1-cp314-cp314-manylinux_2_28_x86_64.whl`（23.8MB；cp311–cp314 + 3.14t 齐全；requires-python ≥3.11） |
| opencv-python | **4.13.0.92**（2026-02-05） | ✅ | `opencv_python-4.13.0.92-cp37-abi3-manylinux_2_28_x86_64.whl`——**自 4.7.0.72 起 wheel 一律为 cp37-abi3 稳定 ABI**，适用于任何 ≥3.7 的 CPython（分类器明确列 3.14）；依赖仅 numpy≥2（py≥3.9） |
| **insightface** | PyPI 停在 **0.7.3**（2023-04-02） | ❌（无 wheel） | **仅 `insightface-0.7.3.tar.gz` 源码包**，无任何平台 wheel，`requires_dist` 为空 → 需 Cython/C++ 编译，py3.14 不可靠。2.x 版本未发布 PyPI。→ 采用"直接 ONNX"路线（见 04 文档） |
| ultralytics | v8.4.218（2026-11-23） | ✅（随 torch） | 纯 Python 包；依赖 torch/torchvision/opencv/numpy 等（均已被上表覆盖） |
| numpy / scipy / fastapi 等 | — | ✅ | 纯 Python 或常规 cp314 wheel（fastapi 为纯 Python；numpy 2.x 对 3.14 支持成熟，未逐条存档） |

> 注：torchvision 与 torch 同节奏发版（未单独存档文件名，装 `torch==2.15.* torchvision` 配对版本即可）；若走 CPU 精简路线可改用 pytorch cpu 源（`download.pytorch.org/whl/cpu`）减少体积（GPU 版 wheel ~539MB，cpu 版显著更小）。

## 2. 运行环境门槛

- **glibc ≥ 2.28**（manylinux_2_28）：Debian 13 "trixie"（glibc 2.41）✅；2020 年后主流发行版基本都满足。
- **Python 3.14 获取**：Debian 13 系统自带 Python 3.13；3.14 经 uv 安装：
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh   # 无需 root
  uv python install 3.14                             # python-build-standalone，可重定位
  uv python pin 3.14                                 # 项目内固定版本
  ```
  uv 托管解释器位于 `~/.local/share/uv/python/`，与 apt 的系统 Python 并存、不随 apt 升级（用 `uv python upgrade` 维护）。

## 3. 对本项目的意义

1. `uv + Python 3.14` 的技术栈要求**完全可行**：torch(ultralytics)、onnxruntime(人脸)、opencv 三个二进制大头全部有兼容 wheel。
2. 唯一的坑是 **insightface pip 包**——因此架构决定不依赖它（详见 [04-调研-人脸识别-InsightFace.md](04-调研-人脸识别-InsightFace.md) §4）。
3. `pyproject.toml` 建议：`requires-python = ">=3.14"`；torch 用 cpu 源 pin；`uv sync --frozen` 产出可复现锁文件（离线内网机可用 `uv sync --offline` + 预先缓存，或在有网机器导出）。

## 4. 原始 wheel 记录（摘录，完整页面见 08 存档）

- torch 2.15.0 发布文件（节选）：cp311/cp312/cp313/cp314/cp315 × {win_amd64, manylinux_2_28 x86_64/aarch64, macosx} + 对应 cp314t/cp315t free-threaded；总发布体积 9.0GB；无 provenance。
- onnxruntime 1.31.1 发布文件（节选）：cp311–cp314（含 3.13t/3.14t free-threaded）全平台，24 个文件。
- opencv-python 4.13.0.92 发布文件（节选）：`cp37-abi3` × {manylinux2014, manylinux_2_28, macosx13/14, win}。
- insightface 历史发布：0.1.x–0.7.3 全部为 `.tar.gz`（仅 0.2.1 曾有一个 `py2.py3-none-any.whl`）。
