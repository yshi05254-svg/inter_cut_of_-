@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
where ffmpeg >nul 2>nul || echo 警告：未找到 ffmpeg，请下载后加入 PATH，或在 config.json 里填写「ffmpeg路径」
if not exist config.json (copy config.example.json config.json >nul & echo 已生成 config.json，请填写后再运行)
pause
