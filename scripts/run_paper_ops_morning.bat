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
set "LOG_FILE=!DAILY_LOG_DIR!\paper_ops_morning_!RUN_DATE!_!RUN_TIME!.log"
set "EXIT_CODE=0"
echo [START] paper_ops_morning !RUN_DATE! !RUN_TIME!
echo [START] paper_ops_morning !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"

set "PRE_RACE_STATUS=pending"
set "ODDS_REFRESH_STATUS=pending"
set "PREDICTION_SHEET_STATUS=pending"
set "FROZEN_BETS_STATUS=pending"
set "CONSENSUS_SHEET_STATUS=pending"
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\logs\step_pre_race.status'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8).Trim() } catch { 'pending' } } else { 'pending' }"') do set "PRE_RACE_STATUS=%%I"
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\logs\step_odds_refresh.status'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8).Trim() } catch { 'pending' } } else { 'pending' }"') do set "ODDS_REFRESH_STATUS=%%I"
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\logs\step_prediction_sheet.status'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8).Trim() } catch { 'pending' } } else { 'pending' }"') do set "PREDICTION_SHEET_STATUS=%%I"
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\logs\step_frozen_bets.status'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8).Trim() } catch { 'pending' } } else { 'pending' }"') do set "FROZEN_BETS_STATUS=%%I"
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\logs\step_consensus_sheet.status'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8).Trim() } catch { 'pending' } } else { 'pending' }"') do set "CONSENSUS_SHEET_STATUS=%%I"

if /I not "!PRE_RACE_STATUS!"=="ok" (
  for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\pre_race_run.json'; if (Test-Path $p) { try { ((Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).status) } catch { '' } }"') do set "PRE_RACE_STATUS=%%I"
)
if /I not "!ODDS_REFRESH_STATUS!"=="ok" (
  for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\odds_refresh_run.json'; if (Test-Path $p) { try { ((Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).status) } catch { '' } }"') do set "ODDS_REFRESH_STATUS=%%I"
)

set "PREDICTION_SHEET_FILE=reports\predictions\!RUN_DATE_ISO!\prediction_sheet.json"
set "FROZEN_BETS_FILE=reports\predictions\!RUN_DATE_ISO!\frozen_bets.json"
set "CONSENSUS_SHEET_FILE=reports\consensus\!RUN_DATE_ISO!\consensus_sheet.json"
set "PREDICTION_SHEET_EXISTS=0"
set "FROZEN_BETS_EXISTS=0"
set "PREDICTION_BUNDLE_EXISTS=0"
if exist "!PREDICTION_SHEET_FILE!" set "PREDICTION_SHEET_EXISTS=1"
if exist "!FROZEN_BETS_FILE!" set "FROZEN_BETS_EXISTS=1"
if "!PREDICTION_SHEET_EXISTS!"=="1" if "!FROZEN_BETS_EXISTS!"=="1" set "PREDICTION_BUNDLE_EXISTS=1"
echo [INFO] prediction_sheet_status=!PREDICTION_SHEET_STATUS!>>"!LOG_FILE!"
echo [INFO] frozen_bets_status=!FROZEN_BETS_STATUS!>>"!LOG_FILE!"
echo [INFO] prediction_sheet_exists=!PREDICTION_SHEET_EXISTS!>>"!LOG_FILE!"
echo [INFO] frozen_bets_exists=!FROZEN_BETS_EXISTS!>>"!LOG_FILE!"

set "PREFLIGHT_CLASS="
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\preflight_source_check.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).sourceClassification } catch { '' } }"') do set "PREFLIGHT_CLASS=%%I"
if not defined PREFLIGHT_CLASS set "PREFLIGHT_CLASS=unknown"
echo [INFO] preflight_classification=!PREFLIGHT_CLASS!>>"!LOG_FILE!"
if /I not "!PREFLIGHT_CLASS!"=="ready" (
  echo [INFO] source_not_ready>>"!LOG_FILE!"
  set "EXIT_CODE=0"
  goto :done
)

