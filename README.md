# 競艇AI予想システム

競艇(ボートレース)の1着予測AIシステム。XGBoost + LightGBM + RandomForest の
スタッキングアンサンブルで各艇の勝率を予測し、回収率バックテストと当日予想を行う。

## セットアップ

```bash
pip install -r requirements.txt
python test_system.py        # 単体テスト
python -m pytest tests/ -v   # 新規モジュールのテスト
```

## 使い方

### 1. 動作確認(ダミーデータ)

```bash
python main.py --quick           # 200レースで高速確認
python main.py --source dummy    # 2000レースでフル実行
```

### 2. 実データの収集と学習

```bash
# 公式サイトから期間指定で取得 → SQLiteへ保存 → 学習・バックテスト
python main.py --source real --fetch --start 2026-05-01 --end 2026-05-31 --places 桐生 住之江

# 取得済みDBで再学習のみ
python main.py --source real
```

- 取得データは `data/boat_race.db`(SQLite: races / entries / results / odds)に保存
- 取得済みHTMLは `data/raw/` にキャッシュされ、再取得しない
- リクエスト間隔は最低2秒、robots.txt 遵守、指数バックオフでリトライ

### 3. 当日予想

```bash
python predict_today.py --date 2026-06-12 --place 桐生            # 全12R
python predict_today.py --date 2026-06-12 --place 桐生 --race 1   # 1Rのみ
```

各艇の勝率・予想着順・単勝期待値をコンソール表示し、
`data/predictions/` にCSV保存する。

### 4. 日次運用(標準入口)

毎日使う場合は `daily_run.py` を入口にする。朝は全開催場の予想を保存し、
夜は保存済み予想を壊さずに結果だけを取得して答え合わせする。

```bash
python daily_run.py --date 2026-06-13 --phase morning
python daily_run.py --date 2026-06-13 --phase night --bankroll-yen 10000
python daily_run.py --date 2026-06-13 --phase full --bankroll-yen 10000
```

個別に実行する場合:

```bash
python daily_predict.py --date 2026-06-13
python daily_settle.py --date 2026-06-13 --bankroll-yen 10000
```

- 出力先は `output/daily/YYYY-MM-DD/`
- 朝: `predictions.csv`, `predictions.json`, `morning_run.json`, `coverage.json`
- 夜: `results.csv`, `settlement.csv`, `daily_report.json`, `daily_report.md`
- 累積: `output/daily/rolling_summary.csv`
- 既存の `predictions.csv` は原則上書きしない。再生成が必要な場合だけ
  `--overwrite` を指定する。
- `daily_settle.py` は保存済み予想だけを読み、予想を再生成しない。
- `value_filter` は収益化検証用の shadow 評価であり、本番推奨買い目ではない。
- 未取得・未公開・中止・パース不能は推測補完せず、`coverage.json` と
  `daily_report.json/md` に理由付きで記録する。

### 5. 収益性分析と資金管理ゲート

勝率だけでは資産は増えないため、日次決済データから ROI・買い条件・資金管理を
shadow 分析する。

夜答え合わせ後に自動実行される。単独で再集計する場合:

```bash
python analyze_profitability.py --bankroll-yen 10000
```

出力:

- `output/analysis/profitability/profitability_summary.json`
- `output/analysis/profitability/profitability_report.md`
- `output/analysis/profitability/profitability_slices.csv`

本番購入は自動では許可しない。最低条件は「十分な買い目数」「複数日での安定」
「ROI 105%以上」をすべて満たすこと。条件未達なら `paper_trade_only` とし、
記録だけを継続する。

## 設計方針(リーク防止)

- 選手の過去成績系特徴量はすべて `groupby(racer_id) → shift(1)` を通し、
  「そのレース開始前に判明している情報」のみを使用
- 実測ST(`start_time`)は結果扱いとし、過去レースの shift(1) 集計でのみ利用
- 展示タイム(`exhibition_time`)はレース前に判明するため直接使用
- 学習/テスト分割は時系列順のみ(レース単位で分割、ランダム分割禁止)
- 欠損補完の統計量(中央値)は学習データのみから算出

## モジュール構成

```
boatrace-ai/  (リポジトリルート)
├── main.py                  # 一括実行(学習→予測→ROI検証→保存)
├── predict_today.py         # 当日予想CLI
├── test_system.py           # システム単体テスト
├── config/config.yaml       # 設定
├── tests/                   # fetcher / database のpytestテスト
└── src/
    ├── data_processing/
    │   ├── dummy_generator.py    # ダミーデータ生成(オッズ付き)
    │   ├── preprocessor.py       # クリーニング・欠損値・エンコード
    │   ├── real_data_fetcher.py  # boatrace.jp スクレイパー(マナー遵守)
    │   └── database.py           # SQLite 保存・読込
    ├── features/feature_engineer.py   # 時系列・レース内相対・相互作用特徴量
    ├── models/prediction_system.py    # スタッキングアンサンブル
    └── evaluation/backtester.py       # 回収率バックテスト(3戦略)
```

## 既知の制約・注意事項

1. **パーサーの実地検証が必要**: `real_data_fetcher.py` の HTMLパーサーは
   公式サイトの構造を想定して実装し、構造を模したフィクスチャでテスト済みだが、
   開発環境から boatrace.jp へ接続できなかったため**実ページでの検証は未実施**。
   初回実行時にパースエラーが出た場合は `parse_*` メソッドのセレクタを
   実際のHTML(`data/raw/` にキャッシュされる)に合わせて調整すること。
2. **代替データソース**: スクレイピングが困難な場合、公式のダウンロードデータ
   (番組表・競走成績のテキストファイル、`http://www1.mbrace.or.jp/od2/` 配下)が
   一括取得に向く。展示タイム・直前オッズは含まれないが、学習データの
   大量収集にはこちらが効率的(LZH解凍と固定長テキストのパーサーが別途必要)。
3. **回収率について**: 単勝市場はオッズに情報が織り込まれているため、
   実データでの回収率100%超えは期待しない。まずはランダム基準(16.7%)を
   有意に上回る1着的中率を目標とする。

## デプロイ(GitHub Actions による日次自動運用)

`.github/workflows/boatrace-daily.yml` で毎朝の予想を自動化している。

### 動作

| トリガー | 内容 |
|---|---|
| push(コード変更時) | テスト + ダミーデータでのスモーク実行 |
| スケジュール(毎朝 JST 7:00) | 前日結果の取得 → 再学習 → 当日予想 → CSVコミット |
| 手動実行(workflow_dispatch) | 日付・レース場・取得日数を指定して同上 |

- DB(`data/boat_race.db`)と学習済みモデルはリポジトリに含めず、
  **Actions キャッシュ**で日次に引き継ぐ。キャッシュが消えた場合は
  自動で過去14日分をブートストラップ取得する。
- 予想CSVはリポジトリ直下の `predictions/` にコミットされ、
  実行ごとのアーティファクトとしてもダウンロードできる。

### 注意

- **スケジュール実行はデフォルトブランチ(main)のワークフローのみ有効。**
  開発ブランチにある間は手動実行(Actions タブ → 競艇AI 日次予想 →
  Run workflow でブランチを選択)で動かす。
- 対象レース場の既定はワークフロー側の設定に従う。ローカルの日次運用では
  `daily_predict.py` が当日開催場を検出する。
- スクレイピングは2秒間隔・キャッシュ付きで公式サイトに配慮しているが、
  対象場を増やすと実行時間が伸びる(1場・1日あたり数分が目安)。
