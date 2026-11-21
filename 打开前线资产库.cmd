@echo off
chcp 65001 >nul
python -X utf8 -B "%~dp0scripts\断箭\启动正式资料库.py" --start
if errorlevel 1 (
  echo 前线资产库启动失败，请检查Python环境、服务日志或8767端口占用。
  pause
  exit /b 1
)
