from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
TODAY_RACES_CSV = ROOT / "data" / "processed" / "today_races.csv"
TODAY_FEATURES_CSV = ROOT / "data" / "features" / "today_features.csv"
TODAY_WIN_PROBA_CSV = ROOT / "data" / "model_outputs" / "today_win_proba.csv"
OUT_CSV = ROOT / "data" / "strategy_outputs" / "live_odds.csv"
OUT_REPORT = ROOT / "data" / "strategy_outputs" / "live_odds_report.json"

ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/oddstf"
ROBOTS_URL = "https://www.boatrace.jp/robots.txt"
TRIFECTA_RE = re.compile(r"([1-6]-[1-6]-[1-6]).{0,20}?([0-9]+(?:\.[0-9]+)?)")


def can_fetch(timeout: int = 8) -> bool:
    try:
        res = requests.get(ROBOTS_URL, timeout=timeout)
        if res.status_code != 200:
            return False
        txt = res.text
        # 明示 disallow が無ければ許可扱い
        if "Disallow: /owpc/pc/race/oddstf" in txt:
            return False
        return True
    except Exception:
        return False


def _load_target_frame() -> pd.DataFrame:
    for path in (TODAY_RACES_CSV, TODAY_FEATURES_CSV, TODAY_WIN_PROBA_CSV):
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if frame.empty:
            continue
        if {"race_id", "date", "jcd", "race_no"}.issubset(frame.columns):
            return frame
        if "race_id" in frame.columns and "date" in frame.columns and "jcd" in frame.columns and "race_no" in frame.columns:
            return frame
    raise FileNotFoundError(
        "No usable target frame found. Expected today_races.csv, today_features.csv, or today_win_proba.csv with race_id/date/jcd/race_no columns."
    )


def race_targets(today_df: pd.DataFrame) -> pd.DataFrame:
    work = today_df.copy()
    if {"race_id", "date", "jcd", "race_no"}.issubset(work.columns):
        t = work[["race_id", "date", "jcd", "race_no"]].dropna().drop_duplicates().copy()
        t["race_id"] = t["race_id"].astype(str).str.strip()
        t["hd"] = pd.to_datetime(t["date"], errors="coerce").dt.strftime("%Y%m%d")
        t["jcd"] = pd.to_numeric(t["jcd"], errors="coerce").astype("Int64")
        t["rno"] = pd.to_numeric(t["race_no"], errors="coerce").astype("Int64")
        return t.dropna(subset=["hd", "jcd", "rno"]).copy()

    if "union_key" in work.columns:
        keys = work["union_key"].dropna().astype(str).str.strip().drop_duplicates()
        rows: list[dict[str, object]] = []
        for key in keys:
            parts = key.split("_")
            if len(parts) != 3:
                continue
            date8, jcd, rno = parts
            if len(date8) != 8 or not jcd.isdigit() or not rno.isdigit():
                continue
            rows.append(
                {
                    "race_id": f"{date8}-{int(jcd):02d}-{int(rno):02d}",
                    "date": f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}",
                    "jcd": int(jcd),
                    "race_no": int(rno),
                }
            )
        if rows:
            t = pd.DataFrame(rows)
            t["hd"] = pd.to_datetime(t["date"], errors="coerce").dt.strftime("%Y%m%d")
            t["jcd"] = pd.to_numeric(t["jcd"], errors="coerce").astype("Int64")
            t["rno"] = pd.to_numeric(t["race_no"], errors="coerce").astype("Int64")
            return t.dropna(subset=["hd", "jcd", "rno"]).copy()

    raise ValueError("target frame missing columns: expected race_id/date/jcd/race_no or union_key")


def parse_odds_from_html(html: str) -> list[tuple[str, float]]:
    pairs: dict[str, float] = {}
    for tri, odds in TRIFECTA_RE.findall(html):
        try:
            o = float(odds.replace(",", ""))
            if o > 0:
                pairs[tri] = o
        except Exception:
            continue
    return sorted(pairs.items())


def fetch_one(jcd: int, hd: str, rno: int, timeout: int = 15) -> requests.Response:
    params = {"jcd": f"{int(jcd):02d}", "hd": hd, "rno": int(rno)}
    return requests.get(ODDS_URL, params=params, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--allow-no-robots", action="store_true")
    parser.add_argument("--max-targets", type=int, default=300)
    args = parser.parse_args()

    robots_ok = can_fetch(timeout=min(8, int(args.timeout)))
    if not robots_ok and not args.allow_no_robots:
        report = {
            "status": "blocked_by_robots",
            "robots_url": ROBOTS_URL,
            "target_url": ODDS_URL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
        OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    today_df = _load_target_frame()
    targets = race_targets(today_df).sort_values(["date", "jcd", "rno"])
    if args.max_targets and len(targets) > int(args.max_targets):
        targets = targets.head(int(args.max_targets)).copy()
    rows: list[dict] = []
    failures: list[dict] = []

    for rec in targets.itertuples(index=False):
        race_id = str(rec.race_id)
        try:
            res = fetch_one(int(rec.jcd), str(rec.hd), int(rec.rno), timeout=int(args.timeout))
            if res.status_code != 200:
                failures.append(
                    {
                        "race_id": race_id,
                        "status_code": int(res.status_code),
                        "jcd": int(rec.jcd),
                        "hd": str(rec.hd),
                        "rno": int(rec.rno),
                    }
                )
                continue
            parsed = parse_odds_from_html(res.text)
            for trifecta, odds in parsed:
                rows.append(
                    {
                        "race_id": race_id,
                        "trifecta": trifecta,
                        "odds": float(odds),
                        "jcd": int(rec.jcd),
                        "hd": str(rec.hd),
                        "rno": int(rec.rno),
                        "odds_source": "real_live",
                        "fetched_at": datetime.now().isoformat(timespec="seconds"),
                        "source_url": ODDS_URL,
                    }
                )
        except Exception as exc:
            failures.append(
                {
                    "race_id": race_id,
                    "error": str(exc),
                    "jcd": int(rec.jcd),
                    "hd": str(rec.hd),
                    "rno": int(rec.rno),
                }
            )

    out_df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if out_df.empty:
        out_df = pd.DataFrame(columns=["race_id", "trifecta", "odds", "jcd", "hd", "rno", "odds_source", "fetched_at", "source_url"])
    else:
        out_df = out_df.sort_values(["race_id", "trifecta"]).drop_duplicates(
            subset=["race_id", "trifecta"], keep="last"
        )
    out_df.to_csv(OUT_CSV, index=False)

    report = {
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "robots_checked": True,
        "robots_allowed": bool(robots_ok),
        "targets": int(len(targets)),
        "rows": int(len(out_df)),
        "races_with_odds": int(out_df["race_id"].nunique()) if not out_df.empty else 0,
        "failures": failures[:50],
        "output_csv": str(OUT_CSV),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
