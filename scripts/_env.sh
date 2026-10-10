# 被其它脚本 source：进入项目目录并选择 python
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
