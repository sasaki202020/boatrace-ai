@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
if not defined PYTHON_EXE set "PYTHON_EXE=py"
if not exist "logs\tasks" mkdir "logs\tasks"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "RUN_DATE=%%I"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=logs\tasks\beforeinfo_refresh_!RUN_DATE!_!RUN_TIME!.log"
echo [START] beforeinfo_refresh !RUN_DATE! !RUN_TIME!
echo [START] beforeinfo_refresh !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_today --date today --jcd all --stage beforeinfo
"%PYTHON_EXE%" -m src.pipeline.run_today --date today --jcd all --stage beforeinfo>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
echo [END] beforeinfo_refresh exit=!EXIT_CODE!
echo [END] beforeinfo_refresh exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
