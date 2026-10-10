#!/usr/bin/env bash
# 后台常驻运行（nohup），日志见 data/logs/clipper.log
source "$(dirname "$0")/_env.sh"
mkdir -p data
if [ -f data/daemon.pid ] && kill -0 "$(cat data/daemon.pid)" 2>/dev/null; then
  echo "已在运行 (pid $(cat data/daemon.pid))"; exit 0
fi
nohup "$PY" main.py run --loop --daemon "$@" >> data/daemon.out 2>&1 &
echo $! > data/daemon.pid
echo "已在后台启动 (pid $!)，日志: data/logs/clipper.log"
