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
set "LOG_FILE=logs\tasks\daily_freeze_!RUN_DATE!_!RUN_TIME!.log"
echo [START] daily_freeze !RUN_DATE! !RUN_TIME!
echo [START] daily_freeze !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"
echo [CMD] preflight_official_source>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\preflight_official_source.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\preflight_official_source.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
if not "!EXIT_CODE!"=="0" goto :done
set "PREFLIGHT_CLASS="
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\preflight_source_check.json'; if (Test-Path $p) { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).sourceClassification }"') do set "PREFLIGHT_CLASS=%%I"
if not defined PREFLIGHT_CLASS set "PREFLIGHT_CLASS=unknown"
echo [INFO] preflight_classification=!PREFLIGHT_CLASS!>>"!LOG_FILE!"
echo [CMD] morning_route_status>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "STATUS_EXIT=!ERRORLEVEL!"
echo [EXIT] !STATUS_EXIT!>>"!LOG_FILE!"
if /I not "!PREFLIGHT_CLASS!"=="ready" (
  set "EXIT_CODE=0"
  goto :done
)
echo [CMD] discover_today>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.discover_today --date !RUN_DATE_ISO!
!PYTHON_EXE! -m src.pipeline.discover_today --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] run_daily_pre_race>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_pre_race --date !RUN_DATE_ISO!
!PYTHON_EXE! -m src.pipeline.run_daily_pre_race --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] run_today odds>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_odds_refresh.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] prediction_sheet>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_prediction_sheet.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] run_today beforeinfo>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_today --date !RUN_DATE_ISO! --jcd all --stage beforeinfo
!PYTHON_EXE! -m src.pipeline.run_today --date !RUN_DATE_ISO! --jcd all --stage beforeinfo>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done
echo [CMD] daily_report>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_daily_report.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
:done
echo [CMD] morning_route_status>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "STATUS_EXIT=!ERRORLEVEL!"
echo [EXIT] !STATUS_EXIT!>>"!LOG_FILE!"
echo [END] daily_freeze exit=!EXIT_CODE!
echo [END] daily_freeze exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
