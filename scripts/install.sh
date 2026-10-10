#!/usr/bin/env bash
# 创建虚拟环境并安装依赖（需要 python3 和 ffmpeg）
set -e
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
command -v ffmpeg >/dev/null || echo "警告：未找到 ffmpeg，请先安装（如 apt install ffmpeg / brew install ffmpeg）"
[ -f config.json ] || { cp config.example.json config.json; echo "已生成 config.json，请填写后再运行"; }
