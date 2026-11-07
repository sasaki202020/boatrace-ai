from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS_PREDICTIONS = ROOT / "reports" / "predictions"
REPORTS_CONSENSUS = ROOT / "reports" / "consensus"
UI_ROOT = ROOT / "data" / "ui"
EXTERNAL_ROOT = ROOT / "data" / "external"
EXTERNAL_PREDICTIONS_ROOT = ROOT / "data" / "external_predictions"
REPO_AUDIT_ROOT = ROOT / "reports" / "repo_audit"

JCD_TO_VENUE = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川", "06": "浜名湖",
    "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国", "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門", "15": "丸亀", "16": "児島", "17": "宮島", "18": "徳山",
    "19": "下関", "20": "若松", "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}
VENUE_TO_JCD = {v: k for k, v in JCD_TO_VENUE.items()}
SOURCE_DIRS = {
    "official_expect": EXTERNAL_ROOT / "official_expect",
    "nikkan": EXTERNAL_ROOT / "nikkan",
    "ace_motors": EXTERNAL_ROOT / "ace_motors",
    "kyotei_ai_pro": EXTERNAL_ROOT / "kyotei_ai_pro",
    "asokabu": EXTERNAL_ROOT / "asokabu",
}
SOURCE_ALIASES = {
    "acemotorz": "ace_motors",
    "nihonkando": "kyotei_ai_pro",
    "nikkan": "nikkan",
    "asokabu": "asokabu",
    "official_expect": "official_expect",
    "teinavi": "teinavi",
    "simulator": "simulator",
}
EXPECTED_SOURCES = [
    "official_expect",
    "nikkan",
    "ace_motors",
    "kyotei_ai_pro",
    "asokabu",
    "teinavi",
    "simulator",
]


def normalize_date(value: str) -> str:
    text = str(value or "").strip().replace("/", "-")
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def compact_date(value: str) -> str:
    return re.sub(r"\D", "", normalize_date(value))[:8]


def normalize_jcd(value: Any, venue: Any = "") -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return text.zfill(2)
    venue_text = str(venue or "").strip()
    return VENUE_TO_JCD.get(venue_text, "")


def normalize_combo(value: Any) -> str:
    parts = re.findall(r"[1-6]", str(value or ""))
    return "-".join(parts[:3]) if len(parts) >= 3 else ""


def canonical_source_name(value: Any) -> str:
    raw = str(value or "").strip()
    return SOURCE_ALIASES.get(raw, raw)


def combo_axes(combo: str) -> tuple[str, str, str]:
    parts = combo.split("-") if combo else []
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def prediction_sheet_path(date_text: str) -> Path:
    normalized = normalize_date(date_text)
    ui_path = UI_ROOT / compact_date(normalized) / "prediction_sheet.json"
    if ui_path.exists():
        return ui_path
    return REPORTS_PREDICTIONS / normalized / "prediction_sheet.json"


def standard_external_row(source_name: str, row: dict[str, Any], fallback_date: str = "") -> dict[str, Any] | None:
    source_name = canonical_source_name(source_name)
    venue = row.get("venue") or row.get("venueName") or row.get("venue_name") or ""
    jcd = normalize_jcd(row.get("jcd") or row.get("venue_code") or row.get("venueCode"), venue)
    race_no_raw = row.get("race_no") or row.get("raceNo") or row.get("rno") or row.get("race") or ""
    race_no_match = re.search(r"\d+", str(race_no_raw))
    race_no = int(race_no_match.group(0)) if race_no_match else 0
    combo = normalize_combo(row.get("combo") or row.get("trifecta") or row.get("buy_combo") or row.get("prediction") or row.get("買い目") or row.get("3連単"))
    race_id = str(row.get("race_id") or row.get("raceId") or "").strip()
    date_value = normalize_date(str(row.get("date") or row.get("target_date") or fallback_date))
    if not jcd and race_id:
        parts = race_id.split("-")
        if len(parts) >= 3:
            jcd = normalize_jcd(parts[1])
            race_no = race_no or int(parts[-1])
    if not race_id and date_value and jcd and race_no:
        race_id = f"{compact_date(date_value)}-{jcd}-{race_no:02d}"
    if not combo or not jcd or not race_no:
        return None
    axis_1st, axis_2nd, axis_3rd = combo_axes(combo)
    return {
        "date": date_value,
        "source_name": source_name,
        "venue": JCD_TO_VENUE.get(jcd, str(venue or "")),
        "jcd": jcd,
        "race_no": race_no,
        "race_id": race_id,
        "rank": row.get("rank") or row.get("order") or "",
        "combo": combo,
        "axis_1st": axis_1st,
        "axis_2nd": axis_2nd,
        "axis_3rd": axis_3rd,
        "confidence": row.get("confidence") or row.get("score") or "",
        "source_url": row.get("sourceUrl") or row.get("source_url") or "",
        "fetched_at": row.get("fetchedAt") or row.get("fetched_at") or "",
    }


