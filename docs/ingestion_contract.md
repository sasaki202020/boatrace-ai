# Ingestion Contract (取り込み規約)

Ingestion 処理が守るべき契約と、失敗（例外）の定義。

## 1. 必須データ (Required)
以下のデータが欠落している場合、その艇/レースの取り込みは失敗(Fail)とする。
- `race_id`, `date`, `venue`, `race_no`, `lane`
- `racer_id`, `racer_class`
- `national_win_rate` (能力指数の基礎)

## 2. 推論時の除外条件
`today_races.csv` 生成時、以下のケースは「予測対象外」としてフラグを立てるか除外する。
- 欠場 (DNS) が発生している
- 5艇立て以下のレース (初期MVP制約)
- 展示不参加

## 3. バリデーションレポート
Ingestion 完了ごとに `validation_summary.json` を出力すること。
- 読み込み成功レース数
- スキップされたレース数 (理由別)
- 欠損値補完が行われたカラムと件数
