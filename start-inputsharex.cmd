@echo off
chcp 65001 >nul
setlocal
rem ============================================================
rem  Double-click this file: find the wireless-debugging device
rem  via mDNS and start inputsharex.
rem  Requires: tablet has "Wireless debugging" ON and was
rem  already paired with this PC (adb pair / InputShare2).
rem  Exit: Ctrl+C in this window.
rem ============================================================
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src"
set "PYTHONIOENCODING=utf-8"

rem Prefer a real Python; PATH "python" may be the Store alias.
set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
if exist "%PY%" goto run
set "PY=%USERPROFILE%\AppData\Local\Python\bin\python.exe"
if exist "%PY%" goto run
set "PY=python"

:run
"%PY%" -u -m inputsharex auto %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" echo [x] exit code %CODE%
pause
