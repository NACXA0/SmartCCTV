#!/usr/bin/env bash
# SmartCCTV 交互菜单：启动 / 运维 / 环境检测（选中后清屏覆盖刷新）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/data/smartcctv.pid"
LOG_FILE="$ROOT/data/logs/smartcctv.log"
COMPOSE_FILE="$ROOT/docker-compose.yml"
FRONTEND_DIR="$ROOT/frontend"
EXPORT_DIR="$ROOT/export"
YES=0
CI=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --ci) CI=1; YES=1 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

clear_screen() {
  # 覆盖刷新：清屏并回顶；非 TTY / CI 不刷
  if [[ -t 1 && "$CI" -eq 0 ]]; then
    printf '\033[H\033[2J'
  fi
}

pause_back() {
  if [[ "$CI" -eq 1 || "$YES" -eq 1 ]]; then
    return 0
  fi
  echo
  read -r -p "按回车返回菜单… " _
}

ensure_dirs() {
  mkdir -p "$ROOT/data/"{db,secrets,recordings,snapshots,thumbs,logs,s3} "$ROOT/models"
}

copy_config() {
  if [[ ! -f "$ROOT/config.yaml" ]]; then
    cp "$ROOT/config.example.yaml" "$ROOT/config.yaml"
    echo "已生成 config.yaml（来自 example）"
  fi
}

