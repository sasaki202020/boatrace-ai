# Leakage Rules for Features (特徴量リーク防止ルール)

特徴量作成時、以下の「未来の情報」が混入することを厳格に禁止する。

## 1. 直接的な結果リーク
- `finish_position` (着順) そのもの。
- `win_label` (1着フラグ) そのもの。
- 払戻金 (Payouts)。

## 2. 間接的な結果リーク
- 決まり手 (Winning Technique)。
- レースタイム。
- 3着以内フラグなど。

## 3. 実運用時に未確定な情報
- 展示タイム以外の、レース中に確定する気象データ（突風など）。
- 審議結果。

## 監査チェック
- `build_features.py` で生成された `train_features.csv` の列名が、`feature_registry.json` の `blocked` リストに含まれていないか、プログラムで自動チェックする機能を `validators.py` に実装すること。
