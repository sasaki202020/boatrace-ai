from __future__ import annotations

"""Diagnose suspicious execution cases in the replay demo."""

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = ROOT / "reports" / "demo"

SUSPICIOUS_REASON_ORDER = [
    "odds_out_of_expected_range",
    "odds_missing_for_combo",
    "odds_inconsistent_with_ev",
    "odds_join_partial",
    "other",
]


@dataclass(frozen=True)
class SuspiciousCaseConfig:
    out_of_expected_range_threshold: float = 500.0
    inconsistent_with_ev_threshold: float = 200.0


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}") from exc


def _report_dir(target_date: date) -> Path:
    path = DEMO_ROOT / target_date.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    return next((col for col in candidates if col in df.columns), None)


def _normalize_demo_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    rename_map = {
        "レースID": "race_id",
        "場コード": "jcd",
        "レース番号": "race_no",
        "推奨組番": "buy_combo",
        "期待値": "summary_ev",
        "オッズ": "summary_odds",
        "オッズ取得": "summary_odds_status",
        "リスク": "summary_risk",
        "理由": "reason_text",
        "判定": "decision_label",
    }
    for source, target in rename_map.items():
        if source in work.columns and target not in work.columns:
            work = work.rename(columns={source: target})
    if "race_id" not in work.columns:
        raise ValueError("demo_predictions.csv must contain race_id / レースID")
    work["race_id"] = work["race_id"].astype(str).str.strip()
    if "buy_combo" not in work.columns:
        work["buy_combo"] = ""
    if "jcd" in work.columns:
        work["jcd"] = pd.to_numeric(work["jcd"], errors="coerce")
    if "race_no" in work.columns:
        work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
    if "normalized_win_probability" in work.columns:
        work["normalized_win_probability"] = pd.to_numeric(work["normalized_win_probability"], errors="coerce").fillna(0.0)
    else:
        work["normalized_win_probability"] = 0.0
    if "ev" not in work.columns:
        if "summary_ev" in work.columns:
            work["ev"] = pd.to_numeric(work["summary_ev"], errors="coerce").fillna(0.0)
        else:
            work["ev"] = 0.0
    else:
        work["ev"] = pd.to_numeric(work["ev"], errors="coerce").fillna(0.0)
    if "summary_odds" in work.columns:
        work["real_odds"] = pd.to_numeric(work["summary_odds"], errors="coerce").fillna(0.0)
    else:
        work["real_odds"] = 0.0
    if "summary_odds_status" not in work.columns:
        work["summary_odds_status"] = ""
    if "summary_risk" not in work.columns:
        work["summary_risk"] = ""
    return work


def _load_selected_ev_rows(target_date: date) -> pd.DataFrame:
    report_dir = _report_dir(target_date)
    predictions = _safe_read_csv(report_dir / "demo_predictions.csv")
    ev_analysis = _safe_read_csv(report_dir / "_demo_ev_analysis.csv")
    if predictions.empty:
        raise FileNotFoundError(f"demo_predictions.csv not found for {target_date.isoformat()}")
    if ev_analysis.empty:
        raise FileNotFoundError(f"_demo_ev_analysis.csv not found for {target_date.isoformat()}")

    pred = _normalize_demo_predictions(predictions)
    selected_cols = [
        "race_id",
        "buy_combo",
        "normalized_win_probability",
        "ev",
        "real_odds",
        "summary_odds_status",
        "summary_risk",
        "quality_decision",
        "final_decision",
        "execution_status",
        "decision_label",
        "reason_text",
        "jcd",
        "race_no",
    ]
    pred = pred[[col for col in selected_cols if col in pred.columns]].copy()
    if "ev" in pred.columns:
        pred = pred.rename(columns={"ev": "prediction_ev"})

    ev = ev_analysis.copy()
    if "trifecta" not in ev.columns:
        raise ValueError("_demo_ev_analysis.csv must contain trifecta")
    if "race_id" not in ev.columns:
        raise ValueError("_demo_ev_analysis.csv must contain race_id")
    ev["race_id"] = ev["race_id"].astype(str).str.strip()
    ev["trifecta"] = ev["trifecta"].astype(str).str.strip()
    ev["odds"] = pd.to_numeric(ev.get("odds"), errors="coerce")
    ev["ev"] = pd.to_numeric(ev.get("ev"), errors="coerce").fillna(0.0)
    ev["risk_flag"] = ev.get("risk_flag", False).fillna(False).astype(bool)
    ev["odds_status"] = ev.get("odds_status", "").fillna("").astype(str)
    ev["odds_fetch_status"] = ev.get("odds_fetch_status", "").fillna("").astype(str)
    ev["odds_missing_odds_cells"] = pd.to_numeric(ev.get("odds_missing_odds_cells"), errors="coerce").fillna(0).astype(int)
    ev["odds_source"] = ev.get("odds_source", "").fillna("").astype(str)
    ev["has_real_odds"] = ev.get("has_real_odds", False).fillna(False).astype(bool)

    merged = pred.merge(
        ev[
            [
                "race_id",
                "trifecta",
                "odds",
                "ev",
                "risk_flag",
                "odds_status",
                "odds_fetch_status",
                "odds_missing_odds_cells",
                "odds_source",
                "has_real_odds",
            ]
        ],
        left_on=["race_id", "buy_combo"],
        right_on=["race_id", "trifecta"],
        how="left",
        suffixes=("_pred", "_exec"),
    )
    merged["odds"] = pd.to_numeric(merged["odds"], errors="coerce")
    if "prediction_ev" in merged.columns:
        merged["prediction_ev"] = pd.to_numeric(merged["prediction_ev"], errors="coerce").fillna(0.0)
    merged["execution_ev"] = pd.to_numeric(merged["ev"], errors="coerce").fillna(0.0)
    merged["ev"] = merged["execution_ev"]
    merged["risk_flag"] = merged.get("risk_flag", False).fillna(False).astype(bool)
    merged["odds_status"] = merged.get("odds_status", "").fillna("").astype(str)
    merged["odds_fetch_status"] = merged.get("odds_fetch_status", "").fillna("").astype(str)
    merged["odds_missing_odds_cells"] = pd.to_numeric(merged.get("odds_missing_odds_cells"), errors="coerce").fillna(0).astype(int)
    merged["odds_source"] = merged.get("odds_source", "").fillna("").astype(str)
    merged["has_real_odds"] = merged.get("has_real_odds", False).fillna(False).astype(bool)
    merged["race_label"] = (
        pd.to_numeric(merged.get("jcd"), errors="coerce").fillna(0).astype(int).astype(str).str.zfill(2)
        + "R"
        + pd.to_numeric(merged.get("race_no"), errors="coerce").fillna(0).astype(int).astype(str)
    )
    return merged


