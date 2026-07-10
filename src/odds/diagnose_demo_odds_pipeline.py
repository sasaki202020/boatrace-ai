from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEMO_REPORT_DIR = ROOT / "reports" / "demo"
ODDS_ROOT = ROOT / "data" / "odds"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except Exception as exc:
        raise ValueError(f"invalid date: {value}") from exc


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _infer_source_phase(race_no: Any) -> str:
    try:
        race_no_int = int(float(race_no))
    except Exception:
        return "final"
    if race_no_int <= 4:
        return "morning"
    if race_no_int <= 8:
        return "late"
    return "final"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_race_label(row: pd.Series) -> str:
    stadium = _normalize_text(row.get("場コード", row.get("stadium", "")))
    race_no = row.get("レース番号", row.get("race_no", ""))
    try:
        race_no_int = int(float(race_no))
    except Exception:
        race_no_int = 0
    return f"{stadium}R{race_no_int}"


def _load_odds_context(target_date: date) -> dict[str, Any]:
    date_key = target_date.strftime("%Y%m%d")
    odds_dir = ODDS_ROOT / date_key
    return {
        "targets": _safe_read_csv(odds_dir / "race_targets.csv"),
        "status": _safe_read_csv(odds_dir / "race_status.csv"),
        "failed": _safe_read_csv(odds_dir / "failed_races.csv"),
        "odds": _safe_read_csv(odds_dir / "trifecta_odds.csv"),
        "fetch_report": _safe_read_json(odds_dir / "fetch_report.json"),
        "odds_dir": odds_dir,
    }


def _classify_odds_status(
    row: pd.Series,
    *,
    target_ids: set[str],
    odds_ids: set[str],
    status_map: dict[str, dict[str, Any]],
    failed_ids: set[str],
    odds_df: pd.DataFrame,
) -> tuple[str, str]:
    race_id = _normalize_text(row.get("レースID", row.get("race_id", "")))
    odds_taken = _normalize_text(row.get("オッズ取得", row.get("odds_status", "")))
    if odds_taken in {"取得済み", "real", "real_live", "real_odds_available"} and race_id in odds_ids:
        return "odds_available", ""

    if race_id not in target_ids:
        return "odds_race_id_mismatch", "odds_race_id_mismatch"

    status_row = status_map.get(race_id, {})
    fetch_status = _normalize_text(status_row.get("fetch_status", "")).lower()
    failed_reason = _normalize_text(status_row.get("failed_reason", "")).lower()

    if fetch_status == "pending_unpublished" or "pending_unpublished" in failed_reason or "unpublished" in failed_reason:
        return "odds_unpublished", "pending_unpublished"

    if race_id in failed_ids or fetch_status == "failed" or "fetch_failed" in failed_reason or "failed" in failed_reason:
        return "odds_fetch_failed", failed_reason or fetch_status or "failed"

    if race_id in odds_ids:
        race_rows = odds_df[odds_df["race_id"].astype(str).eq(race_id)].copy()
        if race_rows.empty:
            return "odds_unknown", "odds_rows_missing"
        odds_status_values = race_rows.get("odds_status", pd.Series(dtype=object)).fillna("").astype(str).str.lower()
        odds_values = pd.to_numeric(race_rows.get("odds", pd.Series(dtype=float)), errors="coerce")
        if len(race_rows) < 120 or odds_values.isna().any() or odds_status_values.ne("ok").any():
            return "odds_parse_failed", "odds_rows_incomplete_or_invalid"
        return "odds_available", ""

    return "odds_unknown", "no_matching_target_or_odds_row"