# ---------- 环境 ----------
cmd_diagnose() {
  echo "=== 环境检测 ==="
  local fail=0
  have uv && echo "[OK] uv $(uv --version 2>/dev/null | head -1)" || { echo "[FAIL] uv"; fail=1; }
  have ffmpeg && echo "[OK] ffmpeg" || { echo "[FAIL] ffmpeg"; fail=1; }
  have ffprobe && echo "[OK] ffprobe" || { echo "[WARN] ffprobe"; }
  have docker && echo "[OK] docker" || echo "[WARN] docker（Docker 启动模式不可用）"
  have docker-compose || have docker && docker compose version >/dev/null 2>&1 && echo "[OK] docker compose" || echo "[WARN] docker compose"
  local python_ver
  python_ver="$(uv python find 3.14 2>/dev/null || true)"
  if [[ -n "$python_ver" ]]; then echo "[OK] python $python_ver"; else echo "[WARN] 未 pin Python 3.14，请先环境修复"; fi
  [[ -f "$ROOT/pyproject.toml" ]] && echo "[OK] pyproject.toml" || { echo "[FAIL] pyproject.toml"; fail=1; }
  [[ -f "$ROOT/config.yaml" || -f "$ROOT/config.example.yaml" ]] && echo "[OK] config" || { echo "[FAIL] config"; fail=1; }
  if [[ -f "$ROOT/config.yaml" ]] || [[ -f "$ROOT/config.example.yaml" ]]; then
    local prod_flag
    prod_flag="$(uv run python -c "from pathlib import Path; from server.config import load_config; p=Path('config.yaml') if Path('config.yaml').exists() else Path('config.example.yaml'); print('true' if load_config(p).is_prod else 'false')" 2>/dev/null || echo "?")"
    if [[ "$prod_flag" == "true" ]]; then
      echo "[OK] is_prod=true（强制会话）"
    elif [[ "$prod_flag" == "false" ]]; then
      echo "[OK] is_prod=false（开发：会话校验放行，前端仍走登录）"
    else
      echo "[WARN] 无法读取 is_prod"
    fi
  fi
  echo "--- 磁盘 ---"
  df -h "$ROOT" | tail -1
  if [[ -f "$ROOT/models/yolo26n.pt" ]]; then echo "[OK] yolo26n.pt"; else echo "[WARN] 缺 models/yolo26n.pt（可观看，检测停用）"; fi
  if [[ -f "$ROOT/models/buffalo_l/det_10g.onnx" ]]; then echo "[OK] buffalo_l"; else echo "[WARN] 缺 buffalo_l 模型"; fi
  if [[ -x "$ROOT/scripts/lib/checks.sh" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/scripts/lib/checks.sh"
    smartcctv_check_port "${SMARTCCTV_PORT:-8300}" || true
  fi
  return "$fail"
}

cmd_repair() {
  ensure_dirs
  copy_config
  echo "将执行: uv python install 3.14 && uv sync --group dev"
  if [[ "$YES" -eq 1 ]] || { read -r -p "继续? [y/N] " a && [[ "$a" =~ ^[Yy]$ ]]; }; then
    uv python install 3.14
    uv python pin 3.14 || true
    # 默认 PyPI 超时则回退国内镜像（torch 仍走 pyproject 中的 pytorch-cpu index）
    if ! UV_HTTP_TIMEOUT=120 uv sync --group dev; then
      echo "PyPI 同步失败，改用阿里云镜像重试…"
      UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}" \
        UV_HTTP_TIMEOUT=180 uv sync --group dev
    fi
  fi
  if [[ -d "$FRONTEND_DIR" ]] && have npm; then
    echo "安装前端依赖（不强制 build）…"
    (cd "$FRONTEND_DIR" && npm install) || true
  fi
  echo "模型下载（可选）: uv run python scripts/download_models.py"
  echo "编译前端请用启动菜单「编译前端」"
}

cmd_test() {
  ensure_dirs
  uv run ruff check server tests || true
  uv run pytest -q
  if [[ -x "$ROOT/scripts/smoke_test.sh" ]]; then
    "$ROOT/scripts/smoke_test.sh" || true
  fi
}

# ---------- 启动 ----------
is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

docker_running() {
  have docker || return 1
  docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q smartcctv
}

cmd_start() {
  ensure_dirs
  copy_config
  if docker_running; then
    echo "[拒绝] Docker 模式已在运行，请先停止 Docker 再终端启动（互斥）"
    return 1
  fi
  if is_running; then echo "已在运行 pid=$(cat "$PID_FILE")"; return 0; fi
  local port="${SMARTCCTV_PORT:-8300}"
  nohup uv run uvicorn server.app:app --host 0.0.0.0 --port "$port" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "已启动 pid=$(cat "$PID_FILE")  log=$LOG_FILE"
  echo "面板: http://127.0.0.1:${port} 或 http://cctv.lobn:${port}"
  if [[ ! -f "$FRONTEND_DIR/dist/index.html" ]]; then
    echo "[WARN] 无 frontend/dist，生产页仅 API 提示；请编译或解压 frontend.zip"
  fi
}

cmd_start_fg() {
  ensure_dirs
  copy_config
  if docker_running; then
    echo "[拒绝] Docker 模式已在运行"
    return 1
  fi
  local port="${SMARTCCTV_PORT:-8300}"
  echo "前台 API port=$port（Ctrl+C 停止）"
  uv run uvicorn server.app:app --host 0.0.0.0 --port "$port"
}

cmd_dev() {
  ensure_dirs
  copy_config
  have npm || { echo "[FAIL] 需要 npm"; return 1; }
  if docker_running; then
    echo "[拒绝] Docker 模式已在运行"
    return 1
  fi
  if is_running; then
    echo "先停止后台进程…"
    cmd_stop
  fi
  [[ -d "$FRONTEND_DIR/node_modules" ]] || (cd "$FRONTEND_DIR" && npm install)
  local port="${SMARTCCTV_PORT:-8300}"
  echo "开发：后端 :${port} + Vite :5173（/api 代理）"
  echo "打开 http://127.0.0.1:5173  · Ctrl+C 停两端"
  echo "----------------------------------------"
  uv run uvicorn server.app:app --host 0.0.0.0 --port "$port" --reload &
  local api_pid=$!
  echo "$api_pid" >"$PID_FILE"
  cleanup() {
    echo
    echo "停止开发进程…"
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
    rm -f "$PID_FILE"
  }
  trap cleanup EXIT INT TERM
  (cd "$FRONTEND_DIR" && npm run dev)
}

cmd_build_frontend() {
  have npm || { echo "[FAIL] 需要 npm"; return 1; }
  [[ -d "$FRONTEND_DIR" ]] || { echo "无 frontend/"; return 1; }
  echo "编译前端 → frontend/dist"
  (cd "$FRONTEND_DIR" && npm install && npm run build)
  echo "完成: $FRONTEND_DIR/dist"
}

cmd_export() {
  ensure_dirs
  mkdir -p "$EXPORT_DIR"
  local stamp stage out_be out_fe
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out_be="$EXPORT_DIR/backend-${stamp}.zip"
  out_fe="$EXPORT_DIR/frontend-${stamp}.zip"
  stage="$(mktemp -d)"
  echo "=== 导出（不编译）结构对齐开发：server/ + frontend/dist/ ==="

  mkdir -p "$stage/pack"
  rsync -a \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'data' --exclude 'frontend' --exclude 'export' \
    --exclude '.pytest_cache' --exclude 'tests' --exclude 'docs' \
    --exclude 'models/*.pt' --exclude 'models/buffalo_l' \
    "$ROOT/server" "$ROOT/pyproject.toml" "$ROOT/START.sh" \
    "$ROOT/config.example.yaml" "$ROOT/docker-compose.yml" \
    "$stage/pack/"
  [[ -f "$ROOT/uv.lock" ]] && cp "$ROOT/uv.lock" "$stage/pack/"
  mkdir -p "$stage/pack/data/"{db,secrets,recordings,snapshots,thumbs,logs,s3} "$stage/pack/models"
  cat >"$stage/pack/README-RUN.txt" <<'EOF'
SmartCCTV 运行包
1. uv sync
2. cp config.example.yaml config.yaml
3. 解压 frontend.zip 得到 frontend/dist/
4. ./START.sh start
模型需自行放入 models/（或 scripts/download_models.py）
EOF
  (cd "$stage/pack" && zip -qr "$out_be" .)
  echo "[OK] $out_be"

  if [[ ! -f "$FRONTEND_DIR/dist/index.html" ]]; then
    echo "[FAIL] 无 frontend/dist，请先编译前端"
    rm -rf "$stage"
    return 1
  fi
  mkdir -p "$stage/fe/frontend"
  rsync -a "$FRONTEND_DIR/dist" "$stage/fe/frontend/"
  (cd "$stage/fe" && zip -qr "$out_fe" frontend)
  echo "[OK] $out_fe"
  ln -sfn "$(basename "$out_be")" "$EXPORT_DIR/backend.zip"
  ln -sfn "$(basename "$out_fe")" "$EXPORT_DIR/frontend.zip"
  echo "软链: $EXPORT_DIR/backend.zip  $EXPORT_DIR/frontend.zip"
  rm -rf "$stage"
}

cmd_stop() {
  # 杀死终端后台进程
  if ! is_running; then
    echo "终端进程未运行"
    rm -f "$PID_FILE"
  else
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 1
    if is_running; then kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true; fi
    rm -f "$PID_FILE"
    echo "已杀死终端进程"
  fi
}

cmd_docker_start() {
  ensure_dirs
  copy_config
  if ! have docker; then echo "[FAIL] 未安装 docker"; return 1; fi
  if is_running; then
    echo "[拒绝] 终端模式已在运行，请先杀死进程再 Docker 启动（互斥）"
    return 1
  fi
  docker compose -f "$COMPOSE_FILE" up -d --build smartcctv
  echo "Docker 已启动（host 网络，端口 ${SMARTCCTV_PORT:-8300}）"
}

cmd_docker_stop() {
  if ! have docker; then echo "[FAIL] 未安装 docker"; return 1; fi
  docker compose -f "$COMPOSE_FILE" stop smartcctv 2>/dev/null || true
  docker compose -f "$COMPOSE_FILE" rm -f smartcctv 2>/dev/null || true
  echo "Docker 已停止"
}

# ---------- 运维 ----------
cmd_status() {
  echo "=== 状态 ==="
  if is_running; then
    echo "终端模式: running  pid=$(cat "$PID_FILE")"
  else
    echo "终端模式: stopped"
  fi
  if have docker; then
    if docker_running; then echo "Docker 模式: running"; else echo "Docker 模式: stopped"; fi
  else
    echo "Docker 模式: （无 docker）"
  fi
  echo "--- 最近日志 ---"
  if [[ -f "$LOG_FILE" ]]; then tail -n 20 "$LOG_FILE"; else echo "(尚日志)"; fi
}

cmd_logs() {
  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  echo "跟踪日志（Ctrl+C 返回）: $LOG_FILE"
  # 交互里 tail -f 会占住；捕获 INT 后回到菜单
  trap 'trap - INT; echo; echo "已停止跟踪"' INT
  tail -f "$LOG_FILE" || true
  trap - INT
}

cmd_reset_totp() {
  ensure_dirs
  echo "将重置 TOTP 并踢出全部会话"
  if [[ "$YES" -eq 1 ]] || { read -r -p "确认? [y/N] " a && [[ "$a" =~ ^[Yy]$ ]]; }; then
    uv run python - <<'PY'
from pathlib import Path
from server.secrets import SecretStore
store = SecretStore(Path("data/secrets"))
print("new totp:", store.rotate_totp())
print(store.totp_uri())
try:
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(store.totp_uri())
    qr.make(fit=True)
    qr.print_ascii(invert=True)
except Exception:
    pass
PY
  else
    echo "已取消"
  fi
}

cmd_reindex() {
  ensure_dirs
  echo "重建录像索引（扫描 data/recordings ↔ DB）…"
  uv run python - <<'PY'
import asyncio
from pathlib import Path
from server.config import load_config
from server.db import Database
from server.record import RecordService
from server.storage import Storage
from server.diskmon import DiskMon
from server.bus import EventBus

async def main():
    root = Path(".").resolve()
    cfg = load_config(root / "config.yaml") if (root / "config.yaml").exists() else load_config(root / "config.example.yaml")
    data = cfg.data_path(root)
    db = Database(data / "db" / "smartcctv.sqlite3")
    await db.init()
    storage = Storage(data, cfg.storage)
    diskmon = DiskMon(data, cfg.disk)
    bus = EventBus()
    rec = RecordService(cfg, db, storage, diskmon, bus, root)
    n = await rec.reindex()
    print(f"完成，新增索引 {n} 条")

asyncio.run(main())
PY
}

cmd_help() {
  cat <<EOF
用法: ./START.sh [子命令] [--yes|--ci]

交互菜单（无参数）分三组，选中后清屏覆盖刷新：
  启动   终端/开发/编译/导出 / 杀死进程 / Docker
  运维   状态 / 日志 / 重置 TOTP / 更新录像索引 / 帮助
  环境   环境检测 / 环境修复 / 自动化测试

子命令:
  diagnose | repair | test
  start | start-fg | dev | build-frontend | export
  stop | restart | status | logs
  docker-start | docker-stop
  reset-totp | reindex | help

域名建议: cctv.lobn → 本机（hosts / 内网 DNS）
EOF
}

# ---------- 菜单（覆盖刷新） ----------
run_action() {
  # $1=标题；其余为要执行的命令函数名
  local title="$1"
  shift
  clear_screen
  echo "SmartCCTV · $title"
  echo "----------------------------------------"
  "$@" || true
  pause_back
}

menu_start() {
  while true; do
    clear_screen
    cat <<EOF
SmartCCTV · 启动
  1) 终端启动（生产：有 dist 则托管面板）
  2) 前台启动 API
  3) 开发（Vite + 后端，终端）
  4) 编译前端
  5) 导出部署包（backend.zip + frontend.zip，不编译）
  6) 杀死进程（终端模式）
  7) Docker 启动
  8) Docker 停止
  0) 返回上级
