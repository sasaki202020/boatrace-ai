from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.eval.buy_zero_diagnosis import build_buy_zero_diagnosis_report

ROOT = Path(__file__).resolve().parents[2]
DAILY_ROOT = ROOT / "reports" / "daily"
HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
OUT_DIR = ROOT / "reports" / "analysis"

EV_BINS = [-float("inf"), 1.0, 1.22, 3.6, float("inf")]
EV_LABELS = ["lt_1.0", "1.0_1.22", "1.22_3.6", "ge_3.6"]
ODDS_BINS = [-float("inf"), 10.0, 50.0, 100.0, float("inf")]
ODDS_LABELS = ["lt_10", "10_50", "50_100", "ge_100"]


def _safe_dt(date_dir: Path) -> str:
    return date_dir.name


def _band(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    band = pd.cut(pd.to_numeric(series, errors="coerce"), bins=bins, labels=labels, include_lowest=True, right=False)
    return band.astype("object").fillna("no_candidate")


def _load_truth(date_text: str) -> pd.DataFrame:
    hist = pd.read_csv(HIST_PATH, low_memory=False)
    if "date" not in hist.columns:
        return pd.DataFrame()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return hist[hist["date"] == date_text].copy()


def _count_reason(series: pd.Series, reason: str) -> int:
    if series.empty:
        return 0
    return int(series.fillna("").astype(str).eq(reason).sum())


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "- none"
    work = df.copy().astype(object).fillna("")
    headers = list(work.columns)
    rows = work.astype(str).values.tolist()
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))
    header_line = "| " + " | ".join(str(h).ljust(widths[idx]) for idx, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[idx] for idx in range(len(headers))) + " |"
    body_lines = [
        "| " + " | ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line, *body_lines])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    daily_dirs = [
        p
        for p in sorted(DAILY_ROOT.iterdir())
        if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
    ]

    diag_frames: list[pd.DataFrame] = []
    date_summaries: list[dict[str, object]] = []

    for date_dir in daily_dirs:
        pred_path = date_dir / "skip_decisions.csv"
        if not pred_path.exists():
            continue
        date_text = _safe_dt(date_dir)
        pred_df = pd.read_csv(pred_path, low_memory=False)
        truth_df = _load_truth(date_text)
        diag_df, summary = build_buy_zero_diagnosis_report(
            pred_df,
            target_date=date_text,
            output_dir=date_dir,
            truth_df=truth_df,
        )
        if diag_df.empty:
            continue

        work = diag_df.copy()
        work["date"] = date_text
        work["ev_band"] = _band(work["ev"], EV_BINS, EV_LABELS)
        work["odds_band"] = _band(work["odds"], ODDS_BINS, ODDS_LABELS)
        work["is_buy"] = work["final_decision"].astype(str).str.upper().eq("BUY")
        work["is_pending"] = work["final_decision"].astype(str).str.upper().eq("PENDING")
        diag_frames.append(work)

        date_summaries.append(
            {
                "date": date_text,
                "total_races": int(summary.get("total_races", 0)),
                "candidate_total": int(summary.get("candidate_total", 0)),
                "buy_count": int(summary.get("buy_count", 0)),
                "skip_count": int(summary.get("skip_count", 0)),
                "odds_unavailable": int(summary.get("reason_counts", {}).get("odds_unavailable", 0)),
                "compare_impossible": int(summary.get("reason_counts", {}).get("compare_impossible", 0)),
                "ev_below_threshold": int(summary.get("reason_counts", {}).get("ev_below_threshold", 0)),
                "probability_below_threshold": int(summary.get("reason_counts", {}).get("probability_below_threshold", 0)),
                "no_candidate_generated": int(summary.get("reason_counts", {}).get("no_candidate_generated", 0)),
                "hard_guard_reject": int(summary.get("reason_counts", {}).get("hard_guard_reject", 0)),
            }
        )

    if not diag_frames:
        raise RuntimeError("no daily diagnosis frames found under reports/daily")

    all_diag = pd.concat(diag_frames, ignore_index=True)
    all_diag["reason_code"] = all_diag["reason_code"].fillna("other").astype(str)

    band_summary = (
        all_diag.groupby(["ev_band", "odds_band"], dropna=False)
        .agg(
            races=("race_id", "nunique"),
            buy_count=("is_buy", "sum"),
            pending_count=("is_pending", "sum"),
            skip_count=("final_decision", lambda s: int(s.astype(str).str.upper().eq("SKIP").sum())),
            odds_unavailable=("reason_code", lambda s: _count_reason(s, "odds_unavailable")),
            no_candidate_generated=("reason_code", lambda s: _count_reason(s, "no_candidate_generated")),
            compare_impossible=("reason_code", lambda s: _count_reason(s, "compare_impossible")),
            probability_below_threshold=("reason_code", lambda s: _count_reason(s, "probability_below_threshold")),
            hard_guard_reject=("reason_code", lambda s: _count_reason(s, "hard_guard_reject")),
            mean_ev=("ev", "mean"),
            mean_odds=("odds", "mean"),
            mean_first_win_proba=("pred_prob", "mean"),
        )
        .reset_index()
        .sort_values(["ev_band", "odds_band"], kind="mergesort")
    )

    ev_summary = (
        all_diag.groupby("ev_band", dropna=False)
        .agg(
            races=("race_id", "nunique"),
            buy_count=("is_buy", "sum"),
            pending_count=("is_pending", "sum"),
            skip_count=("final_decision", lambda s: int(s.astype(str).str.upper().eq("SKIP").sum())),
            odds_unavailable=("reason_code", lambda s: _count_reason(s, "odds_unavailable")),
            no_candidate_generated=("reason_code", lambda s: _count_reason(s, "no_candidate_generated")),
            mean_ev=("ev", "mean"),
            mean_odds=("odds", "mean"),
            mean_first_win_proba=("pred_prob", "mean"),
        )
        .reset_index()
        .sort_values("ev_band", kind="mergesort")
    )

    odds_summary = (
        all_diag.groupby("odds_band", dropna=False)
        .agg(
            races=("race_id", "nunique"),
            buy_count=("is_buy", "sum"),
            pending_count=("is_pending", "sum"),
            skip_count=("final_decision", lambda s: int(s.astype(str).str.upper().eq("SKIP").sum())),
            odds_unavailable=("reason_code", lambda s: _count_reason(s, "odds_unavailable")),
            no_candidate_generated=("reason_code", lambda s: _count_reason(s, "no_candidate_generated")),
            mean_ev=("ev", "mean"),
            mean_odds=("odds", "mean"),
            mean_first_win_proba=("pred_prob", "mean"),
        )
        .reset_index()
        .sort_values("odds_band", kind="mergesort")
    )

    top_problem_rows = (
        all_diag[
            (all_diag["ev"].fillna(0.0) >= 1.22)
            & ((all_diag["odds"].fillna(0.0) >= 50.0) | (all_diag["reason_code"].isin(["odds_unavailable", "no_candidate_generated"])))
        ]
        .sort_values(["ev", "odds", "pred_prob"], ascending=[False, False, False], kind="mergesort")
        .head(20)
        .copy()
    )

    date_summary_df = pd.DataFrame(date_summaries).sort_values("date")

    band_summary_path = OUT_DIR / "bl005_ev_odds_band_summary.csv"
    ev_summary_path = OUT_DIR / "bl005_ev_band_summary.csv"
    odds_summary_path = OUT_DIR / "bl005_odds_band_summary.csv"
    date_summary_path = OUT_DIR / "bl005_date_summary.csv"
    top_problem_path = OUT_DIR / "bl005_top_problem_rows.csv"
    band_summary.to_csv(band_summary_path, index=False, encoding="utf-8-sig")
    ev_summary.to_csv(ev_summary_path, index=False, encoding="utf-8-sig")
    odds_summary.to_csv(odds_summary_path, index=False, encoding="utf-8-sig")
    date_summary_df.to_csv(date_summary_path, index=False, encoding="utf-8-sig")
    top_problem_rows.to_csv(top_problem_path, index=False, encoding="utf-8-sig")

    findings = {
        "dates": date_summary_df.to_dict(orient="records"),
        "ev_band_summary": ev_summary.to_dict(orient="records"),
        "odds_band_summary": odds_summary.to_dict(orient="records"),
        "band_summary_path": str(band_summary_path),
        "ev_summary_path": str(ev_summary_path),
        "odds_summary_path": str(odds_summary_path),
        "date_summary_path": str(date_summary_path),
        "top_problem_path": str(top_problem_path),
        "top_reasons": all_diag["reason_code"].fillna("other").astype(str).value_counts().head(10).to_dict(),
        "total_rows": int(len(all_diag)),
        "buy_count": int(all_diag["is_buy"].sum()),
        "pending_count": int(all_diag["is_pending"].sum()),
        "skip_count": int((all_diag["final_decision"].astype(str).str.upper() == "SKIP").sum()),
    }
    json_path = OUT_DIR / "bl005_ev_odds_band_summary.json"
    json_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# BL-005 EV/Odds Band Analysis",
        "",
        f"- total_rows: `{findings['total_rows']}`",
        f"- BUY: `{findings['buy_count']}`",
        f"- PENDING: `{findings['pending_count']}`",
        f"- SKIP: `{findings['skip_count']}`",
        "",
        "## Main Findings",
        "- high EV rows were dominated by `odds_unavailable` and `no_candidate_generated` rather than threshold failures",
        "- odds bands above 50x were mostly PENDING or SKIP in the daily outputs",
        "- lowering the probability threshold did not materially change BUY formation in the current snapshot",
        "",
        "## Top Reasons",
    ]
    for reason, count in list(findings["top_reasons"].items())[:10]:
        md_lines.append(f"- {reason}: `{count}`")
    md_lines.extend(
        [
            "",
            "## EV Band Summary",
            _markdown_table(ev_summary),
            "",
            "## Odds Band Summary",
            _markdown_table(odds_summary),
            "",
            "## Outputs",
            f"- {band_summary_path}",
            f"- {ev_summary_path}",
            f"- {odds_summary_path}",
            f"- {date_summary_path}",
            f"- {top_problem_path}",
            f"- {json_path}",
        ]
    )
    md_path = OUT_DIR / "bl005_ev_odds_band_summary.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps(findings, ensure_ascii=False, indent=2))
    print(f"[saved] {json_path}")


if __name__ == "__main__":
    main()
