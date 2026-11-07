@echo off
setlocal
cd /d %~dp0

if "%1"=="results" (
  py src\pipeline\daily_update.py --mode results
  goto :eof
)

if "%1"=="predict" (
  py src\pipeline\daily_update.py --mode predict
  goto :eof
)

py src\pipeline\daily_update.py --mode full

endlocal

