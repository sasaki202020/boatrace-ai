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
set "RUN_DATE_ISO=%TARGET_DATE%"
set "RUN_DATE=%TARGET_DATE:-=%"
set "DAILY_LOG_DIR=reports\daily\!RUN_DATE_ISO!\logs"
if not exist "!DAILY_LOG_DIR!" mkdir "!DAILY_LOG_DIR!"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=!DAILY_LOG_DIR!\paper_prediction_day_!RUN_DATE!_!RUN_TIME!.log"
echo [START] paper_prediction_day !RUN_DATE! !RUN_TIME!
echo [START] paper_prediction_day !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"

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

set "REUSE_EXISTING=0"
if exist "reports\daily\!RUN_DATE_ISO!\pre_race_run.json" if exist "reports\daily\!RUN_DATE_ISO!\odds_refresh_run.json" (
  set "PRE_RACE_STATUS="
  set "ODDS_REFRESH_STATUS="
  for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\pre_race_run.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).status } catch { '' } }"') do set "PRE_RACE_STATUS=%%I"
  for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\odds_refresh_run.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).status } catch { '' } }"') do set "ODDS_REFRESH_STATUS=%%I"
  if /I "!PRE_RACE_STATUS!"=="ok" if /I "!ODDS_REFRESH_STATUS!"=="ok" set "REUSE_EXISTING=1"
)
if "!REUSE_EXISTING!"=="1" (
  echo [INFO] reuse_existing_artifacts=1>>"!LOG_FILE!"
  echo [INFO] reused ready-day morning artifacts for !RUN_DATE_ISO!>>"!LOG_FILE!"
  echo [INFO] reuse_status=pre_race:!PRE_RACE_STATUS! odds_refresh:!ODDS_REFRESH_STATUS!>>"!LOG_FILE!"
  if /I not "!PREFLIGHT_CLASS!"=="ready" echo [INFO] preflight_not_ready_but_reused_existing_artifacts=1>>"!LOG_FILE!"
) else (
  if /I not "!PREFLIGHT_CLASS!"=="ready" (
    set "EXIT_CODE=0"
    set "SKIPPED_REASON=source_not_ready"
    echo [INFO] skipped_reason=!SKIPPED_REASON!>>"!LOG_FILE!"
    goto :done
  )
  echo [INFO] reuse_existing_artifacts=!REUSE_EXISTING!>>"!LOG_FILE!"
  echo [INFO] running heavy morning route for !RUN_DATE_ISO!>>"!LOG_FILE!"
  echo [CMD] run_daily_freeze>>"!LOG_FILE!"
  call "%SCRIPT_DIR%run_daily_freeze.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
  set "EXIT_CODE=!ERRORLEVEL!"
  echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
  if not "!EXIT_CODE!"=="0" goto :done
)

echo [CMD] prediction_sheet>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_prediction_sheet.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] build_consensus_sheet>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\build_consensus_sheet.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\build_consensus_sheet.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] check_k_inbox>>"!LOG_FILE!"
call "%SCRIPT_DIR%check_k_inbox.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] import_k_results>>"!LOG_FILE!"
call "%SCRIPT_DIR%import_k_results.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] run_evening_settle>>"!LOG_FILE!"
call "%SCRIPT_DIR%run_evening_settle.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] health_check>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.health_check --date !RUN_DATE_ISO!
!PYTHON_EXE! -m src.pipeline.health_check --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"
if not "!EXIT_CODE!"=="0" goto :done

echo [CMD] audit_repo_health>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\audit_repo_health.py
!PYTHON_EXE! scripts\audit_repo_health.py>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] !EXIT_CODE!>>"!LOG_FILE!"

:done
echo [END] paper_prediction_day exit=!EXIT_CODE!
echo [END] paper_prediction_day exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!
