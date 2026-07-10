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
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=logs\tasks\check_k_inbox_!RUN_DATE!_!RUN_TIME!.log"
echo [START] check_k_inbox !RUN_DATE! !RUN_TIME!
echo [START] check_k_inbox !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date !RUN_DATE! --end-date !RUN_DATE!
!PYTHON_EXE! -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date !RUN_DATE! --end-date !RUN_DATE!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
echo [END] check_k_inbox exit=!EXIT_CODE!
echo [END] check_k_inbox exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
