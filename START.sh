#!/usr/bin/env bash
# SmartCCTV 交互式菜单：检测 / 修复 / 运行 / 维护 / 测试 / 帮助
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/data/smartcctv.pid"
LOG_FILE="$ROOT/data/logs/smartcctv.log"
YES=0
CI=0

for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --ci) CI=1; YES=1 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

ensure_dirs() {
  mkdir -p "$ROOT/data/"{db,secrets,recordings,snapshots,thumbs,logs,s3} "$ROOT/models"
}

copy_config() {
  if [[ ! -f "$ROOT/config.yaml" ]]; then
    cp "$ROOT/config.example.yaml" "$ROOT/config.yaml"
    echo "已生成 config.yaml（来自 example）"
  fi
}

cmd_diagnose() {
  echo "=== 环境检测 ==="
  local fail=0
  have uv && echo "[OK] uv $(uv --version)" || { echo "[FAIL] uv"; fail=1; }
  have ffmpeg && echo "[OK] ffmpeg" || { echo "[FAIL] ffmpeg"; fail=1; }
  have ffprobe && echo "[OK] ffprobe" || { echo "[WARN] ffprobe"; }
  python_ver="$(uv python find 3.14 2>/dev/null || true)"
  if [[ -n "$python_ver" ]]; then echo "[OK] python $python_ver"; else echo "[WARN] 未 pin Python 3.14，将在 repair 安装"; fi
  [[ -f "$ROOT/pyproject.toml" ]] && echo "[OK] pyproject.toml" || { echo "[FAIL] pyproject.toml"; fail=1; }
  [[ -f "$ROOT/config.yaml" || -f "$ROOT/config.example.yaml" ]] && echo "[OK] config" || { echo "[FAIL] config"; fail=1; }
  df -h "$ROOT" | tail -1
  if [[ -f "$ROOT/models/yolo26n.pt" ]]; then echo "[OK] yolo26n.pt"; else echo "[WARN] 缺 models/yolo26n.pt（可观看，检测停用）"; fi
  if [[ -f "$ROOT/models/buffalo_l/det_10g.onnx" ]]; then echo "[OK] buffalo_l"; else echo "[WARN] 缺 buffalo_l 模型"; fi
  return "$fail"
}

cmd_repair() {
  ensure_dirs
  copy_config
  echo "将执行: uv python install 3.14 && uv sync --group dev"
  if [[ "$YES" -eq 1 ]] || { read -r -p "继续? [y/N] " a && [[ "$a" =~ ^[Yy]$ ]]; }; then
    uv python install 3.14
    uv python pin 3.14 || true
    uv sync --group dev
  fi
  if [[ -d "$ROOT/frontend" ]] && have npm; then
    echo "构建前端..."
    (cd "$ROOT/frontend" && npm install && npm run build) || true
  fi
  echo "模型下载: uv run python scripts/download_models.py （可选）"
}

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

cmd_start() {
  ensure_dirs
  copy_config
  if is_running; then echo "已在运行 pid=$(cat "$PID_FILE")"; return 0; fi
  nohup uv run uvicorn server.app:app --host 0.0.0.0 --port "${SMARTCCTV_PORT:-8300}" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "已启动 pid=$(cat "$PID_FILE") log=$LOG_FILE"
}

cmd_stop() {
  if ! is_running; then echo "未运行"; rm -f "$PID_FILE"; return 0; fi
  kill "$(cat "$PID_FILE")" || true
  sleep 1
  if is_running; then kill -9 "$(cat "$PID_FILE")" || true; fi
  rm -f "$PID_FILE"
  echo "已停止"
}

cmd_status() {
  if is_running; then echo "running pid=$(cat "$PID_FILE")"; else echo "stopped"; fi
  [[ -f "$LOG_FILE" ]] && tail -n 20 "$LOG_FILE" || true
}

cmd_test() {
  ensure_dirs
  uv run ruff check server tests || true
  uv run pytest -q
  if [[ -x "$ROOT/scripts/smoke_test.sh" ]]; then
    "$ROOT/scripts/smoke_test.sh" || true
  fi
}

cmd_help() {
  cat <<EOF
用法: ./START.sh [子命令] [--yes|--ci]

子命令:
  diagnose   环境检测
  repair     安装依赖/建目录/可选构建前端
  start      终端后台启动
  stop       停止
  restart    重启
  status     状态与最近日志
  logs       跟踪日志
  test       ruff + pytest + smoke
  reset-totp 重置 TOTP（踢出会话）
  help       本帮助

无参数进入交互菜单。域名建议: cctv.lobn → 本机 IP（hosts/内网 DNS）
EOF
}

cmd_reset_totp() {
  ensure_dirs
  uv run python - <<'PY'
from pathlib import Path
from server.secrets import SecretStore
store = SecretStore(Path("data/secrets"))
print("new totp:", store.rotate_totp())
print(store.totp_uri())
PY
}

menu() {
  while true; do
    cat <<EOF

SmartCCTV
  1) 环境检测
  2) 环境修复/安装
  3) 启动
  4) 停止
  5) 状态
  6) 日志
  7) 自动化测试
  8) 重置 TOTP
  9) 帮助
  0) 退出
EOF
    read -r -p "> " choice
    case "$choice" in
      1) cmd_diagnose || true ;;
      2) cmd_repair ;;
      3) cmd_start ;;
      4) cmd_stop ;;
      5) cmd_status ;;
      6) mkdir -p "$(dirname "$LOG_FILE")"; touch "$LOG_FILE"; tail -f "$LOG_FILE" ;;
      7) cmd_test ;;
      8) cmd_reset_totp ;;
      9) cmd_help ;;
      0) exit 0 ;;
      *) echo "未知选项" ;;
    esac
  done
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    "") menu ;;
    diagnose) cmd_diagnose ;;
    repair|install) cmd_repair ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status) cmd_status ;;
    logs) mkdir -p "$(dirname "$LOG_FILE")"; touch "$LOG_FILE"; tail -f "$LOG_FILE" ;;
    test) cmd_test ;;
    reset-totp) cmd_reset_totp ;;
    help|-h|--help) cmd_help ;;
    *) echo "未知: $cmd"; cmd_help; exit 1 ;;
  esac
}

main "$@"