EOF
    read -r -p "> " c
    case "$c" in
      1) run_action "终端启动" cmd_start ;;
      2) clear_screen; echo "SmartCCTV · 前台 API"; echo "----------------------------------------"; cmd_start_fg || true; pause_back ;;
      3) clear_screen; echo "SmartCCTV · 开发"; echo "----------------------------------------"; cmd_dev || true; pause_back ;;
      4) run_action "编译前端" cmd_build_frontend ;;
      5) run_action "导出部署包" cmd_export ;;
      6) run_action "杀死进程" cmd_stop ;;
      7) run_action "Docker 启动" cmd_docker_start ;;
      8) run_action "Docker 停止" cmd_docker_stop ;;
      0) return 0 ;;
      *) ;;
    esac
  done
}

menu_ops() {
  while true; do
    clear_screen
    cat <<EOF
SmartCCTV · 运维
  1) 状态
  2) 日志（跟踪）
  3) 重置 TOTP 密钥
  4) 更新录像索引
  5) 帮助
  0) 返回上级
EOF
    read -r -p "> " c
    case "$c" in
      1) run_action "状态" cmd_status ;;
      2) clear_screen; echo "SmartCCTV · 日志"; echo "----------------------------------------"; cmd_logs; pause_back ;;
      3) run_action "重置 TOTP" cmd_reset_totp ;;
      4) run_action "更新录像索引" cmd_reindex ;;
      5) run_action "帮助" cmd_help ;;
      0) return 0 ;;
      *) ;;
    esac
  done
}

