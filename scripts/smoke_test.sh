#!/usr/bin/env bash
# 轻量冒烟：起服务 → health/login 路径可达（无模型也可）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/logs
PORT="${SMARTCCTV_PORT:-18300}"
export SMARTCCTV_PORT="$PORT"
cp -n config.example.yaml config.yaml 2>/dev/null || true

uv run uvicorn server.app:app --host 127.0.0.1 --port "$PORT" >/tmp/smartcctv-smoke.log 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; }
trap cleanup EXIT

ok=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/" >/dev/null; then ok=1; break; fi
  sleep 1
done
[[ "$ok" -eq 1 ]] || { echo "FAIL: server not up"; tail -50 /tmp/smartcctv-smoke.log; exit 1; }

code=$(curl -s -o /tmp/login.json -w "%{http_code}" -X POST "http://127.0.0.1:$PORT/api/auth/login" \
  -H 'Content-Type: application/json' -d '{"code":"000000"}')
[[ "$code" == "401" || "$code" == "200" ]] || { echo "FAIL: login endpoint $code"; exit 1; }

echo "PASS: smoke basic"
