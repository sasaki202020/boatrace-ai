@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
if not defined PYTHON_EXE set "PYTHON_EXE=py"
set "TARGET_DATE=%~1"
if not defined TARGET_DATE (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TARGET_DATE=%%I"
)
set "RUN_DATE_ISO=%TARGET_DATE%"
set "RUN_DATE=%TARGET_DATE:-=%"
set "START_DATE=20260425"

echo [CMD] paper_validation_summary
%PYTHON_EXE% -m src.evaluation.paper_validation_summary --start-date %START_DATE% --end-date %RUN_DATE_ISO%
if errorlevel 1 goto :fail

echo [CMD] paper_validation_gate
%PYTHON_EXE% -m src.evaluation.paper_validation_gate --start-date %START_DATE% --end-date %RUN_DATE_ISO%
if errorlevel 1 goto :fail

echo [CMD] find_paper_validation_eligible_dates
%PYTHON_EXE% scripts\find_paper_validation_eligible_dates.py --start-date 2026-04-01 --end-date %RUN_DATE_ISO%
if errorlevel 1 goto :fail

echo [CMD] build_post_v1_operation_progress
%PYTHON_EXE% scripts\build_post_v1_operation_progress.py
if errorlevel 1 goto :fail

echo [OK] paper_validation_refresh complete
popd
exit /b 0

:fail
set "EXIT_CODE=%ERRORLEVEL%"
echo [FAIL] paper_validation_refresh exit=%EXIT_CODE%
popd
exit /b %EXIT_CODE%
