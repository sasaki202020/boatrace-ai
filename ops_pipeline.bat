@echo off
setlocal
cd /d %~dp0

if "%1"=="predict" (
  py -m src.jobs.daily_pipeline --mode predict
  goto :eof
)

if "%1"=="pre-race" (
  py -m src.pipeline.run_daily_pre_race
  goto :eof
)

if "%1"=="odds-refresh" (
  py -m src.pipeline.run_daily_odds_refresh --phase morning
  goto :eof
)

if "%1"=="odds-refresh-late" (
  py -m src.pipeline.run_daily_odds_refresh_late
  goto :eof
)

if "%1"=="post-race" (
  py -m src.pipeline.run_daily_post_race
  goto :eof
)

if "%1"=="backtest" (
  py -m src.jobs.daily_pipeline --mode backtest
  goto :eof
)

if "%1"=="guard" (
  py -m src.jobs.daily_pipeline --mode guard
  goto :eof
)

if "%1"=="full" (
  py -m src.jobs.daily_pipeline --mode full
  goto :eof
)

if "%1"=="weekly" (
  py -m src.jobs.daily_pipeline --mode full --conditional-retrain
  goto :eof
)

if "%1"=="weekly-promote" (
  py -m src.jobs.daily_pipeline --mode full --conditional-retrain --promote-candidate --promote-current-snapshot
  goto :eof
)

py -m src.jobs.daily_pipeline --mode guard

endlocal
