@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "TARGET_DATE=%~1"
if not defined TARGET_DATE (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TARGET_DATE=%%I"
)
call "%~dp0run_paper_ops_preflight.bat" !TARGET_DATE!
if errorlevel 1 exit /b !ERRORLEVEL!
call "%~dp0run_paper_ops_morning.bat" !TARGET_DATE!
if errorlevel 1 exit /b !ERRORLEVEL!
call "%~dp0run_paper_ops_evening.bat" !TARGET_DATE!
if errorlevel 1 exit /b !ERRORLEVEL!
call "%~dp0run_paper_ops_monitor.bat" !TARGET_DATE!
exit /b !ERRORLEVEL!
