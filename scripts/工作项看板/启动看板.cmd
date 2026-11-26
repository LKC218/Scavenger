@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
python -X utf8 -B scripts\工作项看板\启动看板服务.py --open %*
if errorlevel 1 (
  echo.
  echo [提示] 本地工作项看板服务退出异常，请检查上方输出。
  pause
)
