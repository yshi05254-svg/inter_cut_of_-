#!/usr/bin/env bash
source "$(dirname "$0")/_env.sh"
if [ -f data/daemon.pid ] && kill "$(cat data/daemon.pid)" 2>/dev/null; then
  echo "已停止"; rm -f data/daemon.pid
else
  echo "没有在运行"
fi
