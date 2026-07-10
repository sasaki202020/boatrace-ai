@echo off
setlocal
cd /d %~dp0
echo Running Daily BoatRace-AI Pipeline...

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
    echo [FAILED] Python executable not found.
    pause
    exit /b 1
)

set PYTHONPATH=%cd%
%PY_CMD% src\orchestration\orchestrate_daily.py
if %ERRORLEVEL% NEQ 0 (
    echo [FAILED] Pipeline stopped due to errors.
    pause
    exit /b %ERRORLEVEL%
)
echo [SUCCESS] Daily Report and Archive are ready.
pause
