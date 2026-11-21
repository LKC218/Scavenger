@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 旧名称兼容入口，统一打开8784战术演训原型。
call "%~dp0打开战术演训原型.cmd" %*
exit /b %errorlevel%
