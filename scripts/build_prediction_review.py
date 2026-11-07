from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.prediction_sheet import latest_prediction_date, resolve_consensus_sheet, resolve_prediction_sheet

REPORTS_PREDICTIONS_ROOT = ROOT / "reports" / "predictions"
REPORTS_DAILY_ROOT = ROOT / "reports" / "daily"
REPORTS_CONSENSUS_ROOT = ROOT / "reports" / "consensus"
HISTORICAL_PATH = ROOT / "data" / "processed" / "historical_races.csv"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _canonical_race_id(date_value: Any, jcd_value: Any, race_no_value: Any) -> str:
    date_dt = pd.to_datetime(date_value, errors="coerce")
    jcd = pd.to_numeric(pd.Series([jcd_value]), errors="coerce").iloc[0]
    race_no = pd.to_numeric(pd.Series([race_no_value]), errors="coerce").iloc[0]
    if pd.isna(date_dt) or pd.isna(jcd) or pd.isna(race_no):
        return ""
    return f"{date_dt.strftime('%Y%m%d')}-{int(jcd):02d}-{int(race_no):02d}"


def _truth_map_for_date(date_text: str) -> dict[str, str]:
    hist = _read_csv(HISTORICAL_PATH)
    if hist.empty:
        return {}
    required = {"date", "jcd", "race_no", "lane", "finish_position"}
    if not required.issubset(hist.columns):
        return {}
    day = hist[pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d") == date_text].copy()
    if day.empty:
        return {}
    day["finish_position"] = pd.to_numeric(day["finish_position"], errors="coerce")
    day["lane"] = pd.to_numeric(day["lane"], errors="coerce")
    day["race_id_key"] = [
        _canonical_race_id(row.get("date"), row.get("jcd"), row.get("race_no"))
        for _, row in day.iterrows()
    ]
    top3 = day[day["finish_position"].isin([1, 2, 3]) & day["race_id_key"].astype(bool)].copy()
    if top3.empty:
        return {}
    top3 = top3.sort_values(["race_id_key", "finish_position"])
    grouped = top3.groupby("race_id_key")["lane"].apply(
        lambda s: "-".join(str(int(v)) for v in s.tolist()) if len(s) == 3 else ""
    )
    return {str(k): str(v) for k, v in grouped.items() if str(v)}


def _candidate_output_dir(date_text: str) -> Path:
    return REPORTS_PREDICTIONS_ROOT / date_text


def _review_path(date_text: str) -> Path:
    return _candidate_output_dir(date_text) / "prediction_review.json"


def _review_md_path(date_text: str) -> Path:
    return _candidate_output_dir(date_text) / "prediction_review.md"


def _consensus_review_dir(date_text: str) -> Path:
    return REPORTS_CONSENSUS_ROOT / date_text


def _consensus_review_json_path(date_text: str) -> Path:
    return _consensus_review_dir(date_text) / "consensus_review.json"


def _consensus_review_md_path(date_text: str) -> Path:
    return _consensus_review_dir(date_text) / "consensus_review.md"


def _sheet_path(date_text: str) -> Path:
    return _candidate_output_dir(date_text) / "prediction_sheet.csv"


def _daily_summary_path(date_text: str) -> Path:
    return REPORTS_DAILY_ROOT / date_text / "daily_summary.json"


def _daily_eval_path(date_text: str) -> Path:
    return REPORTS_DAILY_ROOT / date_text / "daily_evaluation_race_results.csv"


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _safe_float(value: Any) -> float | None:
    try:
        num = pd.to_numeric(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(num):
        return None
    return float(num)


def _serialize_metric(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _top_rows(df: pd.DataFrame, limit: int = 10) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        result_available = bool(row.get("result_available")) if not pd.isna(row.get("result_available")) else False
        hit = bool(row.get("hit")) if not pd.isna(row.get("hit")) else False
        rows.append(
            {
                "raceId": row.get("race_id", ""),
                "venue": row.get("venue", ""),
                "raceNo": int(row.get("race_no") or 0) if pd.notna(row.get("race_no")) else 0,
                "combo": row.get("combo", ""),
                "finalDecision": row.get("final_decision", ""),
                "paperDecision": row.get("paper_decision", ""),
                "stopReason": row.get("stop_reason", ""),
                "oddsStatus": row.get("odds_status", ""),
                "approxProb": _safe_float(row.get("approx_prob")),
                "realOdds": _safe_float(row.get("real_odds")),
                "expectedValue": _safe_float(row.get("expected_value")),
                "resultAvailable": result_available,
                "hit": hit,
                "resultStatus": "hit" if result_available and hit else ("miss" if result_available else "unconfirmed"),
                "pnl": _safe_float(row.get("pnl")),
                "reason": row.get("reason", ""),
                "consensusGrade": row.get("consensus_grade", "NONE"),
                "consensusScore": int(row.get("consensus_score") or 0) if pd.notna(row.get("consensus_score")) else 0,
                "matchedSources": _listify(row.get("matched_sources")),
                "exactMatchSources": _listify(row.get("exact_match_sources")),
                "axisMatchSources": _listify(row.get("axis_match_sources")),
                "boxOverlapSources": _listify(row.get("box_overlap_sources")),
                "consensusReason": row.get("consensus_reason", ""),
            }
        )
    return rows


def _markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_候補なし_"
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body: list[str] = []
    for row in rows:
        cells = []
        for key, _ in columns:
            cells.append(_serialize_metric(row.get(key)))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body])


def _group_summary(df: pd.DataFrame, label: str) -> dict[str, Any]:
    group = df[df["paper_decision"] == label] if not df.empty else pd.DataFrame()
    if group.empty:
        return {
            "count": 0,
            "resultAvailableCount": 0,
            "hitCount": 0,
            "pnlSum": 0.0,
            "avgExpectedValue": None,
            "topRows": [],
        }
    available = group["result_available"].fillna(False).astype(bool)
    hits = group["hit"].fillna(False).astype(bool)
    pnl = pd.to_numeric(group["pnl"], errors="coerce")
    ev = pd.to_numeric(group["expected_value"], errors="coerce")
    ordered = group.sort_values(by=["expected_value", "approx_prob"], ascending=[False, False], na_position="last")
    return {
        "count": int(len(group)),
        "resultAvailableCount": int(available.sum()),
        "hitCount": int(hits.sum()),
        "pnlSum": float(pnl.fillna(0.0).sum()),
        "avgExpectedValue": None if ev.dropna().empty else float(ev.dropna().mean()),
        "topRows": _top_rows(ordered, 5),
    }


def _return_rate(group: pd.DataFrame) -> float | None:
    if group.empty:
        return None
    available = group["result_available"].fillna(False).astype(bool)
    pnl = pd.to_numeric(group["pnl"], errors="coerce")
    if "stake_amount" in group.columns:
        stake = pd.to_numeric(group["stake_amount"], errors="coerce").fillna(0.0)
        settled_mask = available & pnl.notna() & stake.gt(0)
    else:
        settled_mask = available & pnl.notna()
    settled = int(settled_mask.sum())
    if settled <= 0:
        return None
    pnl = pnl.fillna(0.0)
    gross_return = float((pnl[settled_mask] + 1.0).sum())
    return gross_return / settled * 100.0


def _consensus_grade_summary(df: pd.DataFrame, grade: str) -> dict[str, Any]:
    group = df[df["consensus_grade"] == grade] if not df.empty else pd.DataFrame()
    available = group["result_available"].fillna(False).astype(bool) if not group.empty else pd.Series(dtype=bool)
    hits = group["hit"].fillna(False).astype(bool) if not group.empty else pd.Series(dtype=bool)
    ordered = group.sort_values(by=["consensus_score", "expected_value"], ascending=[False, False], na_position="last") if not group.empty else group
    return {
        "count": int(len(group)),
        "resultAvailableCount": int(available.sum()) if not group.empty else 0,
        "hitCount": int(hits.sum()) if not group.empty else 0,
        "resultPendingCount": int((~available).sum()) if not group.empty else 0,
        "pnlSum": float(pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0).sum()) if not group.empty else 0.0,
        "returnRate": _return_rate(group),
        "topRows": _top_rows(ordered, 10),
    }


