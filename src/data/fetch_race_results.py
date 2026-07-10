from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.utils.race_id import canonical_race_id


ROOT = Path(__file__).resolve().parents[2]
TODAY_RACES_CSV = ROOT / "data" / "processed" / "today_races.csv"
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
OUT_REPORT = ROOT / "data" / "processed" / "fetch_results_report.json"

RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult"
ROBOTS_URL = "https://www.boatrace.jp/robots.txt"
DATE_RE = re.compile(r"(\d{8})")


def can_fetch(timeout: int = 8) -> bool:
    try:
        res = requests.get(ROBOTS_URL, timeout=timeout)
        if res.status_code != 200:
            return False
        txt = res.text
        if "Disallow: /owpc/pc/race/raceresult" in txt:
            return False
        return True
    except Exception:
        return False


def parse_finish_rows(html: str) -> list[tuple[int, int]]:
    # 1) try pandas tables
    try:
        tables = pd.read_html(html)
        for t in tables:
            cols = [str(c) for c in t.columns]
            c_finish = next((c for c in cols if "着" in c), None)
            c_lane = next((c for c in cols if "艇" in c or "枠" in c), None)
            if not c_finish or not c_lane:
                continue
            x = t[[c_finish, c_lane]].copy()
            x[c_finish] = pd.to_numeric(x[c_finish], errors="coerce")
            x[c_lane] = pd.to_numeric(x[c_lane], errors="coerce")
            x = x.dropna()
            x = x[(x[c_finish] >= 1) & (x[c_finish] <= 6) & (x[c_lane] >= 1) & (x[c_lane] <= 6)]
            rows = [(int(r[c_finish]), int(r[c_lane])) for _, r in x.iterrows()]
            if len(rows) >= 3:
                return rows
    except Exception:
        pass

    # 2) fallback regex line by line
    rows: list[tuple[int, int]] = []
    for line in html.splitlines():
        m = re.search(r"^\s*([1-6])\s+([1-6])\s+", line.strip())
        if m:
            rows.append((int(m.group(1)), int(m.group(2))))
    return rows


def race_targets(target_date: str) -> pd.DataFrame:
    if not TODAY_RACES_CSV.exists():
        raise FileNotFoundError(f"today races not found: {TODAY_RACES_CSV}")
    df = pd.read_csv(TODAY_RACES_CSV, low_memory=False)
    for c in ("race_id", "date", "jcd", "race_no", "lane"):
        if c not in df.columns:
            raise ValueError(f"today_races missing column: {c}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    sub = df[df["date"] == target_date].copy()
    sub["jcd"] = pd.to_numeric(sub["jcd"], errors="coerce").astype("Int64")
    sub["race_no"] = pd.to_numeric(sub["race_no"], errors="coerce").astype("Int64")
    sub = sub.dropna(subset=["jcd", "race_no"])
    return sub[["race_id", "date", "jcd", "race_no"]].drop_duplicates()


def fetch_one(jcd: int, hd: str, rno: int, timeout: int = 15) -> requests.Response:
    params = {"jcd": f"{int(jcd):02d}", "hd": hd, "rno": int(rno)}
    return requests.get(RESULT_URL, params=params, timeout=timeout)


def maybe_run_eval() -> dict:
    pred = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
    if not pred.exists() or not HIST_CSV.exists():
        return {"executed": False, "reason": "prediction/results file missing"}
    cmd = [
        "py",
        "src/eval/evaluate_experiments.py",
        "--predictions",
        "data/strategy_outputs/skip_decisions.csv",
        "--results",
        "data/processed/historical_races.csv",
        "--run-id",
        f"auto_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "--window",
        "recent30",
    ]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return {
        "executed": True,
        "returncode": int(p.returncode),
        "stdout_tail": p.stdout[-1000:],
        "stderr_tail": p.stderr[-500:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday JST)")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--allow-no-robots", action="store_true")
    args = parser.parse_args()

    target_date = args.date
    if not target_date:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    robots_ok = can_fetch(timeout=min(8, int(args.timeout)))
    if not robots_ok and not args.allow_no_robots:
        report = {
            "status": "blocked_by_robots",
            "target_date": target_date,
            "robots_url": ROBOTS_URL,
            "target_url": RESULT_URL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
        OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    targets = race_targets(target_date)
    fetched_rows: list[dict] = []
    failures: list[dict] = []

    for rec in targets.itertuples(index=False):
        try:
            hd = str(rec.date).replace("-", "")
            res = fetch_one(int(rec.jcd), hd, int(rec.race_no), timeout=int(args.timeout))
            if res.status_code != 200:
                failures.append({"race_id": rec.race_id, "status_code": int(res.status_code)})
                continue
            finish_rows = parse_finish_rows(res.text)
            if len(finish_rows) < 3:
                failures.append({"race_id": rec.race_id, "reason": "finish_rows_not_found"})
                continue

            for finish_pos, lane in finish_rows:
                fetched_rows.append(
                    {
                        "race_id": str(rec.race_id)
                        if pd.notna(rec.race_id)
                        else canonical_race_id(target_date, int(rec.jcd), int(rec.race_no)),
                        "date": target_date,
                        "jcd": int(rec.jcd),
                        "race_no": int(rec.race_no),
                        "lane": int(lane),
                        "finish_position": int(finish_pos),
                    }
                )
        except Exception as exc:
            failures.append({"race_id": rec.race_id, "error": str(exc)})

    before_rows = 0
    before_races = 0
    if HIST_CSV.exists():
        hist = pd.read_csv(HIST_CSV, low_memory=False)
        before_rows = int(len(hist))
        before_races = int(hist["race_id"].nunique()) if "race_id" in hist.columns else 0
    else:
        hist = pd.DataFrame()

    new_df = pd.DataFrame(fetched_rows)
    if not new_df.empty:
        combined = pd.concat([hist, new_df], ignore_index=True, sort=False)
        combined = combined.drop_duplicates(subset=["race_id", "lane"], keep="last")
        combined.to_csv(HIST_CSV, index=False)
    else:
        combined = hist

    eval_info = maybe_run_eval()
    report = {
        "status": "ok",
        "target_date": target_date,
        "robots_checked": True,
        "robots_allowed": bool(robots_ok),
        "targets": int(len(targets)),
        "fetched_rows": int(len(new_df)),
        "fetched_races": int(new_df["race_id"].nunique()) if not new_df.empty else 0,
        "before_rows": before_rows,
        "before_races": before_races,
        "after_rows": int(len(combined)),
        "after_races": int(combined["race_id"].nunique()) if "race_id" in combined.columns else 0,
        "failures": failures[:50],
        "evaluation": eval_info,
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
