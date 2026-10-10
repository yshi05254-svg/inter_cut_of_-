#!/usr/bin/env bash
# 处理一遍全部任务后退出，适合 crontab，例如每 10 分钟:
# */10 * * * * /path/to/project/scripts/run_once.sh >/dev/null 2>&1
source "$(dirname "$0")/_env.sh"
exec "$PY" main.py run --daemon "$@"
