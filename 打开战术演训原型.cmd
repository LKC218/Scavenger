@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 -B "scripts\启动战术演训原型.py" %*
set "prototype_exit=%errorlevel%"
if not "%prototype_exit%"=="0" (
  if "%~1"=="" pause
  exit /b %prototype_exit%
)
exit /b 0
