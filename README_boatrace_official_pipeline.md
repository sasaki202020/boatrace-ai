# BOAT RACE official pipeline

## 1. 依存関係

```bash
pip install requests beautifulsoup4
```

## 2. 実行

```bash
python boatrace_official_pipeline.py --date 2026-04-19 --out-dir data --top-n 5
```

JST 今日で走らせるなら:

```bash
python boatrace_official_pipeline.py
```

特定場だけ強制するなら:

```bash
python boatrace_official_pipeline.py --date 2026-04-19 --jcds 02,05,20,21
```

## 3. 出力

- `data/odds/YYYYMMDD/all_trifecta_odds.csv`
- `data/predictions/YYYYMMDD/all_race_predictions.csv`
- `data/predictions/YYYYMMDD/top_ev_races.csv`
- `data/predictions/YYYYMMDD/race_bundles.json`
- `data/ui/YYYYMMDD/raceyosou_XX.json`
- `data/predictions/YYYYMMDD/summary.json`

## 4. `RaceYosouView.jsx` への接続例

```jsx
import RaceYosouView from "./RaceYosouView";
import todaData from "./data/ui/20260419/raceyosou_02.json";

export default function App() {
  return <RaceYosouView {...todaData} />;
}
```

## 5. このスクリプトが見ている公式URL

- 当日一覧: `https://www.boatrace.jp/owpc/pc/race/index`
- 出走表: `https://www.boatrace.jp/owpc/pc/race/racelist?hd=YYYYMMDD&jcd=XX&rno=Y`
- 3連単オッズ: `https://www.boatrace.jp/owpc/pc/race/odds3t?hd=YYYYMMDD&jcd=XX&rno=Y`
- 直前情報: `https://www.boatrace.jp/owpc/pc/race/beforeinfo?hd=YYYYMMDD&jcd=XX&rno=Y`
- コンピューター予想: `https://www.boatrace.jp/owpc/pc/race/pcexpect?hd=YYYYMMDD&jcd=XX&rno=Y`

## 6. 正直な注意点

- 公式HTMLの構造変更には弱い。壊れたらパーサー修正が必要。
- EV はヒューリスティック。利益保証ではない。
- 過去日付の「開催場自動発見」は弱いので、その場合は `--jcds` を使う方が安全。
