# Recommended Path: 失敗しないための推奨ルート

この順番でプロジェクトを進めることで、Antigravity（AI）との摩擦を最小限にし、確実に完遂できます。

## 1. 準備フェーズ (Setup)
1. 実データを `data/raw/official/` に置く。
2. `00_MASTER_INDEX.md` を読んで全体を俯瞰する。

## 2. 適合フェーズ (Data Fitting)
1. `03_first_run/` のコマンドでエイリアスを監査。
2. `validation_summary.json` が `"PASS"` になるまで、エイリアス修正を繰り返す。
3. **重要**: ここが通るまで特徴量生成には進まない。

## 3. 安定化フェーズ (Stabilization)
1. `run_today.bat` を回してみる。
2. 止まったら `stabilization_log.md` に記録し、修正。これを3回繰り返すとシステムが筋肉質になります。

## 4. 改善・高度化フェーズ (Benchmark & Advance)
1. 公式データが1ヶ月分貯まったら、`Benchmark Pack` で再学習を試す。
2. 余裕が出たら `MiroFish` のロジックを1層ずつ（まずはデータ収集から）追加する。

---
**一言**: 重ねて言いますが、再学習は「毎日しない」でください。運用の安定が勝利への最短距離です。
