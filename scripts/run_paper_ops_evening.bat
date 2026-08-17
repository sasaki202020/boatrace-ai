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
set "LOG_FILE=!DAILY_LOG_DIR!\paper_ops_evening_!RUN_DATE!_!RUN_TIME!.log"
set "EXIT_CODE=0"
echo [START] paper_ops_evening !RUN_DATE! !RUN_TIME!
echo [START] paper_ops_evening !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"

set "PREFLIGHT_CLASS="
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\preflight_source_check.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).sourceClassification } catch { '' } }"') do set "PREFLIGHT_CLASS=%%I"
if not defined PREFLIGHT_CLASS set "PREFLIGHT_CLASS=unknown"
echo [INFO] preflight_classification=!PREFLIGHT_CLASS!>>"!LOG_FILE!"
if /I not "!PREFLIGHT_CLASS!"=="ready" (
  echo [INFO] source_not_ready>>"!LOG_FILE!"
  set "EXIT_CODE=0"
  goto :done
)

echo [CMD] check_k_inbox>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date !RUN_DATE_ISO! --end-date !RUN_DATE_ISO!>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.check_k_inbox --input-dir data/inbox/k_results --start-date !RUN_DATE_ISO! --end-date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] check_k_inbox=!EXIT_CODE!>>"!LOG_FILE!"
if /I "!EXIT_CODE!"=="0" (
  call :mark_step check_k_inbox done
) else (
  call :mark_step check_k_inbox result_data_missing
  set "EXIT_CODE=0"
)

echo [CMD] import_k_results>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.import_k_results --input-dir data/inbox/k_results --target-dir data/raw/official/results>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.import_k_results --input-dir data/inbox/k_results --target-dir data/raw/official/results>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] import_k_results=!EXIT_CODE!>>"!LOG_FILE!"
if /I "!EXIT_CODE!"=="0" (
  call :mark_step import_k_results done
) else (
  call :mark_step import_k_results failed
  set "EXIT_CODE=0"
)

echo [CMD] run_evening_settle>>"!LOG_FILE!"
if exist "reports\daily\!RUN_DATE_ISO!\daily_summary.json" (
  echo [SKIP] daily_summary already exists>>"!LOG_FILE!"
  call :mark_step evening_settle skipped_existing
) else (
  call :run_evening_settle_direct
  set "STEP_EXIT=!ERRORLEVEL!"
  echo [EXIT] run_evening_settle=!STEP_EXIT!>>"!LOG_FILE!"
  if exist "reports\daily\!RUN_DATE_ISO!\daily_summary.json" (
    call :mark_step evening_settle done
  ) else if exist "reports\daily\!RUN_DATE_ISO!\post_race_run.json" (
    call :mark_step evening_settle result_data_missing
  ) else (
    call :mark_step evening_settle failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
    if not "!EXIT_CODE!"=="0" goto :done
  )
)

echo [CMD] run_daily_report>>"!LOG_FILE!"
if exist "reports\daily\!RUN_DATE_ISO!\daily_report.json" (
  echo [SKIP] daily_report already exists>>"!LOG_FILE!"
  call :mark_step daily_report skipped_existing
) else (
  call :run_daily_report_direct
  set "STEP_EXIT=!ERRORLEVEL!"
  echo [EXIT] run_daily_report=!STEP_EXIT!>>"!LOG_FILE!"
  if exist "reports\daily\!RUN_DATE_ISO!\daily_report.json" (
    call :mark_step daily_report done
  ) else (
    call :mark_step daily_report failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
    if not "!EXIT_CODE!"=="0" goto :done
  )
)

if not exist "reports\predictions\!RUN_DATE_ISO!\prediction_review.json" (
  echo [CMD] run_prediction_review>>"!LOG_FILE!"
  call :run_prediction_review_direct
  set "STEP_EXIT=!ERRORLEVEL!"
  echo [EXIT] run_prediction_review=!STEP_EXIT!>>"!LOG_FILE!"
  if exist "reports\predictions\!RUN_DATE_ISO!\prediction_review.json" (
    call :mark_step prediction_review done
  ) else (
    call :mark_step prediction_review failed
    if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
  )
) else (
  call :mark_step prediction_review skipped_existing
)

:done
echo [END] paper_ops_evening exit=!EXIT_CODE!
echo [END] paper_ops_evening exit=!EXIT_CODE!>>"!LOG_FILE!"
popd
exit /b !EXIT_CODE!

:mark_step
set "STEP_NAME=%~1"
set "STEP_STATUS=%~2"
> "!DAILY_LOG_DIR!\step_%STEP_NAME%.status" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="ok" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.done" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="done" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.done" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="skipped_existing" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.skipped_existing" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="failed" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.failed" echo %STEP_STATUS%
if /I "%STEP_STATUS%"=="result_data_missing" > "!DAILY_LOG_DIR!\step_%STEP_NAME%.result_data_missing" echo %STEP_STATUS%
exit /b 0

:run_evening_settle_direct
echo [CMD] !PYTHON_EXE! -m src.pipeline.run_daily_post_race --date !RUN_DATE_ISO!>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.run_daily_post_race --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
echo [EXIT] run_daily_post_race=!STEP_EXIT!>>"!LOG_FILE!"
if not "!STEP_EXIT!"=="0" exit /b !STEP_EXIT!

echo [CMD] !PYTHON_EXE! -m src.pipeline.settle_today --date !RUN_DATE_ISO! --jcd all --stake 100>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.settle_today --date !RUN_DATE_ISO! --jcd all --stake 100>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
echo [EXIT] settle_today=!STEP_EXIT!>>"!LOG_FILE!"
if not "!STEP_EXIT!"=="0" exit /b !STEP_EXIT!

call :run_daily_report_direct
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" exit /b !STEP_EXIT!

call :run_prediction_review_direct
exit /b !ERRORLEVEL!

:run_daily_report_direct
echo [CMD] !PYTHON_EXE! -m src.pipeline.daily_report --date !RUN_DATE_ISO! --jcd all>>"!LOG_FILE!"
!PYTHON_EXE! -m src.pipeline.daily_report --date !RUN_DATE_ISO! --jcd all>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
echo [EXIT] run_daily_report=!STEP_EXIT!>>"!LOG_FILE!"

echo [CMD] !PYTHON_EXE! scripts\generate_ops_goal_board.py --date !RUN_DATE_ISO!>>"!LOG_FILE!"
!PYTHON_EXE! scripts\generate_ops_goal_board.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "OPS_BOARD_EXIT=!ERRORLEVEL!"
echo [OPS_BOARD_EXIT] !OPS_BOARD_EXIT!>>"!LOG_FILE!"
if not "!OPS_BOARD_EXIT!"=="0" echo [WARN] ops_goal_board generation failed, continuing with daily_report exit=!STEP_EXIT!>>"!LOG_FILE!"
exit /b !STEP_EXIT!

:run_prediction_review_direct
echo [CMD] !PYTHON_EXE! scripts\build_prediction_review.py --date !RUN_DATE_ISO!>>"!LOG_FILE!"
!PYTHON_EXE! scripts\build_prediction_review.py --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
echo [EXIT] run_prediction_review=!STEP_EXIT!>>"!LOG_FILE!"
exit /b !STEP_EXIT!
