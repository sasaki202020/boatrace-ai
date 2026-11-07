# 移行計画

目的は、`boatrace-ai-mvp` を壊さずに、重複名を整理して運用の見通しをよくすることです。今回は実削除しません。

## KEEP

- `boatrace-ai-mvp/` 本体
- `btlink -> boatrace-ai-mvp` の symlink
- `data/odds/today_trifecta_odds.csv`
- `data/odds/today_trifecta_odds_failures.csv`
- `data/odds/today_trifecta_odds_race_status.csv`
- `reports/calibration/approx_prob_calibration_latest.md`
- `reports/calibration/approx_prob_calibration_latest.json`
- `reports/calibration/approx_prob_calibration_table_latest.csv`
- `reports/calibration/approx_prob_calibrated_rows_latest.csv`
- `reports/v2/` の集約済み出力
- `data/features/today_features.csv`
- `data/tmp/ev_20260311_top40/ev_table.csv`
- `data/tmp/ev_20260311_60/ev_table.csv`
- `data/strategy_outputs/ev_build_test/ev_table.csv`

## KEEP_FOR_COMPAT

- `data/odds/20260404/race_targets.csv`
- `data/odds/20260405/race_targets.csv`
- `data/odds/20260406/race_targets.csv`
- `data/odds/20260407/race_targets.csv`
- `data/odds/20260408/race_targets.csv`
- `data/odds/20260411/race_targets.csv`
- `data/tmp/20260311_eval/today_features.backup.csv`
- `reports/calibration/approx_prob_calibration_20260411_20260411.*` の日付付き一式
- `data/strategy_outputs/ev_build_test/ev_table_20250310_20260322.csv`
- `data/tmp/ev_20260311_top40/ev_table_20260311_20260311.csv`
- `data/tmp/ev_20260311_60/ev_table_20260311_20260311.csv`

## FUTURE_DELETE_AFTER_MIGRATION

- `reports/v2_shadow/shadow_v2_20260404.json`
- `reports/v2_shadow/shadow_v2_20260406.json`
- `reports/v2_shadow/shadow_v2_20260407.json`
- `reports/v2_shadow/shadow_v2_20260411.json`
- `data/features/today_features.backup_final_eval.csv.bak`
- `data/features/today_features.backup_final_eval2.csv.bak`
- `data/features/today_features.backup_pair_eval.csv.bak`
- `data/odds/20260403/failed_races.csv`
- `data/odds/20260404/failed_races.csv`
- `data/odds/20260406/failed_races.csv`
- `data/odds/20260407/failed_races.csv`
- `data/odds/20260408/failed_races.csv`
- `data/odds/20260411/failed_races.csv`

## 先に参照先修正が必要なもの

- `race_targets.csv` を直接読む箇所
  - 旧互換名として扱うため、今後は `race_status.csv` を主参照に寄せる
- `reports/v2_shadow/*.json` を直接読む箇所
  - 正本集約先を `reports/v2` に寄せる
- `approx_prob_calibration_latest.*` を読む箇所
  - `latest` を正本として維持する前提で固定
- `data/features/today_features.backup*.bak` を使っている復元系処理
  - `data/tmp/20260311_eval/today_features.backup.csv` へ寄せる
- `ev_table_YYYYMMDD_YYYYMMDD.csv` を正本として仮定している箇所
  - `ev_table.csv` を各ディレクトリ内の正本に統一する

## 削除前チェック項目

- 参照先が `today_*` / `latest` / `reports/v2` / `ev_table.csv` に寄っていること
- `race_targets.csv` を読んでいるスクリプトが互換扱いになっていること
- `reports/v2_shadow/*.json` への直接参照が残っていないこと
- `data/features/today_features.backup*.bak` が復元専用になっていること
- `ev_table.csv` が「各ディレクトリ内の正本」であり、グローバル単一正本ではないことを関係者が理解していること
- 同一内容の通常ファイルだけを対象にしていること
- `raw TXT`、`DB`、`data/tmp` の元データを触っていないこと
- 実削除前に、影響スクリプトと参照箇所の一覧を再確認すること

## 明記事項

- `race_targets.csv` は旧互換名として扱う
- `ev_table.csv` は各ディレクトリ内の正本であり、グローバルな単一正本ではない
- `reports/v2` を正本集約先とする
- `reports/v2_shadow/*.json` は移行後削除候補とする

