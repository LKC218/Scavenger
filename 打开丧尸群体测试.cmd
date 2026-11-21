@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 -B "scripts\原作群尸\启动.py"
if errorlevel 1 pause
