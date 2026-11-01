@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\src"
set "PYTHONIOENCODING=utf-8"
set "PY=%LOCALAPPDATA%\Python\bin\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"
start "" "%PY%" -m inputsharex.gui %*