def build_demo_odds_pipeline_diagnostics(target_date: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    report_dir = DEMO_REPORT_DIR / target_date.isoformat()
    predictions_path = report_dir / "demo_predictions.csv"
    predictions_df = _safe_read_csv(predictions_path)
    if predictions_df.empty:
        raise FileNotFoundError(f"demo predictions not found: {predictions_path}")

    odds_context = _load_odds_context(target_date)
    targets_df = odds_context["targets"]
    status_df = odds_context["status"]
    failed_df = odds_context["failed"]
    odds_df = odds_context["odds"]
    fetch_report = odds_context["fetch_report"]

    target_ids = set(targets_df.get("race_id", pd.Series(dtype=object)).fillna("").astype(str).tolist())
    odds_ids = set(odds_df.get("race_id", pd.Series(dtype=object)).fillna("").astype(str).tolist())
    failed_ids = set(failed_df.get("race_id", pd.Series(dtype=object)).fillna("").astype(str).tolist())
    status_map = {
        str(row.get("race_id", "")): row
        for row in status_df.to_dict(orient="records")
        if str(row.get("race_id", "")).strip()
    }

    work = predictions_df.copy()
    if "レースID" not in work.columns:
        raise ValueError("demo predictions missing レースID column")
    if "quality_decision" not in work.columns:
        work["quality_decision"] = work.get("判定", pd.Series("", index=work.index)).astype(str)
    if "final_decision" not in work.columns:
        work["final_decision"] = work.get("判定", pd.Series("", index=work.index)).astype(str)
    if "オッズ取得" not in work.columns:
        work["オッズ取得"] = work.get("odds_status", pd.Series("", index=work.index)).astype(str)

    rows: list[dict[str, Any]] = []
    phase_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, row in work.iterrows():
        race_id = _normalize_text(row.get("レースID"))
        race_label = _build_race_label(row)
        race_no = row.get("レース番号", row.get("race_no", 0))
        source_phase = _infer_source_phase(race_no)
        odds_status, missing_reason = _classify_odds_status(
            row,
            target_ids=target_ids,
            odds_ids=odds_ids,
            status_map=status_map,
            failed_ids=failed_ids,
            odds_df=odds_df,
        )
        record = {
            "race_id": race_id,
            "race_label": race_label,
            "quality_decision": _normalize_text(row.get("quality_decision", "")),
            "final_decision": _normalize_text(row.get("final_decision", "")),
            "odds_status": odds_status,
            "missing_reason": missing_reason,
            "source_phase": source_phase,
        }
        rows.append(record)
        phase_rows[source_phase].append(record)

    all_df = pd.DataFrame(rows)
    missing_df = all_df[all_df["odds_status"].ne("odds_available")].copy()
    missing_df = missing_df.sort_values(["source_phase", "race_id"]).reset_index(drop=True)

    missing_reason_counts = Counter(missing_df["missing_reason"].astype(str).replace("", "odds_unknown").tolist())
    missing_reason_counts = {key: int(value) for key, value in missing_reason_counts.items()}
    phase_breakdown: dict[str, Any] = {}
    for phase in ["morning", "late", "final"]:
        phase_df = pd.DataFrame(phase_rows.get(phase, []))
        if phase_df.empty:
            phase_breakdown[phase] = {
                "total_races": 0,
                "odds_available_count": 0,
                "missing_odds_count": 0,
                "missing_reason_counts": {},
            }
            continue
        phase_missing = phase_df[phase_df["odds_status"].ne("odds_available")].copy()
        phase_breakdown[phase] = {
            "total_races": int(len(phase_df)),
            "odds_available_count": int((phase_df["odds_status"] == "odds_available").sum()),
            "missing_odds_count": int(len(phase_missing)),
            "missing_reason_counts": {
                key: int(value) for key, value in Counter(phase_missing["missing_reason"].astype(str).replace("", "odds_unknown").tolist()).items()
            },
        }

    buy_candidate_missing = int(
        ((missing_df["quality_decision"] == "buy_candidate") | missing_df["final_decision"].str.startswith("BUY")).sum()
    )
    watch_candidate_missing = int(
        ((missing_df["quality_decision"] == "watch_candidate") | missing_df["final_decision"].str.startswith("WATCH")).sum()
    )

    summary = {
        "target_date": target_date.isoformat(),
        "total_races": int(len(all_df)),
        "odds_available_count": int((all_df["odds_status"] == "odds_available").sum()),
        "missing_odds_count": int(len(missing_df)),
        "missing_reason_counts": missing_reason_counts,
        "phase_breakdown": phase_breakdown,
        "buy_candidate_missing_odds_count": buy_candidate_missing,
        "watch_candidate_missing_odds_count": watch_candidate_missing,
        "notes": [
            "source_phase は race_number を 1-4=morning, 5-8=late, 9-12=final で推定したものです。",
            "missing_reason は odds_targets / race_status / failed_races / trifecta_odds の突き合わせ結果です。",
            "BUY候補（未取得）1件を優先して確認できるようにしています。",
        ],
        "odds_context": {
            "targets_rows": int(len(targets_df)),
            "status_rows": int(len(status_df)),
            "failed_rows": int(len(failed_df)),
            "odds_rows": int(len(odds_df)),
            "fetch_report_status": _normalize_text(fetch_report.get("status", "")),
            "fetch_report_generated_at": _normalize_text(fetch_report.get("generated_at", "")),
        },
    }

    return missing_df, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose demo odds pipeline availability.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD")
    args = parser.parse_args()

    target_date = _parse_date(args.date)
    report_dir = DEMO_REPORT_DIR / target_date.isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / "demo_odds_missing_cases.csv"
    json_path = report_dir / "demo_odds_pipeline_diagnostics.json"

    missing_df, summary = build_demo_odds_pipeline_diagnostics(target_date)
    missing_df.to_csv(csv_path, index=False, encoding="utf-8")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"csv": str(csv_path), "json": str(json_path), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
