@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
if not defined PYTHON_EXE set "PYTHON_EXE=py"
if not exist "logs\tasks" mkdir "logs\tasks"
set "TARGET_DATE=%~1"
if not defined TARGET_DATE (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TARGET_DATE=%%I"
)
set "RUN_DATE=%TARGET_DATE:-=%"
set "RUN_DATE_ISO=%TARGET_DATE%"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=logs\tasks\odds_refresh_!RUN_DATE!_!RUN_TIME!.log"
echo [START] odds_refresh !RUN_DATE! !RUN_TIME!
echo [START] odds_refresh !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_odds_refresh --date !RUN_DATE_ISO! --phase final --refresh --pending-only
"%PYTHON_EXE%" -m src.pipeline.run_daily_odds_refresh --date !RUN_DATE_ISO! --phase final --refresh --pending-only>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
echo [END] odds_refresh exit=!EXIT_CODE!
echo [END] odds_refresh exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
