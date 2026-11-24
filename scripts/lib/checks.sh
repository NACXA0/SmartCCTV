#!/usr/bin/env bash
# START.sh 共用检测函数
set -euo pipefail

smartcctv_check_bin() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "OK $name"
    return 0
  fi
  echo "FAIL $name missing"
  return 1
}

smartcctv_check_port() {
  local port="${1:-8300}"
  if ss -ltn 2>/dev/null | grep -q ":${port} "; then
    echo "WARN port $port in use"
    return 1
  fi
  echo "OK port $port free"
  return 0
}
