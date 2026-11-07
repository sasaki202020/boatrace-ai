@echo off
setlocal
cd /d %~dp0
py -m src.eval.update_auto_filter_rules
endlocal
