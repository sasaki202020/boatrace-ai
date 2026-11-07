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
set "LOG_FILE=logs\tasks\daily_report_!RUN_DATE!_!RUN_TIME!.log"
echo [START] daily_report !RUN_DATE! !RUN_TIME!
echo [START] daily_report !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.daily_report --date !RUN_DATE_ISO! --jcd all
echo [CMD] !PYTHON_EXE! -m src.pipeline.daily_report --date !RUN_DATE_ISO! --jcd all>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.daily_report --date !RUN_DATE_ISO! --jcd all>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_ops_goal_board.bat" !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "OPS_BOARD_EXIT=!ERRORLEVEL!"
echo [OPS_BOARD_EXIT] !OPS_BOARD_EXIT!>>"!LOG_FILE!"
if not "!OPS_BOARD_EXIT!"=="0" (
  echo [WARN] ops_goal_board generation failed, continuing with daily_report exit=!EXIT_CODE!
  echo [WARN] ops_goal_board generation failed, continuing with daily_report exit=!EXIT_CODE!>>"!LOG_FILE!"
)
echo [END] daily_report exit=!EXIT_CODE!
echo [END] daily_report exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
