# MiroFish 再現用：完全設計図 (BluePrint)

MiroFish の構造を参考に、競艇予想システムを再構築するための推奨アーキテクチャです。

## 1. フォルダ構造 (Directory Structure)
```text
mirofish-reborn/
├── data/               # 生データ、中間データ、モデル
├── config/             # URL、エイリアス、モデルパラメータ
├── src/
│   ├── collect/        # 1. データ取得 (Scraper / API Client)
│   ├── process/        # 2. 特徴量生成 (Feature Engineering)
│   ├── model/          # 3. 予測モデル (Inference)
│   └── strategy/       # 4. 賭け判断 (Betting Logic)
├── tests/              # 各層の単体テスト
└── main.py             # 統合実行スクリプト
```

## 2. 開発の 4層構造 (The 4 Layers)

### Layer 1: データ取得 (Data Collection)
- **目的**: 開催情報、オッズ、選手情報を取得。
- **MiroFishの肝**: どのサイトから、どの頻度で取得しているか（スクレイピング耐性含む）。

### Layer 2: 特徴量生成 (Feature Generation)
- **目的**: 取得したデータを「予測に使える形」に加工。
- **MiroFishの肝**: 選手勝率だけでなく、場、風、展示タイム、独自の指数などの算出ロジック。

### Layer 3: 予測モデル (Prediction Model)
- **目的**: 1着確率、3連単確率などを算出。
- **MiroFishの肝**: アルゴリズムの種類（LR, XGBoost, LightGBM等）とその入力データの組み合わせ。

### Layer 4: 賭け判断 (Strategy/Betting)
- **目的**: 期待値に基づき、「買う・買わない」および「いくら買うか」を決定。
- **MiroFishの肝**: オッズの乖離検知と資金配分（ケリー基準等）。

## 3. 再現成功の鉄則
1. **「データ取得ボット」を最初に作る**: データがなければ何も始まりません。
2. **比較可能な状態を作る**: 本物の MiroFish の出力と、自分の再現コードの出力を比較し、不一致を潰していく。
3. **最初は Logistic Regression から**: ロジックが追いやすいモデルで全体を通してから、高度なモデルへ移行する。