def load_external_prediction_files(date_text: str) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    compact = compact_date(date_text)
    loaded: list[dict[str, Any]] = []
    loaded_sources: set[str] = set()
    present_sources: set[str] = set()
    unavailable_sources: set[str] = set()

    for source_name, root in SOURCE_DIRS.items():
        source_dir = root / compact
        files: list[Path] = []
        if source_dir.exists():
            files = list(source_dir.glob("*.json")) + list(source_dir.glob("*.csv"))
        if not files:
            continue
        present_sources.add(source_name)
        for path in files:
            if path.suffix.lower() == ".csv":
                for row in read_csv_rows(path):
                    std = standard_external_row(source_name, row, date_text)
                    if std:
                        loaded.append(std)
                        loaded_sources.add(source_name)
                continue
            payload = load_json(path)
            rows = payload if isinstance(payload, list) else (payload.get("predictions") or payload.get("races") or payload.get("items") if isinstance(payload, dict) else [])
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                std = standard_external_row(source_name, row, date_text)
                if std:
                    loaded.append(std)
                    loaded_sources.add(source_name)

    saved_dir = EXTERNAL_PREDICTIONS_ROOT / compact
    if saved_dir.exists():
        for path in sorted(saved_dir.glob("*/*.json")):
            if path.parent.name == "sources" or not path.name.startswith("race_"):
                continue
            payload = load_json(path)
            if not isinstance(payload, dict):
                continue
            for source in payload.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_name = canonical_source_name(source.get("source") or source.get("name") or "external_predictions")
                present_sources.add(source_name)
                if str(source.get("status") or "").lower() != "ok" or not (source.get("predictions") or []):
                    unavailable_sources.add(source_name)
                for pred in source.get("predictions") or []:
                    row = dict(pred)
                    row.update({
                        "date": payload.get("date") or date_text,
                        "sourceUrl": source.get("sourceUrl"),
                        "fetchedAt": payload.get("generatedAt") or payload.get("savedAt"),
                        "jcd": payload.get("jcd"),
                        "venue": payload.get("venue"),
                        "raceNo": payload.get("raceNo"),
                        "raceId": f"{compact}-{payload.get('jcd')}-{int(payload.get('raceNo') or 0):02d}",
                    })
                    std = standard_external_row(source_name, row, date_text)
                    if std:
                        loaded.append(std)
                        loaded_sources.add(source_name)
    missing_sources = sorted(source for source in EXPECTED_SOURCES if source not in present_sources)
    unavailable = sorted(source for source in unavailable_sources if source not in loaded_sources)
    return loaded, sorted(loaded_sources), missing_sources, unavailable


def candidate_key(candidate: dict[str, Any], date_text: str) -> tuple[str, int]:
    jcd = normalize_jcd(candidate.get("jcd"), candidate.get("venue"))
    race_no = int(candidate.get("raceNo") or candidate.get("race_no") or 0)
    return jcd, race_no


