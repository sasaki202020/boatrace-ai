@echo off
setlocal
cd /d "%~dp0.."
if not defined BOATRACE_MVP_ROOT exit /b 2
"C:\Users\goo10\AppData\Local\Programs\Python\Python313\python.exe" scripts\run_feature_forward_collector_v1.py --approval config\feature_forward_v1\source_approval.json --inbox "%BOATRACE_MVP_ROOT%\data\research\feature_forward_v1\inbox" --store "%BOATRACE_MVP_ROOT%\data\research\feature_forward_v1\store" --status "%BOATRACE_MVP_ROOT%\reports\feature_forward_v1\latest_status.json"
exit /b %ERRORLEVEL%
