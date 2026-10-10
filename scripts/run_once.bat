@echo off
rem 处理一遍全部任务后退出，可在「任务计划程序」中定时执行
chcp 65001 >nul
cd /d "%~dp0.."
set PY=python
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
%PY% main.py run --daemon %*