menu_env() {
  while true; do
    clear_screen
    cat <<EOF
SmartCCTV · 环境检测
  1) 环境检测
  2) 环境修复 / 安装
  3) 自动化测试
  0) 返回上级
EOF
    read -r -p "> " c
    case "$c" in
      1) run_action "环境检测" cmd_diagnose ;;
      2) run_action "环境修复" cmd_repair ;;
      3) run_action "自动化测试" cmd_test ;;
      0) return 0 ;;
      *) ;;
    esac
  done
}

menu() {
  while true; do
    clear_screen
    cat <<EOF
SmartCCTV
  1) 启动
  2) 运维
  3) 环境检测
  0) 退出
EOF
    read -r -p "> " choice
    case "$choice" in
      1) menu_start ;;
      2) menu_ops ;;
      3) menu_env ;;
      0) clear_screen; exit 0 ;;
      *) ;;
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
    start-fg) cmd_start_fg ;;
    dev) cmd_dev ;;
    build-frontend|build) cmd_build_frontend ;;
    export) cmd_export ;;
    stop|kill) cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status) cmd_status ;;
    logs) cmd_logs ;;
    docker-start) cmd_docker_start ;;
    docker-stop) cmd_docker_stop ;;
    test) cmd_test ;;
    reset-totp) cmd_reset_totp ;;
    reindex) cmd_reindex ;;
    help|-h|--help) cmd_help ;;
    *) echo "未知: $cmd"; cmd_help; exit 1 ;;
  esac
}

main "$@"