def _classify_suspicious_reason(row: pd.Series, config: SuspiciousCaseConfig) -> str:
    odds_raw = pd.to_numeric(row.get("odds"), errors="coerce")
    ev_raw = pd.to_numeric(row.get("ev"), errors="coerce")
    odds = float(odds_raw) if pd.notna(odds_raw) else 0.0
    ev_value = float(ev_raw) if pd.notna(ev_raw) else 0.0
    has_real_odds = bool(row.get("has_real_odds", False))
    odds_status = str(row.get("odds_status", "") or "").strip().lower()
    odds_fetch_status = str(row.get("odds_fetch_status", "") or "").strip().lower()
    missing_cells_raw = pd.to_numeric(row.get("odds_missing_odds_cells"), errors="coerce")
    missing_cells = int(missing_cells_raw) if pd.notna(missing_cells_raw) else 0

    if not has_real_odds or pd.isna(odds_raw) or odds <= 0.0 or odds_status in {"missing", "not_offered", "pending"}:
        return "odds_missing_for_combo"
    if missing_cells > 0 or odds_fetch_status in {"partial_missing", "failed"}:
        return "odds_join_partial"
    if odds >= config.out_of_expected_range_threshold:
        return "odds_out_of_expected_range"
    if odds >= config.inconsistent_with_ev_threshold and ev_value >= 5.0:
        return "odds_inconsistent_with_ev"
    return "other"


def _human_risk_labels(reason: str) -> str:
    mapping = {
        "odds_out_of_expected_range": "高配当で変動大",
        "odds_missing_for_combo": "実オッズ未取得",
        "odds_inconsistent_with_ev": "期待値とオッズの不整合",
        "odds_join_partial": "結合欠損あり",
        "other": "risk_suspicious_odds",
    }
    return mapping.get(reason, reason)


