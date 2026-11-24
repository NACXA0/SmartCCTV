# SmartCCTV

内网智能监控：≤10 路 RTSP、纯 CPU、YOLO26 检测、人脸库、行为规则告警、TOTP 登录、极简告警（Gotify / 可选企业微信）。

**开门**：本地不执行继电器（`door.adapter: center_only`）。识别结果经 `center.events_url` 上报 SmartCenter，由总控唯一发往 ESP32ETH。

域名建议：`cctv.lobn`（内网 DNS 或 hosts 指向本机）。默认端口 `8300`。

> 模型许可：InsightFace 预训练模型仅限非商业研究（个人自用）；YOLO26 / Ultralytics 为 AGPL-3.0。详见 `docs/`。

## 快速开始

```bash
./START.sh repair --yes    # uv + Python 3.14 + 依赖；可选构建前端
uv run python scripts/download_models.py   # 可选：拉取 yolo26n / buffalo_l
cp -n config.example.yaml config.yaml      # 填写摄像头 / 门禁 / 通知
./START.sh start
```

浏览器打开 `http://localhost:8300`（或 `http://cctv.lobn:8300`）。首次启动终端会打印 TOTP 密钥与二维码。

开发前端：

```bash
cd frontend && npm install && npm run dev   # :5173，代理 /api → :8300
uv run uvicorn server.app:app --host 0.0.0.0 --port 8300
```

无摄像头可用本地文件联调：

```yaml
cameras:
  - id: demo
    name: 联调
    url: file:///absolute/path/to/video.mp4
    role: indoor
```

## 结构

- `server/` — FastAPI 单进程（相机 / 检测 / 人脸 / 门禁 / 录像 / 通知 / 调度）
- `frontend/` — React + Vite + Tailwind 面板
- `docs/` — 需求、调研与完整实施方案
- `START.sh` — 检测 / 修复 / 运行 / 测试菜单
- `iot/` — 原仓库烟雾传感器示例（非核心路径）

## 门禁硬件（推荐）

HTTP 网络继电器（KMtronic / 亿佰特等）：常开触点**并联**原门禁面板「开门按钮」。断电即锁（fail-secure）。配置见 `config.example.yaml` 的 `door:`。未接线时用 `adapter: mock`。

## 验证

```bash
./START.sh test
# 或
uv run pytest -q && uv run ruff check server tests
```

## 文档

从 [`docs/00-INDEX.md`](docs/00-INDEX.md) 开始读。实施方案蓝本：[`docs/06-实施方案-完整版.md`](docs/06-实施方案-完整版.md)。








