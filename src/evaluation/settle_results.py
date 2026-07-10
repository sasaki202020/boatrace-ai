from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from src.ingest.official_fetcher import JCD_TO_VENUE, PAY_URL, fetch_result_html
from src.ingest.official_k_loader import find_k_file_for_date
from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file


ROOT = Path(__file__).resolve().parents[2]
PRED_ROOT = ROOT / "data" / "predictions"
BACKFILL_ROOT = ROOT / "data" / "predictions_backfill"
NORM_ROOT = ROOT / "data" / "normalized"
REPORT_ROOT = ROOT / "reports" / "daily"
ERRORS_ROOT = ROOT / "reports" / "errors"


@dataclass(frozen=True)
class SettlementKey:
    jcd: str
    race_no: int


@dataclass(frozen=True)
class PayResult:
    combo: str | None
    payout: int | None
    popularity: int | None


def _stable_hash_payload(row: dict[str, Any]) -> str:
    payload = {
        "combo": _combo_pair(row.get("combo") or row.get("trifecta") or row.get("comboParts")),
        "decision": str(row.get("decision") or "").upper(),
        "prob": row.get("prob"),
        "odds": row.get("odds"),
        "expectedValue": row.get("expectedValue") if row.get("expectedValue") is not None else row.get("expected_value"),
        "edge": row.get("edge"),
        "rank": row.get("rank"),
        "probRank": row.get("probRank") or row.get("prob_rank"),
        "evRank": row.get("evRank") or row.get("ev_rank"),
        "reason": row.get("reason") or "",
        "modelVersion": row.get("modelVersion") or row.get("model_version") or "",
        "stage": row.get("stage") or "",
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_prediction_row(row: dict[str, Any], *, source: str, original_path: str = "", recovered_at: str = "") -> dict[str, Any]:
    normalized = dict(row)
    normalized.setdefault("predictionSource", source)
    normalized.setdefault("source", source)
    source_type = {
        "frozen": "live_frozen",
        "ui_recovered": "ui_recovered",
        "backfill": "backfill",
        "legacy_predictions": "missing",
    }.get(source, "missing")
    normalized.setdefault("sourceType", source_type)
    normalized.setdefault("source_type", source_type)
    if recovered_at:
        normalized.setdefault("recoveredAt", recovered_at)
    if original_path:
        normalized.setdefault("originalUiPath", original_path)
    existing_hash = normalized.get("predictionHash") or normalized.get("prediction_hash") or ""
    computed_hash = _stable_hash_payload(normalized)
    if existing_hash:
        normalized["predictionHash"] = str(existing_hash)
        normalized["predictionHashComputed"] = computed_hash
    else:
        normalized["predictionHash"] = computed_hash
        normalized["predictionHashComputed"] = computed_hash
        normalized["predictionHashMissing"] = True
    return normalized


def _source_type_for_row(row: dict[str, Any]) -> str:
    value = str(row.get("sourceType") or row.get("source_type") or row.get("predictionSource") or row.get("source") or "missing").strip().lower()
    mapping = {
        "frozen": "live_frozen",
        "live_frozen": "live_frozen",
        "ui_recovered": "ui_recovered",
        "backfill": "backfill",
        "legacy_predictions": "missing",
        "missing": "missing",
    }
    return mapping.get(value, value if value in {"live_frozen", "ui_recovered", "backfill"} else "missing")


def _normalize_prediction_source_mode(value: str | None) -> str:
    token = str(value or "auto").strip().lower()
    if token in {"live", "ui_recovered", "backfill", "auto", "all"}:
        return token
    return "auto"


def _scan_prediction_sources(date_key: str, jcd: str = "all") -> dict[str, Any]:
    pred_dir = PRED_ROOT / date_key
    backfill_dir = BACKFILL_ROOT / date_key
    ui_dir = ROOT / "data" / "ui" / date_key
    normalized_jcd = _normalize_jcd(jcd) if jcd != "all" else "all"

    frozen_paths = [path for path in sorted(pred_dir.glob("frozen_bets_all.json"))] if pred_dir.exists() else []
    frozen_paths.extend(path for path in sorted(pred_dir.glob("frozen_bets_*.json")) if pred_dir.exists())
    ui_paths = sorted(ui_dir.glob("raceyosou_*.json")) if ui_dir.exists() else []
    legacy_paths = []
    if pred_dir.exists():
        for venue_dir in sorted([p for p in pred_dir.iterdir() if p.is_dir()]):
            if jcd != "all" and venue_dir.name.zfill(2) != normalized_jcd:
                continue
            legacy_paths.extend(sorted(venue_dir.glob("race_*.json")))
    backfill_paths = []
    if backfill_dir.exists():
        for path in sorted(backfill_dir.glob("backfilled_bets_*.json")):
            venue_jcd = path.stem.rsplit("_", 1)[-1].zfill(2)
            if jcd != "all" and venue_jcd != normalized_jcd:
                continue
            backfill_paths.append(path)

    frozen_state = "missing"
    frozen_rows = 0
    frozen_invalid = False
    frozen_empty = False
    frozen_source_types: set[str] = set()
    for path in frozen_paths:
        payload = _load_json(path)
        if payload is None:
            frozen_invalid = True
            continue
        if isinstance(payload, dict):
            freeze_type = str(payload.get("freezeType") or payload.get("freeze_type") or "").strip().lower()
            if freeze_type:
                frozen_source_types.add(freeze_type)
            races = payload.get("races")
            if isinstance(races, list) and races:
                frozen_state = "present"
                frozen_rows += len(races)
            else:
                frozen_empty = True
        else:
            frozen_invalid = True

    ui_state = "missing"
    ui_rows = 0
    ui_invalid = False
    ui_empty = False
    ui_stage = ""
    ui_updated_at = ""
    ui_prediction_hash_missing = 0
    ui_prediction_hash_changed = 0
    for path in ui_paths:
        payload = _load_json(path)
        if payload is None:
            ui_invalid = True
            continue
        if not isinstance(payload, dict):
            ui_invalid = True
            continue
        ui_stage = str(payload.get("stage") or ui_stage)
        ui_updated_at = str(payload.get("updatedAt") or ui_updated_at)
        races = payload.get("races")
        if not isinstance(races, list) or not races:
            ui_empty = True
            continue
        ui_state = "present"
        for race in races:
            if not isinstance(race, dict):
                continue
            bets = race.get("aiPredictions") or []
            if not isinstance(bets, list):
                continue
            for bet in bets:
                if not isinstance(bet, dict):
                    continue
                ui_rows += 1
                if not bet.get("predictionHash"):
                    ui_prediction_hash_missing += 1
                else:
                    if str(bet.get("predictionHash")) != _stable_hash_payload(bet):
                        ui_prediction_hash_changed += 1

    legacy_state = "missing"
    legacy_rows = 0
    for path in legacy_paths:
        payload = load_prediction_rows(path)
        if payload:
            legacy_state = "present"
            legacy_rows += len(payload)

    backfill_state = "missing"
    backfill_rows = 0
    for path in backfill_paths:
        payload = _load_json(path)
        if not isinstance(payload, dict):
            continue
        races = payload.get("races")
        if not isinstance(races, list):
            continue
        has_rows = False
        for race in races:
            if not isinstance(race, dict):
                continue
            bets = race.get("bets") or []
            if isinstance(bets, list):
                backfill_rows += len([bet for bet in bets if isinstance(bet, dict)])
                if bets:
                    has_rows = True
        if has_rows:
            backfill_state = "present"

    if frozen_state == "present":
        if "backfill" in frozen_source_types and "live" not in frozen_source_types:
            source = "backfill"
        elif "ui_recovered" in frozen_source_types and "live" not in frozen_source_types:
            source = "ui_recovered"
        else:
            source = "frozen"
    elif ui_state == "present":
        source = "ui_recovered"
    elif backfill_state == "present":
        source = "backfill"
    elif legacy_state == "present":
        source = "legacy"
    else:
        source = "missing"

    warnings: list[str] = []
    if frozen_state == "missing" and ui_state == "present":
        warnings.append("settle_frozen_bets_missing_but_ui_available")
    if frozen_state == "missing" and ui_state == "missing" and legacy_state == "missing":
        warnings.append("settle_frozen_bets_missing_and_ui_missing")
    if frozen_invalid:
        warnings.append("frozen_bets_invalid")
    if frozen_empty:
        warnings.append("frozen_bets_empty")
    if ui_invalid:
        warnings.append("ui_json_invalid")
    if ui_prediction_hash_missing:
        warnings.append("prediction_hash_missing")
    if ui_prediction_hash_changed:
        warnings.append("prediction_hash_changed")
    if source == "ui_recovered":
        warnings.append("ui_recovered_predictions_used")
        if ui_stage and ui_stage != "pre_race":
            warnings.append("ui_recovered_after_result_warning")

    return {
        "date": date_key,
        "jcd": normalized_jcd,
        "source": source,
        "frozenState": frozen_state,
        "uiState": ui_state,
        "legacyState": legacy_state,
        "backfillState": backfill_state,
        "frozenPaths": [str(path) for path in frozen_paths],
        "uiPaths": [str(path) for path in ui_paths],
        "legacyPaths": [str(path) for path in legacy_paths],
        "backfillPaths": [str(path) for path in backfill_paths],
        "uiStage": ui_stage,
        "uiUpdatedAt": ui_updated_at,
        "hasFrozenBets": frozen_state == "present",
        "hasUiJson": ui_state == "present",
        "hasBackfillBets": backfill_rows > 0,
        "hasAiPredictions": ui_rows > 0 or frozen_rows > 0 or legacy_rows > 0 or backfill_rows > 0,
        "frozenRows": frozen_rows,
        "uiRows": ui_rows,
        "legacyRows": legacy_rows,
        "backfillRows": backfill_rows,
        "predictionHashMissingCount": ui_prediction_hash_missing,
        "predictionHashChangedCount": ui_prediction_hash_changed,
        "warnings": warnings,
    }


def _extract_pay_results(
    html: str,
    *,
    source_url: str = "",
    source_status: str = "",
    date: str = "",
) -> dict[tuple[str, int], PayResult]:
    del source_url, source_status, date
    results: dict[tuple[str, int], PayResult] = {}
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 4:
            continue
        race_text = cells[0].get_text(" ", strip=True)
        m_rno = re.search(r"(\d{1,2})R", race_text)
        if not m_rno:
            continue
        race_no = int(m_rno.group(1))
        href = " ".join(cell.get("data-href", "") for cell in cells)
        m_jcd = re.search(r"jcd=(\d{1,2})", href)
        if not m_jcd:
            continue
        jcd = f"{int(m_jcd.group(1)):02d}"
        combo_text = cells[1].get_text(" ", strip=True)
        digits = re.findall(r"[1-6]", combo_text)
        combo = "-".join(digits[:3]) if len(digits) >= 3 else None
        payout_text = cells[2].get_text(" ", strip=True)
        payout = None
        try:
            payout = int(re.sub(r"[^\d]", "", payout_text)) if re.sub(r"[^\d]", "", payout_text) else None
        except Exception:
            payout = None
        popularity = None
        try:
            pop_text = cells[3].get_text(" ", strip=True)
            popularity = int(re.sub(r"[^\d]", "", pop_text)) if re.sub(r"[^\d]", "", pop_text) else None
        except Exception:
            popularity = None
        results[(jcd, race_no)] = PayResult(combo=combo, payout=payout, popularity=popularity)
    return results


def _normalize_date(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"invalid date: {value!r}")
    return digits


def _normalize_jcd(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{int(value):02d}" if str(value).isdigit() else str(value)


def _combo_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        digits = re.findall(r"[1-6]", value)
        return "-".join(digits[:3]) if len(digits) >= 3 else value.strip()
    if isinstance(value, (list, tuple)):
        digits = [str(v) for v in value if str(v).isdigit()]
        return "-".join(digits[:3])
    return str(value)


def _combo_pair(value: Any) -> str:
    return _combo_text(value).replace("=", "-").replace(" ", "")


def _normalize_result_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "ok": "ok",
        "available": "ok",
        "ready": "ok",
        "pending": "pending",
        "missing": "missing",
        "unavailable": "unavailable",
        "parse_error": "parse_error",
        "available_without_trifecta": "available_without_trifecta",
        "refund": "refund",
        "invalid": "no_contest",
        "no_contest": "no_contest",
        "canceled": "canceled",
        "cancelled": "canceled",
    }
    return mapping.get(text, text or "missing")


def _settle_status(result_status: str, result_race_status: str, actual_combo: str | None) -> str:
    status = _normalize_result_status(result_status)
    race_status = _normalize_result_status(result_race_status)
    if race_status == "ok" and actual_combo:
        return "hit"
    if race_status == "ok":
        return "miss"
    if race_status in {"refund", "canceled", "no_contest"}:
        return "void"
    if race_status == "parse_error":
        return "parse_error"
    if race_status == "pending":
        return "pending"
    if status in {"missing", "unavailable"}:
        return "no_result"
    if race_status == "available_without_trifecta":
        return "no_result"
    return "pending"


def _settle_status_for_bet(
    *,
    decision: Any,
    result_status: str,
    result_race_status: str,
    actual_combo: str | None,
    bet_combo: str,
) -> str:
    if str(decision or "").upper() != "BUY":
        return "excluded"
    status = _normalize_result_status(result_status)
    race_status = _normalize_result_status(result_race_status)
    if race_status in {"refund", "canceled", "no_contest"}:
        return "void"
    if race_status == "parse_error" or status == "parse_error":
        return "parse_error"
    if race_status in {"pending"}:
        return "pending"
    if race_status in {"missing", "unavailable"}:
        return "no_result"
    if race_status == "available_without_trifecta":
        return "no_result"
    if race_status == "ok":
        if actual_combo and bet_combo == actual_combo:
            return "hit"
        if actual_combo:
            return "miss"
        return "no_result"
    return "no_result"


def _write_error(date_key: str, payload: dict[str, Any]) -> None:
    ERRORS_ROOT.mkdir(parents=True, exist_ok=True)
    path = ERRORS_ROOT / f"{date_key}_errors.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _archive_existing(path: Path, *, date_key: str, kind: str) -> None:
    if not path.exists():
        return
    archive_dir = ROOT / "_archive" / date_key / kind
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    archived = archive_dir / f"{path.stem}_{stamp}{path.suffix}"
    try:
        shutil.move(str(path), str(archived))
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _persist_frozen_rows(date_key: str, rows_by_key: dict[SettlementKey, list[dict[str, Any]]], *, freeze_type: str) -> list[str]:
    if not rows_by_key:
        return []
    grouped: dict[str, list[tuple[int, list[dict[str, Any]]]]] = {}
    for key, rows in rows_by_key.items():
        grouped.setdefault(key.jcd, []).append((key.race_no, rows))
    out_dir = PRED_ROOT / date_key
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    venue_payloads: list[dict[str, Any]] = []
    for venue_jcd in sorted(grouped):
        races: list[dict[str, Any]] = []
        total_bet_count = 0
        total_buy_count = 0
        for race_no, rows in sorted(grouped[venue_jcd], key=lambda item: item[0]):
            bets = [dict(row) for row in rows if isinstance(row, dict)]
            buy_count = sum(1 for bet in bets if str(bet.get("decision") or "").upper() == "BUY")
            total_bet_count += len(bets)
            total_buy_count += buy_count
            races.append(
                {
                    "rno": race_no,
                    "raceNo": race_no,
                    "bets": bets,
                    "betCount": len(bets),
                    "buyCount": buy_count,
                }
            )
        venue_payload = {
            "date": date_key,
            "jcd": venue_jcd,
            "venue": JCD_TO_VENUE.get(venue_jcd, venue_jcd),
            "stage": "result" if freeze_type != "backfill" else "odds",
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "freezeType": freeze_type,
            "source": freeze_type,
            "races": races,
            "totalBetCount": total_bet_count,
            "totalBuyCount": total_buy_count,
            "betCount": total_bet_count,
            "buyCount": total_buy_count,
            "predictionHash": _stable_hash_payload(
                {
                    "date": date_key,
                    "jcd": venue_jcd,
                    "freezeType": freeze_type,
                    "races": races,
                }
            ),
        }
        venue_path = out_dir / f"frozen_bets_{venue_jcd}.json"
        _archive_existing(venue_path, date_key=date_key, kind="frozen_bets")
        _write_json(venue_path, venue_payload)
        written.append(str(venue_path))
        venue_payloads.append(venue_payload)
    all_path = out_dir / "frozen_bets_all.json"
    _archive_existing(all_path, date_key=date_key, kind="frozen_bets")
    all_payload = {
        "date": date_key,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "freezeType": freeze_type,
        "source": freeze_type,
        "venues": venue_payloads,
        "totalBetCount": sum(int(payload.get("totalBetCount") or 0) for payload in venue_payloads),
        "totalBuyCount": sum(int(payload.get("totalBuyCount") or 0) for payload in venue_payloads),
        "predictionHash": _stable_hash_payload({"date": date_key, "freezeType": freeze_type, "venues": venue_payloads}),
    }
    _write_json(all_path, all_payload)
    written.insert(0, str(all_path))
    return written


def load_prediction_rows(pred_path: Path) -> list[dict[str, Any]]:
    if not pred_path.exists():
        return []
    try:
        payload = json.loads(pred_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("predictions")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _load_daily_predictions(date_key: str, jcd: str = "all") -> dict[SettlementKey, list[dict[str, Any]]]:
    pred_dir = PRED_ROOT / date_key
    if not pred_dir.exists():
        return {}
    out: dict[SettlementKey, list[dict[str, Any]]] = {}
    for venue_dir in sorted([p for p in pred_dir.iterdir() if p.is_dir()]):
        venue_jcd = venue_dir.name.zfill(2)
        if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
            continue
        for path in sorted(venue_dir.glob("race_*.json")):
            m = re.fullmatch(r"race_(\d{1,2})\.json", path.name)
            if not m:
                continue
            race_no = int(m.group(1))
            out[SettlementKey(venue_jcd, race_no)] = load_prediction_rows(path)
    return out


def _load_race_snapshot(date_key: str, jcd: str, race_no: int) -> dict[str, Any]:
    path = NORM_ROOT / date_key / jcd / f"race_{race_no}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_k_result_cache(date_key: str, jcd: str = "all") -> tuple[dict[SettlementKey, dict[str, Any]], dict[str, Any]]:
    file_path = find_k_file_for_date(date_key)
    if file_path is None:
        return {}, {"status": "missing", "path": "", "parseWarnings": ["result_txt_missing"]}
    try:
        parsed = parse_official_k_result_file(file_path, date8=date_key)
    except Exception as exc:  # pragma: no cover - defensive
        _write_error(
            date_key,
            {
                "date": date_key,
                "stage": "result",
                "type": "result_txt_parse_error",
                "message": str(exc),
                "path": str(file_path),
            },
        )
        return {}, {"status": "parse_error", "path": str(file_path), "parseWarnings": ["result_txt_parse_error"]}
    out: dict[SettlementKey, dict[str, Any]] = {}
    for race in parsed.get("races") or []:
        if not isinstance(race, dict):
            continue
        race_jcd = str(race.get("jcd") or "").zfill(2)
        if jcd != "all" and race_jcd != _normalize_jcd(jcd):
            continue
        race_no = int(race.get("rno") or race.get("raceNo") or 0)
        if race_no <= 0:
            continue
        out[SettlementKey(race_jcd, race_no)] = {
            "date": date_key,
            "jcd": race_jcd,
            "raceNo": race_no,
            "dataStatus": race.get("dataStatus") or race.get("raceStatus") or "missing",
            "resultRaceStatus": race.get("raceStatus") or race.get("resultRaceStatus") or "missing",
            "finishOrder": race.get("finishOrder") or race.get("finish_order") or [],
            "trifectaCombo": race.get("trifectaCombo") or race.get("trifecta_combo"),
            "trifectaPayout": race.get("trifectaPayout") or race.get("trifecta_payout"),
            "trifectaPopularity": race.get("trifectaPopularity") or race.get("trifecta_popularity"),
            "raceStatus": race.get("raceStatus") or race.get("resultRaceStatus") or "missing",
            "resultPublishedAt": race.get("resultPublishedAt"),
            "source": {
                "resultSource": "official_txt_k",
                "resultSourceType": "official_txt_k",
                "kFilePath": str(file_path),
                "kResultPath": str(file_path),
                "sourceUrl": str(file_path),
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            },
            "raw": race,
            "parseWarnings": race.get("parseWarnings") or [],
        }
    return out, {"status": "ok", "path": str(file_path), "parseWarnings": parsed.get("parseWarnings") or []}


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _combo_normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        digits = [str(v).strip() for v in value if str(v).strip().isdigit()]
        return "-".join(digits[:3])
    text = str(value).strip().replace("=", "-").replace(" ", "")
    parts = [p for p in text.split("-") if p]
    digits = [p for p in parts if p.isdigit()]
    if len(digits) >= 3:
        return "-".join(digits[:3])
    return text


def _load_frozen_bets(
    date_key: str,
    jcd: str = "all",
    *,
    prediction_source: str = "auto",
    persist_ui_recovered: bool = True,
    allow_backfill_fallback: bool = False,
) -> tuple[dict[SettlementKey, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
    pred_dir = PRED_ROOT / date_key
    backfill_dir = BACKFILL_ROOT / date_key
    warnings: list[str] = []
    errors: list[dict[str, Any]] = []
    out: dict[SettlementKey, list[dict[str, Any]]] = {}
    source_info = _scan_prediction_sources(date_key, jcd=jcd)
    warnings.extend(source_info.get("warnings") or [])
    hash_missing_count = 0
    hash_changed_count = 0
    mode = _normalize_prediction_source_mode(prediction_source)
    allow_live = mode in {"auto", "live"}
    allow_ui = mode in {"auto", "ui_recovered"}
    allow_backfill = mode in {"auto", "backfill"} or allow_backfill_fallback
    allow_legacy = mode == "auto"

    def _ingest_race_rows(payload: dict[str, Any], *, source: str) -> None:
        nonlocal hash_missing_count, hash_changed_count
        if not isinstance(payload, dict):
            return
        races = payload.get("races")
        if not isinstance(races, list):
            return
        for race in races:
            if not isinstance(race, dict):
                continue
            venue_jcd = str(race.get("jcd") or payload.get("jcd") or "").zfill(2)
            if not venue_jcd:
                continue
            if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                continue
            race_no = int(race.get("rno") or race.get("raceNo") or 0)
            if race_no <= 0:
                continue
            bets = race.get("bets") or race.get("aiPredictions") or []
            if not isinstance(bets, list):
                bets = []
            rows: list[dict[str, Any]] = []
            for bet in bets:
                if not isinstance(bet, dict):
                    continue
                row = _normalize_prediction_row(dict(bet), source=source)
                if row.get("predictionHashMissing"):
                    hash_missing_count += 1
                if row.get("predictionHashComputed") and row.get("predictionHash") and row.get("predictionHash") != row.get("predictionHashComputed"):
                    hash_changed_count += 1
                rows.append(row)
            out[SettlementKey(venue_jcd, race_no)] = rows

    def _source_label(payload: dict[str, Any], default: str) -> str:
        freeze_type = str(payload.get("freezeType") or payload.get("freeze_type") or "").strip().lower()
        if freeze_type in {"backfill", "ui_recovered"}:
            return freeze_type
        source_type = str(payload.get("sourceType") or payload.get("source_type") or "").strip().lower()
        if source_type in {"backfill", "ui_recovered"}:
            return source_type
        if freeze_type == "live":
            return default
        return default

    if allow_live:
        frozen_all = _load_json(pred_dir / "frozen_bets_all.json")
        if isinstance(frozen_all, dict):
            source_label = _source_label(frozen_all, "frozen")
            venues = frozen_all.get("venues")
            if isinstance(venues, list) and venues:
                for venue_payload in venues:
                    if isinstance(venue_payload, dict):
                        _ingest_race_rows(venue_payload, source=_source_label(venue_payload, source_label))
            else:
                _ingest_race_rows(frozen_all, source=source_label)
        else:
            any_frozen = False
            for path in sorted(pred_dir.glob("frozen_bets_*.json")) if pred_dir.exists() else []:
                if path.name == "frozen_bets_all.json":
                    continue
                venue_jcd = path.stem.rsplit("_", 1)[-1].zfill(2)
                if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                    continue
                payload = _load_json(path)
                if isinstance(payload, dict):
                    any_frozen = True
                    _ingest_race_rows(payload, source=_source_label(payload, "frozen"))
            if not any_frozen:
                if source_info.get("uiState") == "present":
                    warnings.append("settle_frozen_bets_missing_but_ui_available")
                elif source_info.get("backfillState") == "present":
                    warnings.append("settle_frozen_bets_missing_but_backfill_available")
                else:
                    warnings.append("settle_frozen_bets_missing")
        if out:
            if not any(rows for rows in out.values()):
                if source_info.get("hasAiPredictions"):
                    warnings.append("frozen_bets_empty_prediction_missing")
                else:
                    warnings.append("frozen_bets_empty_no_buy")
            return out, errors, warnings
        if mode == "live":
            if source_info.get("uiState") == "present":
                warnings.append("settle_frozen_bets_missing_but_ui_available")
            elif source_info.get("backfillState") == "present":
                warnings.append("settle_frozen_bets_missing_but_backfill_available")
            else:
                warnings.append("settle_frozen_bets_missing")
            if source_info.get("hasAiPredictions"):
                warnings.append("frozen_bets_empty_prediction_missing")
            else:
                warnings.append("frozen_bets_empty_no_buy")
            return out, errors, warnings

    if allow_ui:
        # UI recovery fallback, still explicit.
        ui_dir = ROOT / "data" / "ui" / date_key
        recovered = False
        for path in sorted(ui_dir.glob("raceyosou_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            venue_jcd = str(payload.get("jcd") or path.stem.split("_")[-1]).zfill(2)
            if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                continue
            races = payload.get("races") or []
            if not isinstance(races, list):
                continue
            for race in races:
                if not isinstance(race, dict):
                    continue
                race_no = int(race.get("raceNumber") or race.get("raceNo") or 0)
                if race_no <= 0:
                    continue
                bets = race.get("aiPredictions") or []
                if not isinstance(bets, list):
                    bets = []
                rows: list[dict[str, Any]] = []
                for bet in bets:
                    if not isinstance(bet, dict):
                        continue
                    row = _normalize_prediction_row(
                        {
                            "combo": _combo_normalize(bet.get("combo") or bet.get("trifecta")),
                            "decision": bet.get("decision"),
                            "prob": bet.get("prob"),
                            "odds": bet.get("odds"),
                            "expectedValue": bet.get("expectedValue"),
                            "edge": bet.get("edge"),
                            "rank": bet.get("rank"),
                            "probRank": bet.get("probRank"),
                            "evRank": bet.get("evRank"),
                            "reason": bet.get("reason") or "",
                            "stake": 100,
                        },
                        source="ui_recovered",
                        original_path=str(path),
                        recovered_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    if row.get("predictionHashMissing"):
                        hash_missing_count += 1
                    if row.get("predictionHashComputed") and row.get("predictionHash") and row.get("predictionHash") != row.get("predictionHashComputed"):
                        hash_changed_count += 1
                    rows.append(row)
                out[SettlementKey(venue_jcd, race_no)] = rows
                recovered = True
        if recovered:
            if persist_ui_recovered:
                _persist_frozen_rows(date_key, out, freeze_type="ui_recovered")
            warnings.append("settle_frozen_bets_missing_but_ui_available")
            warnings.append("ui_recovered_predictions_used")
            warnings.append("prediction_hash_missing_recovered")
            return out, errors, warnings
        if mode == "ui_recovered":
            if source_info.get("hasFrozenBets"):
                warnings.append("frozen_bets_missing_but_ui_available")
            else:
                warnings.append("frozen_bets_missing_and_ui_missing")
            warnings.append("settle_prediction_missing")
            return out, errors, warnings

    if allow_backfill:
        # backfill fallback is separate from live settlement and used only when explicitly available.
        backfill_races = False
        if backfill_dir.exists():
            for path in sorted(backfill_dir.glob("backfilled_bets_*.json")):
                payload = _load_json(path)
                if not isinstance(payload, dict):
                    continue
                venue_jcd = str(payload.get("jcd") or path.stem.rsplit("_", 1)[-1]).zfill(2)
                if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                    continue
                races = payload.get("races")
                if not isinstance(races, list):
                    continue
                for race in races:
                    if not isinstance(race, dict):
                        continue
                    race_no = int(race.get("rno") or race.get("raceNo") or 0)
                    if race_no <= 0:
                        continue
                    bets = race.get("bets") or []
                    rows: list[dict[str, Any]] = []
                    for bet in bets:
                        if not isinstance(bet, dict):
                            continue
                        row = _normalize_prediction_row(dict(bet), source="backfill")
                        if row.get("predictionHashMissing"):
                            hash_missing_count += 1
                        if row.get("predictionHashComputed") and row.get("predictionHash") and row.get("predictionHash") != row.get("predictionHashComputed"):
                            hash_changed_count += 1
                        rows.append(row)
                    if rows:
                        out[SettlementKey(venue_jcd, race_no)] = rows
                        backfill_races = True
        if backfill_races:
            warnings.append("backfill_predictions_used")
            if hash_missing_count:
                warnings.append("prediction_hash_missing_recovered")
            if hash_changed_count:
                warnings.append("prediction_hash_changed")
            return out, errors, warnings
        if mode == "backfill":
            warnings.append("settle_prediction_missing")
            return out, errors, warnings

    if allow_legacy:
        legacy_dir = pred_dir
        for venue_dir in sorted([p for p in legacy_dir.iterdir() if p.is_dir()]) if legacy_dir.exists() else []:
            venue_jcd = venue_dir.name.zfill(2)
            if jcd != "all" and venue_jcd != _normalize_jcd(jcd):
                continue
            for path in sorted(venue_dir.glob("race_*.json")):
                m = re.fullmatch(r"race_(\d{1,2})\.json", path.name)
                if not m:
                    continue
                race_no = int(m.group(1))
                rows = load_prediction_rows(path)
                for row in rows:
                    normalized = _normalize_prediction_row(dict(row), source="legacy_predictions")
                    if normalized.get("predictionHashMissing"):
                        hash_missing_count += 1
                    if normalized.get("predictionHashComputed") and normalized.get("predictionHash") and normalized.get("predictionHash") != normalized.get("predictionHashComputed"):
                        hash_changed_count += 1
                    row.clear()
                    row.update(normalized)
                out[SettlementKey(venue_jcd, race_no)] = rows
    if not out:
        if source_info.get("hasUiJson"):
            warnings.append("frozen_bets_missing_but_ui_available")
        else:
            warnings.append("frozen_bets_missing_and_ui_missing")
        warnings.append("settle_prediction_missing")
    if hash_missing_count:
        warnings.append("prediction_hash_missing")
    if hash_changed_count:
        warnings.append("prediction_hash_changed")
    return out, errors, warnings


def inspect_prediction_sources(date_key: str, jcd: str = "all") -> dict[str, Any]:
    return _scan_prediction_sources(date_key, jcd=jcd)


def _normalize_result_record(record: dict[str, Any], *, date_key: str, jcd: str, race_no: int) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    if not isinstance(result, dict) or not result:
        result = record if isinstance(record, dict) else {}
    source = result.get("source") if isinstance(result.get("source"), dict) else record.get("source") if isinstance(record.get("source"), dict) else {}
    data_status = _normalize_result_status(
        result.get("dataStatus")
        or result.get("data_status")
        or record.get("data_status", {}).get("result")
        or record.get("dataStatus", {}).get("result")
        or "missing"
    )
    combo = _combo_text(result.get("trifectaCombo") or result.get("trifecta_combo") or result.get("trifecta"))
    payout = result.get("trifectaPayout") or result.get("trifecta_payout") or result.get("payout")
    popularity = result.get("trifectaPopularity") or result.get("trifecta_popularity")
    finish_order = result.get("finishOrder") or result.get("finish_order") or []
    if isinstance(finish_order, list):
        finish_order = [item for item in finish_order if isinstance(item, int)]
    else:
        finish_order = []
    race_status = _normalize_result_status(result.get("raceStatus") or result.get("race_status") or data_status)
    if race_status == "ok" and not combo and finish_order:
        race_status = "available_without_trifecta"
    if race_status == "ok" and combo and payout is None:
        race_status = "available_without_trifecta"
    if race_status in {"available", "available_without_trifecta"} and combo and payout is not None and finish_order:
        race_status = "ok"
    result_source = str(source.get("resultSource") or source.get("resultSourceType") or result.get("resultSource") or result.get("result_source") or "official_html")
    return {
        "date": date_key,
        "jcd": jcd,
        "raceNo": race_no,
        "dataStatus": data_status,
        "resultRaceStatus": race_status,
        "finishOrder": finish_order,
        "trifectaCombo": combo or None,
        "trifectaPayout": payout if isinstance(payout, int) else None,
        "trifectaPopularity": popularity if isinstance(popularity, int) else None,
        "raceStatus": race_status,
        "resultPublishedAt": result.get("resultPublishedAt") or result.get("result_published_at"),
        "source": source,
        "resultSource": result_source,
        "resultRawPath": str(source.get("rawHtmlPath") or source.get("resultRawPath") or source.get("kResultPath") or source.get("kFilePath") or ""),
        "kResultPath": str(source.get("kResultPath") or source.get("kFilePath") or ""),
        "raw": result,
    }


def _fetch_live_result(date_key: str, jcd: str, race_no: int) -> dict[str, Any]:
    race_id = f"{date_key}-{jcd}-{race_no:02d}"
    try:
        fetched = fetch_result_html(target_date=date_key, jcd=jcd, race_no=race_no, race_id=race_id)
        parsed = fetched.get("parsed") or {}
        return {
            "date": date_key,
            "jcd": jcd,
            "raceNo": race_no,
            "dataStatus": _normalize_result_status(fetched.get("dataStatus") or parsed.get("dataStatus") or "missing"),
            "resultRaceStatus": _normalize_result_status(parsed.get("raceStatus") or parsed.get("race_status") or fetched.get("dataStatus") or "missing"),
            "finishOrder": parsed.get("finishOrder") or parsed.get("finish_order") or [],
            "trifectaCombo": parsed.get("trifectaCombo") or parsed.get("trifecta_combo"),
            "trifectaPayout": parsed.get("trifectaPayout") or parsed.get("trifecta_payout"),
            "trifectaPopularity": parsed.get("trifectaPopularity") or parsed.get("trifecta_popularity"),
            "raceStatus": _normalize_result_status(parsed.get("raceStatus") or parsed.get("race_status") or "missing"),
            "resultPublishedAt": parsed.get("resultPublishedAt") or parsed.get("result_published_at"),
            "source": {
                "url": fetched.get("url", PAY_URL.format(date8=date_key)),
                "status": fetched.get("fetchStatus", "unavailable"),
                "fetchedAt": fetched.get("fetchedAt", ""),
                "rawHtmlPath": fetched.get("rawHtmlPath", ""),
            },
            "raw": parsed,
            "parseWarnings": fetched.get("parseWarnings") or parsed.get("parseWarnings") or [],
        }
    except Exception as exc:  # pragma: no cover - defensive
        _write_error(
            date_key,
            {
                "date": date_key,
                "jcd": jcd,
                "rno": race_no,
                "stage": "result",
                "type": "result_unknown_error",
                "message": str(exc),
            },
        )
        return {
            "date": date_key,
            "jcd": jcd,
            "raceNo": race_no,
            "dataStatus": "missing",
            "resultRaceStatus": "missing",
            "finishOrder": [],
            "trifectaCombo": None,
            "trifectaPayout": None,
            "trifectaPopularity": None,
            "raceStatus": "missing",
            "resultPublishedAt": None,
            "source": {"url": PAY_URL.format(date8=date_key), "status": "error", "fetchedAt": datetime.now().isoformat(timespec="seconds")},
            "raw": {},
            "parseWarnings": ["result_unknown_error"],
        }


def _stake_from_buy_count(buy_count: int, stake_per_buy: int) -> float:
    return float(max(0, buy_count) * max(0, stake_per_buy))


def _iter_race_keys(predictions: dict[SettlementKey, list[dict[str, Any]]], results: dict[SettlementKey, dict[str, Any]]) -> list[SettlementKey]:
    keys = set(predictions.keys()) | set(results.keys())
    return sorted(keys, key=lambda k: (k.jcd, k.race_no))


def settle_daily_predictions(
    *,
    date: str,
    jcd: str = "all",
    stake_per_buy: int = 100,
    timeout: float = 30.0,
    prediction_source: str = "auto",
    persist_ui_recovered: bool = True,
    allow_live_fallback: bool = False,
    allow_backfill_fallback: bool = False,
) -> dict[str, Any]:
    date_key = _normalize_date(date)
    stake_per_buy = max(0, int(stake_per_buy))
    predictions, prediction_errors, warnings = _load_frozen_bets(
        date_key,
        jcd=jcd,
        prediction_source=prediction_source,
        persist_ui_recovered=persist_ui_recovered,
        allow_backfill_fallback=allow_backfill_fallback,
    )
    if prediction_errors:
        for err in prediction_errors:
            _write_error(date_key, err)

    results: dict[SettlementKey, dict[str, Any]] = {}
    result_snapshot_cache: dict[SettlementKey, dict[str, Any]] = {}
    k_result_cache, k_meta = _load_k_result_cache(date_key, jcd=jcd)
    if k_meta.get("status") == "parse_error":
        warnings.append("result_txt_parse_error")
    elif k_meta.get("status") == "missing":
        warnings.append("result_txt_missing")
    for key in _iter_race_keys(predictions, {}):
        snapshot = _load_race_snapshot(date_key, key.jcd, key.race_no)
        normalized = _normalize_result_record(snapshot, date_key=date_key, jcd=key.jcd, race_no=key.race_no) if snapshot else {}
        k_record = k_result_cache.get(key) or {}
        k_normalized = _normalize_result_record(k_record, date_key=date_key, jcd=key.jcd, race_no=key.race_no) if k_record else {}

        selected = normalized or k_normalized
        selected_source = str((selected.get("source") or {}).get("resultSource") or selected.get("resultSource") or "missing")
        normalized_status = _normalize_result_status(normalized.get("dataStatus") or normalized.get("resultRaceStatus") or normalized.get("raceStatus") or "missing") if normalized else "missing"
        k_status = _normalize_result_status(k_normalized.get("dataStatus") or k_normalized.get("resultRaceStatus") or k_normalized.get("raceStatus") or "missing") if k_normalized else "missing"
        normalized_is_ok = normalized_status == "ok" and bool(normalized.get("trifectaCombo")) and normalized.get("trifectaPayout") is not None
        k_is_ok = k_status == "ok" and bool(k_normalized.get("trifectaCombo")) and k_normalized.get("trifectaPayout") is not None
        normalized_void = normalized_status in {"refund", "canceled", "no_contest"}
        k_void = k_status in {"refund", "canceled", "no_contest"}
        if normalized_is_ok and k_is_ok:
            norm_combo = _combo_text(normalized.get("trifectaCombo"))
            k_combo = _combo_text(k_normalized.get("trifectaCombo"))
            norm_payout = normalized.get("trifectaPayout")
            k_payout = k_normalized.get("trifectaPayout")
            if norm_combo and k_combo and (norm_combo != k_combo or str(norm_payout) != str(k_payout)):
                warnings.append("result_conflict")
                _write_error(
                    date_key,
                    {
                        "date": date_key,
                        "jcd": key.jcd,
                        "rno": key.race_no,
                        "stage": "result",
                        "type": "result_conflict",
                        "message": f"html={norm_combo}/{norm_payout}, txt={k_combo}/{k_payout}",
                    },
                )
        if normalized_is_ok:
            selected = normalized
            selected_source = str((normalized.get("source") or {}).get("resultSource") or normalized.get("resultSource") or "official_html")
        elif k_is_ok:
            selected = k_normalized
            selected_source = "official_txt_k"
        elif normalized_void:
            selected = normalized
            selected_source = str((normalized.get("source") or {}).get("resultSource") or normalized.get("resultSource") or "official_html")
        elif k_void:
            selected = k_normalized
            selected_source = "official_txt_k"
        elif k_normalized and not normalized:
            selected = k_normalized
            selected_source = "official_txt_k"
        result_snapshot_cache[key] = selected
        if selected.get("dataStatus") in {"available", "ok"} and selected.get("trifectaCombo"):
            results[key] = selected

    if allow_live_fallback:
        for key in _iter_race_keys(predictions, results):
            if key in results:
                continue
            live = _fetch_live_result(date_key, key.jcd, key.race_no)
            result_snapshot_cache[key] = live
            if live.get("trifectaCombo") and str(live.get("dataStatus") or "").lower() in {"available", "ok"}:
                results[key] = live

    settlement_rows: list[dict[str, Any]] = []
    bets_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    venue_codes: list[str] = []
    venue_summaries: dict[str, dict[str, Any]] = {}
    result_ready_count = 0
    result_missing_count = 0
    result_ok_count = 0
    result_pending_count = 0
    result_parse_error_count = 0
    result_refund_count = 0
    result_canceled_count = 0
    result_no_contest_count = 0
    result_html_ok_count = 0
    result_txt_ok_count = 0
    watch_count = 0
    skip_count = 0
    bet_count = 0
    hit_count = 0
    miss_count = 0
    void_count = 0
    parse_error_count = 0
    pending_count = 0
    no_result_count = 0
    frozen_stake_amount = 0.0
    settled_bet_count = 0
    settled_stake_amount = 0.0
    unresolved_bet_count = 0
    unresolved_stake_amount = 0.0
    void_bet_count = 0
    void_stake_amount = 0.0
    payout_amount = 0.0
    live_bet_count = 0
    ui_recovered_bet_count = 0
    backfill_bet_count = 0
    live_settled_bet_count = 0
    ui_recovered_settled_bet_count = 0
    backfill_settled_bet_count = 0
    result_source_breakdown: dict[str, int] = {}

    for key in _iter_race_keys(predictions, result_snapshot_cache):
        rows = predictions.get(key, [])
        if key.jcd not in venue_codes:
            venue_codes.append(key.jcd)
        venue_summary = venue_summaries.setdefault(
            key.jcd,
            {
                "jcd": key.jcd,
                "venue": JCD_TO_VENUE.get(key.jcd, key.jcd),
                "raceCount": 0,
                "resultReadyCount": 0,
                "resultMissingCount": 0,
                "resultOkCount": 0,
                "resultPendingCount": 0,
                "resultParseErrorCount": 0,
                "resultRefundCount": 0,
                "resultCanceledCount": 0,
                "resultNoContestCount": 0,
                "buyCount": 0,
                "watchCount": 0,
                "skipCount": 0,
                "hitCount": 0,
                "missCount": 0,
                "voidCount": 0,
                "parseErrorCount": 0,
                "pendingCount": 0,
                "noResultCount": 0,
                "stakeAmount": 0.0,
                "payoutAmount": 0.0,
            },
        )
        result = result_snapshot_cache.get(key) or {}
        result_status = _normalize_result_status(result.get("dataStatus") or "missing")
        result_race_status = _normalize_result_status(result.get("resultRaceStatus") or result.get("raceStatus") or result_status)
        result_source = str((result.get("source") or {}).get("resultSource") or result.get("resultSource") or "missing")
        result_source_breakdown[result_source] = result_source_breakdown.get(result_source, 0) + 1
        venue_summary["raceCount"] += 1
        if result_race_status == "ok":
            result_ready_count += 1
            venue_summary["resultReadyCount"] += 1
            if result_race_status == "ok" and result.get("trifectaCombo"):
                result_ok_count += 1
                venue_summary["resultOkCount"] += 1
                if result_source == "official_txt_k":
                    result_txt_ok_count += 1
                else:
                    result_html_ok_count += 1
            elif result_race_status == "refund":
                result_refund_count += 1
                venue_summary["resultRefundCount"] += 1
            elif result_race_status == "canceled":
                result_canceled_count += 1
                venue_summary["resultCanceledCount"] += 1
            elif result_race_status == "no_contest":
                result_no_contest_count += 1
                venue_summary["resultNoContestCount"] += 1
        elif result_race_status == "pending":
            result_pending_count += 1
            venue_summary["resultPendingCount"] += 1
            result_missing_count += 1
            venue_summary["resultMissingCount"] += 1
        elif result_race_status == "parse_error":
            result_parse_error_count += 1
            venue_summary["resultParseErrorCount"] += 1
            result_missing_count += 1
            venue_summary["resultMissingCount"] += 1
        else:
            result_missing_count += 1
            venue_summary["resultMissingCount"] += 1

        buy_rows = [row for row in rows if str(row.get("decision") or "").upper() == "BUY"]
        watch_rows = [row for row in rows if str(row.get("decision") or "").upper() == "WATCH"]
        skip_rows = [row for row in rows if str(row.get("decision") or "").upper() == "SKIP"]
        watch_count += len(watch_rows)
        skip_count += len(skip_rows)
        venue_summary["watchCount"] += len(watch_rows)
        venue_summary["skipCount"] += len(skip_rows)
        bet_count += len(buy_rows)
        frozen_stake_amount += _stake_from_buy_count(len(buy_rows), stake_per_buy)
        actual_combo = _combo_pair(result.get("trifectaCombo"))
        actual_payout = result.get("trifectaPayout")
        if actual_payout is not None and not isinstance(actual_payout, int):
            _write_error(
                date_key,
                {
                    "date": date_key,
                    "jcd": key.jcd,
                    "rno": key.race_no,
                    "stage": "settle",
                    "type": "settle_payout_parse_error",
                    "message": str(actual_payout),
                },
            )
            actual_payout = None

        race_hit_count = 0
        race_miss_count = 0
        race_void_count = 0
        race_parse_error_count = 0
        race_pending_count = 0
        race_no_result_count = 0
        for bet in buy_rows:
            combo = _combo_pair(bet.get("combo") or bet.get("trifecta") or bet.get("comboParts"))
            if not combo:
                _write_error(
                    date_key,
                    {
                        "date": date_key,
                        "jcd": key.jcd,
                        "rno": key.race_no,
                        "stage": "settle",
                        "type": "settle_combo_format_error",
                        "message": str(bet.get("combo") or bet.get("trifecta") or bet.get("comboParts")),
                    },
                )
                continue
            settle_status = _settle_status_for_bet(
                decision=bet.get("decision"),
                result_status=result_status,
                result_race_status=result_race_status,
                actual_combo=actual_combo,
                bet_combo=combo,
            )
            source_type = _source_type_for_row(bet)
            if source_type == "live_frozen":
                live_bet_count += 1
            elif source_type == "ui_recovered":
                ui_recovered_bet_count += 1
            elif source_type == "backfill":
                backfill_bet_count += 1
            if settle_status == "hit":
                race_hit_count += 1
                hit_count += 1
                settled_bet_count += 1
                payout_amount += float(actual_payout or 0) * (stake_per_buy / 100.0)
                settled_stake_amount += float(stake_per_buy)
                if source_type == "live_frozen":
                    live_settled_bet_count += 1
                elif source_type == "ui_recovered":
                    ui_recovered_settled_bet_count += 1
                elif source_type == "backfill":
                    backfill_settled_bet_count += 1
            elif settle_status == "miss":
                race_miss_count += 1
                miss_count += 1
                settled_bet_count += 1
                settled_stake_amount += float(stake_per_buy)
                if source_type == "live_frozen":
                    live_settled_bet_count += 1
                elif source_type == "ui_recovered":
                    ui_recovered_settled_bet_count += 1
                elif source_type == "backfill":
                    backfill_settled_bet_count += 1
            elif settle_status == "void":
                race_void_count += 1
                void_count += 1
                void_bet_count += 1
                void_stake_amount += float(stake_per_buy)
            elif settle_status == "parse_error":
                race_parse_error_count += 1
                parse_error_count += 1
                unresolved_bet_count += 1
                unresolved_stake_amount += float(stake_per_buy)
            elif settle_status == "pending":
                race_pending_count += 1
                pending_count += 1
                unresolved_bet_count += 1
                unresolved_stake_amount += float(stake_per_buy)
            elif settle_status == "no_result":
                race_no_result_count += 1
                no_result_count += 1
                unresolved_bet_count += 1
                unresolved_stake_amount += float(stake_per_buy)
            else:
                race_no_result_count += 1
                no_result_count += 1
                unresolved_bet_count += 1
                unresolved_stake_amount += float(stake_per_buy)

        race_buy_count = len(buy_rows)
        race_frozen_stake = _stake_from_buy_count(race_buy_count, stake_per_buy)
        race_settled_bet_count = race_hit_count + race_miss_count
        race_settled_stake = float(race_settled_bet_count * stake_per_buy)
        race_payout_amount = round(float(actual_payout or 0) * (stake_per_buy / 100.0) * race_hit_count, 2) if race_hit_count else 0.0
        race_hit_rate = round(race_hit_count / race_settled_bet_count, 4) if race_settled_bet_count > 0 else None
        race_recovery = round(race_payout_amount / race_settled_stake, 4) if race_settled_stake > 0 else None
        race_roi = round(race_payout_amount / race_settled_stake, 4) if race_settled_stake > 0 else None
        venue_summary["buyCount"] += race_buy_count
        venue_summary["hitCount"] += race_hit_count
        venue_summary["missCount"] += race_miss_count
        venue_summary["stakeAmount"] += race_frozen_stake
        venue_summary["payoutAmount"] += race_payout_amount

        settlement_rows.append(
            {
                "date": date_key,
                "jcd": key.jcd,
                "venue": venue_summary["venue"],
                "rno": key.race_no,
                "raceNo": key.race_no,
                "resultStatus": result_status,
                "resultRaceStatus": result_race_status,
                "actualTrifecta": actual_combo or None,
                "trifectaPayout": actual_payout,
                "trifectaPopularity": result.get("trifectaPopularity"),
                "resultSource": result_source,
                "resultRawPath": str((result.get("source") or {}).get("rawHtmlPath") or (result.get("source") or {}).get("resultRawPath") or ""),
                "kResultPath": str((result.get("source") or {}).get("kResultPath") or (result.get("source") or {}).get("kFilePath") or ""),
                "buyCount": race_buy_count,
                "hitCount": race_hit_count,
                "hit": race_hit_count > 0,
                "stakeAmount": race_frozen_stake,
                "payoutAmount": race_payout_amount,
                "hitRate": race_hit_rate,
                "recoveryRate": race_recovery,
                "roi": race_roi,
                "settleStatus": "hit" if race_hit_count > 0 else ("miss" if race_miss_count > 0 else ("void" if race_void_count > 0 else ("parse_error" if race_parse_error_count > 0 else ("pending" if race_pending_count > 0 else "no_result")))),
                "result": result,
                "predictions": rows,
                "predictionSource": (rows[0].get("predictionSource") if rows else None) or "missing",
                "source": result.get("source") or {},
            }
        )

        result_rows.append(
            {
                "date": date_key,
                "jcd": key.jcd,
                "venue": venue_summary["venue"],
                "rno": key.race_no,
                "raceNo": key.race_no,
                "raceStatus": result.get("raceStatus") or result_status,
                "finishOrder": result.get("finishOrder") or [],
                "trifectaCombo": actual_combo or None,
                "trifectaPayout": actual_payout,
                "resultStatus": result_status,
                "resultRaceStatus": result_race_status,
                "sourceUrl": (result.get("source") or {}).get("url", ""),
                "fetchedAt": (result.get("source") or {}).get("fetchedAt", ""),
                "trifectaPopularity": result.get("trifectaPopularity"),
                "resultSource": result_source,
                "resultRawPath": str((result.get("source") or {}).get("rawHtmlPath") or (result.get("source") or {}).get("resultRawPath") or ""),
                "kResultPath": str((result.get("source") or {}).get("kResultPath") or (result.get("source") or {}).get("kFilePath") or ""),
            }
        )

        for bet in buy_rows:
            combo = _combo_pair(bet.get("combo") or bet.get("trifecta") or bet.get("comboParts"))
            if not combo:
                continue
            settle_status = _settle_status_for_bet(
                decision=bet.get("decision"),
                result_status=result_status,
                result_race_status=result_race_status,
                actual_combo=actual_combo,
                bet_combo=combo,
            )
            bets_rows.append(
                {
                    "date": date_key,
                    "jcd": key.jcd,
                    "venue": venue_summary["venue"],
                    "rno": key.race_no,
                    "raceNo": key.race_no,
                    "combo": combo,
                "decision": bet.get("decision"),
                    "sourceType": source_type,
                    "stake": stake_per_buy,
                    "prob": bet.get("prob"),
                    "odds": bet.get("odds"),
                    "expectedValue": bet.get("expectedValue") if bet.get("expectedValue") is not None else bet.get("expected_value"),
                    "edge": bet.get("edge"),
                    "resultCombo": actual_combo or None,
                    "resultPayout": actual_payout if actual_combo and combo == actual_combo else 0,
                    "hit": True if settle_status == "hit" else (False if settle_status == "miss" else None),
                    "payout": float(actual_payout or 0) if settle_status == "hit" else (0.0 if settle_status == "miss" else None),
                    "resultRaceStatus": result_race_status,
                    "resultSource": result_source,
                    "resultRawPath": str((result.get("source") or {}).get("rawHtmlPath") or (result.get("source") or {}).get("resultRawPath") or ""),
                    "kResultPath": str((result.get("source") or {}).get("kResultPath") or (result.get("source") or {}).get("kFilePath") or ""),
                    "settledStatus": settle_status,
                    "modelVersion": bet.get("modelVersion") or bet.get("model_version") or "",
                    "predictionHash": bet.get("predictionHash") or "",
                    "sourceType": source_type,
                    "rank": bet.get("rank"),
                    "probRank": bet.get("probRank") or bet.get("prob_rank"),
                    "evRank": bet.get("evRank") or bet.get("ev_rank"),
                    "reason": bet.get("reason") or "",
                    "isSettled": settle_status in {"hit", "miss"},
                    "isVoid": settle_status == "void",
                    "isPending": settle_status in {"pending", "parse_error", "no_result"},
                    "settleStatus": settle_status,
                }
            )

    venue_codes = sorted(venue_codes)
    total_race_count = len(settlement_rows)
    results_status = "missing"
    if total_race_count == 0:
        results_status = "missing"
    elif result_ready_count == total_race_count:
        results_status = "ok"
    elif result_ready_count > 0 and result_missing_count > 0:
        results_status = "partial"
    elif result_ready_count == 0 and result_missing_count > 0:
        results_status = "missing"
    else:
        results_status = "pending"

    if bet_count > max(50, total_race_count * 2):
        warnings.append("high_buy_count")
        _write_error(
            date_key,
            {
                "date": date_key,
                "stage": "settle",
                "type": "settle_high_buy_count_warning",
                "message": f"buyCount={bet_count}",
            },
        )

    hit_rate = round(hit_count / settled_bet_count, 4) if settled_bet_count > 0 else None
    recovery_rate = round(payout_amount / settled_stake_amount, 4) if settled_stake_amount > 0 else None
    roi = round(payout_amount / settled_stake_amount, 4) if settled_stake_amount > 0 else None
    summary = {
        "date": date_key,
        "jcd": jcd,
        "predictionSource": _normalize_prediction_source_mode(prediction_source),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "stakeUnit": stake_per_buy,
        "venues": venue_codes,
        "venueSummaries": [venue_summaries[jcd] for jcd in sorted(venue_summaries)],
        "raceCount": total_race_count,
        "resultReadyCount": result_ready_count,
        "resultMissingCount": result_missing_count,
        "resultOkCount": result_ok_count,
        "resultPendingCount": result_pending_count,
        "resultParseErrorCount": result_parse_error_count,
        "resultRefundCount": result_refund_count,
        "resultCanceledCount": result_canceled_count,
        "resultNoContestCount": result_no_contest_count,
        "resultHtmlOkCount": result_html_ok_count,
        "resultTxtOkCount": result_txt_ok_count,
        "liveSettledBetCount": live_settled_bet_count,
        "uiRecoveredSettledBetCount": ui_recovered_settled_bet_count,
        "backfillSettledBetCount": backfill_settled_bet_count,
        "liveBetCount": live_bet_count,
        "uiRecoveredBetCount": ui_recovered_bet_count,
        "backfillBetCount": backfill_bet_count,
        "liveSettlementCoverage": round(live_settled_bet_count / live_bet_count, 4) if live_bet_count > 0 else None,
        "backfillSettlementCoverage": round(backfill_settled_bet_count / backfill_bet_count, 4) if backfill_bet_count > 0 else None,
        "sourceTypeCounts": {
            "live_frozen": live_bet_count,
            "ui_recovered": ui_recovered_bet_count,
            "backfill": backfill_bet_count,
            "missing": max(0, bet_count - (live_bet_count + ui_recovered_bet_count + backfill_bet_count)),
        },
        "buyCount": bet_count,
        "watchCount": watch_count,
        "skipCount": skip_count,
        "betCount": bet_count,
        "frozenStakeAmount": round(frozen_stake_amount, 2),
        "settledBetCount": settled_bet_count,
        "settledStakeAmount": round(settled_stake_amount, 2),
        "unresolvedBetCount": unresolved_bet_count,
        "unresolvedStakeAmount": round(unresolved_stake_amount, 2),
        "voidBetCount": void_bet_count,
        "voidStakeAmount": round(void_stake_amount, 2),
        "hitCount": hit_count,
        "missCount": miss_count,
        "pendingCount": pending_count,
        "voidCount": void_count,
        "parseErrorCount": parse_error_count,
        "noResultCount": no_result_count,
        "stakeAmount": round(frozen_stake_amount, 2),
        "payoutAmount": round(payout_amount, 2),
        "profit": round(payout_amount - settled_stake_amount, 2),
        "roi": roi,
        "settledRoi": roi,
        "hitRate": hit_rate,
        "resultsStatus": results_status,
        "errorsCount": 0,
        "missingCount": result_missing_count,
        "warnings": warnings,
        "resultSourceBreakdown": result_source_breakdown,
        "settlements": settlement_rows,
        "bets": bets_rows,
        "results": result_rows,
        "source": {
            "url": PAY_URL.format(date8=date_key),
            "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            "status": results_status,
        },
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    summary_path = REPORT_ROOT / f"{date_key}_settlement.json"
    bets_path = REPORT_ROOT / f"{date_key}_bets.csv"
    results_path = REPORT_ROOT / f"{date_key}_results.csv"
    summary_compat_path = REPORT_ROOT / f"{date_key}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_compat_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with bets_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "jcd", "venue", "rno", "combo", "decision", "stake", "prob", "odds", "expectedValue", "edge", "resultRaceStatus", "resultSource", "resultRawPath", "kResultPath", "resultCombo", "resultPayout", "settleStatus", "isSettled", "isVoid", "isPending", "hit", "payout", "modelVersion", "predictionHash"],
        )
        writer.writeheader()
        for row in bets_rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})

    with results_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "jcd", "venue", "rno", "raceStatus", "resultRaceStatus", "finishOrder", "trifectaCombo", "trifectaPayout", "resultStatus", "sourceUrl", "fetchedAt", "resultSource", "resultRawPath", "kResultPath"],
        )
        writer.writeheader()
        for row in result_rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})

    return summary
