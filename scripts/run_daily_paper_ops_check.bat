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
set "DAILY_LOG_DIR=reports\daily\!RUN_DATE_ISO!\logs"
if not exist "!DAILY_LOG_DIR!" mkdir "!DAILY_LOG_DIR!"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=!DAILY_LOG_DIR!\daily_paper_ops_check_!RUN_DATE!_!RUN_TIME!.log"
echo [START] daily_paper_ops_check !RUN_DATE! !RUN_TIME!
echo [START] daily_paper_ops_check !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"

echo [CMD] run_paper_ops_preflight>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_paper_ops_preflight.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
if not "!EXIT_CODE!"=="0" goto :write_report

set "PREFLIGHT_CLASS="
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\preflight_source_check.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).sourceClassification } catch { '' } }"') do set "PREFLIGHT_CLASS=%%I"
if not defined PREFLIGHT_CLASS set "PREFLIGHT_CLASS=unknown"
echo [INFO] preflight_classification=!PREFLIGHT_CLASS!>>"!LOG_FILE!"

if /I "!PREFLIGHT_CLASS!"=="ready" (
  echo [CMD] run_paper_ops_morning>>"!LOG_FILE!"
  call "%SCRIPT_DIR%run_paper_ops_morning.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
  set "STEP_EXIT=!ERRORLEVEL!"
  if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  echo [EXIT] run_paper_ops_morning=!STEP_EXIT!>>"!LOG_FILE!"

  echo [CMD] run_paper_ops_evening>>"!LOG_FILE!"
  call "%SCRIPT_DIR%run_paper_ops_evening.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
  set "STEP_EXIT=!ERRORLEVEL!"
  if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  echo [EXIT] run_paper_ops_evening=!STEP_EXIT!>>"!LOG_FILE!"
)

echo [CMD] run_paper_ops_monitor>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_paper_ops_monitor.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
echo [EXIT] run_paper_ops_monitor=!STEP_EXIT!>>"!LOG_FILE!"

:write_report
echo [END] daily_paper_ops_check exit=!EXIT_CODE!
echo [END] daily_paper_ops_check exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
