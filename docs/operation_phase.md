# Operation Phase

This project is in live shadow operation, not production tuning.

## Rules

- Save `frozen_bets` every day and keep it as the production prediction record.
- Run `result`, `settle`, and `daily_report` every day.
- BUY decisions may be produced, but they remain shadow records until an explicit later approval.
- Do not change BUY thresholds or `baseline_score_model` weights yet.
- Keep backfill and live separate.
- Do not use result data to regenerate predictions.
- Do not fill missing data with samples.

## Tuning gate

- Live tuning remains blocked until `liveSettledBetCount >= 100`.
- Live tuning also requires `liveSettlementCoverage >= 0.5`.
- `canTuneWithBackfill=true` is not enough on its own.
- Do not promote BUY rule changes without live confirmation.

## Daily operation

- Morning: run the daily freeze flow and check health, then let the scheduler keep doing it automatically.
- Evening: run the result / settle flow and rebuild the daily report, then let the scheduler keep doing it automatically.
- If K files arrive, import them first, then refresh backfill readiness.
- Keep live shadow operation running every day so `liveSettledBetCount` accumulates over time.
- Do not change BUY thresholds until `tuning_gate` passes and live confirmation is sufficient.

## Monitoring

- Check `health_check` daily.
- Review `live_operation_summary` over a rolling window.
- Use `tuning_gate` as the final "can we start tuning" decision.