if /I "!PRE_RACE_STATUS!"=="ok" (
  echo [SKIP] pre_race_run already exists>>"!LOG_FILE!"
  call :mark_step pre_race skipped_existing
) else (
  echo [CMD] run_daily_pre_race>>"!LOG_FILE!"
  echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_pre_race --date !RUN_DATE_ISO!
  !PYTHON_EXE! -m src.pipeline.run_daily_pre_race --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
  set "EXIT_CODE=!ERRORLEVEL!"
  echo [EXIT] run_daily_pre_race=!EXIT_CODE!>>"!LOG_FILE!"
  if exist "reports\daily\!RUN_DATE_ISO!\pre_race_run.json" (
    call :mark_step pre_race done
  ) else (
    call :mark_step pre_race failed
  )
  if not "!EXIT_CODE!"=="0" goto :done
)

if /I "!ODDS_REFRESH_STATUS!"=="ok" (
  echo [SKIP] odds_refresh already exists>>"!LOG_FILE!"
  call :mark_step odds_refresh skipped_existing
) else (
  echo [CMD] run_odds_refresh>>"!LOG_FILE!"
  call "%SCRIPT_DIR%run_odds_refresh.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
  set "EXIT_CODE=!ERRORLEVEL!"
  echo [EXIT] run_odds_refresh=!EXIT_CODE!>>"!LOG_FILE!"
  if exist "reports\daily\!RUN_DATE_ISO!\odds_refresh_run.json" (
    call :mark_step odds_refresh done
  ) else (
    call :mark_step odds_refresh failed
  )
  if not "!EXIT_CODE!"=="0" goto :done
)

if "!PREDICTION_BUNDLE_EXISTS!"=="1" (
  echo [SKIP] prediction_sheet already exists>>"!LOG_FILE!"
  call :mark_step prediction_sheet skipped_existing
  call :mark_step frozen_bets skipped_existing
) else (
  echo [CMD] run_prediction_sheet>>"!LOG_FILE!"
  call "%SCRIPT_DIR%run_prediction_sheet.bat" !RUN_DATE_ISO! >>"!LOG_FILE!" 2>&1
  set "STEP_EXIT=!ERRORLEVEL!"
  echo [EXIT] run_prediction_sheet=!STEP_EXIT!>>"!LOG_FILE!"
  if exist "reports\predictions\!RUN_DATE_ISO!\prediction_sheet.json" (
    call :mark_step prediction_sheet done
  ) else (
    call :mark_step prediction_sheet failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  )
  if exist "reports\predictions\!RUN_DATE_ISO!\frozen_bets.json" (
    call :mark_step frozen_bets done
  ) else (
    call :mark_step frozen_bets failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  )
)

set "CONSENSUS_CAN_BUILD=0"
if exist "!PREDICTION_SHEET_FILE!" set "CONSENSUS_CAN_BUILD=1"
if "!CONSENSUS_CAN_BUILD!"=="1" (
  echo [CMD] build_consensus_sheet>>"!LOG_FILE!"
  echo [CMD] !PYTHON_EXE! scripts\build_consensus_sheet.py --date !RUN_DATE_ISO!
  !PYTHON_EXE! scripts\build_consensus_sheet.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
  set "STEP_EXIT=!ERRORLEVEL!"
  echo [EXIT] build_consensus_sheet=!STEP_EXIT!>>"!LOG_FILE!"
  if exist "!CONSENSUS_SHEET_FILE!" (
    call :mark_step consensus_sheet done
  ) else (
    call :mark_step consensus_sheet failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  )
) else (
  echo [SKIP] build_consensus_sheet prediction_sheet_missing>>"!LOG_FILE!"
  call :mark_step consensus_sheet skipped_missing_prediction_sheet
)

:done
echo [CMD] write_morning_route_status>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!
!PYTHON_EXE! scripts\write_morning_route_status.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "STATUS_EXIT=!ERRORLEVEL!"
if not "!STATUS_EXIT!"=="0" set "EXIT_CODE=!STATUS_EXIT!"
echo [EXIT] write_morning_route_status=!STATUS_EXIT!>>"!LOG_FILE!"
echo [END] paper_ops_morning exit=!EXIT_CODE!
echo [END] paper_ops_morning exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!

:mark_step
set "STEP_NAME=%~1"
set "STEP_STATUS=%~2"
> "!DAILY_LOG_DIR!\step_%STEP_NAME%.status" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="ok" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.done" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="done" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.done" echo %STEP_STATUS%
if "%STEP_STATUS:~0,8%"=="skipped_" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.%STEP_STATUS%" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="failed" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.failed" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="result_data_missing" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.result_data_missing" echo %STEP_STATUS%
exit /b 0
