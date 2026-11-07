# データ取得ガイド: BOAT RACE 公式データの自動取得

## ⚡ 最短スタート（2コマンド）

### 1. 競走成績を1年分取得
```powershell
py src/data_fetch/fetch_official.py --type results --start 2025-01-01 --end 2025-12-31
```
→ `data/raw/official/results/` に TXT ファイルが保存されます。

### 2. 番組表を当日分取得
```powershell
py src/data_fetch/fetch_official.py --type entries --date 2026-03-12
```
→ `data/raw/official/entries/` に TXT ファイルが保存されます。

---

## 🗄️ データの保存先
```
data/raw/official/
├── results/     ← 競走成績（1年分以上推奨）
├── entries/     ← 番組表（当日 or 翌日分）
├── motor/       ← モーター成績（出走表内に含有）
├── pre_race/    ← 展示タイム（当日取得）
└── odds/        ← オッズ（当日取得）
```

## 📐 データ取得の段階

| 段階 | 取得するもの | Gate |
|:---|:---|:---|
| **最低限** | 競走成績1年 + 番組表1日 | Gate2 → Gate4 まで |
| **実用化** | + モーター成績 + 展示タイム | Gate4 精度 2倍向上 |
| **最強化** | + オッズ（3連単） | EV計算が実弾化 |

## ⚠️ 注意事項
- サーバー負荷を考慮し、`--delay 1.0`（デフォルト1秒）を守ること。
- LZH ファイルの中身は固定長テキスト。CSV への変換は `build_processed.py` が行う。
- 開催のない日は 404 が返るため、スキップされます（正常動作）。
