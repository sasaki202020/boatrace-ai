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
set "LOG_FILE=logs\tasks\evening_settle_!RUN_DATE!_!RUN_TIME!.log"
echo [START] evening_settle !RUN_DATE! !RUN_TIME!
echo [START] evening_settle !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_post_race --date !RUN_DATE_ISO!
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_post_race --date !RUN_DATE_ISO!>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.run_daily_post_race --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] !PYTHON_EXE! -m src.pipeline.settle_today --date !RUN_DATE_ISO! --jcd all --stake 100
echo [CMD] !PYTHON_EXE! -m src.pipeline.settle_today --date !RUN_DATE_ISO! --jcd all --stake 100>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.settle_today --date !RUN_DATE_ISO! --jcd all --stake 100>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] call "%SCRIPT_DIR%run_daily_report.bat" !RUN_DATE_ISO!>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_daily_report.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] call "%SCRIPT_DIR%run_prediction_review.bat" !RUN_DATE_ISO!>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_prediction_review.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
:done
echo [END] evening_settle exit=!EXIT_CODE!
echo [END] evening_settle exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