def _cross_summary(df: pd.DataFrame, row_key: str, col_key: str) -> dict[str, dict[str, int]]:
    if df.empty or row_key not in df.columns or col_key not in df.columns:
        return {}
    table = pd.crosstab(df[row_key].fillna("(empty)").replace("", "(empty)"), df[col_key].fillna("NONE").replace("", "NONE"))
    out: dict[str, dict[str, int]] = {}
    for idx, row in table.iterrows():
        out[str(idx)] = {str(col): int(row[col]) for col in table.columns}
    return out


def _external_source_tendency(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        for source in row.get("matchedSources") or []:
            bucket = summary.setdefault(source, {"matchedCount": 0, "exactCount": 0, "axisCount": 0, "boxCount": 0})
            bucket["matchedCount"] += 1
        for source in row.get("exactMatchSources") or []:
            summary.setdefault(source, {"matchedCount": 0, "exactCount": 0, "axisCount": 0, "boxCount": 0})["exactCount"] += 1
        for source in row.get("axisMatchSources") or []:
            summary.setdefault(source, {"matchedCount": 0, "exactCount": 0, "axisCount": 0, "boxCount": 0})["axisCount"] += 1
        for source in row.get("boxOverlapSources") or []:
            summary.setdefault(source, {"matchedCount": 0, "exactCount": 0, "axisCount": 0, "boxCount": 0})["boxCount"] += 1
    return summary


def _consensus_payload(date_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = resolve_consensus_sheet(date_text)
    if str(resolved.get("status") or "") != "ok":
        return resolved, {}
    data = resolved.get("data")
    return resolved, data if isinstance(data, dict) else {}


def _consensus_frame(date_text: str) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    resolved, payload = _consensus_payload(date_text)
    rows = payload.get("candidates") or []
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(), resolved, payload
    frame = pd.DataFrame(rows).copy()
    if "ai_combo" in frame.columns:
        if "combo" in frame.columns:
            frame["combo"] = frame["ai_combo"].where(frame["ai_combo"].notna(), frame["combo"])
        else:
            frame["combo"] = frame["ai_combo"]
    if "race_id" in frame.columns:
        frame["race_id"] = frame["race_id"].fillna("").astype(str)
    if "combo" in frame.columns:
        frame["combo"] = frame["combo"].fillna("").astype(str)
    for col in [
        "matched_sources",
        "exact_match_sources",
        "axis_match_sources",
        "box_overlap_sources",
    ]:
        if col in frame.columns:
            frame[col] = frame[col].apply(_listify)
    keep = [
        "race_id",
        "combo",
        "consensus_grade",
        "consensus_score",
        "matched_sources",
        "exact_match_sources",
        "axis_match_sources",
        "box_overlap_sources",
        "consensus_reason",
    ]
    existing = [col for col in keep if col in frame.columns]
    return frame[existing], resolved, payload


def _write_consensus_review(
    source_date: str,
    review_payload: dict[str, Any],
    consensus_meta: dict[str, Any],
    consensus_payload: dict[str, Any],
) -> None:
    review_dir = _consensus_review_dir(source_date)
    review_dir.mkdir(parents=True, exist_ok=True)
    review_json_path = _consensus_review_json_path(source_date)
    review_md_path = _consensus_review_md_path(source_date)

    summary = review_payload.get("summary", {})
    consensus_summary = review_payload.get("consensusSummary", {})
    b_plus_candidates = review_payload.get("consensusBPlusCandidates", [])
    b_plus_results = review_payload.get("consensusBPlusResults", [])
    hit_candidates = [row for row in b_plus_results if row.get("resultStatus") == "hit"]
    near_candidates = [row for row in b_plus_results if row.get("resultStatus") != "hit"]
    external_tendency = review_payload.get("externalSourceTendency", {})

    payload = {
        "status": review_payload.get("status"),
        "date": source_date,
        "consensusGradeCounts": consensus_summary.get("gradeCounts", {}),
        "loadedExternalSources": consensus_payload.get("loadedExternalSources", []),
        "missingExternalSources": consensus_payload.get("missingExternalSources", []),
        "unavailableExternalSources": consensus_payload.get("unavailableExternalSources", []),
        "consensusBPlusCandidates": b_plus_candidates,
        "consensusBPlusResults": b_plus_results,
        "hitCandidates": hit_candidates,
        "nearMissCandidates": near_candidates,
        "externalSourceTendency": external_tendency,
        "paperDecisionByConsensus": review_payload.get("paperDecisionByConsensus", {}),
        "stopReasonByConsensus": review_payload.get("stopReasonByConsensus", {}),
        "buyWatchPaperRelation": {
            "BUY": review_payload.get("groups", {}).get("BUY", {}),
            "WATCH": review_payload.get("groups", {}).get("WATCH", {}),
            "PAPER": review_payload.get("groups", {}).get("PAPER", {}),
        },
        "nextHypotheses": review_payload.get("nextHypotheses", []),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    review_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# Consensus Review {source_date}",
        "",
        f"- status: {review_payload.get('status')}",
        f"- consensus A/B/C/NONE: {consensus_summary.get('gradeCounts', {})}",
        f"- loadedExternalSources: {', '.join(consensus_payload.get('loadedExternalSources', [])) or '-'}",
        f"- missingExternalSources: {', '.join(consensus_payload.get('missingExternalSources', [])) or '-'}",
        f"- unavailableExternalSources: {', '.join(consensus_payload.get('unavailableExternalSources', [])) or '-'}",
        f"- BUY判定未変更: true",
        "",
        "## B以上の候補",
        _markdown_table(b_plus_candidates, [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("paperDecision", "paper"), ("consensusGrade", "grade"), ("consensusScore", "score"), ("resultStatus", "結果")]),
        "",
        "## B以上の結果",
        _markdown_table(b_plus_results, [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("consensusGrade", "grade"), ("resultStatus", "結果"), ("pnl", "pnl"), ("consensusReason", "理由")]),
        "",
        "## 的中候補",
        _markdown_table(hit_candidates, [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("consensusGrade", "grade"), ("pnl", "pnl")]),
        "",
        "## 惜しかった候補",
        _markdown_table(near_candidates, [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("consensusGrade", "grade"), ("resultStatus", "結果"), ("stopReason", "stop_reason")]),
        "",
        "## 外部ソース別の一致傾向",
    ]
    if external_tendency:
        for source, stats in external_tendency.items():
            md_lines.append(f"- {source}: matched={stats.get('matchedCount', 0)} exact={stats.get('exactCount', 0)} axis={stats.get('axisCount', 0)} box={stats.get('boxCount', 0)}")
    else:
        md_lines.append("- なし")
    md_lines.extend(["", "## BUY/WATCH/PAPERとの関係"])
    for decision, stats in payload["buyWatchPaperRelation"].items():
        md_lines.append(f"- {decision}: count={stats.get('count', 0)} resultAvailable={stats.get('resultAvailableCount', 0)} hit={stats.get('hitCount', 0)}")
    md_lines.extend(["", "## 次に見るべき仮説"])
    for item in payload["nextHypotheses"] or ["結果未取得のため仮説保留"]:
        md_lines.append(f"- {item}")
    review_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def build_prediction_review(date_text: str | None = None) -> dict[str, Any]:
    resolved = resolve_prediction_sheet(date_text)
    source_date = str(resolved.get("sourceDate") or "")
    requested_date = str(resolved.get("requestedDate") or date_text or "")
    fallback_reason = str(resolved.get("fallbackReason") or "")
    if not source_date:
        return {
            "status": "missing",
            "requestedDate": requested_date,
            "sourceDate": "",
            "fallbackReason": fallback_reason,
            "dailySummaryExists": False,
            "dailySummaryStatus": "missing",
            "predictionSheetExists": False,
            "predictionReviewExists": False,
            "summary": {},
            "groups": {},
            "topCandidates": [],
            "hitCandidates": [],
            "nearMissCandidates": [],
            "stopReasonCounts": {},
            "oddsStatusCounts": {},
            "consensusSummary": {},
            "nextHypotheses": [],
            "files": {},
        }

    sheet_path = _sheet_path(source_date)
    daily_summary_path = _daily_summary_path(source_date)
    daily_eval_path = _daily_eval_path(source_date)
    review_dir = _candidate_output_dir(source_date)
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = _review_path(source_date)
    review_md_path = _review_md_path(source_date)

    sheet_df = _read_csv(sheet_path)
    daily_summary = _read_json(daily_summary_path)
    eval_df = _read_csv(daily_eval_path)
    consensus_df, consensus_meta, consensus_payload = _consensus_frame(source_date)

    daily_summary_exists = daily_summary_path.exists()
    daily_summary_status = str(daily_summary.get("results_status") or ("missing" if not daily_summary_exists else "available"))
    prediction_sheet_exists = sheet_path.exists()
    prediction_review_exists = review_path.exists()

    if sheet_df.empty:
        return {
            "status": "missing",
            "requestedDate": requested_date,
            "sourceDate": source_date,
            "fallbackReason": fallback_reason,
            "dailySummaryExists": daily_summary_exists,
            "dailySummaryStatus": daily_summary_status,
            "predictionSheetExists": prediction_sheet_exists,
            "predictionReviewExists": prediction_review_exists,
            "summary": {},
            "groups": {},
            "topCandidates": [],
            "hitCandidates": [],
            "nearMissCandidates": [],
            "stopReasonCounts": {},
            "oddsStatusCounts": {},
            "consensusSummary": {},
            "nextHypotheses": [],
            "files": {},
        }

    sheet_df = sheet_df.copy()
    eval_df = eval_df.copy()
    for col in ["race_id", "venue", "combo", "final_decision", "paper_decision", "stop_reason", "odds_status", "reason"]:
        if col in sheet_df.columns:
            sheet_df[col] = sheet_df[col].fillna("").astype(str)
    if {"date", "jcd", "race_no"}.issubset(sheet_df.columns):
        sheet_df["race_id_key"] = [
            _canonical_race_id(row.get("date"), row.get("jcd"), row.get("race_no"))
            for _, row in sheet_df.iterrows()
        ]
    else:
        sheet_df["race_id_key"] = sheet_df.get("race_id", "").fillna("").astype(str)

    if not consensus_df.empty:
        sheet_df = sheet_df.merge(consensus_df, on=["race_id", "combo"], how="left")
    for col, default in [
        ("consensus_grade", "NONE"),
        ("consensus_score", 0),
        ("matched_sources", []),
        ("exact_match_sources", []),
        ("axis_match_sources", []),
        ("box_overlap_sources", []),
        ("consensus_reason", "外部予想なし"),
    ]:
        if col not in sheet_df.columns:
            sheet_df[col] = [default for _ in range(len(sheet_df))]
        elif isinstance(default, list):
            sheet_df[col] = sheet_df[col].apply(_listify)
        else:
            sheet_df[col] = sheet_df[col].fillna(default)

    if not eval_df.empty and "race_id" in eval_df.columns:
        eval_df["race_id"] = eval_df["race_id"].fillna("").astype(str)
        merged = sheet_df.merge(
            eval_df[[c for c in ["race_id", "result_available", "hit", "pnl", "settled_odds", "actual_trifecta", "date_result", "stake_amount", "payout_amount"] if c in eval_df.columns]],
            on="race_id",
            how="left",
        )
        results_available = bool(daily_summary.get("results_available", False) or not eval_df.empty)
        status = "ok" if daily_summary_status in {"ok", "available", "settled"} and results_available else "result_data_missing"
    else:
        merged = sheet_df.copy()
        merged["result_available"] = False
        merged["hit"] = False
        merged["pnl"] = None
        results_available = bool(daily_summary.get("results_available", False))
        status = "ok" if daily_summary_status in {"ok", "available", "settled"} and results_available else "result_data_missing"

    merged["result_available"] = merged.get("result_available", False).fillna(False).astype(bool)
    merged["hit"] = merged.get("hit", False).fillna(False).astype(bool)
    merged["pnl"] = pd.to_numeric(merged.get("pnl"), errors="coerce")
    merged["expected_value"] = pd.to_numeric(merged.get("expected_value"), errors="coerce")
    merged["approx_prob"] = pd.to_numeric(merged.get("approx_prob"), errors="coerce")
    merged["consensus_score"] = pd.to_numeric(merged.get("consensus_score"), errors="coerce").fillna(0).astype(int)
    if "actual_trifecta" in merged.columns:
        merged["actual_trifecta"] = merged["actual_trifecta"].fillna("").astype(object)
    else:
        merged["actual_trifecta"] = pd.Series([""] * len(merged), index=merged.index, dtype=object)
    if "date_result" in merged.columns:
        merged["date_result"] = merged["date_result"].fillna("").astype(object)
    else:
        merged["date_result"] = pd.Series([""] * len(merged), index=merged.index, dtype=object)
    truth_map = _truth_map_for_date(source_date)
    if truth_map:
        fallback_actual = merged["race_id_key"].map(truth_map).fillna("").astype(object)
        missing_actual = merged["actual_trifecta"].astype(str).eq("") & fallback_actual.astype(str).ne("")
        merged.loc[missing_actual, "actual_trifecta"] = fallback_actual[missing_actual]
        merged.loc[missing_actual, "date_result"] = source_date
        merged.loc[missing_actual, "result_available"] = True
        merged.loc[missing_actual, "hit"] = (
            merged.loc[missing_actual, "combo"].fillna("").astype(str)
            == merged.loc[missing_actual, "actual_trifecta"].fillna("").astype(str)
        )
    merged["resultStatus"] = [
        "hit" if bool(result_available) and bool(hit) else ("miss" if bool(result_available) else "unconfirmed")
        for result_available, hit in zip(merged["result_available"].fillna(False), merged["hit"].fillna(False))
    ]
    merged["sort_ev"] = merged["expected_value"].fillna(float("-inf"))
    merged["sort_prob"] = merged["approx_prob"].fillna(float("-inf"))

    top_candidates_df = merged[merged["paper_decision"].isin(["BUY", "WATCH", "PAPER"])].sort_values(by=["sort_ev", "sort_prob"], ascending=[False, False], na_position="last")
    hit_candidates_df = merged[merged["resultStatus"] == "hit"].sort_values(by=["sort_ev", "sort_prob"], ascending=[False, False], na_position="last")
    near_miss_df = merged[(merged["resultStatus"] == "miss") & (merged["paper_decision"].isin(["BUY", "WATCH", "PAPER"]))].sort_values(by=["sort_ev", "sort_prob"], ascending=[False, False], na_position="last")
    b_plus_df = merged[merged["consensus_grade"].isin(["A", "B"])].sort_values(by=["consensus_score", "sort_ev"], ascending=[False, False], na_position="last")

    groups = {label: _group_summary(merged, label) for label in ["BUY", "WATCH", "PAPER", "SKIP"]}
    stop_reason_counts = merged["stop_reason"].fillna("").replace("", "(empty)").value_counts().to_dict() if "stop_reason" in merged.columns else {}
    odds_status_counts = merged["odds_status"].fillna("").replace("", "(empty)").value_counts().to_dict() if "odds_status" in merged.columns else {}

    summary = {
        "buyCount": groups["BUY"]["count"],
        "watchCount": groups["WATCH"]["count"],
        "paperCount": groups["PAPER"]["count"],
        "skipCount": groups["SKIP"]["count"],
        "topStopReason": str(daily_summary.get("main_rejection_reason") or (next(iter(stop_reason_counts)) if stop_reason_counts else "")),
        "latestCompleteOpsDate": str(daily_summary.get("latest_complete_ops_date") or ""),
        "resultsStatus": daily_summary_status,
        "resultsAvailable": bool(daily_summary.get("results_available", False)),
    }

    consensus_grade_counts = merged["consensus_grade"].fillna("NONE").replace("", "NONE").value_counts().to_dict()
    consensus_by_grade = {grade: _consensus_grade_summary(merged, grade) for grade in ["A", "B", "C", "NONE"]}
    paper_by_consensus = _cross_summary(merged, "paper_decision", "consensus_grade")
    stop_by_consensus = _cross_summary(merged, "stop_reason", "consensus_grade")

    top_candidates = _top_rows(top_candidates_df, 10)
    hit_candidates = _top_rows(hit_candidates_df, 10)
    near_miss_candidates = _top_rows(near_miss_df, 10)
    b_plus_candidates = _top_rows(b_plus_df, 20)

    next_hypotheses: list[str] = []
    for item in daily_summary.get("improvement_candidates_top3") or []:
        if isinstance(item, dict) and item.get("candidate"):
            next_hypotheses.append(str(item["candidate"]))
    if consensus_grade_counts.get("B", 0):
        next_hypotheses.append("consensus B の3件が unconfirmed のままなら、結果取得後に A/B/C 別の回収率を比較")
    if not next_hypotheses and summary["topStopReason"]:
        next_hypotheses.append(f"stop_reason={summary['topStopReason']} の解消余地を確認")

    external_tendency = _external_source_tendency(b_plus_candidates)
    consensus_summary = {
        "gradeCounts": {str(k): int(v) for k, v in consensus_grade_counts.items()},
        "loadedExternalSources": consensus_payload.get("loadedExternalSources", []),
        "missingExternalSources": consensus_payload.get("missingExternalSources", []),
        "unavailableExternalSources": consensus_payload.get("unavailableExternalSources", []),
        "byGrade": consensus_by_grade,
    }

    review_payload = {
        "status": status,
        "requestedDate": requested_date,
        "sourceDate": source_date,
        "fallbackReason": fallback_reason,
        "dailySummaryExists": daily_summary_exists,
        "dailySummaryStatus": daily_summary_status,
        "predictionSheetExists": prediction_sheet_exists,
        "predictionReviewExists": True,
        "summary": summary,
        "groups": groups,
        "topCandidates": top_candidates,
        "hitCandidates": hit_candidates,
        "nearMissCandidates": near_miss_candidates,
        "stopReasonCounts": stop_reason_counts,
        "oddsStatusCounts": odds_status_counts,
        "consensusSummary": consensus_summary,
        "consensusBPlusCandidates": b_plus_candidates,
        "consensusBPlusResults": b_plus_candidates,
        "stopReasonByConsensus": stop_by_consensus,
        "paperDecisionByConsensus": paper_by_consensus,
        "externalSourceTendency": external_tendency,
        "nextHypotheses": next_hypotheses,
        "files": {
            "predictionSheet": str(sheet_path),
            "dailySummary": str(daily_summary_path) if daily_summary_exists else "",
            "dailyEvaluation": str(daily_eval_path) if daily_eval_path.exists() else "",
            "predictionReview": str(review_path),
            "consensusReview": str(_consensus_review_json_path(source_date)),
        },
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }

    md_lines = [
        f"# Prediction Review {source_date}",
        "",
        f"- status: {review_payload['status']}",
        f"- requestedDate: {requested_date or '-'}",
        f"- sourceDate: {source_date or '-'}",
        f"- fallbackReason: {fallback_reason or '-'}",
        f"- dailySummaryStatus: {daily_summary_status}",
        f"- resultsAvailable: {bool(daily_summary.get('results_available', False))}",
        "",
        "## Summary",
        f"- BUY: {summary['buyCount']}",
        f"- WATCH: {summary['watchCount']}",
        f"- PAPER: {summary['paperCount']}",
        f"- SKIP: {summary['skipCount']}",
        f"- topStopReason: {summary['topStopReason'] or '-'}",
        "",
        "## Consensus Summary",
        f"- gradeCounts: {consensus_summary['gradeCounts']}",
        f"- loadedExternalSources: {', '.join(consensus_summary['loadedExternalSources']) or '-'}",
        f"- missingExternalSources: {', '.join(consensus_summary['missingExternalSources']) or '-'}",
        f"- unavailableExternalSources: {', '.join(consensus_summary['unavailableExternalSources']) or '-'}",
        "",
        "## Consensus B以上の候補",
        _markdown_table(b_plus_candidates, [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("paperDecision", "paper"), ("consensusGrade", "grade"), ("consensusScore", "score"), ("resultStatus", "結果")]),
        "",
        "## BUY候補の結果",
        _markdown_table(groups["BUY"]["topRows"], [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("expectedValue", "expected_value"), ("pnl", "pnl"), ("resultStatus", "結果")]),
        "",
        "## WATCH候補の結果",
        _markdown_table(groups["WATCH"]["topRows"], [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("expectedValue", "expected_value"), ("resultStatus", "結果"), ("reason", "reason")]),
        "",
        "## PAPER候補の結果",
        _markdown_table(groups["PAPER"]["topRows"], [("venue", "会場"), ("raceNo", "R"), ("combo", "買い目"), ("expectedValue", "expected_value"), ("resultStatus", "結果"), ("reason", "reason")]),
        "",
        "## stop_reason × consensusGrade",
    ]
    for key, bucket in stop_by_consensus.items():
        md_lines.append(f"- {key}: {bucket}")
    md_lines.extend(["", "## paperDecision × consensusGrade"])
    for key, bucket in paper_by_consensus.items():
        md_lines.append(f"- {key}: {bucket}")
    md_lines.extend(["", "## 次に検証すべき仮説"])
    for item in next_hypotheses or ["結果未取得のため追加仮説は保留"]:
        md_lines.append(f"- {item}")

    review_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    review_path.write_text(json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_consensus_review(source_date, review_payload, consensus_meta, consensus_payload)
    return review_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build prediction review artifacts")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    date_text = args.date
    if not date_text:
        resolved = resolve_prediction_sheet(None)
        date_text = str(resolved.get("sourceDate") or latest_prediction_date() or "")
    if not date_text:
        raise SystemExit("prediction sheet date could not be resolved")

    review = build_prediction_review(date_text)
    print(json.dumps({
        "status": review.get("status"),
        "requestedDate": review.get("requestedDate"),
        "sourceDate": review.get("sourceDate"),
        "fallbackReason": review.get("fallbackReason"),
        "files": review.get("files", {}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
