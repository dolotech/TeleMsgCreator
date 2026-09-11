#!/usr/bin/env bash
# 一键启动本地站点；参数会透传给 serverctl.py（例如 --port 9000）
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/serverctl.py start "$@"
