# Daily Runbook: 日次運用手順書

毎日、同じ手順で `pre-race -> nightly -> 改善候補確認` まで回すための運用手順です。  
個別スクリプトを手で叩くのではなく、まずは日次 CLI を基準にします。

## 1. 毎朝やること（pre-race）

### 実行コマンド
```powershell
py -m src.pipeline.run_daily_pre_race
```

日付指定で再実行したい場合:
```powershell
py -m src.pipeline.run_daily_pre_race --date 2026-04-05
```

### この1本で実行されるもの
1. 公式エントリー取得
2. fixed width 解析
3. 特徴量再生成
4. モデル再学習
5. 確率校正更新
6. 今日分の勝率予測
7. 三連単候補再生成
8. 当日オッズ取得
9. `skip_decisions.csv` 再生成
10. gate health 診断

### 主な入力
- [historical_races.csv](../data/processed/historical_races.csv)
- [today_races.csv](../data/processed/today_races.csv)

### 主な出力
- [today_features.csv](../data/features/today_features.csv)
- [today_win_proba.csv](../data/model_outputs/today_win_proba.csv)
- [trifecta_candidates.csv](../data/strategy_outputs/trifecta_candidates.csv)
- [today_trifecta_odds.csv](../data/odds/today_trifecta_odds.csv)
- [skip_decisions.csv](../data/strategy_outputs/skip_decisions.csv)
- [pre_race_run.json](../reports/daily/2026-04-05/pre_race_run.json)

### オッズ取得の診断
- 日別保存先: `data/odds/YYYYMMDD/`
- 成功率レポート: `fetch_report.json`
- 対象レース一覧: `race_targets.csv`
- 欠損/失敗レース: `failed_races.csv`

## 2. 毎晩やること（nightly）

### 実行コマンド
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_nightly_pipeline.ps1
```

日付指定:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_nightly_pipeline.ps1 -Date 2026-04-05
```

### この1本で実行されるもの
1. late odds refresh
2. 結果データ取得
3. fixed width 解析
4. 予測と実結果の照合
5. 三連単候補順位評価
6. BUY/SKIP の日次評価
7. 改善候補レポート生成
8. 直近7日/30日/全期間の累積比較更新

late refresh を飛ばしたい場合:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_nightly_pipeline.ps1 -Date 2026-04-05 -SkipLateRefresh
```

### 主な出力
- [daily_evaluation.json](../reports/daily/2026-04-05/daily_evaluation.json)
- [daily_summary.json](../reports/daily/2026-04-05/daily_summary.json)
- [improvement_report.json](../reports/daily/2026-04-05/improvement_report.json)
- [daily_evaluation.md](../reports/daily/2026-04-05/daily_evaluation.md)
- [rolling_summary.json](../reports/daily/rolling_summary.json)
- [daily_summary_history.csv](../reports/daily/daily_summary_history.csv)
- [post_race_run.json](../reports/daily/2026-04-05/post_race_run.json)

## 3. ログの見方

PowerShell ラッパーを使う場合は、日付ごとのログが残ります。

- 朝: [logs/pre_race](../logs/pre_race)
- 夜: [logs/nightly](../logs/nightly)
- post-race 単体ログ: [logs/post_race](../logs/post_race)

各ログには以下が残ります。
- step名
- 開始時刻
- 終了時刻
- 実行結果
- 失敗時の例外

## 4. 自動実行

### PowerShell から単体実行
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_daily_pre_race.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_nightly_pipeline.ps1
```

### タスク登録
```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_daily_tasks.ps1
```

登録内容:
- `boatrace_pre_race_daily`: 毎日 07:00
- `boatrace_post_race_daily`: 毎日 23:30
  - 実体は `run_nightly_pipeline.ps1`
  - late refresh と post-race を順に実行

## 5. 失敗時の扱い

- 学習失敗: 旧モデルを backup から復元
- 校正失敗: 旧 calibrator を復元して継続
- オッズ取得失敗: 失敗レースを記録し、暫定オッズ前提で後続継続
- 結果取得失敗: 既存の historical があれば post-race は継続

## 6. 毎日見るべきもの

1. `daily_summary.json`
2. `improvement_report.json`
3. `rolling_summary.json`

この3つで
- 今日どうだったか
- 直近で悪化していないか
- 明日どこを直すべきか
が分かります。
