@echo off
REM 错题本 · Windows 启动脚本
REM 双击运行，或在本目录执行 start.bat
REM 需要已安装 Python 并装好依赖：pip install -r requirements.txt

cd /d "%~dp0"
python run.py

REM 若启动失败，保留窗口以便看到报错
if errorlevel 1 pause
