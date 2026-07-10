# Feature Missing Policy

Day 4: current active sets (`win_baseline_core`, `win_baseline_core_relative`, `win_baseline_extended`) で使う特徴量の欠損処理方針。

## Principles

- key columns are never imputed silently.
- pre-race history features may be imputed, but only from stable historical aggregates.
- just-before-race signals should be treated as unavailable rather than guessed when the upstream fetch fails.
- training and today inference should follow the same fill semantics where possible.
- if a feature is missing in a way that changes race ordering or candidate ranking, keep a missing flag or exclude it from the active set.

## Canonical rules by column class

| column class | examples | policy | fallback / notes |
| --- | --- | --- | --- |
| key columns | `race_id`, `date`, `jcd`, `race_number`, `boat_no` | do not impute | drop the record if any are missing or invalid |
| just-before-race signals | `exhibition_time`, `exhibition_time_rank`, `start_timing` | preserve missingness as operational risk | use only when upstream fetch succeeded; if the source is missing, the race is not promoted to BUY by execution rules |
| history-derived ability features | `avg_st`, `national_2ren_rate`, `local_2ren_rate`, `recent*`, `lane_win_rate_prior`, `win_rate_venue`, `course_win_rate`, `course1_win_rate` | stable historical imputation only | prefer racer-level median, then boat-level median, then global median; if still missing, use 0.0 for trainable features that must remain numeric |
| functionally derived flags | `low_motor_flag`, `low_boat_flag`, `inside_course_flag`, `jcd_low_motor_flag`, `jcd_low_boat_flag` | recompute from source columns | do not impute directly |
| relative features | `*_rank_in_race`, `*_diff_from_race_mean`, `*_z_in_race` | recompute at race-card assembly time | if race-card is incomplete, keep them missing rather than guessed |

## Trainable dataset policy

The current `trainable_win_training_data.csv` build uses the following stable imputation order for the active 1着 sets:

1. `racer_id` median
2. `boat_no` median
3. global median
4. constant `0.0` if the column still has no valid median

This is applied to:

- `national_2ren_rate`
- `local_2ren_rate`
- `avg_st`

Current observed missing rates after cleaning/building:

- `national_2ren_rate`: `0.0`
- `local_2ren_rate`: `0.0`
- `avg_st`: `0.0`

## Operational decisions

- `exhibition_time` may be used in the 1着 active set only when it is available from the official day-of race card.
- If official day-of entry or odds ingest fails, the race stays in the dataset for training only when it can be filled from the trainable pipeline; for live execution, missing execution-side inputs are a skip condition.
- `racer_id` is never treated as a model feature in the current active sets.

## Current active-set summary

- `win_baseline_core`: no imputed columns beyond the upstream build contract.
- `win_baseline_core_relative`: depends on the same trainable imputation for `avg_st`, `national_2ren_rate`, and `local_2ren_rate`.
- `win_baseline_extended`: challenger only; the sparse history-derived columns are allowed to be imputed in trainable data but remain excluded from the official baseline.

## Review notes

- If a future feature requires `ffill`, it must be reviewed separately before promotion to core.
- The most sensitive exception is `course1_win_rate`; keep it in review until row-order and fill scope are fully documented.
