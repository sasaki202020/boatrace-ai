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
set "LOG_FILE=!DAILY_LOG_DIR!\paper_ops_preflight_!RUN_DATE!_!RUN_TIME!.log"
echo [START] paper_ops_preflight !RUN_DATE! !RUN_TIME!
echo [START] paper_ops_preflight !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\preflight_official_source.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\preflight_official_source.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
echo [END] paper_ops_preflight exit=!EXIT_CODE!
echo [END] paper_ops_preflight exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
