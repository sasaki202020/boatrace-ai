# Market Evaluation v1

この機能は研究専用です。既存のtree_15、prospective prediction、settlement、BUY/EV本番ロジック、Windows taskへ接続しません。

## 契約

- 3連単market baselineは、同一レースの120通りの正のfinite oddsから逆数を正規化する。
- 1着probabilityから3連単probabilityを推測しない。
- `DECISION_TIME`以前のsnapshotだけがprediction input候補である。
- `CLOSING_TIME`と`FINAL_PAYOUT`は結果・変動評価専用で、prediction inputには使えない。
- snapshot storeとexperiment registryは独立SQLiteでappend-only、hash chain付きである。
- payout unitが検証されない場合、EV bandのROIは計算しない。
- 決済入力の`realizedReturn`は検証済み通貨額、`stake`は同じ通貨単位とし、`payoutUnitVerified=true`を必須にする。最大払戻1/3/5件除外もこの明示値だけで行う。
- 既存runtimeへ入力を暗黙に探索せず、scriptへ明示的にローカルJSONLを渡す。

## レポート生成

ネットワークを使わず、明示したローカルJSONLだけを読みます。

```powershell
py -3.13 scripts/build_market_evaluation_reports_v1.py `
  --snapshots path\to\odds_snapshots.jsonl `
  --ev-input path\to\settled_ev_rows.jsonl `
  --output-dir reports/market_evaluation_v1
```

入力を渡さない場合もblockedレポートを生成し、数値を推測しません。
