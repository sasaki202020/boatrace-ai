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
for /f %%I in ('powershell -NoProfile -Command "(Get-Date '%RUN_DATE_ISO%').AddDays(-6).ToString('yyyyMMdd')"') do set "START_DATE=%%I"
if not defined START_DATE set "START_DATE=%RUN_DATE%"
set "DAILY_LOG_DIR=reports\daily\!RUN_DATE_ISO!\logs"
if not exist "!DAILY_LOG_DIR!" mkdir "!DAILY_LOG_DIR!"
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set "RUN_TIME=%%I"
set "LOG_FILE=!DAILY_LOG_DIR!\paper_ops_monitor_!RUN_DATE!_!RUN_TIME!.log"
echo [START] paper_ops_monitor !RUN_DATE! !RUN_TIME!
echo [START] paper_ops_monitor !RUN_DATE! !RUN_TIME!>>"!LOG_FILE!"

set "PREFLIGHT_CLASS="
for /f %%I in ('powershell -NoProfile -Command "$p = 'reports\daily\!RUN_DATE_ISO!\preflight_source_check.json'; if (Test-Path $p) { try { (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json).sourceClassification } catch { '' } }"') do set "PREFLIGHT_CLASS=%%I"
if not defined PREFLIGHT_CLASS set "PREFLIGHT_CLASS=unknown"
echo [INFO] preflight_classification=!PREFLIGHT_CLASS!>>"!LOG_FILE!"

echo [CMD] health_check>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.pipeline.health_check --date !RUN_DATE_ISO!
!PYTHON_EXE! -m src.pipeline.health_check --date !RUN_DATE_ISO!>>"!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"
echo [EXIT] health_check=!EXIT_CODE!>>"!LOG_FILE!"
if /I "!EXIT_CODE!"=="0" (call :mark_step health_check done) else (call :mark_step health_check failed)

echo [CMD] live_operation_summary>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.evaluation.live_operation_summary --start-date !START_DATE! --end-date !RUN_DATE!
!PYTHON_EXE! -m src.evaluation.live_operation_summary --start-date !START_DATE! --end-date !RUN_DATE!>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
echo [EXIT] live_operation_summary=!STEP_EXIT!>>"!LOG_FILE!"
if /I "!STEP_EXIT!"=="0" (call :mark_step live_operation_summary done) else (call :mark_step live_operation_summary failed)

echo [CMD] tuning_gate>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! -m src.evaluation.tuning_gate --start-date !START_DATE! --end-date !RUN_DATE!
!PYTHON_EXE! -m src.evaluation.tuning_gate --start-date !START_DATE! --end-date !RUN_DATE!>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
echo [EXIT] tuning_gate=!STEP_EXIT!>>"!LOG_FILE!"
if /I "!STEP_EXIT!"=="0" (call :mark_step tuning_gate done) else (call :mark_step tuning_gate failed)

echo [CMD] audit_repo_health>>"!LOG_FILE!"
echo [CMD] write_daily_paper_ops_check>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\write_daily_paper_ops_check.py --date !RUN_DATE_ISO! --preflight-class !PREFLIGHT_CLASS! --full-route-executed 0
!PYTHON_EXE! scripts\write_daily_paper_ops_check.py --date !RUN_DATE_ISO! --preflight-class !PREFLIGHT_CLASS! --full-route-executed 0 >>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
echo [EXIT] write_daily_paper_ops_check=!STEP_EXIT!>>"!LOG_FILE!"
if /I "!STEP_EXIT!"=="0" (call :mark_step daily_paper_ops_check done) else (call :mark_step daily_paper_ops_check failed)

echo [CMD] audit_repo_health>>"!LOG_FILE!"
echo [CMD] !PYTHON_EXE! scripts\audit_repo_health.py
!PYTHON_EXE! scripts\audit_repo_health.py>>"!LOG_FILE!" 2>&1
set "STEP_EXIT=!ERRORLEVEL!"
if not "!STEP_EXIT!"=="0" set "EXIT_CODE=!STEP_EXIT!"
echo [EXIT] audit_repo_health=!STEP_EXIT!>>"!LOG_FILE!"
if /I "!STEP_EXIT!"=="0" (call :mark_step final_goal_progress done) else (call :mark_step final_goal_progress failed)

echo [END] paper_ops_monitor exit=!EXIT_CODE!
echo [END] paper_ops_monitor exit=!EXIT_CODE!>>"!LOG_FILE!"
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
