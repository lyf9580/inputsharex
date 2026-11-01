@echo off
chcp 65001 >nul
rem Usage: inputsharex.cmd run --serial 192.168.1.2:43175
set "PYTHONPATH=%~dp0..\src"
set "PYTHONIOENCODING=utf-8"
python -u -m inputsharex %*
