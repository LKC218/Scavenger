@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 -B "scripts/资源优化/游戏版服务.py" --app range --open
if errorlevel 1 pause
