@echo off
rem 无窗口后台常驻运行，日志见 data\logs\clipper.log；停止请在任务管理器结束 pythonw.exe
chcp 65001 >nul
cd /d "%~dp0.."
set PYW=pythonw
if exist .venv\Scripts\pythonw.exe set PYW=.venv\Scripts\pythonw.exe
start "" %PYW% main.py run --loop --daemon %*
echo 已在后台启动，日志: data\logs\clipper.log
