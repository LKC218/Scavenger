@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 工作任务看板 (127.0.0.1:18800)
cd /d "%~dp0"

set "PY_CMD="
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python"
  goto :py_ready
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py -3"
  goto :py_ready
)

echo [错误] 未检测到可用的 Python 3.11 及以上版本（已检测 python 与 py -3）。
echo 请先安装 Python 3.11+ 并勾选 Add Python to PATH 后重试。
if "%~1"=="" if not "%WORKBOARD_NO_PAUSE%"=="1" if not "%SCAVENGER_NO_PAUSE%"=="1" pause
exit /b 1

:py_ready
if "%~1"=="" goto :run_default
%PY_CMD% -X utf8 -B "%~dp0scripts\工作项看板\启动看板服务.py" %*
set "WB_EXIT=%errorlevel%"
exit /b %WB_EXIT%

:run_default
%PY_CMD% -X utf8 -B "%~dp0scripts\工作项看板\启动看板服务.py" serve --open --panel
set "WB_EXIT=%errorlevel%"
if "%WB_EXIT%"=="0" exit /b 0
echo.
echo [提示] 工作任务看板异常退出（退出码 %WB_EXIT%），请根据上方信息排查。
if not "%WORKBOARD_NO_PAUSE%"=="1" if not "%SCAVENGER_NO_PAUSE%"=="1" pause
exit /b %WB_EXIT%
