# BOAT RACE official pipeline

This repo includes a standalone official pipeline at:

- `boatrace_official_pipeline.py`
- `src/pipeline/boatrace_official_pipeline.py`

It fetches:

- `racelist`
- `odds3t`
- `beforeinfo`
- `pcexpect`

for all venues on a target date, then writes:

- `data/odds/YYYYMMDD/all_trifecta_odds.csv`
- `data/predictions/YYYYMMDD/all_race_predictions.csv`
- `data/predictions/YYYYMMDD/top_ev_races.csv`
- `data/predictions/YYYYMMDD/race_bundles.json`
- `data/ui/YYYYMMDD/raceyosou_XX.json`
- `data/predictions/YYYYMMDD/summary.json`

Run:

```bash
python boatrace_official_pipeline.py --date 2026-04-19 --out-dir data --top-n 5
```

Notes:

- The pipeline now writes `race_id` using the repo-wide canonical helper.
- The UI JSON is shaped to be consumed by `RaceYosouView`-style components.
- If the official HTML changes, parser updates may be needed.
