@echo off
setlocal
cd /d "%~dp0"

echo ==== Downloading 2024-2026 BOAT RACE Official Data ====
echo This process will take about 30-40 minutes depending on network speed.
echo (1 second sleep is included per file to prevent server overload)

:: Pythonの存在確認
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python Launcher (py) not found.
    pause
    exit /b 1
)

:: 2024年から現在までの全データをダウンロード
py src\data\download_official.py --start 20240101 --end 20261231 --types results entries

echo ==== Download Finished ====
echo Now extracting LZH files using 7-Zip...

:: 解凍処理
py src\data\extract_official.py

echo ==== Extraction Finished ====
echo Now parsing fixed-width TXT files to historical_races.csv...

:: パース＆統合処理
py src\data\parse_fixed_width.py

echo ==== All Data Collection and Parsing Completed ====
pause
