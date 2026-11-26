@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Scavenger 主服务 (127.0.0.1:8800)
cd /d "%~dp0"

set "PY_CMD="
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python"
  goto :py_ready
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py -3"
  goto :py_ready
)

echo [错误] 未检测到可用的 Python 3.10 及以上版本（已检测 python 与 py -3）。
echo 请先安装 Python 3.10+ 并勾选 Add Python to PATH 后重试。
if "%~1"=="" if not "%SCAVENGER_NO_PAUSE%"=="1" pause
exit /b 1

:py_ready
if "%~1"=="" goto :run_default
%PY_CMD% -X utf8 -B "%~dp0scripts\启动主服务.py" %*
set "SCV_EXIT=%errorlevel%"
exit /b %SCV_EXIT%

:run_default
%PY_CMD% -X utf8 -B "%~dp0scripts\启动主服务.py" serve --open --panel
set "SCV_EXIT=%errorlevel%"
if "%SCV_EXIT%"=="0" exit /b 0
echo.
echo [提示] Scavenger 主服务异常退出（退出码 %SCV_EXIT%），请根据上方信息排查。
if not "%SCAVENGER_NO_PAUSE%"=="1" pause
exit /b %SCV_EXIT%