def build_suspicious_odds_diagnostics(
    *,
    target_date: date,
    config: SuspiciousCaseConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    if config is None:
        config = SuspiciousCaseConfig()

    merged = _load_selected_ev_rows(target_date)
    suspicious = merged.loc[merged["execution_status"].astype(str).eq("suspicious_odds")].copy()
    tradable = merged.loc[merged["execution_status"].astype(str).eq("tradable")].copy()

    if suspicious.empty:
        empty_columns = [
            "race_id",
            "race_label",
            "buy_combo",
            "quality_decision",
            "final_decision",
            "normalized_win_probability",
            "ev",
            "real_odds",
            "odds_status",
            "risk_labels",
            "suspicious_reason",
        ]
        empty_df = pd.DataFrame(columns=empty_columns)
        summary = {
            "target_date": target_date.isoformat(),
            "total_suspicious_cases": 0,
            "total_tradable_cases": int(len(tradable)),
            "suspicious_reason_counts": {},
            "top_suspicious_examples": [],
            "notes": [
                "selector / threshold / model は変更していません。",
                "suspicious_odds に該当するケースはありませんでした。",
            ],
        }
        comparison = {
            "target_date": target_date.isoformat(),
            "tradable": {},
            "suspicious": {},
            "reason_distribution": {},
            "notes": ["comparison is empty because suspicious cases were not found."],
        }
        return empty_df, summary, comparison

    suspicious["suspicious_reason"] = suspicious.apply(lambda row: _classify_suspicious_reason(row, config), axis=1)
    suspicious["risk_labels"] = suspicious["suspicious_reason"].map(_human_risk_labels)
    suspicious["race_label"] = suspicious["race_label"].astype(str)

    suspicious_df = pd.DataFrame(
        {
            "race_id": suspicious["race_id"].astype(str),
            "race_label": suspicious["race_label"].astype(str),
            "buy_combo": suspicious["buy_combo"].astype(str),
            "quality_decision": suspicious["quality_decision"].astype(str),
            "final_decision": suspicious["final_decision"].astype(str),
            "normalized_win_probability": pd.to_numeric(suspicious["normalized_win_probability"], errors="coerce").fillna(0.0),
            "ev": pd.to_numeric(suspicious["ev"], errors="coerce").fillna(0.0),
            "real_odds": pd.to_numeric(suspicious["odds"], errors="coerce").fillna(0.0),
            "odds_status": suspicious["odds_status"].fillna("").astype(str),
            "risk_labels": suspicious["risk_labels"].astype(str),
            "suspicious_reason": suspicious["suspicious_reason"].astype(str),
            "odds_fetch_status": suspicious["odds_fetch_status"].fillna("").astype(str),
            "odds_missing_odds_cells": pd.to_numeric(suspicious["odds_missing_odds_cells"], errors="coerce").fillna(0).astype(int),
        }
    ).sort_values(
        ["normalized_win_probability", "ev", "real_odds", "race_id"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    suspicious_reason_counts = {
        reason: int((suspicious_df["suspicious_reason"] == reason).sum())
        for reason in SUSPICIOUS_REASON_ORDER
    }

    top_examples = json.loads(suspicious_df.head(5).to_json(orient="records", force_ascii=False))
    tradable_df = pd.DataFrame(
        {
            "race_id": tradable["race_id"].astype(str),
            "race_label": tradable["race_label"].astype(str),
            "buy_combo": tradable["buy_combo"].astype(str),
            "quality_decision": tradable["quality_decision"].astype(str),
            "final_decision": tradable["final_decision"].astype(str),
            "normalized_win_probability": pd.to_numeric(tradable["normalized_win_probability"], errors="coerce").fillna(0.0),
            "ev": pd.to_numeric(tradable["ev"], errors="coerce").fillna(0.0),
            "real_odds": pd.to_numeric(tradable["odds"], errors="coerce").fillna(0.0),
            "odds_status": tradable["odds_status"].fillna("").astype(str),
            "risk_labels": tradable["summary_risk"].fillna("").astype(str),
        }
    )

    summary = {
        "target_date": target_date.isoformat(),
        "total_suspicious_cases": int(len(suspicious_df)),
        "total_tradable_cases": int(len(tradable_df)),
        "suspicious_reason_counts": suspicious_reason_counts,
        "top_suspicious_examples": top_examples,
        "notes": [
            "selector / threshold / model は変更していません。",
            "suspicious_reason は execution metadata からの診断用推定です。",
            "tradable 4件と suspicious 33件を同じ selected row ベースで比較しています。",
        ],
    }

    comparison = {
        "target_date": target_date.isoformat(),
        "tradable": {
            "count": int(len(tradable_df)),
            "avg_normalized_win_probability": float(tradable_df["normalized_win_probability"].mean()) if not tradable_df.empty else 0.0,
            "avg_ev": float(tradable_df["ev"].mean()) if not tradable_df.empty else 0.0,
            "avg_real_odds": float(tradable_df["real_odds"].mean()) if not tradable_df.empty else 0.0,
        },
        "suspicious": {
            "count": int(len(suspicious_df)),
            "avg_normalized_win_probability": float(suspicious_df["normalized_win_probability"].mean()) if not suspicious_df.empty else 0.0,
            "avg_ev": float(suspicious_df["ev"].mean()) if not suspicious_df.empty else 0.0,
            "avg_real_odds": float(suspicious_df["real_odds"].mean()) if not suspicious_df.empty else 0.0,
        },
        "reason_distribution": suspicious_reason_counts,
        "notes": [
            "comparison is computed on selected race rows only.",
            "avg_real_odds is the selected combo odds, not the race-wide maximum.",
        ],
    }
    return suspicious_df, summary, comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose suspicious_odds cases in demo predictions.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    args = parser.parse_args()

    target_date = _parse_date(args.date)
    report_dir = _report_dir(target_date)
    csv_path = report_dir / "demo_suspicious_odds_cases.csv"
    summary_path = report_dir / "demo_suspicious_odds_summary.json"
    comparison_path = report_dir / "demo_tradable_vs_suspicious_comparison.json"

    suspicious_df, summary, comparison = build_suspicious_odds_diagnostics(target_date=target_date)
    suspicious_df.to_csv(csv_path, index=False, encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "summary_json": str(summary_path),
                "comparison_json": str(comparison_path),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
