#!/usr/bin/env bash
# 需要人工参与的部分：密码登录失败时在终端显示二维码，用 B站 App 扫码
# 用法: scripts/login.sh            登录 config 里全部账号
#       scripts/login.sh 账号 --qrcode 直接扫码登录指定账号
source "$(dirname "$0")/_env.sh"
exec "$PY" main.py login "$@"
