# 7日間運用プラン

明日から7日間、朝と夜に同じ手順で回して、`improvement_report` の上位だけを小さく触るためのメモです。  
大改造はせず、「基盤が安定して回るか」「三連単と二連単のどちらが安定するか」「どの gate が本当の詰まりか」を見極める目的で使います。

## 朝にやること

### 実行
```powershell
py -m src.pipeline.run_daily_pre_race --date YYYY-MM-DD
```

### 確認
- `reports/daily/YYYY-MM-DD/pre_race_run.json`
- `reports/daily/YYYY-MM-DD/skip_decisions.csv`
- `reports/daily/YYYY-MM-DD/skip_decisions_exacta_mode.csv`
- `reports/daily/YYYY-MM-DD/today_trifecta_odds.csv`
- `reports/daily/YYYY-MM-DD/fetch_report.json`

### 見るポイント
- `status = ok`
- `failure_step = null`
- `success_races > 0`
- `skip_decisions.csv` の行数 > 0
- `today_trifecta_odds.csv` の `odds` 列に数値がある

### ブラウザ確認
- 当日予想一覧
- 場別表示
- 実行ログ
- 三連単 / 二連単の状態

## 夜にやること

### 実行
```powershell
py -m src.pipeline.run_daily_post_race --date YYYY-MM-DD
```

### 確認
- `reports/daily/YYYY-MM-DD/post_race_run.json`
- `reports/daily/YYYY-MM-DD/daily_summary.json`
- `reports/daily/YYYY-MM-DD/improvement_report.json`
- `reports/daily/rolling_summary.json`

### 見るポイント
- `status = ok`
- `BUY件数`
- `hit件数`
- `hit_rate`
- `ROI`
- `exact`
- `top5`
- `top10`
- `avg_rank`
- `gate別落選件数`
- `max_buy_count に押し出された候補`
- `改善候補トップ3`

### 三連単と二連単を比較
- `hit_rate`
- `ROI`
- `BUY件数`
- `安定性`

## 毎日1行で記録する

```powershell
py -m src.pipeline.export_daily_review_row --date YYYY-MM-DD
```

改善を入れた日は、変更内容も一緒に残します。

```powershell
py -m src.pipeline.export_daily_review_row --date YYYY-MM-DD --touched "buy_min_approx_prob 0.012 -> 0.011" --note "三連単の詰まりだけ確認"
```

出力:
- `reports/daily/seven_day_review_log.csv`

## 改善ルール

- 1日1改善まで
- 同時に複数 gate を触らない
- before / after を比較できる変更だけ入れる
- 3日連続で悪化した変更は戻し候補

## 7日後に見るもの

- 三連単の `ROI / hit_rate / exact / top5 / avg_rank`
- 二連単の `ROI / hit_rate / BUY件数`
- 共通のボトルネック
  - `hard skip`
  - `odds cap`
  - `risk_flag`
  - `payout_outlier`
  - `buy_eligible`
  - `final_score`
  - `max_buy_count`

## 判断のしかた

- 三連単を本線にするか
- 二連単を強めるか
- 採用順を次に触るべきか
- `max_buy_count` を見直すべきか
- 上流精度改善に戻るべきか

この7日間は「大きくいじる」より、「毎日回して、数字がどう動くかを落ち着いて観察する」ことを優先します。