def consensus_for_candidate(candidate: dict[str, Any], externals: list[dict[str, Any]], date_text: str) -> dict[str, Any]:
    ai_combo = normalize_combo(candidate.get("combo") or candidate.get("recommended_trifecta"))
    ai_parts = set(ai_combo.split("-")) if ai_combo else set()
    ai_axis_1st, ai_axis_2nd, _ = combo_axes(ai_combo)
    exact_sources: list[str] = []
    axis_sources: list[str] = []
    first_second_sources: list[str] = []
    box_sources: list[str] = []
    first_axis_counter: Counter[str] = Counter()
    external_combos: list[dict[str, str]] = []
    for item in externals:
        source = item["source_name"]
        combo = item["combo"]
        external_combos.append({"source": source, "combo": combo})
        if combo == ai_combo:
            exact_sources.append(source)
        if ai_axis_1st and item.get("axis_1st") == ai_axis_1st:
            axis_sources.append(source)
        if ai_axis_1st and ai_axis_2nd and item.get("axis_1st") == ai_axis_1st and item.get("axis_2nd") == ai_axis_2nd:
            first_second_sources.append(source)
        if ai_parts and len(ai_parts.intersection(set(combo.split("-")))) >= 2:
            box_sources.append(source)
        if item.get("axis_1st"):
            first_axis_counter[str(item.get("axis_1st"))] += 1
    exact_unique = sorted(set(exact_sources))
    axis_unique = sorted(set(axis_sources))
    first_second_unique = sorted(set(first_second_sources))
    box_unique = sorted(set(box_sources))
    matched_sources = sorted(set(exact_unique + axis_unique + first_second_unique + box_unique))
    external_source_count = len(set(item["source_name"] for item in externals))
    exact_n = len(exact_unique)
    axis_n = len(axis_unique)
    first_second_n = len(first_second_unique)
    box_n = len(box_unique)
    same_favorite_multi = any(count >= 2 for count in first_axis_counter.values())
    if exact_n >= 2 or axis_n >= 3:
        grade = "A"
    elif exact_n >= 1 or axis_n >= 2 or first_second_n >= 1:
        grade = "B"
    elif box_n >= 1 or same_favorite_multi:
        grade = "C"
    else:
        grade = "NONE"
    score = exact_n * 40 + axis_n * 15 + first_second_n * 20 + box_n * 5
    if external_source_count == 0:
        reason = "外部予想なし"
    elif grade == "NONE":
        reason = "独自AIと外部予想の一致なし"
    else:
        reason = f"exact={exact_n}, 1着軸={axis_n}, 1-2着軸={first_second_n}, 構成艇近似={box_n}"
    return {
        "consensus_score": score,
        "consensus_grade": grade,
        "exact_combo_match": exact_n,
        "first_axis_match": axis_n,
        "first_second_axis_match": first_second_n,
        "box_overlap_match": box_n,
        "source_count": external_source_count,
        "matched_sources": matched_sources,
        "exact_match_sources": exact_unique,
        "axis_match_sources": axis_unique,
        "first_second_axis_match_sources": first_second_unique,
        "box_overlap_sources": box_unique,
        "external_source_count": external_source_count,
        "ai_combo": ai_combo,
        "external_combos": external_combos,
        "consensus_reason": reason,
        "consensus_caution": "表示専用。BUY判定・EV計算・予想ロジックには未使用。",
    }


def camel_consensus(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "consensusGrade": row["consensus_grade"],
        "consensusScore": row["consensus_score"],
        "exactComboMatch": row.get("exact_combo_match", 0),
        "firstAxisMatch": row.get("first_axis_match", 0),
        "firstSecondAxisMatch": row.get("first_second_axis_match", 0),
        "boxOverlapMatch": row.get("box_overlap_match", 0),
        "sourceCount": row.get("source_count", row.get("external_source_count", 0)),
        "matchedSources": row["matched_sources"],
        "exactMatchSources": row["exact_match_sources"],
        "axisMatchSources": row["axis_match_sources"],
        "firstSecondAxisMatchSources": row.get("first_second_axis_match_sources", []),
        "boxOverlapSources": row["box_overlap_sources"],
        "consensusReason": row["consensus_reason"],
        "externalSourceCount": row["external_source_count"],
    }


