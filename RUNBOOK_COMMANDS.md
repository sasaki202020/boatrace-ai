# 競艇AI 実行コマンド運用表

この文書は `OPERATIONS.md` の補助です。  
目的は、朝・昼・夜に何を打つかを固定し、判断をブレさせないことです。

---

## 前提

- プロジェクトルート: このリポジトリのルート（`git rev-parse --show-toplevel` で確認）
- Windows PowerShell では `py` を優先
- 日付引数は `YYYY-MM-DD` を前提

---

# 1. 朝の実行

## 目的
- 当日データを整える
- BUY / SKIP 判定を出す
- 明らかな異常を検知する

## 実行順

### 1-1. pre-race 実行
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py -m src.pipeline.run_daily_pre_race --date 2026-04-11
```

### 1-2. 朝の一括運用
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_daily_operations.py --date 2026-04-11 --phase morning
```

### 1-3. ダッシュボード確認
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
streamlit run app/dashboard.py
```

確認対象例:
- `reports/daily/`
- `reports/diagnostics/`
- `reports/monitoring/`

---

## 朝の確認項目
- 対象レース数が想定内か
- BUY件数が 0 ではないか
- 出力ファイルが生成されているか
- `approx_prob` が空でないか
- 特定場だけ欠けていないか

## 朝の停止条件
以下のどれかが起きたら、その日の改善判断は止める。

- BUY件数が 0
- 主要出力ファイル未生成
- 特定場の出力欠損が多い
- 前日比で対象レース数が不自然に少ない

## 朝の結論テンプレ
- pre-race: `成功 / 失敗`
- BUY件数:
- 入力欠損: `なし / 要確認`
- 朝時点の判定: `続行 / 要調査`

---

# 2. 昼〜夕方の実行

## 目的
- odds供給の品質監視
- モデルではなく供給が悪い日を切り分ける

## 実行候補

### 2-1. odds refresh 実行
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py -m src.pipeline.run_daily_odds_refresh --date 2026-04-11
```

### 2-2. late odds refresh 実行
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py -m src.pipeline.run_daily_odds_refresh_late --date 2026-04-11 --wait-minutes 30
```

### 2-3. 昼だけ回すなら
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_daily_operations.py --date 2026-04-11 --phase late --wait-minutes 30
```

---

## 昼の確認項目
- `real_odds_available`
- `pending_unpublished`
- `real_odds_missing_fetch`
- 場別の偏り

## 昼の停止条件
- `pending_unpublished` が多い
- `real_odds_missing_fetch` が急増
- `real_odds_available` が極端に低い

この場合、その日のモデル評価は参考値扱いに落とす。

## 昼の結論テンプレ
- odds供給: `正常 / 要注意 / 異常`
- `real_odds_available`:
- `pending_unpublished`:
- `real_odds_missing_fetch`:
- 判断: `夜に評価可能 / 保留寄り`

---

# 3. 夜の実行

## 目的
- 結果反映
- 日次メトリクス更新
- 比較対象日に入れるか判定

## 実行順

### 3-1. post-race 実行
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py -m src.pipeline.run_daily_post_race --date 2026-04-11
```

### 3-2. 夜だけ回すなら
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_daily_operations.py --date 2026-04-11 --phase night
```

### 3-3. シミュレータ実行
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_simulator_for_date.py --date 2026-04-11
```

### 3-4. raw vs calibrated 比較
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\compare_raw_vs_calibrated.py --date 2026-04-11 --input-path data/strategy_outputs/skip_decisions_with_calibrated_prob.csv --buy-min-ev 0.1 --buy-min-prob 0.0 --max-buy-count 3 --stake 100
```

---

## 夜の確認項目
- 結果TXTが揃っているか
- `raw_incomplete` の有無
- BUY件数
- 的中数
- 暫定ROI
- `real_odds_available`
- 比較出力の成否

## 夜の比較対象日判定

### 比較対象日に入れてよい
- 結果TXTが揃っている
- `raw_incomplete` ではない
- シミュレーションが最後まで通る
- 比較スクリプトが保留で終わっていない

### 比較対象日に入れてはいけない
- 結果TXT未完
- `raw_incomplete`
- 実オッズ不足
- 比較保留
- 中間出力欠損

---

# 4. 校正更新を回す日

## 目的
- 単日ではなく、複数日母集団で校正を評価する

## 実行候補

### 4-1. approx_prob 校正
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\calibrate_approx_prob.py --dates 2026-04-04,2026-04-05,2026-04-06
```

### 4-2. 校正済み確率を付与
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\attach_calibrated_prob.py --source-path data/strategy_outputs/skip_decisions.csv --calibrated-rows-path reports/calibration/approx_prob_calibrated_rows_latest.csv --output-path data/strategy_outputs/skip_decisions_with_calibrated_prob.csv
```

### 4-3. raw / calibrated 再判定
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_buy_judgement_calibrated.py --input-path data/strategy_outputs/skip_decisions_with_calibrated_prob.csv --output-path data/strategy_outputs/skip_decisions_rejudged_calibrated.csv --buy-min-ev 0.1 --buy-min-prob 0.0 --max-buy-count 3 --prob-source calibrated
```

### 4-4. raw vs calibrated 比較
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\compare_raw_vs_calibrated.py --date 2026-04-11 --input-path data/strategy_outputs/skip_decisions_with_calibrated_prob.csv --buy-min-ev 0.1 --buy-min-prob 0.0 --max-buy-count 3 --stake 100
```

> 単日でなく、結果が揃った複数日を対象に回すこと。

### 4-5. TARGET日だけのバッチ比較
```powershell
Set-Location -LiteralPath (git rev-parse --show-toplevel)
py .\scripts\run_batch_simulation.py --target-dates 2026-04-04,2026-04-05,2026-04-06
```

または `COMPARISON_TARGET_DAYS.md` の `TARGET` 行をそのまま読む。

---

# 5. 日次まとめテンプレ

毎日これだけ残せば十分。

```md
- 日付:
- pre-race: 成功 / 失敗
- odds供給: 正常 / 要注意 / 異常
- BUY件数:
- 的中数:
- 暫定ROI:
- 結果確定可否: 確定 / 保留
- 比較対象日: 入れる / 入れない
- 今日の最大ボトルネック:
- 明日やること1つ:
```

---

# 6. 絶対にやらない判断

- 単日だけで閾値を変える
- 未完データ日を比較対象に入れる
- 供給欠損をモデルのせいにする
- 校正差が小さいのに校正チューニングを深追いする

---

# 7. 次に固定すべきもの

この文書を使うなら、次に固定するのはこの3つ。

1. `post-race` の正確な実行ファイル名
2. `--date` の実引数仕様
3. 朝 / 昼 / 夜でタスクスケジューラに入れる時刻

---

# 8. 厳しめの結論

今の段階で必要なのは、新しい仕組みではない。  
毎日同じ順番で回して、保留日に結論を出さないことです。

実験内容の記録は [EXPERIMENT_LOG.md](./EXPERIMENT_LOG.md)、比較対象日の判定は [COMPARISON_TARGET_DAYS.md](./COMPARISON_TARGET_DAYS.md) に寄せる。

そこが固まれば、次にやる価値があるのは
- 比較対象日の自動管理
- 週次集計自動化
- 識別力改善の分析

の順です。
