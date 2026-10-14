#!/bin/bash
# Local convenience wrapper; all entry points use the same selected environment.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"
ASHARE_INTERPRETER="${ASHARE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [ ! -x "$ASHARE_INTERPRETER" ]; then
    echo '请先运行 uv sync --extra data --extra web，或设置 ASHARE_PYTHON。'
    exit 1
fi
RUNTIME_DIR="$("$ASHARE_INTERPRETER" -c 'from ashare_alpha.paths import JOBS_ROOT; print(JOBS_ROOT.parent)')"
mkdir -p "$RUNTIME_DIR"
if [ -f "$RUNTIME_DIR/server.json" ]; then
    SERVER_PID="$("$ASHARE_INTERPRETER" -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$RUNTIME_DIR/server.json")"
    if lsof -a -p "$SERVER_PID" -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
        open 'http://127.0.0.1:8787'
        exit 0
    fi
fi
"$ASHARE_INTERPRETER" -m ashare_alpha web >"$RUNTIME_DIR/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 30); do
    if lsof -a -p "$SERVER_PID" -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
        open 'http://127.0.0.1:8787'
        echo "操作台已启动；日志：$RUNTIME_DIR/server.log"
        exit 0
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "服务退出，请查看 $RUNTIME_DIR/server.log"
        exit 1
    fi
    sleep 1
done
echo "服务未就绪，请查看 $RUNTIME_DIR/server.log"
exit 1
