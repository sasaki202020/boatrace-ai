import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKIP_PATH = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
DEFAULT_OUT_DIR = ROOT / "reports" / "gate_health"


REASON_PATTERNS = {
    "real_odds_missing": re.compile(r"実オッズ未取得"),
    "pre_race_missing": re.compile(r"直前情報欠損|直前情報不足"),
    "first_place_missing": re.compile(r"1着情報欠損|1着情報不足"),
    "second_place_missing": re.compile(r"2着情報欠損|2着情報不足"),
    "third_place_missing": re.compile(r"3着情報欠損|3着情報不足"),
    "data_missing": re.compile(r"データ欠損あり"),
    "high_odds": re.compile(r"オッズ上限を超過|高配当で変動大"),
    "high_ev": re.compile(r"EV上限を超過|EV が上限超過"),
}


def _safe_counter(series: pd.Series) -> dict[str, int]:
    counts = series.fillna("missing").astype(str).value_counts(dropna=False)
    return {str(k): int(v) for k, v in counts.items()}


def _parse_list_like(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text or text == "nan":
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return [str(x) for x in parsed]
    except Exception:
        pass
    parts = [part.strip() for part in re.split(r"[,/|]", text) if part.strip()]
    return parts


def _keyword_counts(reason_series: pd.Series) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for reason in reason_series.fillna("").astype(str):
        for label, pattern in REASON_PATTERNS.items():
            if pattern.search(reason):
                counter[label] += 1
    return {k: int(v) for k, v in counter.items()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize gate health from skip_decisions.csv")
    parser.add_argument("--skip-path", default=str(DEFAULT_SKIP_PATH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    skip_path = Path(args.skip_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not skip_path.exists():
        raise FileNotFoundError(f"skip decisions not found: {skip_path}")

    df = pd.read_csv(skip_path)
    for col in ("decision", "first_place_gate", "pre_race_gate", "race_gate", "reason"):
        if col not in df.columns:
            df[col] = ""
    for col, default_value in {
        "stop_reason": "",
        "odds_status": "missing",
        "buy_eligible": False,
        "risk_flag": False,
        "candidate_rank_by_sort": None,
    }.items():
        if col not in df.columns:
            df[col] = default_value

    if "risk_labels" not in df.columns:
        df["risk_labels"] = ""

    df["risk_label_list"] = df["risk_labels"].apply(_parse_list_like)
    df["buy_eligible"] = df["buy_eligible"].astype(str).str.lower().eq("true")
    df["risk_flag"] = df["risk_flag"].astype(str).str.lower().eq("true")
    df["candidate_rank_by_sort"] = pd.to_numeric(df["candidate_rank_by_sort"], errors="coerce")

    summary = {
        "rows": int(len(df)),
        "decision_counts": _safe_counter(df["decision"]),
        "first_place_gate_counts": _safe_counter(df["first_place_gate"]),
        "pre_race_gate_counts": _safe_counter(df["pre_race_gate"]),
        "race_gate_counts": _safe_counter(df["race_gate"]),
        "odds_status_counts": _safe_counter(df["odds_status"]),
        "stop_reason_counts": _safe_counter(df["stop_reason"]),
        "buy_eligible_counts": {
            "true": int(df["buy_eligible"].sum()),
            "false": int((~df["buy_eligible"]).sum()),
        },
        "risk_flag_counts": {
            "true": int(df["risk_flag"].sum()),
            "false": int((~df["risk_flag"]).sum()),
        },
        "real_odds_missing_rows": int(df["stop_reason"].astype(str).str.startswith("real_odds_missing").sum()),
        "real_odds_pending_before_deadline_rows": int(df["stop_reason"].astype(str).eq("real_odds_pending_before_deadline").sum()),
        "pending_rows": int(df["decision"].astype(str).eq("PENDING").sum()),
        "max_buy_count_rows": int(df["stop_reason"].astype(str).eq("max_buy_count").sum()),
        "gate_combo_counts": _safe_counter(
            df["first_place_gate"].astype(str)
            + " / "
            + df["pre_race_gate"].astype(str)
            + " / "
            + df["race_gate"].astype(str)
            + " / "
            + df["decision"].astype(str)
        ),
        "reason_keyword_counts": _keyword_counts(df["reason"]),
        "risk_label_counts": _safe_counter(
            pd.Series([label for labels in df["risk_label_list"] for label in labels])
        ),
    }
    top3 = df[df["candidate_rank_by_sort"].le(3)].copy()
    summary["candidate_top3_rows"] = int(len(top3))
    summary["candidate_top3_stop_reason_counts"] = _safe_counter(top3["stop_reason"]) if not top3.empty else {}

    missing_rows = df[df["first_place_gate"].astype(str).eq("MISSING") | df["pre_race_gate"].astype(str).eq("MISSING")].copy()
    pending_rows = df[df["decision"].astype(str).eq("PENDING")].copy()

    summary["missing_rows"] = int(len(missing_rows))
    summary["pending_rows"] = int(len(pending_rows))
    summary["missing_breakdown"] = {
        "first_place_only": int(
            ((df["first_place_gate"].astype(str) == "MISSING") & (df["pre_race_gate"].astype(str) != "MISSING")).sum()
        ),
        "pre_race_only": int(
            ((df["pre_race_gate"].astype(str) == "MISSING") & (df["first_place_gate"].astype(str) != "MISSING")).sum()
        ),
        "both_missing": int(
            ((df["first_place_gate"].astype(str) == "MISSING") & (df["pre_race_gate"].astype(str) == "MISSING")).sum()
        ),
    }

    examples_cols = [
        "race_id",
        "decision",
        "first_place_gate",
        "pre_race_gate",
        "race_gate",
        "has_real_odds",
        "odds_source",
        "first_place_score",
        "pre_race_score",
        "race_score",
        "reason",
    ]
    examples_cols = [c for c in examples_cols if c in df.columns]

    missing_examples = missing_rows[examples_cols].head(20)
    pending_examples = pending_rows[examples_cols].head(20)

    summary_path = out_dir / "gate_health_summary.json"
    missing_csv = out_dir / "missing_examples.csv"
    pending_csv = out_dir / "pending_examples.csv"

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    missing_examples.to_csv(missing_csv, index=False, encoding="utf-8-sig")
    pending_examples.to_csv(pending_csv, index=False, encoding="utf-8-sig")

    print(json.dumps(
        {
            "summary_path": str(summary_path),
            "missing_examples": str(missing_csv),
            "pending_examples": str(pending_csv),
        "decision_counts": summary["decision_counts"],
        "gate_combo_counts_top": dict(list(summary["gate_combo_counts"].items())[:8]),
        "reason_keyword_counts": summary["reason_keyword_counts"],
        "stop_reason_counts_top": dict(list(summary["stop_reason_counts"].items())[:8]),
        "candidate_top3_stop_reason_counts": summary["candidate_top3_stop_reason_counts"],
        "missing_breakdown": summary["missing_breakdown"],
    },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
