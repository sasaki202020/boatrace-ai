@echo off
setlocal
cd /d "%~dp0"

:: ログ出力先の設定
if not exist reports mkdir reports
set LOG=reports\master_run.log

echo ==== START %date% %time% ==== > "%LOG%"
echo Running BoatRace-AI-MVP Master Pipeline...

:: Python実行コマンドの解決
set PY_CMD=
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe"
) else (
    py --version >nul 2>&1
    if %errorlevel%==0 (
        set PY_CMD=py
    ) else (
        python --version >nul 2>&1
        if %errorlevel%==0 set PY_CMD=python
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python executable not found. >> "%LOG%"
    echo [ERROR] Python executable not found.
    pause
    exit /b 1
)

:: パイプライン実行
set PYTHONPATH=%cd%
%PY_CMD% master_run.py >> "%LOG%" 2>&1

set EXIT_CODE=%errorlevel%
echo ==== END %date% %time% / EXITCODE=%EXIT_CODE% ==== >> "%LOG%"

if %EXIT_CODE% neq 0 (
    echo [FAILED] See reports\master_run.log for details.
) else (
    echo [SUCCESS] Pipeline completed. Check reports\ for results.
)

pause
