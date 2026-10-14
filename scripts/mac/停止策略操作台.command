#!/bin/bash
# Stop only this workspace's recorded server and allow child-task cleanup.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"
ASHARE_INTERPRETER="${ASHARE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [ ! -x "$ASHARE_INTERPRETER" ]; then
    echo '请设置 ASHARE_PYTHON，或使用创建工作区的 .venv 环境。'
    exit 1
fi
RUNTIME_DIR="$("$ASHARE_INTERPRETER" -c 'from ashare_alpha.paths import JOBS_ROOT; print(JOBS_ROOT.parent)')"
if [ ! -f "$RUNTIME_DIR/server.json" ]; then
    echo '当前工作区没有运行中的服务。'
    exit 0
fi
SERVER_PID="$("$ASHARE_INTERPRETER" -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$RUNTIME_DIR/server.json")"
if ! lsof -a -p "$SERVER_PID" -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
    echo '记录的服务已退出。'
    exit 0
fi
kill -TERM "$SERVER_PID"
for _ in $(seq 1 20); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo '服务和后台任务已停止。'
        exit 0
    fi
    sleep 1
done
echo "服务仍在收尾，请查看 $RUNTIME_DIR/server.log"
exit 1
