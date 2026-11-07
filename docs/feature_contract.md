# Feature Contract (特徴量生成契約)

## 1. 入力保証
- `processed/*.csv` は Gate 2 のバリデーションを通過していること。
- `race_id` ごとに必ず 6 艇揃っていること（相対特徴量計算のため）。

## 2. 出力制約
- 出力される特徴量セットには `win_label` や `finish_position` を**含めてはならない**（リーク防止）。
- `train_features.csv` と `today_features.csv` は**全く同じ列構成**であること。
- 欠損値は `0.0` または `mean` 等で埋められていること（モデル投入可能状態）。

## 3. Availability 管理
- `pre_race` 特徴量のみのモデルと、`just_before`（展示含む）を統合したモデルを切り替えられるよう、生成時に区分けを行う。