def update_prediction_sheet(path: Path, consensus_rows: list[dict[str, Any]]) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        return
    by_key = {}
    for row in consensus_rows:
        by_key[(str(row.get("jcd") or "").zfill(2), int(row.get("race_no") or 0), normalize_combo(row.get("ai_combo")))] = row
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        key = (normalize_jcd(candidate.get("jcd"), candidate.get("venue")), int(candidate.get("raceNo") or candidate.get("race_no") or 0), normalize_combo(candidate.get("combo")))
        row = by_key.get(key)
        if row:
            candidate.update(camel_consensus(row))
        else:
            candidate.update({
                "consensusGrade": "NONE", "consensusScore": 0, "matchedSources": [],
                "exactMatchSources": [], "axisMatchSources": [], "boxOverlapSources": [],
                "firstSecondAxisMatchSources": [], "consensusReason": "外部予想なし",
                "externalSourceCount": 0, "exactComboMatch": 0, "firstAxisMatch": 0,
                "firstSecondAxisMatch": 0, "boxOverlapMatch": 0, "sourceCount": 0,
            })
    payload.setdefault("summary", {})["consensus"] = dict(Counter(c.get("consensusGrade", "NONE") for c in candidates))
    payload["consensusNotice"] = "合意スコアは表示専用。BUY判定・EV計算・予想ロジックには未使用。"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build(date_text: str) -> dict[str, Any]:
    normalized = normalize_date(date_text)
    compact = compact_date(normalized)
    sheet_path = prediction_sheet_path(normalized)
    payload = load_json(sheet_path)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"prediction_sheet missing: {sheet_path}")
    candidates = payload.get("candidates") or []
    external_rows, loaded_sources, missing_sources, unavailable_sources = load_external_prediction_files(normalized)
    by_race: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in external_rows:
        by_race[(row["jcd"], int(row["race_no"]))].append(row)

    consensus_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        jcd, race_no = candidate_key(candidate, normalized)
        item = consensus_for_candidate(candidate, by_race.get((jcd, race_no), []), normalized)
        out = {
            "date": normalized,
            "venue": candidate.get("venue") or JCD_TO_VENUE.get(jcd, ""),
            "jcd": jcd,
            "race_no": race_no,
            "race_id": candidate.get("raceId") or candidate.get("race_id") or f"{compact}-{jcd}-{race_no:02d}",
            "final_decision": candidate.get("finalDecision") or candidate.get("final_decision") or "",
            "paper_decision": candidate.get("paperDecision") or candidate.get("paper_decision") or "",
        }
        out.update(item)
        consensus_rows.append(out)

    out_dir = REPORTS_CONSENSUS / normalized
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "consensus_sheet.csv"
    json_path = out_dir / "consensus_sheet.json"
    md_path = out_dir / "consensus_sheet.md"
    ui_dir = UI_ROOT / compact
    ui_dir.mkdir(parents=True, exist_ok=True)
    ui_path = ui_dir / "consensus_sheet.json"

    fieldnames = [
        "date", "venue", "jcd", "race_no", "race_id", "final_decision", "paper_decision",
        "consensus_score", "consensus_grade", "exact_combo_match", "first_axis_match",
        "first_second_axis_match", "box_overlap_match", "source_count", "matched_sources",
        "exact_match_sources", "axis_match_sources", "first_second_axis_match_sources",
        "box_overlap_sources", "external_source_count", "ai_combo", "external_combos",
        "consensus_reason", "consensus_caution",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in consensus_rows:
            serial = dict(row)
            for key in ("matched_sources", "exact_match_sources", "axis_match_sources", "first_second_axis_match_sources", "box_overlap_sources", "external_combos"):
                serial[key] = json.dumps(serial.get(key), ensure_ascii=False)
            writer.writerow({k: serial.get(k, "") for k in fieldnames})

    grade_counts = dict(Counter(row["consensus_grade"] for row in consensus_rows))
    top_matches = sorted(
        [row for row in consensus_rows if row["consensus_grade"] != "NONE"],
        key=lambda r: (-int(r["consensus_score"]), r["venue"], int(r["race_no"] or 0)),
    )[:5]
    output = {
        "status": "ok",
        "date": normalized,
        "sourceDate": normalized,
        "loadedExternalSources": loaded_sources,
        "missingExternalSources": missing_sources,
        "unavailableExternalSources": unavailable_sources,
        "summary": {
            "gradeCounts": grade_counts,
            "exactMatchCount": sum(1 for row in consensus_rows if row["exact_match_sources"]),
            "axisMatchCount": sum(1 for row in consensus_rows if row["axis_match_sources"]),
            "topMatches": top_matches,
        },
        "candidates": [dict(row, **camel_consensus(row)) for row in consensus_rows],
        "notice": "合意スコアは表示専用。BUY判定・EV計算・予想ロジックには未使用。",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    ui_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# Consensus Sheet ({normalized})", "",
        "- 合意スコアは表示専用。BUY判定・EV計算・予想ロジックには未使用。", "",
        f"- loadedExternalSources: {', '.join(loaded_sources) if loaded_sources else '-'}",
        f"- missingExternalSources: {', '.join(missing_sources) if missing_sources else '-'}",
        f"- unavailableExternalSources: {', '.join(unavailable_sources) if unavailable_sources else '-'}",
        f"- gradeCounts: {grade_counts}", "", "## TOP一致レース",
    ]
    if top_matches:
        for row in top_matches:
            md_lines.append(f"- {row['venue']} {row['race_no']}R {row['consensus_grade']} score={row['consensus_score']} {row['ai_combo']} / {row['consensus_reason']}")
    else:
        md_lines.append("- なし")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    for path in [REPORTS_PREDICTIONS / normalized / "prediction_sheet.json", UI_ROOT / compact / "prediction_sheet.json"]:
        if path.exists():
            update_prediction_sheet(path, consensus_rows)

    REPO_AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "targetDate": normalized,
        "loadedExternalSources": loaded_sources,
        "missingExternalSources": missing_sources,
        "unavailableExternalSources": unavailable_sources,
        "consensusGradeCounts": grade_counts,
        "exactMatchCount": output["summary"]["exactMatchCount"],
        "axisMatchCount": output["summary"]["axisMatchCount"],
        "topMatches": top_matches,
        "webApiUrls": [f"/api/consensus-sheet?date={normalized}", "/api/consensus-sheet/latest", "/api/prediction-sheet/latest"],
        "webUrl": "/predictions",
        "generatedFiles": [str(csv_path), str(json_path), str(md_path), str(ui_path)],
        "buyDecisionChanged": False,
        "evCalculationChanged": False,
        "predictionLogicChanged": False,
        "next": "外部予想保存対象を増やし、複数日で合意度別の的中率を比較する。",
    }
    (REPO_AUDIT_ROOT / "consensus_display_completion.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_report = [
        "# Consensus Display Completion", "",
        f"- 対象日: {normalized}",
        f"- 読み込んだ外部ソース: {', '.join(loaded_sources) if loaded_sources else '-'}",
        f"- missing外部ソース: {', '.join(missing_sources) if missing_sources else '-'}",
        f"- unavailable外部ソース: {', '.join(unavailable_sources) if unavailable_sources else '-'}",
        f"- consensus A/B/C/NONE: {grade_counts}",
        f"- exact match件数: {report['exactMatchCount']}",
        f"- axis match件数: {report['axisMatchCount']}",
        "- BUY判定未変更: true",
        "- EV計算未変更: true",
        "- 予想ロジック未変更: true",
        "", "## TOP一致レース",
    ]
    if top_matches:
        for row in top_matches:
            md_report.append(f"- {row['venue']} {row['race_no']}R {row['consensus_grade']} score={row['consensus_score']} {row['ai_combo']} / {row['consensus_reason']}")
    else:
        md_report.append("- なし")
    md_report.extend(["", "## 次", "- 外部予想保存対象を増やし、複数日で合意度別の的中率を比較する。"])
    (REPO_AUDIT_ROOT / "consensus_display_completion.md").write_text("\n".join(md_report) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build display-only consensus sheet")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    result = build(args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
