import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.eval.evaluate_experiments import load_inputs


def apply_window(df: pd.DataFrame, window: str) -> tuple[pd.DataFrame, str]:
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    if len(work) == 0:
        return work, "no valid date rows"
    latest = work["date"].dt.normalize().max()
    if window == "all":
        start = work["date"].dt.normalize().min()
        out = work
    elif window == "recent30":
        start = latest - timedelta(days=29)
        out = work[work["date"].dt.normalize() >= start].copy()
    elif window == "recent60":
        start = latest - timedelta(days=59)
        out = work[work["date"].dt.normalize() >= start].copy()
    else:
        raise ValueError(f"unsupported window: {window}")
    return out, f"{window} ({start:%Y-%m-%d} - {latest:%Y-%m-%d})"


def first2(text: str) -> str:
    parts = str(text).split("-")
    if len(parts) < 2:
        return ""
    return f"{parts[0]}-{parts[1]}"


def first2_set(text: str) -> str:
    parts = str(text).split("-")
    if len(parts) < 2:
        return ""
    a, b = sorted(parts[:2])
    return f"{a}-{b}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate exacta metrics using current trifecta outputs")
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--window", choices=["all", "recent30", "recent60"], default="recent30")
    args = parser.parse_args()

    merged = load_inputs(Path(args.predictions), Path(args.results)).copy()
    merged, window_desc = apply_window(merged, args.window)

    # Keep evaluation universe aligned with evaluate_experiments:
    # 1) apply window on all races first
    # 2) then restrict to BUY rows
    # 3) skip rows without reconstructable actual trifecta
    merged = merged[merged["is_buy"]].copy()
    before_actual = len(merged)
    merged = merged[merged["actual_trifecta"].notna()].copy()
    skipped_after_buy = before_actual - len(merged)

    merged["pred_exacta"] = merged["predicted_trifecta"].map(first2)
    merged["act_exacta"] = merged["actual_trifecta"].map(first2)
    merged["pred_quinella"] = merged["predicted_trifecta"].map(first2_set)
    merged["act_quinella"] = merged["actual_trifecta"].map(first2_set)
    merged["exacta_hit"] = merged["pred_exacta"] == merged["act_exacta"]
    merged["quinella_hit"] = merged["pred_quinella"] == merged["act_quinella"]

    # Prefer official exacta odds when available; otherwise fallback to trifecta settled odds as proxy.
    merged["settled_odds_proxy"] = pd.to_numeric(merged["official_odds"], errors="coerce").fillna(
        pd.to_numeric(merged["odds"], errors="coerce")
    )
    merged["settled_exacta_odds"] = pd.to_numeric(merged.get("official_exacta_odds"), errors="coerce")
    merged["settled_exacta_odds"] = merged["settled_exacta_odds"].fillna(merged["settled_odds_proxy"])
    merged["exacta_odds_is_official"] = pd.to_numeric(merged.get("official_exacta_odds"), errors="coerce").notna()

    n = len(merged)
    exacta_hit = int(merged["exacta_hit"].sum())
    quinella_hit = int(merged["quinella_hit"].sum())
    exacta_rate = round(exacta_hit / n, 4) if n else 0.0
    quinella_rate = round(quinella_hit / n, 4) if n else 0.0
    exacta_roi = round(
        float(merged.loc[merged["exacta_hit"], "settled_exacta_odds"].sum()) / n, 4
    ) if n else 0.0
    quinella_roi_proxy = round(
        float(merged.loc[merged["quinella_hit"], "settled_odds_proxy"].sum()) / n, 4
    ) if n else 0.0

    out = {
        "run_id": args.run_id,
        "window": window_desc,
        "buy_count": int(n),
        "exacta": {
            "hit_count": exacta_hit,
            "hit_rate": exacta_rate,
            "roi": exacta_roi,
            "official_exacta_odds_coverage": round(float(merged["exacta_odds_is_official"].mean()), 4) if n else 0.0,
        },
        "quinella_orderless": {
            "hit_count": quinella_hit,
            "hit_rate": quinella_rate,
            "roi_proxy_using_trifecta_odds": quinella_roi_proxy,
        },
        "note": "Exacta ROI uses official exacta odds when available; otherwise it falls back to trifecta settled odds as proxy.",
    }

    out_dir = Path("reports/experiments") / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "exacta_proxy_summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cols = [
        "race_id", "date", "pred_exacta", "act_exacta", "exacta_hit",
        "pred_quinella", "act_quinella", "quinella_hit", "settled_exacta_odds", "exacta_odds_is_official", "settled_odds_proxy"
    ]
    merged[cols].to_csv(out_dir / "exacta_proxy_race_level.csv", index=False, encoding="utf-8-sig")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if skipped_after_buy > 0:
        print(f"[warn] skipped {skipped_after_buy} rows after BUY filter (missing actual_trifecta etc.)")
    print(f"[saved] {out_dir / 'exacta_proxy_summary.json'}")
    print(f"[saved] {out_dir / 'exacta_proxy_race_level.csv'}")


if __name__ == "__main__":
    main()
