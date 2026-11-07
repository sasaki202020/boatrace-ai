from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import threading
import webbrowser
from io import StringIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from typing import Any
from urllib.parse import quote, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, request, send_from_directory

from src.ingest.parsers.beforeinfo_parser import parse_beforeinfo_html
from src.ingest.parsers.racelist_parser import parse_racelist_html
from src.pipeline.prediction_sheet import resolve_consensus_sheet, resolve_prediction_sheet


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger(__name__)

PRED_CSV = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
EXPERIMENT_LOG = ROOT / "reports" / "experiments" / "experiments_log.csv"
MODE_FLAGS = ROOT / "data" / "strategy_outputs" / "mode_flags.json"
STRATEGY_CONFIG = ROOT / "config" / "strategy_config.json"
ROI_FILTER_RULES = ROOT / "data" / "strategy_outputs" / "roi_filter_rules.json"
AUTO_FILTER_RULES = ROOT / "data" / "strategy_outputs" / "auto_filter_rules.json"
CALIBRATION_ARTIFACT = ROOT / "models" / "probability_calibrator.json"
GATE_HEALTH_SUMMARY = ROOT / "reports" / "gate_health" / "gate_health_summary.json"
OPS_BACKTEST_REPORT = ROOT / "reports" / "ops" / "backtest_runner_report.json"
OPS_MODEL_GUARD = ROOT / "reports" / "ops" / "model_guard_latest.json"
OPS_MODEL_COMPARE = ROOT / "reports" / "ops" / "model_compare_latest.json"
OPS_DAILY_PIPELINE = ROOT / "reports" / "ops" / "daily_pipeline_report.json"
OPS_PIPELINE_BAT = ROOT / "ops_pipeline.bat"
LIVE_ODDS_CSV = ROOT / "data" / "strategy_outputs" / "live_odds.csv"
UPSTREAM_DIAGNOSTIC_SUMMARY = ROOT / "reports" / "upstream_after" / "diagnostics" / "diagnostic_summary.json"
PROBABILITY_CALIBRATION_SUMMARY = ROOT / "reports" / "probability_calibration_summary.json"
TASK1_CALIBRATION_SUMMARY = ROOT / "reports" / "task1_probability_calibration_summary.json"
PROBABILITY_CALIBRATION_COMPARE = ROOT / "reports" / "calibrated_ev_summary.json"
TASK2_SELECTION_LEAK_SUMMARY = ROOT / "reports" / "task2_selection_leak_summary.json"
EXPERIMENT_DIR = ROOT / "reports" / "experiments"
TODAY_RACES = ROOT / "data" / "processed" / "today_races.csv"
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
DAILY_ROLLING_SUMMARY = ROOT / "reports" / "daily" / "rolling_summary.json"
FINAL_GOAL_PROGRESS_JSON = ROOT / "reports" / "repo_audit" / "final_goal_progress.json"
TODAY_FEATURES_CSV = ROOT / "data" / "features" / "today_features.csv"
TODAY_WIN_PROBA_CSV = ROOT / "data" / "model_outputs" / "today_win_proba.csv"
TRIFECTA_CANDIDATES_CSV = ROOT / "data" / "strategy_outputs" / "trifecta_candidates.csv"
OFFICIAL_UI_DIR = ROOT / "data" / "ui"

_PRED_CACHE: pd.DataFrame | None = None
_PRED_MTIME: float | None = None
_META_CACHE: pd.DataFrame | None = None
_META_MTIME: float | None = None
_EXP_CACHE: pd.DataFrame | None = None
_EXP_MTIME: float | None = None
_ACTUAL_MAP_CACHE: dict[str, str] | None = None
_ACTUAL_MTIME: float | None = None
_ROI_FILTER_CACHE: dict | None = None
_ROI_FILTER_MTIME: float | None = None
_AUTO_FILTER_CACHE: dict | None = None
_AUTO_FILTER_MTIME: float | None = None
_CALIBRATION_CACHE: dict | None = None
_CALIBRATION_MTIME: float | None = None
_STRATEGY_CONFIG_CACHE: dict | None = None
_STRATEGY_CONFIG_MTIME: float | None = None
_GATE_HEALTH_CACHE: dict | None = None
_GATE_HEALTH_MTIME: float | None = None
_OPS_HEALTH_CACHE: dict | None = None
_OPS_HEALTH_MTIME: tuple[float | None, float | None, float | None, float | None] | None = None
_UPSTREAM_HEALTH_CACHE: dict | None = None
_UPSTREAM_HEALTH_MTIME: tuple[float | None, float | None, float | None] | None = None
_OPS_RUN_LOCK = threading.Lock()
_OPS_RUN_PROCESS: subprocess.Popen | None = None
_NIKKAN_AI_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_EXTERNAL_YOSOU_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_EXTERNAL_HTML_CACHE: dict[str, str] = {}
_RACE_RESULT_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_PUBLIC_RACE_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_PUBLIC_RACE_CACHE_TS: dict[tuple[str, str, int], datetime] = {}
_HAMANAKO_HOME_CACHE: dict[str, Any] | None = None
_HAMANAKO_HOME_CACHE_TS: datetime | None = None
_OPS_RUN_STATE: dict = {
    "status": "idle",
    "mode": None,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "message": "ready",
}

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

JCD_TO_VENUE = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}
VENUE_TO_JCD = {venue: jcd for jcd, venue in JCD_TO_VENUE.items()}
JCD_TO_VENUE_SLUG = {
    "01": "kiryu",
    "02": "toda",
    "03": "edogawa",
    "04": "heiwajima",
    "05": "tamagawa",
    "06": "hamanako",
    "07": "gamagori",
    "08": "tokoname",
    "09": "tsu",
    "10": "mikuni",
    "11": "biwako",
    "12": "suminoe",
    "13": "amagasaki",
    "14": "naruto",
    "15": "marugame",
    "16": "kojima",
    "17": "miyajima",
    "18": "tokuyama",
    "19": "shimonoseki",
    "20": "wakamatsu",
    "21": "ashiya",
    "22": "fukuoka",
    "23": "karatsu",
    "24": "omura",
}
VENUE_SLUG_TO_JCD = {slug: jcd for jcd, slug in JCD_TO_VENUE_SLUG.items()}

GARBLED_VENUE_FIX = {
    "‘å‘º": "大村",
    "“‚’Ã": "唐津",
    "‰ºŠÖ": "下関",
}
RACE_PREFIX_TO_VENUE = {
    "B": "大村",
    "K": "唐津",
    "S": "下関",
}


def _to_float(v: object) -> float | None:
    n = pd.to_numeric(v, errors="coerce")
    if pd.isna(n):
        return None
    return float(n)


def _to_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, float) and pd.isna(v):
        return False
    text = str(v).strip().lower()
    if text in {"true", "1", "yes", "y", "t"}:
        return True
    if text in {"false", "0", "no", "n", "f", ""}:
        return False
    try:
        return bool(int(float(text)))
    except Exception:
        return bool(v)


def _fmt_iso(v: object) -> str:
    dt = pd.to_datetime(v, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


def _text_or_empty(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    if pd.isna(v):
        return ""
    return str(v)


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + (z * z / n)
    center = (p + (z * z / (2.0 * n))) / denom
    margin = (z * (((p * (1.0 - p)) + (z * z / (4.0 * n))) / n) ** 0.5) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _confidence_grade(n: int, width: float) -> str:
    if n >= 100 and width <= 0.12:
        return "A"
    if n >= 30 and width <= 0.20:
        return "B"
    return "C"


def _normalize_race_no(v: object) -> tuple[int | None, int | None]:
    """Convert raw race sequence to display race number (1-12) + raw sequence."""
    n = _to_float(v)
    if n is None:
        return None, None
    seq = int(n)
    if seq <= 0:
        return None, seq
    display = ((seq - 1) % 12) + 1 if seq > 12 else seq
    return display, seq


def _normalize_odds_source(v: object) -> str:
    s = str(v or "").lower()
    if s in {"real", "file", "official_result_odds"}:
        return "real"
    if s in {"estimated", "fallback_fixed", "fallback"}:
        return "estimated"
    return "missing"


def _normalize_jcd(v: object) -> str:
    digits = re.findall(r"\d+", str(v or ""))
    if not digits:
        return ""
    # 24.0 -> ["24", "0"] のようなケースを吸収
    cand = digits[0]
    if len(cand) >= 2:
        return cand[-2:].zfill(2)
    return cand.zfill(2)


def _extract_race_no_from_id(race_id: object) -> int | None:
    text = str(race_id or "")
    if not text:
        return None
    parts = [p for p in text.split("-") if p]
    if len(parts) >= 3:
        n = _to_float(parts[-1])
        return int(n) if n is not None else None
    if "-" in text:
        tail = text.rsplit("-", 1)[-1]
        n = _to_float(tail)
        return int(n) if n is not None else None
    return None


def _normalize_trifecta_text(v: object) -> str:
    raw = str(v or "")
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = raw.replace("&nbsp;", " ")
    parts = re.findall(r"\d+", raw)
    if len(parts) >= 3:
        return "-".join(parts[:3])
    return ""


def _normalize_race_key(v: object) -> str:
    return re.sub(r"[^0-9]", "", str(v or ""))


def _normalize_venue_name(v: object) -> str:
    s = _text_or_empty(v).strip()
    if not s:
        return ""
    return GARBLED_VENUE_FIX.get(s, s)


def _venue_from_race_id(race_id: object) -> str:
    text = str(race_id or "")
    if not text or "-" not in text:
        return ""
    parts = [p for p in text.split("-") if p]
    if len(parts) >= 2:
        mid = parts[1]
        if re.fullmatch(r"\d{1,2}", mid):
            return JCD_TO_VENUE.get(mid.zfill(2), "")
        prefix = mid[:1].upper()
        return RACE_PREFIX_TO_VENUE.get(prefix, "")
    return ""


def _read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _normalize_date_text(value: object) -> str:
    dt_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt_value):
        return ""
    return dt_value.strftime("%Y-%m-%d")


def _jst_today_date() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def _weekday_label_jst(date_text: str) -> str:
    try:
        dt_value = datetime.strptime(date_text, "%Y-%m-%d")
    except Exception:
        return ""
    labels = ("月", "火", "水", "木", "金", "土", "日")
    return labels[dt_value.weekday()]


def _normalize_venue_filter(value: object) -> str:
    text = _normalize_venue_name(value)
    if not text:
        return ""
    jcd = _normalize_jcd(text)
    if jcd in JCD_TO_VENUE:
        return JCD_TO_VENUE[jcd]
    return text


def _venue_matches(row_venue: str, row_jcd: str, wanted: str) -> bool:
    if not wanted:
        return True
    wanted_norm = _normalize_venue_filter(wanted)
    if not wanted_norm:
        return False
    row_norm = _normalize_venue_filter(row_venue) or JCD_TO_VENUE.get(_normalize_jcd(row_jcd), "")
    if row_norm == wanted_norm:
        return True
    return _normalize_jcd(row_jcd) == _normalize_jcd(wanted)


def _candidate_status_label(closed: bool, exhibition_missing: bool, reporter_missing: bool) -> str:
    if closed:
        return "締切済み"
    if exhibition_missing:
        return "展示未取得"
    if reporter_missing:
        return "記者予想なし"
    return "表示中"


def _venue_to_jcd(value: object) -> str:
    normalized = _normalize_venue_filter(value)
    if not normalized:
        return ""
    jcd = _normalize_jcd(normalized)
    if jcd in JCD_TO_VENUE:
        return jcd
    return VENUE_TO_JCD.get(normalized, "")


def _official_raceyosou_path(date_text: str, venue_text: object, base_dir: Path = OFFICIAL_UI_DIR) -> Path | None:
    normalized_date = _normalize_date_text(date_text) or _jst_today_date()
    venue_jcd = _venue_to_jcd(venue_text)
    if not venue_jcd:
        return None
    path = base_dir / normalized_date.replace("-", "") / f"raceyosou_{venue_jcd}.json"
    return path if path.exists() else None


def _load_official_raceyosou_payload(
    date_text: str,
    venue_text: object,
    base_dir: Path = OFFICIAL_UI_DIR,
) -> tuple[dict | None, Path | None]:
    path = _official_raceyosou_path(date_text, venue_text, base_dir=base_dir)
    if not path:
        return None, None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f), path
    except Exception as exc:
        LOGGER.warning("official raceyosou json read failed: %s (%s)", path, exc)
        return None, path


def _is_official_raceyosou_payload_valid(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("date", "venue", "races"):
        if key not in payload:
            return False
    if not _normalize_date_text(payload.get("date")):
        return False
    if not _normalize_venue_filter(payload.get("venue") or payload.get("event")):
        return False
    races = payload.get("races")
    if not isinstance(races, list) or not races:
        return False
    for race in races:
        if not isinstance(race, dict):
            return False
        if not race.get("raceId"):
            return False
        boats = race.get("boats")
        if not isinstance(boats, list) or len(boats) != 6:
            return False
        start_exhibition = race.get("startExhibition")
        if not isinstance(start_exhibition, list):
            return False
    return True


def _official_start_exhibition_time(race: dict, lane: int) -> float | None:
    items = race.get("startExhibition")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if int(_to_float(item.get("no")) or 0) == lane:
            return _to_float(item.get("time"))
    return None


def _official_raceyosou_status_flags(race: dict) -> dict[str, bool]:
    closed = _to_bool(race.get("closed")) or _to_bool(race.get("isClosed")) or _to_bool(race.get("deadlineClosed"))
    start_exhibition = race.get("startExhibition")
    if isinstance(start_exhibition, list) and start_exhibition:
        exhibition_missing = not any(
            _to_float(item.get("time")) not in (None, 0.0)
            for item in start_exhibition
            if isinstance(item, dict)
        )
    else:
        exhibition_missing = True
    reporter_source = race.get("reporterPredictions") or race.get("reporterBets") or []
    reporter_missing = not isinstance(reporter_source, list) or len(reporter_source) == 0
    return {
        "closed": bool(closed),
        "exhibitionMissing": bool(exhibition_missing),
        "reporterMissing": bool(reporter_missing),
    }


def _official_raceyosou_boat(race: dict, boat: dict) -> dict:
    lane = int(_to_float(boat.get("no")) or 0)
    start_exhibition_time = _official_start_exhibition_time(race, lane)
    nat_rate = _to_float(boat.get("natRate"))
    local_rate = _to_float(boat.get("localRate"))
    return {
        "lane": lane or 0,
        "label": boat.get("name") or (f"{lane}号艇" if lane else "-"),
        "avgSt": _to_float(boat.get("avgSt")),
        "nationalWinRate": nat_rate,
        "national2RenRate": nat_rate,
        "local2RenRate": local_rate,
        "motor2RenRate": _to_float(boat.get("motorRate")),
        "boat2RenRate": _to_float(boat.get("boatRate")),
        "raceNo": int(_to_float(race.get("raceNo")) or 0) or None,
        "exhibitionTime": start_exhibition_time,
        "exhibitionTimeRank": None,
        "startTiming": None,
        "insideCourseFlag": lane in {1, 2},
        "laneWinRatePrior": None,
        "lowMotorFlag": False,
        "lowBoatFlag": False,
        "jcdLowMotorFlag": False,
        "jcdLowBoatFlag": False,
        "national2RenRank": None,
        "local2RenRank": None,
        "avgStRank": None,
        "avgStAdvantage": None,
    }


def _official_ai_prediction(item: dict, rank: int) -> dict:
    lane = int(_to_float(item.get("lane") or item.get("no")) or 0)
    label = item.get("label") or item.get("name") or (f"{lane}号艇" if lane else "-")
    trifecta = item.get("trifecta") or item.get("combo") or item.get("buy_combo") or item.get("recommended_trifecta")
    win_raw = item.get("winProbaRaw")
    if win_raw is None:
        win_raw = item.get("win_proba_raw")
    if win_raw is None:
        win_raw = item.get("prob")
    win_norm = item.get("winProbaNorm")
    if win_norm is None:
        win_norm = item.get("win_proba_norm")
    if win_norm is None:
        win_norm = item.get("prob_norm")
    return {
        "rank": rank,
        "lane": lane or None,
        "label": label,
        "trifecta": _normalize_trifecta_text(trifecta),
        "winProbaRaw": _to_float(win_raw),
        "winProbaNorm": _to_float(win_norm),
        "approxProb": _to_float(item.get("approxProb") or item.get("approx_prob") or item.get("prob")),
        "approxProbRaw": _to_float(item.get("approxProbRaw") or item.get("approx_prob_raw")),
        "mainScore": _to_float(item.get("mainScore") or item.get("main_score") or item.get("decision_score")),
        "winScoreScaled": _to_float(item.get("winScoreScaled") or item.get("win_score_scaled")),
        "placeScoreScaled": _to_float(item.get("placeScoreScaled") or item.get("place_score_scaled")),
        "conditionalMode": _to_bool(item.get("conditionalMode") or item.get("conditional_mode")),
    }


def _official_reporter_prediction(item: dict, rank: int) -> dict:
    trifecta = item.get("trifecta") or item.get("combo") or item.get("bet") or ""
    first_lane = item.get("firstLane") or item.get("first_lane") or item.get("no1")
    second_lane = item.get("secondLane") or item.get("second_lane") or item.get("no2")
    third_lane = item.get("thirdLane") or item.get("third_lane") or item.get("no3")
    approx_prob = item.get("approxProb")
    if approx_prob is None:
        approx_prob = item.get("approx_prob")
    if approx_prob is None:
        approx_prob = item.get("prob")
    approx_prob_raw = item.get("approxProbRaw")
    if approx_prob_raw is None:
        approx_prob_raw = item.get("approx_prob_raw")
    main_score = item.get("mainScore")
    if main_score is None:
        main_score = item.get("main_score")
    win_score_scaled = item.get("winScoreScaled")
    if win_score_scaled is None:
        win_score_scaled = item.get("win_score_scaled")
    place_score_scaled = item.get("placeScoreScaled")
    if place_score_scaled is None:
        place_score_scaled = item.get("place_score_scaled")
    return {
        "rank": rank,
        "trifecta": _normalize_trifecta_text(trifecta),
        "firstLane": int(_to_float(first_lane) or 0) or None,
        "secondLane": int(_to_float(second_lane) or 0) or None,
        "thirdLane": int(_to_float(third_lane) or 0) or None,
        "approxProb": _to_float(approx_prob),
        "approxProbRaw": _to_float(approx_prob_raw),
        "mainScore": _to_float(main_score),
        "winScoreScaled": _to_float(win_score_scaled),
        "placeScoreScaled": _to_float(place_score_scaled),
        "conditionalMode": _to_bool(item.get("conditionalMode") or item.get("conditional_mode")),
    }


def _build_raceyosou_view_from_official(payload: dict, source_path: Path) -> dict:
    normalized_date = _normalize_date_text(payload.get("date")) or _jst_today_date()
    venue_label = _normalize_venue_filter(payload.get("venue") or payload.get("event")) or ""
    venue_jcd = _venue_to_jcd(venue_label)
    races: list[dict] = []
    for race in payload.get("races", []):
        boats = race.get("boats") or []
        status_flags = _official_raceyosou_status_flags(race)
        ai_predictions = []
        for rank, item in enumerate(race.get("aiPredictions") or [], start=1):
            if isinstance(item, dict):
                ai_predictions.append(_official_ai_prediction(item, rank))
        reporter_predictions = []
        reporter_source = race.get("reporterPredictions") or race.get("reporterBets") or []
        for rank, item in enumerate(reporter_source, start=1):
            if isinstance(item, dict):
                reporter_predictions.append(_official_reporter_prediction(item, rank))
        status_label = _candidate_status_label(
            status_flags["closed"],
            status_flags["exhibitionMissing"],
            status_flags["reporterMissing"],
        )
        races.append(
            {
                "raceId": str(race.get("raceId") or ""),
                "raceNo": int(_to_float(race.get("raceNo")) or 0) or None,
                "date": normalized_date,
                "dateLabel": f"{normalized_date}{f'({_weekday_label_jst(normalized_date)})' if _weekday_label_jst(normalized_date) else ''}",
                "venue": venue_label,
                "venueLabel": venue_label,
                "jcd": venue_jcd,
                "statusLabel": status_label,
                "statusFlags": status_flags,
                "boats": [_official_raceyosou_boat(race, boat) for boat in boats if isinstance(boat, dict)],
                "aiPredictions": ai_predictions,
                "reporterPredictions": reporter_predictions,
                "deadline": race.get("deadline"),
                "grade": race.get("grade"),
                "weather": race.get("weather") if isinstance(race.get("weather"), dict) else {},
                "reporterComment": race.get("reporterComment") or "",
                "sourcePath": str(source_path),
            }
        )
    races.sort(key=lambda item: (item.get("raceNo") or 0, item.get("raceId") or ""))
    return {
        "date": normalized_date,
        "date_label": f"{normalized_date}{f'({_weekday_label_jst(normalized_date)})' if _weekday_label_jst(normalized_date) else ''}",
        "venue": venue_label,
        "venue_label": venue_label,
        "meta": _derive_raceyosou_meta({"races": races}),
        "races": races,
        "source_counts": {
            "official_files": 1,
            "features": 0,
            "win_proba": 0,
            "trifecta_candidates": 0,
        },
        "source": "official",
    }


def buildLegacyRaceYosouViewModel(date: str | None = None, venue: str | None = None) -> dict:
    feature_df = _read_csv_safe(TODAY_FEATURES_CSV)
    proba_df = _read_csv_safe(TODAY_WIN_PROBA_CSV)
    candidate_df = _read_csv_safe(TRIFECTA_CANDIDATES_CSV)

    source_counts = {
        "features": int(feature_df.shape[0]) if not feature_df.empty else 0,
        "win_proba": int(proba_df.shape[0]) if not proba_df.empty else 0,
        "trifecta_candidates": int(candidate_df.shape[0]) if not candidate_df.empty else 0,
    }

    if feature_df.empty:
        return {
            "date": _normalize_date_text(date) or _jst_today_date(),
            "date_label": "",
            "venue": _normalize_venue_filter(venue) or "",
            "venue_label": _normalize_venue_filter(venue) or "",
            "races": [],
            "source_counts": source_counts,
        }

    for df in (feature_df, proba_df, candidate_df):
        if df.empty:
            continue
        if "date" in df.columns:
            df["date"] = df["date"].map(_normalize_date_text)
        else:
            df["date"] = ""
        if "race_id" in df.columns:
            df["race_id"] = df["race_id"].astype(str).str.strip()
        if "jcd" in df.columns:
            df["jcd"] = df["jcd"].map(_normalize_jcd)
        if "lane" in df.columns:
            df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
        if "race_no" in df.columns:
            df["race_no"] = pd.to_numeric(df["race_no"], errors="coerce")

    available_dates = [
        d for d in sorted(set(feature_df.get("date", pd.Series(dtype=str)).dropna().astype(str).tolist()))
        if d
    ]
    selected_date = _normalize_date_text(date) or (available_dates[-1] if available_dates else _jst_today_date())
    date_feature_df = feature_df[feature_df["date"] == selected_date].copy()
    if date_feature_df.empty:
        date_feature_df = feature_df[feature_df["date"] == (available_dates[-1] if available_dates else selected_date)].copy()
        selected_date = date_feature_df["date"].dropna().astype(str).iloc[0] if not date_feature_df.empty else selected_date

    if date_feature_df.empty:
        return {
            "date": selected_date,
            "date_label": f"{selected_date}{f'({ _weekday_label_jst(selected_date) })' if _weekday_label_jst(selected_date) else ''}",
            "venue": _normalize_venue_filter(venue) or "",
            "venue_label": _normalize_venue_filter(venue) or "",
            "races": [],
            "source_counts": source_counts,
        }

    date_feature_df["venue_name"] = date_feature_df.get("jcd", pd.Series(dtype=str)).map(JCD_TO_VENUE).fillna("")
    date_feature_df["venue_name"] = date_feature_df["venue_name"].astype(str).str.strip()
    if "venue" in date_feature_df.columns:
        date_feature_df["venue_name"] = date_feature_df["venue_name"].where(
            date_feature_df["venue_name"].astype(str).str.strip() != "",
            date_feature_df["venue"].astype(str).str.strip(),
        )

    selected_venue = _normalize_venue_filter(venue)
    if not selected_venue:
        venue_candidates = [
            v for v in date_feature_df["venue_name"].dropna().astype(str).tolist() if v
        ]
        selected_venue = venue_candidates[0] if venue_candidates else ""

    if selected_venue:
        date_feature_df = date_feature_df[
            date_feature_df.apply(
                lambda row: _venue_matches(
                    str(row.get("venue_name", "")),
                    str(row.get("jcd", "")),
                    selected_venue,
                ),
                axis=1,
            )
        ].copy()

    if date_feature_df.empty:
        return {
            "date": selected_date,
            "date_label": f"{selected_date}{f'({ _weekday_label_jst(selected_date) })' if _weekday_label_jst(selected_date) else ''}",
            "venue": selected_venue,
            "venue_label": selected_venue,
            "races": [],
            "source_counts": source_counts,
        }

    races: list[dict] = []
    closed = selected_date < _jst_today_date()

    for race_id, race_df in date_feature_df.groupby("race_id", dropna=False):
        race_df = race_df.copy()
        race_df = race_df.sort_values(["lane", "lane_num"], ascending=[True, True], na_position="last")
        first_row = race_df.iloc[0]
        race_jcd = _normalize_jcd(first_row.get("jcd", ""))
        race_venue = _normalize_venue_filter(first_row.get("venue_name", "")) or JCD_TO_VENUE.get(race_jcd, "")
        race_no = int(_to_float(first_row.get("race_no")) or 0)

        proba_race_df = proba_df[proba_df.get("race_id", pd.Series(dtype=str)).astype(str).str.strip() == str(race_id)].copy() if not proba_df.empty else pd.DataFrame()
        if not proba_race_df.empty and "lane" in proba_race_df.columns:
            proba_race_df = proba_race_df.sort_values(["win_proba_norm", "win_proba_raw", "lane"], ascending=[False, False, True], na_position="last")

        candidate_race_df = candidate_df[candidate_df.get("race_id", pd.Series(dtype=str)).astype(str).str.strip() == str(race_id)].copy() if not candidate_df.empty else pd.DataFrame()
        if not candidate_race_df.empty and "approx_prob" in candidate_race_df.columns:
            candidate_race_df = candidate_race_df.sort_values(["approx_prob", "main_score", "first_win_proba"], ascending=[False, False, False], na_position="last")

        exhibition_missing = bool("exhibition_time" not in race_df.columns or race_df["exhibition_time"].isna().all())
        reporter_missing = bool(candidate_race_df.empty)
        status_label = _candidate_status_label(closed, exhibition_missing, reporter_missing)

        boats: list[dict] = []
        lane_rows = {int(_to_float(row.get("lane")) or 0): row for _, row in race_df.iterrows() if _to_float(row.get("lane")) is not None}
        for lane in range(1, 7):
            row = lane_rows.get(lane)
            boats.append(
                {
                    "lane": lane,
                    "label": f"{lane}号艇",
                    "avgSt": _to_float(row.get("avg_st")) if row is not None else None,
                    "nationalWinRate": _to_float(row.get("national_win_rate")) if row is not None else None,
                    "national2RenRate": _to_float(row.get("national_2ren_rate")) if row is not None else None,
                    "local2RenRate": _to_float(row.get("local_2ren_rate")) if row is not None else None,
                    "motor2RenRate": _to_float(row.get("motor_2ren_rate")) if row is not None else None,
                    "boat2RenRate": _to_float(row.get("boat_2ren_rate")) if row is not None else None,
                    "raceNo": race_no,
                    "exhibitionTime": _to_float(row.get("exhibition_time")) if row is not None else None,
                    "exhibitionTimeRank": _to_float(row.get("exhibition_time_rank")) if row is not None else None,
                    "startTiming": _to_float(row.get("start_timing")) if row is not None else None,
                    "insideCourseFlag": _to_bool(row.get("inside_course_flag")) if row is not None else False,
                    "laneWinRatePrior": _to_float(row.get("lane_win_rate_prior")) if row is not None else None,
                    "lowMotorFlag": _to_bool(row.get("low_motor_flag")) if row is not None else False,
                    "lowBoatFlag": _to_bool(row.get("low_boat_flag")) if row is not None else False,
                    "jcdLowMotorFlag": _to_bool(row.get("jcd_low_motor_flag")) if row is not None else False,
                    "jcdLowBoatFlag": _to_bool(row.get("jcd_low_boat_flag")) if row is not None else False,
                    "national2RenRank": _to_float(row.get("national_2ren_rate_rank_in_race")) if row is not None else None,
                    "local2RenRank": _to_float(row.get("local_2ren_rate_rank_in_race")) if row is not None else None,
                    "avgStRank": _to_float(row.get("avg_st_rank_in_race")) if row is not None else None,
                    "avgStAdvantage": _to_float(row.get("avg_st_advantage_vs_mean")) if row is not None else None,
                }
            )

        ai_predictions: list[dict] = []
        if not proba_race_df.empty:
            for rank, (_, row) in enumerate(proba_race_df.head(6).iterrows(), start=1):
                lane = int(_to_float(row.get("lane")) or 0)
                ai_predictions.append(
                    {
                        "rank": rank,
                        "lane": lane or None,
                        "label": f"{lane}号艇" if lane else "-",
                        "winProbaRaw": _to_float(row.get("win_proba_raw")),
                        "winProbaNorm": _to_float(row.get("win_proba_norm")),
                    }
                )

        reporter_predictions: list[dict] = []
        if not candidate_race_df.empty:
            for rank, (_, row) in enumerate(candidate_race_df.head(5).iterrows(), start=1):
                first_lane = int(_to_float(row.get("first_lane")) or 0)
                second_lane = int(_to_float(row.get("second_lane")) or 0)
                third_lane = int(_to_float(row.get("third_lane")) or 0)
                reporter_predictions.append(
                    {
                        "rank": rank,
                        "trifecta": _normalize_trifecta_text(row.get("trifecta")),
                        "firstLane": first_lane or None,
                        "secondLane": second_lane or None,
                        "thirdLane": third_lane or None,
                        "approxProb": _to_float(row.get("approx_prob")),
                        "approxProbRaw": _to_float(row.get("approx_prob_raw")),
                        "mainScore": _to_float(row.get("main_score")),
                        "winScoreScaled": _to_float(row.get("win_score_scaled")),
                        "placeScoreScaled": _to_float(row.get("place_score_scaled")),
                        "conditionalMode": _to_bool(row.get("conditional_mode")),
                    }
                )

        races.append(
            {
                "raceId": _text_or_empty(race_id),
                "raceNo": race_no or None,
                "date": selected_date,
                "dateLabel": f"{selected_date}{f'({_weekday_label_jst(selected_date)})' if _weekday_label_jst(selected_date) else ''}",
                "venue": race_venue,
                "venueLabel": race_venue,
                "jcd": race_jcd,
                "statusLabel": status_label,
                "statusFlags": {
                    "closed": bool(closed),
                    "exhibitionMissing": bool(exhibition_missing),
                    "reporterMissing": bool(reporter_missing),
                },
                "boats": boats,
                "aiPredictions": ai_predictions,
                "reporterPredictions": reporter_predictions,
            }
        )

    races.sort(key=lambda item: (item.get("raceNo") or 0, item.get("raceId") or ""))
    venue_label = selected_venue or (races[0].get("venue") if races else "")
    date_label = f"{selected_date}{f'({_weekday_label_jst(selected_date)})' if _weekday_label_jst(selected_date) else ''}"
    return {
        "date": selected_date,
        "date_label": date_label,
        "venue": venue_label,
        "venue_label": venue_label,
        "races": races,
        "source_counts": source_counts,
    }


def buildRaceYosouViewModel(date: str | None = None, venue: str | None = None) -> dict:
    official_payload, official_path = _load_official_raceyosou_payload(date or "", venue)
    if _is_official_raceyosou_payload_valid(official_payload):
        official_view = _build_raceyosou_view_from_official(official_payload, official_path or Path(""))
        official_view["source"] = "official"
        return official_view

    if official_payload is not None:
        reason = "missing required keys"
        if isinstance(official_payload, dict):
            races = official_payload.get("races")
            if not isinstance(races, list) or not races:
                reason = "empty races"
            else:
                bad_race = next(
                    (
                        race
                        for race in races
                        if not isinstance(race, dict)
                        or not race.get("raceId")
                        or not isinstance(race.get("boats"), list)
                        or len(race.get("boats") or []) != 6
                        or not isinstance(race.get("startExhibition"), list)
                    ),
                    None,
                )
                if bad_race is not None:
                    reason = "shape mismatch"
        LOGGER.warning(
            "official raceyosou json fallback to legacy: date=%s venue=%s path=%s reason=%s",
            date,
            venue,
            official_path,
            reason,
        )
    elif official_path is not None:
        LOGGER.warning(
            "official raceyosou json fallback to legacy: date=%s venue=%s path=%s reason=read_failed",
            date,
            venue,
            official_path,
        )

    normalized_date = _normalize_date_text(date) or _jst_today_date()
    venue_label = _normalize_venue_filter(venue) or ""
    empty_view = {
        "date": normalized_date,
        "date_label": f"{normalized_date}{f'({_weekday_label_jst(normalized_date)})' if _weekday_label_jst(normalized_date) else ''}",
        "venue": venue_label,
        "venue_label": venue_label,
        "meta": _derive_raceyosou_meta({"races": []}),
        "races": [],
        "source_counts": {
            "official_files": 0,
            "features": 0,
            "win_proba": 0,
            "trifecta_candidates": 0,
        },
        "source": "missing",
    }
    return empty_view


def _derive_raceyosou_meta(view_model: dict) -> dict:
    races = view_model.get("races") if isinstance(view_model, dict) else []
    races = races if isinstance(races, list) else []
    boat_rows: list[dict] = []
    for race in races:
        if not isinstance(race, dict):
            continue
        for boat in race.get("boats") or []:
            if isinstance(boat, dict):
                boat_rows.append({"race": race, "boat": boat})

    top_compi = None
    for item in boat_rows:
        compi = _to_float(item["boat"].get("compi") or item["boat"].get("confidence_score"))
        if compi is None:
            continue
        if top_compi is None or compi > top_compi["value"]:
            top_compi = {
                "value": compi,
                "label": f"{item['race'].get('venueLabel') or item['race'].get('venue') or '-'} "
                f"{item['race'].get('raceNo') or '-'}R {item['boat'].get('label') or item['boat'].get('lane') or '-'}号艇",
            }

    fastest = None
    for item in boat_rows:
        exhibition = _to_float(item["boat"].get("exhibitionTime") or item["boat"].get("exhibition_time"))
        if exhibition is None:
            continue
        if fastest is None or exhibition < fastest["value"]:
            fastest = {
                "value": exhibition,
                "label": f"{item['race'].get('venueLabel') or item['race'].get('venue') or '-'} "
                f"{item['race'].get('raceNo') or '-'}R {item['boat'].get('label') or item['boat'].get('lane') or '-'}号艇",
            }

    max_race_no = 0
    for race in races:
        race_no = _to_float(race.get("raceNo"))
        if race_no is not None:
            max_race_no = max(max_race_no, int(race_no))

    return {
        "brand": "日刊スポーツ風レイアウト",
        "updatedAt": f"{max_race_no}R時点" if max_race_no > 0 else "",
        "compiLeader": top_compi["label"] if top_compi else "",
        "compiLeaderValue": round(top_compi["value"], 2) if top_compi else None,
        "exhibitionFastest": fastest["label"] if fastest else "",
        "exhibitionFastestValue": round(fastest["value"], 2) if fastest else None,
    }


def load_gate_health_summary() -> dict:
    global _GATE_HEALTH_CACHE, _GATE_HEALTH_MTIME
    default = {
        "rows": 0,
        "decision_counts": {},
        "first_place_gate_counts": {},
        "pre_race_gate_counts": {},
        "race_gate_counts": {},
        "gate_combo_counts": {},
        "reason_keyword_counts": {},
        "risk_label_counts": {},
        "missing_rows": 0,
        "pending_rows": 0,
        "missing_breakdown": {
            "first_place_only": 0,
            "pre_race_only": 0,
            "both_missing": 0,
        },
    }
    if not GATE_HEALTH_SUMMARY.exists():
        return default
    mtime = GATE_HEALTH_SUMMARY.stat().st_mtime
    if _GATE_HEALTH_CACHE is not None and _GATE_HEALTH_MTIME == mtime:
        return _GATE_HEALTH_CACHE
    try:
        payload = json.loads(GATE_HEALTH_SUMMARY.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return default
        for key, value in default.items():
            payload.setdefault(key, value)
        _GATE_HEALTH_CACHE = payload
        _GATE_HEALTH_MTIME = mtime
        return payload
    except Exception:
        return default


def _load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else default
    except Exception:
        return default


def load_ops_health_summary() -> dict:
    global _OPS_HEALTH_CACHE, _OPS_HEALTH_MTIME
    default = {
        "backtest": {},
        "guard": {
            "status": "UNKNOWN",
            "reasons": [],
        },
        "compare": {
            "status": "UNKNOWN",
            "reasons": [],
        },
        "pipeline": {
            "status": "unknown",
            "mode": None,
            "step_count": 0,
        },
    }
    mtimes = tuple(
        p.stat().st_mtime if p.exists() else None
        for p in [OPS_BACKTEST_REPORT, OPS_MODEL_GUARD, OPS_MODEL_COMPARE, OPS_DAILY_PIPELINE]
    )
    if _OPS_HEALTH_CACHE is not None and _OPS_HEALTH_MTIME == mtimes:
        return _OPS_HEALTH_CACHE

    backtest = _load_json_file(OPS_BACKTEST_REPORT, {})
    guard = _load_json_file(OPS_MODEL_GUARD, {"status": "UNKNOWN", "reasons": []})
    compare = _load_json_file(OPS_MODEL_COMPARE, {"status": "UNKNOWN", "reasons": []})
    pipeline = _load_json_file(OPS_DAILY_PIPELINE, {"status": "unknown", "mode": None, "steps": []})
    payload = {
        "backtest": {
            "generated_at": backtest.get("generated_at"),
            "status": backtest.get("status"),
            "buy_count": (backtest.get("metrics") or {}).get("buy_count"),
            "hit_count": (backtest.get("metrics") or {}).get("hit_count"),
            "hit_rate": (backtest.get("metrics") or {}).get("hit_rate"),
            "roi": (backtest.get("metrics") or {}).get("roi"),
            "max_drawdown": (backtest.get("metrics") or {}).get("max_drawdown"),
            "avg_odds": (backtest.get("metrics") or {}).get("avg_odds"),
        },
        "guard": {
            "generated_at": guard.get("generated_at"),
            "status": guard.get("status", "UNKNOWN"),
            "reasons": list(guard.get("reasons") or []),
            "current_roi": (guard.get("current") or {}).get("roi"),
            "current_buy_count": (guard.get("current") or {}).get("buy_count"),
        },
        "compare": {
            "generated_at": compare.get("generated_at"),
            "status": compare.get("status", "UNKNOWN"),
            "reasons": list(compare.get("reasons") or []),
            "candidate_roi": (compare.get("candidate") or {}).get("roi"),
            "candidate_buy_count": (compare.get("candidate") or {}).get("buy_count"),
            "promoted": bool(compare.get("promoted")),
            "candidate_run": compare.get("candidate_run"),
        },
        "pipeline": {
            "generated_at": pipeline.get("generated_at"),
            "status": pipeline.get("status", "unknown"),
            "mode": pipeline.get("mode"),
            "step_count": len(pipeline.get("steps") or []),
            "last_step": ((pipeline.get("steps") or [])[-1] or {}).get("label") if (pipeline.get("steps") or []) else None,
        },
    }
    _OPS_HEALTH_CACHE = payload
    _OPS_HEALTH_MTIME = mtimes
    return payload


def load_upstream_health_summary() -> dict:
    global _UPSTREAM_HEALTH_CACHE, _UPSTREAM_HEALTH_MTIME
    default = {
        "diagnostics": {
            "approx_prob_hit_rate": None,
            "approx_prob_avg_pred": None,
            "approx_prob_roi": None,
            "exact_rate": None,
            "top5_rate": None,
            "top10_rate": None,
            "median_rank": None,
            "first_lane_ok_but_order_weak_count": 0,
            "first_lane_itself_weak_count": 0,
            "actual_outside_top20_count": 0,
        },
        "calibration": {
            "method": None,
            "raw_source": None,
            "selected_source": None,
            "base_brier": None,
            "calibrated_brier": None,
            "base_logloss": None,
            "calibrated_logloss": None,
            "improved": False,
        },
        "calibration_compare": {
            "generated_at": None,
            "status": "UNKNOWN",
            "raw_source": None,
            "selected_source": None,
            "result_available_races": None,
            "top_pick_changed_rate": None,
            "top_feature": None,
            "top_feature_abs_gap_improvement": None,
        },
        "selection_leak": {
            "target": None,
            "count": 0,
            "top_reason": None,
        },
    }
    calib_summary_path = PROBABILITY_CALIBRATION_SUMMARY if PROBABILITY_CALIBRATION_SUMMARY.exists() else TASK1_CALIBRATION_SUMMARY
    mtimes = tuple(
        p.stat().st_mtime if p.exists() else None
        for p in [UPSTREAM_DIAGNOSTIC_SUMMARY, calib_summary_path, PROBABILITY_CALIBRATION_COMPARE, TASK2_SELECTION_LEAK_SUMMARY]
    )
    if _UPSTREAM_HEALTH_CACHE is not None and _UPSTREAM_HEALTH_MTIME == mtimes:
        return _UPSTREAM_HEALTH_CACHE

    diag = _load_json_file(UPSTREAM_DIAGNOSTIC_SUMMARY, {})
    calib = _load_json_file(calib_summary_path, {})
    calib_compare = _load_json_file(PROBABILITY_CALIBRATION_COMPARE, {})
    leak = _load_json_file(TASK2_SELECTION_LEAK_SUMMARY, {})

    approx = (diag.get("approx_prob_summary") or {}).get("approx_prob") or {}
    rank = diag.get("trifecta_rank_summary") or {}
    cause = diag.get("cause_summary") or {}
    top_reasons = leak.get("top_reasons") or []
    top_reason = top_reasons[0] if top_reasons else {}

    payload = {
        "diagnostics": {
            "approx_prob_hit_rate": approx.get("hit_rate"),
            "approx_prob_avg_pred": approx.get("avg_pred"),
            "approx_prob_roi": approx.get("roi"),
            "exact_rate": rank.get("exact_rate"),
            "top5_rate": rank.get("top5_rate"),
            "top10_rate": rank.get("top10_rate"),
            "median_rank": rank.get("median_rank"),
            "first_lane_ok_but_order_weak_count": cause.get("first_lane_ok_but_order_weak_count", 0),
            "first_lane_itself_weak_count": cause.get("first_lane_itself_weak_count", 0),
            "actual_outside_top20_count": cause.get("actual_outside_top20_count", 0),
        },
        "calibration": {
            "method": calib.get("method"),
            "raw_source": calib.get("base_prob_col"),
            "selected_source": "calibrated_prob" if str(calib.get("method") or "").lower() != "fallback" else calib.get("base_prob_col"),
            "base_brier": (calib.get("base_metrics") or {}).get("brier"),
            "calibrated_brier": (calib.get("logistic_metrics") or {}).get("brier"),
            "base_logloss": (calib.get("base_metrics") or {}).get("logloss"),
            "calibrated_logloss": (calib.get("logistic_metrics") or {}).get("logloss"),
            "improved": bool(calib.get("selected_better_than_base_brier")),
        },
        "calibration_compare": {
            "generated_at": calib_compare.get("generated_at"),
            "status": calib_compare.get("status", "UNKNOWN"),
            "raw_source": (calib_compare.get("default_probability_path") or {}).get("raw_source"),
            "selected_source": (calib_compare.get("default_probability_path") or {}).get("selected_source"),
            "result_available_races": calib_compare.get("result_available_races"),
            "top_pick_changed_rate": calib_compare.get("top_pick_changed_rate"),
            "top_feature": (calib_compare.get("feature_gap_summary") or [{}])[0].get("feature") if calib_compare.get("feature_gap_summary") else None,
            "top_feature_abs_gap_improvement": (calib_compare.get("feature_gap_summary") or [{}])[0].get("abs_gap_improvement") if calib_compare.get("feature_gap_summary") else None,
        },
        "selection_leak": {
            "target": leak.get("target"),
            "count": leak.get("count", 0),
            "top_reason": top_reason.get("reason"),
        },
    }
    _UPSTREAM_HEALTH_CACHE = payload
    _UPSTREAM_HEALTH_MTIME = mtimes
    return payload


def load_actual_trifecta_map() -> dict[str, str]:
    global _ACTUAL_MAP_CACHE, _ACTUAL_MTIME
    if not HIST_CSV.exists():
        _ACTUAL_MAP_CACHE = {}
        _ACTUAL_MTIME = None
        return {}

    mtime = HIST_CSV.stat().st_mtime
    if _ACTUAL_MAP_CACHE is not None and _ACTUAL_MTIME == mtime:
        return _ACTUAL_MAP_CACHE

    hist = pd.read_csv(HIST_CSV)
    required = {"race_id", "lane", "finish_position"}
    if not required.issubset(set(hist.columns)):
        _ACTUAL_MAP_CACHE = {}
        _ACTUAL_MTIME = mtime
        return {}

    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")
    top3 = (
        hist[hist["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.fillna(-1).astype(int).astype(str)))
        .reset_index()
    )
    out: dict[str, str] = {}
    for _, r in top3.iterrows():
        race_id = str(r["race_id"])
        tri = _normalize_trifecta_text(r["lane"])
        if not tri:
            continue
        out[race_id] = tri
        out[_normalize_race_key(race_id)] = tri

    _ACTUAL_MAP_CACHE = out
    _ACTUAL_MTIME = mtime
    return out


def load_race_meta() -> pd.DataFrame:
    global _META_CACHE, _META_MTIME
    if TODAY_RACES.exists():
        mtime = TODAY_RACES.stat().st_mtime
        if _META_CACHE is not None and _META_MTIME == mtime:
            return _META_CACHE
    else:
        _META_CACHE = pd.DataFrame(columns=["race_id", "jcd", "venue", "race_no", "venue_name"])
        _META_MTIME = None
        return _META_CACHE

    df = pd.read_csv(TODAY_RACES)
    meta = df.copy()
    if "race_id" not in meta.columns:
        _META_CACHE = pd.DataFrame(columns=["race_id", "jcd", "venue", "race_no", "venue_name"])
        _META_MTIME = TODAY_RACES.stat().st_mtime
        return _META_CACHE
    if "venue" not in meta.columns:
        meta["venue"] = meta["race_id"].astype(str).str.split("-").str[1].fillna("")
    if "race_no" not in meta.columns:
        meta["race_no"] = meta["race_id"].map(_extract_race_no_from_id)
    if "jcd" not in meta.columns:
        meta["jcd"] = ""
    meta = meta[["race_id", "jcd", "venue", "race_no"]].drop_duplicates("race_id").copy()
    meta["jcd"] = meta["jcd"].map(_normalize_jcd)
    meta["venue_name"] = meta["jcd"].map(JCD_TO_VENUE).fillna(meta["venue"].astype(str))
    meta["race_no"] = pd.to_numeric(meta["race_no"], errors="coerce")
    _META_CACHE = meta
    _META_MTIME = TODAY_RACES.stat().st_mtime
    return meta


def load_predictions_df() -> pd.DataFrame:
    global _PRED_CACHE, _PRED_MTIME
    if PRED_CSV.exists():
        mtime = PRED_CSV.stat().st_mtime
        if _PRED_CACHE is not None and _PRED_MTIME == mtime:
            return _PRED_CACHE
    else:
        _PRED_CACHE = pd.DataFrame()
        _PRED_MTIME = None
        return _PRED_CACHE

    df = pd.read_csv(PRED_CSV)
    if "date" in df.columns:
        df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        df["date_dt"] = pd.NaT

    meta = load_race_meta()
    if not meta.empty:
        df = df.merge(meta, on="race_id", how="left")

    _PRED_CACHE = df
    _PRED_MTIME = PRED_CSV.stat().st_mtime
    return df


def load_predictions(
    limit: int = 300,
    decision: str | None = None,
    date_from: str | None = None,
    venue: str | None = None,
    include_actual: bool = False,
) -> list[dict]:
    df = load_predictions_df().copy()
    if df.empty:
        return []

    if decision and decision.upper() in {"BUY", "WATCH", "SKIP"}:
        df = df[df["decision"] == decision.upper()].copy()

    if date_from:
        date_min = pd.to_datetime(date_from, errors="coerce")
        if not pd.isna(date_min):
            df = df[df["date_dt"] >= date_min].copy()

    if venue and venue not in {"", "ALL"}:
        venue_s = _normalize_venue_filter(venue)
        venue_norm = _normalize_venue_name(venue_s)
        venue_col = (
            df["venue_name"].map(_normalize_venue_name)
            if "venue_name" in df.columns
            else pd.Series([""] * len(df), index=df.index)
        )
        jcd_col = df["jcd"].map(_normalize_jcd) if "jcd" in df.columns else pd.Series([""] * len(df), index=df.index)
        race_venue_col = df["race_id"].map(_venue_from_race_id) if "race_id" in df.columns else pd.Series([""] * len(df), index=df.index)
        df = df[
            (venue_col == venue_s)
            | (venue_col == venue_norm)
            | (jcd_col.map(JCD_TO_VENUE).fillna("") == venue_s)
            | (jcd_col.map(JCD_TO_VENUE).fillna("") == venue_norm)
            | (race_venue_col == venue_s)
            | (race_venue_col == venue_norm)
        ].copy()

    df = df.sort_values(["date_dt", "race_id"], ascending=[False, True]).head(limit)
    actual_map = load_actual_trifecta_map() if include_actual else {}

    out: list[dict] = []
    for _, r in df.iterrows():
        jcd = _normalize_jcd(r.get("jcd", ""))
        fallback_venue = JCD_TO_VENUE.get(jcd, "")
        venue_raw = _normalize_venue_name(r.get("venue_name", ""))
        venue_name = fallback_venue or _venue_from_race_id(r.get("race_id", "")) or venue_raw
        raw_race_no = r.get("race_no")
        if _to_float(raw_race_no) is None:
            raw_race_no = _extract_race_no_from_id(r.get("race_id", ""))
        display_race_no, race_seq = _normalize_race_no(raw_race_no)
        if venue_name and display_race_no is not None:
            venue_race_label = f"{venue_name} {display_race_no}R"
        elif venue_name:
            venue_race_label = venue_name
        elif display_race_no is not None:
            venue_race_label = f"{display_race_no}R"
        else:
            venue_race_label = ""

        rec_tri = _normalize_trifecta_text(r.get("recommended_trifecta"))
        actual_tri = (
            actual_map.get(_text_or_empty(r.get("race_id")))
            or actual_map.get(_normalize_race_key(r.get("race_id")))
            if include_actual
            else ""
        )
        exact_hit = None
        if rec_tri and actual_tri:
            exact_hit = int(rec_tri == actual_tri)
        realized_return = None
        if exact_hit is not None and _to_float(r.get("odds")) is not None:
            realized_return = float(r.get("odds")) if exact_hit == 1 else 0.0

        out.append(
            {
                "race_id": _text_or_empty(r.get("race_id", "")),
                "date": _fmt_iso(r.get("date")),
                "jcd": jcd,
                "venue_name": _text_or_empty(venue_name),
                "venue_race_label": _text_or_empty(venue_race_label),
                "race_no": display_race_no,
                "race_seq": race_seq,
                "decision": _text_or_empty(r.get("decision", "")),
                "recommended_trifecta": _text_or_empty(r.get("recommended_trifecta", "")),
                "first_win_proba": _to_float(r.get("first_win_proba")),
                "first_place_prob": _to_float(r.get("first_place_prob")),
                "first_place_score": _to_float(r.get("first_place_score")),
                "first_place_gate": _text_or_empty(r.get("first_place_gate", "")),
                "first_place_block": _to_bool(r.get("first_place_block", False)),
                "first_place_priority": _to_bool(r.get("first_place_priority", False)),
                "first_place_multiplier": _to_float(r.get("first_place_multiplier")),
                "first_place_note": _text_or_empty(r.get("first_place_note", "")),
                "second_place_score": _to_float(r.get("second_place_score")),
                "second_place_gate": _text_or_empty(r.get("second_place_gate", "")),
                "second_place_block": _to_bool(r.get("second_place_block", False)),
                "second_place_priority": _to_bool(r.get("second_place_priority", False)),
                "second_place_multiplier": _to_float(r.get("second_place_multiplier")),
                "second_place_note": _text_or_empty(r.get("second_place_note", "")),
                "third_place_score": _to_float(r.get("third_place_score")),
                "third_place_gate": _text_or_empty(r.get("third_place_gate", "")),
                "third_place_block": _to_bool(r.get("third_place_block", False)),
                "third_place_priority": _to_bool(r.get("third_place_priority", False)),
                "third_place_multiplier": _to_float(r.get("third_place_multiplier")),
                "third_place_note": _text_or_empty(r.get("third_place_note", "")),
                "race_score": _to_float(r.get("race_score")),
                "race_first_confidence": _to_float(r.get("race_first_confidence")),
                "race_odds_balance_score": _to_float(r.get("race_odds_balance_score")),
                "race_data_quality_score": _to_float(r.get("race_data_quality_score")),
                "race_gate": _text_or_empty(r.get("race_gate", "")),
                "race_block": _to_bool(r.get("race_block", False)),
                "race_watch": _to_bool(r.get("race_watch", False)),
                "race_priority": _to_bool(r.get("race_priority", False)),
                "race_note": _text_or_empty(r.get("race_note", "")),
                "first_lane": _text_or_empty(r.get("first_lane", "")),
                "second_lane": _text_or_empty(r.get("second_lane", "")),
                "third_lane": _text_or_empty(r.get("third_lane", "")),
                "approx_prob": _to_float(r.get("approx_prob")),
                "calibrated_hit_prob": _to_float(r.get("calibrated_hit_prob")),
                "calibrated_hit_prob_adjusted": _to_float(r.get("calibrated_hit_prob_adjusted")),
                "prob_bin": _text_or_empty(r.get("prob_bin", "")),
                "odds_bin": _text_or_empty(r.get("odds_bin", "")),
                "roi_filter_prob_metric": _text_or_empty(r.get("roi_filter_prob_metric", "")),
                "roi_filter_match": bool(r.get("roi_filter_match", False))
                if not pd.isna(r.get("roi_filter_match", False))
                else False,
                "odds": _to_float(r.get("odds")),
                "gross_return": _to_float(r.get("gross_return")),
                "odds_source": _normalize_odds_source(r.get("odds_source", "")),
                "has_real_odds": _to_bool(r.get("has_real_odds", False))
                if str(r.get("has_real_odds", "")).strip() != ""
                else (_normalize_odds_source(r.get("odds_source", "")) == "real"),
                "ev": _to_float(r.get("ev")),
                "decision_score": _to_float(r.get("decision_score")),
                "kelly_fraction": _to_float(r.get("kelly_fraction")),
                "bet_pct": _to_float(r.get("bet_pct")),
                "bet_amount": _to_float(r.get("bet_amount")),
                "bankroll": _to_float(r.get("bankroll")),
                "kelly_max_fraction": _to_float(r.get("kelly_max_fraction")),
                "odds_status": _text_or_empty(r.get("odds_status", "")),
                "odds_fetch_status": _text_or_empty(r.get("odds_fetch_status", "")),
                "pre_race_score": _to_float(r.get("pre_race_score")),
                "pre_race_gate": _text_or_empty(r.get("pre_race_gate", "")),
                "pre_race_multiplier": _to_float(r.get("pre_race_multiplier")),
                "pre_race_time_score": _to_float(r.get("pre_race_time_score")),
                "pre_race_motor_score": _to_float(r.get("pre_race_motor_score")),
                "pre_race_rank_score": _to_float(r.get("pre_race_rank_score")),
                "pre_race_source": _text_or_empty(r.get("pre_race_source", "")),
                "stop_reason": _text_or_empty(r.get("stop_reason", "")),
                "skip_reason": _text_or_empty(r.get("skip_reason", r.get("stop_reason", ""))),
                "risk_codes": _text_or_empty(r.get("risk_codes", "")),
                "risk_labels": _text_or_empty(r.get("risk_labels", "")),
                "risk_penalty": _to_float(r.get("risk_penalty")),
                "confidence_score": _to_float(r.get("confidence_score")),
                "reason": _text_or_empty(r.get("reason", "")),
                "strategy_mode": _text_or_empty(r.get("strategy_mode", "")),
                "effective_strategy_mode": _text_or_empty(r.get("effective_strategy_mode", r.get("strategy_mode", ""))),
                "actual_trifecta": _text_or_empty(actual_tri),
                "exact_hit": exact_hit,
                "realized_return": realized_return,
            }
        )
    return out


def list_venues() -> list[str]:
    preds = load_predictions(limit=5000)
    venues = sorted(
        {
            (JCD_TO_VENUE.get(_normalize_jcd(r.get("jcd"))) or _venue_from_race_id(r.get("race_id")) or str(r.get("venue_name", "")).strip())
            for r in preds
            if (JCD_TO_VENUE.get(_normalize_jcd(r.get("jcd"))) or _venue_from_race_id(r.get("race_id")) or str(r.get("venue_name", "")).strip())
        }
    )
    return venues


def build_venue_summary(window: str = "all", date_from: str | None = None) -> list[dict]:
    rows = load_predictions(limit=5000, date_from=date_from)
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: (r.get("date", ""), r.get("race_id", "")), reverse=True)
    if window == "30":
        rows = rows[:30]
    elif window == "100":
        rows = rows[:100]
    elif window == "300":
        rows = rows[:300]

    grouped: dict[str, dict] = {}
    for r in rows:
        venue = str(r.get("venue_name") or "不明")
        g = grouped.setdefault(
            venue,
            {
                "venue_name": venue,
                "pred_count": 0,
                "buy_count": 0,
                "watch_count": 0,
                "skip_count": 0,
                "real_odds_rows": 0,
                "buy_odds_sum": 0.0,
                "buy_odds_n": 0,
                "buy_settled_n": 0,
                "buy_hits": 0,
                "buy_return_sum": 0.0,
                "buy_prob_sum": 0.0,
                "buy_prob_n": 0,
                "buy_roi_est_sum": 0.0,
                "buy_roi_est_n": 0,
            },
        )
        g["pred_count"] += 1
        d = str(r.get("decision", "")).upper()
        if d == "BUY":
            g["buy_count"] += 1
            o = _to_float(r.get("odds"))
            if o is not None:
                g["buy_odds_sum"] += o
                g["buy_odds_n"] += 1
            p = _to_float(r.get("approx_prob"))
            if p is not None:
                g["buy_prob_sum"] += min(max(p, 0.0), 1.0)
                g["buy_prob_n"] += 1
            gross = _to_float(r.get("gross_return"))
            if gross is None:
                ev = _to_float(r.get("ev"))
                gross = (ev + 1.0) if ev is not None else None
            if gross is not None:
                g["buy_roi_est_sum"] += gross
                g["buy_roi_est_n"] += 1
            if r.get("exact_hit") is not None:
                g["buy_settled_n"] += 1
                g["buy_hits"] += int(r.get("exact_hit") or 0)
                g["buy_return_sum"] += float(r.get("realized_return") or 0.0)
        elif d == "WATCH":
            g["watch_count"] += 1
        elif d == "SKIP":
            g["skip_count"] += 1
        if r.get("odds_source") == "real":
            g["real_odds_rows"] += 1

    out: list[dict] = []
    for _, g in grouped.items():
        settled_n = g["buy_settled_n"]
        hit_rate = (g["buy_hits"] / settled_n) if settled_n > 0 else None
        roi = (g["buy_return_sum"] / settled_n) if settled_n > 0 else None
        hit_rate_est = (g["buy_prob_sum"] / g["buy_prob_n"]) if g["buy_prob_n"] > 0 else None
        roi_est = (g["buy_roi_est_sum"] / g["buy_roi_est_n"]) if g["buy_roi_est_n"] > 0 else None
        avg_odds = (g["buy_odds_sum"] / g["buy_odds_n"]) if g["buy_odds_n"] > 0 else None
        real_odds_rate = g["real_odds_rows"] / g["pred_count"] if g["pred_count"] > 0 else None
        out.append(
            {
                "venue_name": g["venue_name"],
                "pred_count": g["pred_count"],
                "buy_count": g["buy_count"],
                "watch_count": g["watch_count"],
                "skip_count": g["skip_count"],
                "buy_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                "buy_hit_rate_est": round(hit_rate_est, 4) if hit_rate_est is not None else None,
                "buy_hits": g["buy_hits"],
                "buy_settled_n": settled_n,
                "buy_roi": round(roi, 4) if roi is not None else None,
                "buy_roi_est": round(roi_est, 4) if roi_est is not None else None,
                "avg_buy_odds": round(avg_odds, 2) if avg_odds is not None else None,
                "real_odds_rate": round(real_odds_rate, 4) if real_odds_rate is not None else None,
            }
        )
    out.sort(key=lambda x: (x["buy_hit_rate"] if x["buy_hit_rate"] is not None else -1, x["buy_count"]), reverse=True)
    return out


def _slice_window(rows: list[dict], window: str) -> list[dict]:
    sorted_rows = sorted(rows, key=lambda r: (r.get("date", ""), r.get("race_id", "")), reverse=True)
    if window == "30":
        return sorted_rows[:30]
    if window == "100":
        return sorted_rows[:100]
    if window == "300":
        return sorted_rows[:300]
    return sorted_rows


def build_performance_breakdown(window: str = "all", date_from: str | None = None, venue: str | None = None) -> dict:
    rows = load_predictions(limit=5000, date_from=date_from, venue=venue)
    if not rows:
        return {"window": window, "rows": 0, "decision_stats": [], "odds_band_stats": []}
    rows = _slice_window(rows, window)

    decision_bucket: dict[str, dict] = {}
    for r in rows:
        d = str(r.get("decision", "")).upper() or "UNKNOWN"
        b = decision_bucket.setdefault(
            d,
            {
                "decision": d,
                "count": 0,
                "prob_sum": 0.0,
                "prob_n": 0,
                "gross_sum": 0.0,
                "gross_n": 0,
                "avg_odds_sum": 0.0,
                "avg_odds_n": 0,
            },
        )
        b["count"] += 1
        p = _to_float(r.get("approx_prob"))
        if p is not None:
            b["prob_sum"] += min(max(p, 0.0), 1.0)
            b["prob_n"] += 1
        gross = _to_float(r.get("gross_return"))
        if gross is None:
            ev = _to_float(r.get("ev"))
            gross = (ev + 1.0) if ev is not None else None
        if gross is not None:
            b["gross_sum"] += gross
            b["gross_n"] += 1
        odds = _to_float(r.get("odds"))
        if odds is not None:
            b["avg_odds_sum"] += odds
            b["avg_odds_n"] += 1

    decision_stats: list[dict] = []
    for _, b in decision_bucket.items():
        decision_stats.append(
            {
                "decision": b["decision"],
                "count": b["count"],
                "hit_rate_est": round((b["prob_sum"] / b["prob_n"]), 4) if b["prob_n"] > 0 else None,
                "roi_est": round((b["gross_sum"] / b["gross_n"]), 4) if b["gross_n"] > 0 else None,
                "avg_odds": round((b["avg_odds_sum"] / b["avg_odds_n"]), 2) if b["avg_odds_n"] > 0 else None,
            }
        )
    decision_stats.sort(key=lambda x: {"BUY": 0, "WATCH": 1, "SKIP": 2}.get(x["decision"], 9))

    odds_bands = [
        ("1-20", 1.0, 20.0),
        ("20-50", 20.0, 50.0),
        ("50-100", 50.0, 100.0),
        ("100+", 100.0, float("inf")),
    ]
    odds_stats: list[dict] = []
    for name, low, high in odds_bands:
        sub = []
        for r in rows:
            o = _to_float(r.get("odds"))
            if o is None:
                continue
            if (o >= low) and (o < high):
                sub.append(r)
        if not sub:
            odds_stats.append({"band": name, "count": 0, "hit_rate_est": None, "roi_est": None})
            continue
        probs = [min(max(_to_float(r.get("approx_prob")) or 0.0, 0.0), 1.0) for r in sub]
        grosses = []
        for r in sub:
            gross = _to_float(r.get("gross_return"))
            if gross is None:
                ev = _to_float(r.get("ev"))
                gross = (ev + 1.0) if ev is not None else None
            if gross is not None:
                grosses.append(gross)
        odds_stats.append(
            {
                "band": name,
                "count": len(sub),
                "hit_rate_est": round(sum(probs) / len(probs), 4) if probs else None,
                "roi_est": round(sum(grosses) / len(grosses), 4) if grosses else None,
            }
        )

    return {
        "window": window,
        "rows": len(rows),
        "decision_stats": decision_stats,
        "odds_band_stats": odds_stats,
    }


def load_experiments(limit: int = 50) -> list[dict]:
    global _EXP_CACHE, _EXP_MTIME
    if EXPERIMENT_LOG.exists():
        mtime = EXPERIMENT_LOG.stat().st_mtime
        if _EXP_CACHE is None or _EXP_MTIME != mtime:
            _EXP_CACHE = pd.read_csv(EXPERIMENT_LOG)
            _EXP_MTIME = mtime
    else:
        _EXP_CACHE = pd.DataFrame()
        _EXP_MTIME = None

    df = _EXP_CACHE.copy()
    if df.empty:
        return []
    if "generated_at" in df.columns:
        df["generated_at_dt"] = pd.to_datetime(df["generated_at"], errors="coerce")
        df = df.sort_values("generated_at_dt", ascending=False)

    df = df.head(limit)
    rows: list[dict] = []
    for _, r in df.iterrows():
        rows.append(
            {
                "run_id": str(r.get("run_id", "")),
                "window": str(r.get("window", "")),
                "exact_hit_rate": _to_float(r.get("exact_hit_rate")),
                "roi": _to_float(r.get("roi")),
                "generated_at": str(r.get("generated_at", "")),
            }
        )
    return rows


def load_latest_dual_mode() -> dict:
    files = sorted(EXPERIMENT_DIR.glob("*_dual_mode_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_daily_rolling_summary() -> dict:
    if not DAILY_ROLLING_SUMMARY.exists():
        return {}
    try:
        return json.loads(DAILY_ROLLING_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_mode_flags() -> dict:
    if not MODE_FLAGS.exists():
        return {"trifecta_enabled": None, "exacta_enabled": None, "strategy_mode": "NORMAL"}
    try:
        payload = json.loads(MODE_FLAGS.read_text(encoding="utf-8"))
        return {
            "trifecta_enabled": payload.get("trifecta_enabled"),
            "exacta_enabled": payload.get("exacta_enabled"),
            "strategy_mode": str(payload.get("strategy_mode", "NORMAL") or "NORMAL").upper(),
        }
    except Exception:
        return {"trifecta_enabled": None, "exacta_enabled": None, "strategy_mode": "NORMAL"}


def load_strategy_config() -> dict:
    global _STRATEGY_CONFIG_CACHE, _STRATEGY_CONFIG_MTIME
    default = {
        "bet_management": {
            "bankroll": 100000.0,
            "max_kelly_fraction": 0.05,
        }
    }
    if not STRATEGY_CONFIG.exists():
        _STRATEGY_CONFIG_CACHE = default
        _STRATEGY_CONFIG_MTIME = None
        return default

    mtime = STRATEGY_CONFIG.stat().st_mtime
    if _STRATEGY_CONFIG_CACHE is not None and _STRATEGY_CONFIG_MTIME == mtime:
        return _STRATEGY_CONFIG_CACHE

    try:
        payload = json.loads(STRATEGY_CONFIG.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("strategy config must be a JSON object")
        payload.setdefault("bet_management", {})
        payload["bet_management"].setdefault("bankroll", 100000.0)
        payload["bet_management"].setdefault("max_kelly_fraction", 0.05)
    except Exception:
        payload = default

    _STRATEGY_CONFIG_CACHE = payload
    _STRATEGY_CONFIG_MTIME = mtime
    return payload


def load_roi_filter_rules() -> dict:
    global _ROI_FILTER_CACHE, _ROI_FILTER_MTIME
    if not ROI_FILTER_RULES.exists():
        _ROI_FILTER_CACHE = {
            "strategy_mode": "ROI_FILTER",
            "prob_metric": "first_place_prob",
            "prob_bin_edges": [round(x / 10, 1) for x in range(0, 11)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "min_sample_count": 30,
            "min_roi": 1.0,
        }
        _ROI_FILTER_MTIME = None
        return _ROI_FILTER_CACHE

    mtime = ROI_FILTER_RULES.stat().st_mtime
    if _ROI_FILTER_CACHE is not None and _ROI_FILTER_MTIME == mtime:
        return _ROI_FILTER_CACHE

    try:
        payload = json.loads(ROI_FILTER_RULES.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("roi filter rules must be a JSON object")
        payload.setdefault("strategy_mode", "ROI_FILTER")
        payload.setdefault("prob_metric", "first_place_prob")
        payload.setdefault("prob_bin_edges", [round(x / 10, 1) for x in range(0, 11)])
        payload.setdefault("odds_bin_edges", [0, 20, 50, 100, 200, 500, 1000, 999999])
        payload.setdefault("allowed_prob_bins", [])
        payload.setdefault("allowed_odds_bins", [])
        payload.setdefault("allowed_places", [])
        payload.setdefault("min_sample_count", 30)
        payload.setdefault("min_roi", 1.0)
    except Exception:
        payload = {
            "strategy_mode": "ROI_FILTER",
            "prob_metric": "first_place_prob",
            "prob_bin_edges": [round(x / 10, 1) for x in range(0, 11)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "min_sample_count": 30,
            "min_roi": 1.0,
        }

    _ROI_FILTER_CACHE = payload
    _ROI_FILTER_MTIME = mtime
    return payload


def build_roi_filter_summary(rules: dict) -> str:
    if not rules:
        return "未生成"
    prob_metric = str(rules.get("prob_metric") or "first_place_prob")
    allowed_prob = rules.get("allowed_prob_bins", []) or []
    allowed_odds = rules.get("allowed_odds_bins", []) or []
    allowed_places = rules.get("allowed_places", []) or []

    def _join(items: list, limit: int = 3) -> str:
        vals = [str(v) for v in items if str(v)]
        if not vals:
            return "なし"
        if len(vals) > limit:
            return ", ".join(vals[:limit]) + f" ほか{len(vals) - limit}"
        return ", ".join(vals)

    return (
        f"{prob_metric} / P:{_join(list(allowed_prob))} / "
        f"O:{_join(list(allowed_odds))} / 場:{_join(list(allowed_places))}"
    )


def load_auto_filter_rules() -> dict:
    global _AUTO_FILTER_CACHE, _AUTO_FILTER_MTIME
    if not AUTO_FILTER_RULES.exists():
        _AUTO_FILTER_CACHE = {
            "strategy_mode": "AUTO_FILTER",
            "generated_at": None,
            "enabled": False,
            "prob_metric": "calibrated_hit_prob",
            "prob_bin_edges": [round(x / 20, 2) for x in range(0, 21)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "window": {},
            "window_label": "未生成",
            "min_sample_count": 30,
            "min_roi": 1.0,
        }
        _AUTO_FILTER_MTIME = None
        return _AUTO_FILTER_CACHE

    mtime = AUTO_FILTER_RULES.stat().st_mtime
    if _AUTO_FILTER_CACHE is not None and _AUTO_FILTER_MTIME == mtime:
        return _AUTO_FILTER_CACHE

    try:
        payload = json.loads(AUTO_FILTER_RULES.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("auto filter rules must be a JSON object")
        payload.setdefault("strategy_mode", "AUTO_FILTER")
        payload.setdefault("generated_at", None)
        payload.setdefault("enabled", False)
        payload.setdefault("prob_metric", "calibrated_hit_prob")
        payload.setdefault("prob_bin_edges", [round(x / 20, 2) for x in range(0, 21)])
        payload.setdefault("odds_bin_edges", [0, 20, 50, 100, 200, 500, 1000, 999999])
        payload.setdefault("allowed_prob_bins", [])
        payload.setdefault("allowed_odds_bins", [])
        payload.setdefault("allowed_places", [])
        payload.setdefault("window", {})
        payload.setdefault("window_label", "未生成")
        payload.setdefault("min_sample_count", 30)
        payload.setdefault("min_roi", 1.0)
        _AUTO_FILTER_CACHE = payload
        _AUTO_FILTER_MTIME = mtime
        return payload
    except Exception:
        _AUTO_FILTER_CACHE = {
            "strategy_mode": "AUTO_FILTER",
            "generated_at": None,
            "enabled": False,
            "prob_metric": "calibrated_hit_prob",
            "prob_bin_edges": [round(x / 20, 2) for x in range(0, 21)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "window": {},
            "window_label": "未生成",
            "min_sample_count": 30,
            "min_roi": 1.0,
        }
        _AUTO_FILTER_MTIME = mtime
        return _AUTO_FILTER_CACHE


def build_auto_filter_summary(rules: dict) -> str:
    if not rules:
        return "未生成"
    prob_metric = str(rules.get("prob_metric") or "calibrated_hit_prob")
    allowed_prob = rules.get("allowed_prob_bins", []) or []
    allowed_odds = rules.get("allowed_odds_bins", []) or []
    allowed_places = rules.get("allowed_places", []) or []
    generated_at = str(rules.get("generated_at") or "").strip()
    window_label = str(rules.get("window_label") or "").strip()
    enabled = bool(rules.get("enabled", False))

    def _join(items: list, limit: int = 3) -> str:
        vals = [str(v) for v in items if str(v)]
        if not vals:
            return "なし"
        if len(vals) > limit:
            return ", ".join(vals[:limit]) + f" ほか{len(vals) - limit}"
        return ", ".join(vals)

    return (
        f"{prob_metric} / P:{_join(list(allowed_prob))} / "
        f"O:{_join(list(allowed_odds))} / 場:{_join(list(allowed_places))} / "
        f"更新:{generated_at or '未生成'} / 範囲:{window_label or '全期間'} / "
        f"{'有効' if enabled else '未生成'}"
    )


def load_probability_calibration() -> dict:
    global _CALIBRATION_CACHE, _CALIBRATION_MTIME
    default = {
        "method": "fallback",
        "base_prob_col": "approx_prob",
        "fallback_scale": 0.7,
        "generated_at": None,
        "train_rows": None,
        "validation_rows": None,
        "validation_metrics": {},
    }
    if not CALIBRATION_ARTIFACT.exists():
        _CALIBRATION_CACHE = default
        _CALIBRATION_MTIME = None
        return default

    mtime = CALIBRATION_ARTIFACT.stat().st_mtime
    if _CALIBRATION_CACHE is not None and _CALIBRATION_MTIME == mtime:
        return _CALIBRATION_CACHE

    try:
        payload = json.loads(CALIBRATION_ARTIFACT.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("calibration artifact must be a JSON object")
        payload.setdefault("method", "fallback")
        payload.setdefault("base_prob_col", "approx_prob")
        payload.setdefault("fallback_scale", 0.7)
        payload.setdefault("validation_metrics", {})
        _CALIBRATION_CACHE = payload
        _CALIBRATION_MTIME = mtime
        return payload
    except Exception:
        _CALIBRATION_CACHE = default
        _CALIBRATION_MTIME = mtime
        return default


def build_probability_calibration_summary(artifact: dict) -> str:
    if not artifact:
        return "未生成"
    method = str(artifact.get("method") or "fallback").lower()
    base_prob_col = str(artifact.get("base_prob_col") or "approx_prob")
    selected_source = "calibrated_prob" if method != "fallback" else base_prob_col
    metrics = artifact.get("validation_metrics") or {}
    bits: list[str] = []
    if metrics.get("brier") is not None:
        bits.append(f"Brier:{float(metrics['brier']):.4f}")
    if metrics.get("logloss") is not None:
        bits.append(f"LogLoss:{float(metrics['logloss']):.4f}")
    metric_text = " / ".join(bits) if bits else "評価なし"
    return f"{method} / 基準:{base_prob_col} / 採用:{selected_source} / {metric_text}"


def _normalize_day_mode_rules(rules: object) -> dict:
    if not isinstance(rules, dict):
        return {}
    out: dict[str, dict[str, float | int]] = {}
    for mode_name in ("normal", "reduced", "stop"):
        mode_rules = rules.get(mode_name)
        if isinstance(mode_rules, dict):
            out[mode_name] = dict(mode_rules)
    return out


def _derive_day_mode(
    *,
    rules: dict,
    real_odds_available_rate: float | None,
    missing_feature_rate: float | None,
    today_races: int | None,
    race_coverage: float | None,
) -> tuple[str, list[str], dict[str, object]]:
    normal = rules.get("normal", {}) if isinstance(rules, dict) else {}
    reduced = rules.get("reduced", {}) if isinstance(rules, dict) else {}
    stop = rules.get("stop", {}) if isinstance(rules, dict) else {}

    def _fail_if(cond: bool, reason: str, reasons: list[str]) -> None:
        if cond:
            reasons.append(reason)

    stop_reasons: list[str] = []
    _fail_if(
        real_odds_available_rate is not None
        and _to_float(stop.get("below_real_odds_available_rate")) is not None
        and real_odds_available_rate < float(stop.get("below_real_odds_available_rate")),
        f"実オッズ率 {real_odds_available_rate:.3f} < {float(stop.get('below_real_odds_available_rate')):.3f}" if real_odds_available_rate is not None and stop.get("below_real_odds_available_rate") is not None else "実オッズ率不足",
        stop_reasons,
    )
    _fail_if(
        missing_feature_rate is not None
        and _to_float(stop.get("above_missing_feature_rate")) is not None
        and missing_feature_rate > float(stop.get("above_missing_feature_rate")),
        f"欠損率 {missing_feature_rate:.3f} > {float(stop.get('above_missing_feature_rate')):.3f}" if missing_feature_rate is not None and stop.get("above_missing_feature_rate") is not None else "欠損率過多",
        stop_reasons,
    )
    _fail_if(
        today_races is not None
        and _to_float(stop.get("below_min_today_races")) is not None
        and today_races < int(float(stop.get("below_min_today_races"))),
        f"本日レース数 {today_races} < {int(float(stop.get('below_min_today_races')))}" if today_races is not None and stop.get("below_min_today_races") is not None else "本日レース数不足",
        stop_reasons,
    )
    _fail_if(
        race_coverage is not None
        and _to_float(stop.get("below_min_race_coverage")) is not None
        and race_coverage < float(stop.get("below_min_race_coverage")),
        f"網羅率 {race_coverage:.3f} < {float(stop.get('below_min_race_coverage')):.3f}" if race_coverage is not None and stop.get("below_min_race_coverage") is not None else "網羅率不足",
        stop_reasons,
    )
    if stop_reasons:
        return "stop", stop_reasons, {
            "real_odds_available_rate": real_odds_available_rate,
            "missing_feature_rate": missing_feature_rate,
            "today_races": today_races,
            "race_coverage": race_coverage,
            "threshold": stop,
        }

    normal_reasons: list[str] = []
    normal_ok = True
    if real_odds_available_rate is None or _to_float(normal.get("min_real_odds_available_rate")) is None:
        normal_ok = False
        normal_reasons.append("実オッズ率の判定不可")
    elif real_odds_available_rate < float(normal.get("min_real_odds_available_rate")):
        normal_ok = False
        normal_reasons.append(f"実オッズ率 {real_odds_available_rate:.3f} < {float(normal.get('min_real_odds_available_rate')):.3f}")

    if missing_feature_rate is None or _to_float(normal.get("max_missing_feature_rate")) is None:
        normal_ok = False
        normal_reasons.append("欠損率の判定不可")
    elif missing_feature_rate > float(normal.get("max_missing_feature_rate")):
        normal_ok = False
        normal_reasons.append(f"欠損率 {missing_feature_rate:.3f} > {float(normal.get('max_missing_feature_rate')):.3f}")

    if today_races is None or _to_float(normal.get("min_today_races")) is None:
        normal_ok = False
        normal_reasons.append("本日レース数の判定不可")
    elif today_races < int(float(normal.get("min_today_races"))):
        normal_ok = False
        normal_reasons.append(f"本日レース数 {today_races} < {int(float(normal.get('min_today_races')))}")

    if race_coverage is None or _to_float(normal.get("min_race_coverage")) is None:
        normal_ok = False
        normal_reasons.append("網羅率の判定不可")
    elif race_coverage < float(normal.get("min_race_coverage")):
        normal_ok = False
        normal_reasons.append(f"網羅率 {race_coverage:.3f} < {float(normal.get('min_race_coverage')):.3f}")

    if normal_ok:
        return "normal", [], {
            "real_odds_available_rate": real_odds_available_rate,
            "missing_feature_rate": missing_feature_rate,
            "today_races": today_races,
            "race_coverage": race_coverage,
            "threshold": normal,
        }

    reduced_reasons: list[str] = []
    reduced_ok = True
    if real_odds_available_rate is None or _to_float(reduced.get("min_real_odds_available_rate")) is None:
        reduced_ok = False
        reduced_reasons.append("実オッズ率の判定不可")
    elif real_odds_available_rate < float(reduced.get("min_real_odds_available_rate")):
        reduced_ok = False
        reduced_reasons.append(f"実オッズ率 {real_odds_available_rate:.3f} < {float(reduced.get('min_real_odds_available_rate')):.3f}")

    if missing_feature_rate is None or _to_float(reduced.get("max_missing_feature_rate")) is None:
        reduced_ok = False
        reduced_reasons.append("欠損率の判定不可")
    elif missing_feature_rate > float(reduced.get("max_missing_feature_rate")):
        reduced_ok = False
        reduced_reasons.append(f"欠損率 {missing_feature_rate:.3f} > {float(reduced.get('max_missing_feature_rate')):.3f}")

    if today_races is None or _to_float(reduced.get("min_today_races")) is None:
        reduced_ok = False
        reduced_reasons.append("本日レース数の判定不可")
    elif today_races < int(float(reduced.get("min_today_races"))):
        reduced_ok = False
        reduced_reasons.append(f"本日レース数 {today_races} < {int(float(reduced.get('min_today_races')))}")

    if race_coverage is None or _to_float(reduced.get("min_race_coverage")) is None:
        reduced_ok = False
        reduced_reasons.append("網羅率の判定不可")
    elif race_coverage < float(reduced.get("min_race_coverage")):
        reduced_ok = False
        reduced_reasons.append(f"網羅率 {race_coverage:.3f} < {float(reduced.get('min_race_coverage')):.3f}")

    if reduced_ok:
        return "reduced", reduced_reasons, {
            "real_odds_available_rate": real_odds_available_rate,
            "missing_feature_rate": missing_feature_rate,
            "today_races": today_races,
            "race_coverage": race_coverage,
            "threshold": reduced,
        }

    return "stop", normal_reasons or reduced_reasons, {
        "real_odds_available_rate": real_odds_available_rate,
        "missing_feature_rate": missing_feature_rate,
        "today_races": today_races,
        "race_coverage": race_coverage,
        "threshold": stop or reduced or normal,
    }


def build_summary(date_from: str | None = None, venue: str | None = None) -> dict:
    preds = load_predictions(limit=5000, date_from=date_from, venue=venue, include_actual=True)
    meta = load_race_meta()
    total = len(preds)
    decision_counts = Counter(str(r.get("decision", "")) for r in preds)
    buy_rows = [r for r in preds if r["decision"] == "BUY"]
    watch_rows = [r for r in preds if r["decision"] == "WATCH"]
    skip_rows = [r for r in preds if r["decision"] == "SKIP"]
    buy_count = len(buy_rows)
    watch_count = len(watch_rows)
    skip_count = len(skip_rows)
    avg_net_ev = round(sum((r["ev"] or 0.0) for r in buy_rows) / buy_count, 4) if buy_count else None
    avg_odds = round(sum((r["odds"] or 0.0) for r in buy_rows) / buy_count, 2) if buy_count else None
    avg_first_win = round(sum((r["first_win_proba"] or 0.0) for r in buy_rows) / buy_count, 4) if buy_count else None
    avg_approx_prob = round(sum((r["approx_prob"] or 0.0) for r in buy_rows) / buy_count, 4) if buy_count else None
    kelly_total_bet = round(sum((r.get("bet_amount") or 0.0) for r in buy_rows), 2) if buy_count else 0.0
    pre_race_block_rows = sum(1 for r in preds if str(r.get("pre_race_gate", "")).upper() == "BLOCK")
    pre_race_priority_rows = sum(1 for r in preds if str(r.get("pre_race_gate", "")).upper() == "PRIORITY")
    pre_race_boost_rows = sum(1 for r in preds if str(r.get("pre_race_gate", "")).upper() == "BOOST")
    pre_race_avg_score = round(
        sum((r.get("pre_race_score") or 0.0) for r in preds) / total, 4
    ) if total else None
    first_place_block_rows = sum(1 for r in preds if bool(r.get("first_place_block", False)))
    first_place_priority_rows = sum(1 for r in preds if bool(r.get("first_place_priority", False)))
    first_place_boost_rows = sum(1 for r in preds if str(r.get("first_place_gate", "")).upper() == "BOOST")
    first_place_avg_score = round(
        sum((r.get("first_place_score") or 0.0) for r in preds) / total, 4
    ) if total else None
    second_place_block_rows = sum(1 for r in preds if bool(r.get("second_place_block", False)))
    second_place_priority_rows = sum(1 for r in preds if bool(r.get("second_place_priority", False)))
    second_place_boost_rows = sum(1 for r in preds if str(r.get("second_place_gate", "")).upper() == "BOOST")
    second_place_avg_score = round(
        sum((r.get("second_place_score") or 0.0) for r in preds) / total, 4
    ) if total else None
    third_place_block_rows = sum(1 for r in preds if bool(r.get("third_place_block", False)))
    third_place_priority_rows = sum(1 for r in preds if bool(r.get("third_place_priority", False)))
    third_place_boost_rows = sum(1 for r in preds if str(r.get("third_place_gate", "")).upper() == "BOOST")
    third_place_avg_score = round(
        sum((r.get("third_place_score") or 0.0) for r in preds) / total, 4
    ) if total else None
    race_block_rows = sum(1 for r in preds if bool(r.get("race_block", False)))
    race_watch_rows = sum(1 for r in preds if bool(r.get("race_watch", False)))
    race_priority_rows = sum(1 for r in preds if bool(r.get("race_priority", False)))
    race_avg_score = round(
        sum((r.get("race_score") or 0.0) for r in preds) / total, 4
    ) if total else None
    official_odds_rows = sum(1 for r in preds if r.get("odds_source") == "real")
    fallback_odds_rows = sum(1 for r in preds if r.get("odds_source") == "estimated")
    missing_odds_rows = sum(1 for r in preds if r.get("odds_source") == "missing")
    target_races = len({str(r.get("race_id") or "").strip() for r in preds if str(r.get("race_id") or "").strip()})
    result_available_races = sum(1 for r in preds if str(r.get("actual_trifecta") or "").strip())
    real_odds_available_races = sum(1 for r in preds if bool(r.get("has_real_odds")) or r.get("odds_source") == "real")
    pending_unpublished_races = sum(
        1
        for r in preds
        if str(r.get("odds_status") or "").strip().lower() == "real_odds_pending_unpublished"
        or str(r.get("odds_fetch_status") or "").strip().lower() == "pending_unpublished"
        or "pending_unpublished" in str(r.get("stop_reason") or r.get("reason") or "").lower()
    )
    skip_reason_counts = Counter(
        str(r.get("skip_reason") or r.get("stop_reason") or "").strip()
        for r in skip_rows
        if str(r.get("skip_reason") or r.get("stop_reason") or "").strip()
    )
    top_skip_reasons = [
        {"reason": reason, "count": count}
        for reason, count in skip_reason_counts.most_common(5)
    ]
    buy_candidates = sorted(
        buy_rows,
        key=lambda r: (
            _to_float(r.get("decision_score")) if _to_float(r.get("decision_score")) is not None else float("-inf"),
            _to_float(r.get("ev")) if _to_float(r.get("ev")) is not None else float("-inf"),
            str(r.get("race_id") or ""),
        ),
        reverse=True,
    )[:3]
    buy_candidates_top = [
        {
            "race_id": r.get("race_id"),
            "date": r.get("date"),
            "jcd": r.get("jcd"),
            "venue_name": r.get("venue_name"),
            "venue_race_label": r.get("venue_race_label"),
            "race_no": r.get("race_no"),
            "race_seq": r.get("race_seq"),
            "decision": r.get("decision"),
            "recommended_trifecta": r.get("recommended_trifecta"),
            "decision_score": r.get("decision_score"),
            "ev": r.get("ev"),
            "odds": r.get("odds"),
            "calibrated_hit_prob": r.get("calibrated_hit_prob"),
            "first_place_prob": r.get("first_place_prob"),
            "second_place_score": r.get("second_place_score"),
            "third_place_score": r.get("third_place_score"),
            "first_place_gate": r.get("first_place_gate"),
            "second_place_gate": r.get("second_place_gate"),
            "third_place_gate": r.get("third_place_gate"),
            "pre_race_score": r.get("pre_race_score"),
            "pre_race_gate": r.get("pre_race_gate"),
            "kelly_fraction": r.get("kelly_fraction"),
            "bet_amount": r.get("bet_amount"),
            "odds_source": r.get("odds_source"),
            "reason": r.get("reason"),
        }
        for r in buy_candidates
    ]
    venue_counts: dict[str, int] = {}
    for row in preds:
        venue_name = row.get("venue_name") or "不明"
        venue_counts[venue_name] = venue_counts.get(venue_name, 0) + 1

    dual = load_latest_dual_mode()
    rolling = load_daily_rolling_summary()
    mode_flags = load_mode_flags()
    strategy_mode = str(mode_flags.get("strategy_mode") or "NORMAL").upper()
    roi_filter_rules = load_roi_filter_rules()
    roi_filter_summary = build_roi_filter_summary(roi_filter_rules)
    auto_filter_rules = load_auto_filter_rules()
    auto_filter_summary = build_auto_filter_summary(auto_filter_rules)
    strategy_config = load_strategy_config()
    day_mode_rules = _normalize_day_mode_rules(strategy_config.get("day_mode_rules", {}))
    bet_management = dict(strategy_config.get("bet_management", {}) or {})
    current_venues = {
        _venue_from_race_id(r.get("race_id"))
        for r in preds
        if _venue_from_race_id(r.get("race_id"))
    }
    allowed_places = set(auto_filter_rules.get("allowed_places", []) or [])
    auto_filter_enabled = bool(auto_filter_rules.get("enabled", False))
    auto_filter_live_note = ""
    if strategy_mode == "AUTO_FILTER" and not auto_filter_enabled:
        auto_filter_live_note = "条件未生成のためNORMALへフォールバック"
    elif strategy_mode == "AUTO_FILTER" and allowed_places and current_venues and not (allowed_places & current_venues):
        auto_filter_live_note = (
            f"現場:{', '.join(sorted(current_venues))} / 条件:{', '.join(sorted(allowed_places))}"
        )
    calibration_artifact = load_probability_calibration()
    calibration_summary = build_probability_calibration_summary(calibration_artifact)
    gate_health = load_gate_health_summary()
    ops_health = load_ops_health_summary()
    upstream_health = load_upstream_health_summary()
    effective_mode_counts = Counter(
        str(r.get("effective_strategy_mode", r.get("strategy_mode", "")) or "").upper()
        for r in preds
        if str(r.get("effective_strategy_mode", r.get("strategy_mode", "")) or "").strip()
    )
    effective_strategy_mode = max(effective_mode_counts.items(), key=lambda kv: kv[1])[0] if effective_mode_counts else strategy_mode
    trifecta_recent30 = {}
    exacta_recent30 = {}
    rolling_recent30 = {}
    if isinstance(rolling, dict):
        for row in rolling.get("windows", []):
            if row.get("window") == "recent30":
                rolling_recent30 = row
                break
    if isinstance(dual, dict):
        for row in dual.get("results", []):
            if row.get("window") == "recent30" and row.get("mode") == "trifecta" and not rolling_recent30:
                trifecta_recent30 = row
            if row.get("window") == "recent30" and row.get("mode") == "exacta_filtered":
                exacta_recent30 = row
    if rolling_recent30:
        trifecta_recent30 = {
            "buy": int(rolling_recent30.get("buy_count") or 0),
            "hits": int(rolling_recent30.get("hit_count") or 0),
            "trifecta_hit_rate": rolling_recent30.get("hit_rate"),
            "trifecta_roi": rolling_recent30.get("roi"),
            "races": int(rolling_recent30.get("races") or 0),
            "days": int(rolling_recent30.get("days") or 0),
            "sample_note": rolling_recent30.get("sample_note") or "",
        }

    trifecta_buy = int(trifecta_recent30.get("buy") or 0)
    trifecta_hit_rate = trifecta_recent30.get("trifecta_hit_rate")
    trifecta_hits = int(trifecta_recent30.get("hits") or 0)
    if trifecta_hits <= 0 and trifecta_buy > 0:
        trifecta_hits = int(round((float(trifecta_hit_rate) if trifecta_hit_rate is not None else 0.0) * trifecta_buy))
    trifecta_ci_low, trifecta_ci_high = _wilson_interval(trifecta_hits, trifecta_buy)
    trifecta_width = trifecta_ci_high - trifecta_ci_low
    trifecta_quality = _confidence_grade(trifecta_buy, trifecta_width)

    exacta_buy = int(exacta_recent30.get("buy") or 0)
    exacta_hit_rate = exacta_recent30.get("exacta_hit_rate")
    exacta_hits = int(round((float(exacta_hit_rate) if exacta_hit_rate is not None else 0.0) * exacta_buy))
    exacta_ci_low, exacta_ci_high = _wilson_interval(exacta_hits, exacta_buy)
    exacta_width = exacta_ci_high - exacta_ci_low
    exacta_quality = _confidence_grade(exacta_buy, exacta_width)

    top_venue = None
    if venue_counts:
        top_venue = max(venue_counts.items(), key=lambda kv: kv[1])[0]
    real_odds_coverage = round(official_odds_rows / total, 4) if total else None
    latest_prediction_date = None
    if preds:
        pred_dates = [str(r.get("date") or "").strip() for r in preds if str(r.get("date") or "").strip()]
        latest_prediction_date = max(pred_dates) if pred_dates else None
    latest_source_date = None
    if TODAY_RACES.exists():
        try:
            src_df = pd.read_csv(TODAY_RACES, usecols=lambda c: str(c).lower() == "date")
            if "date" in src_df.columns:
                src_dates = pd.to_datetime(src_df["date"], errors="coerce").dropna()
                if not src_dates.empty:
                    latest_source_date = str(src_dates.max().date())
        except Exception:
            latest_source_date = None
    staleness_days = None
    if latest_prediction_date:
        try:
            staleness_days = (datetime.now().date() - datetime.fromisoformat(latest_prediction_date).date()).days
        except Exception:
            staleness_days = None
    today_races_count = 0
    if TODAY_RACES.exists():
        try:
            today_races_df = pd.read_csv(TODAY_RACES, low_memory=False)
            if "race_id" in today_races_df.columns:
                today_races_count = int(today_races_df["race_id"].astype(str).nunique())
            elif "union_key" in today_races_df.columns:
                today_races_count = int(today_races_df["union_key"].astype(str).nunique())
            else:
                today_races_count = int(today_races_df.shape[0])
        except Exception:
            today_races_count = int(len(meta)) if meta is not None else 0
    elif meta is not None:
        today_races_count = int(len(meta))
    predicted_race_count = int(
        len({str(r.get("race_id") or "").strip() for r in preds if str(r.get("race_id") or "").strip()})
    )
    missing_feature_rate = None
    gate_rows = _to_float(gate_health.get("rows"))
    gate_missing_rows = _to_float(gate_health.get("missing_rows"))
    if gate_rows and gate_missing_rows is not None:
        missing_feature_rate = round(float(gate_missing_rows) / float(gate_rows), 4)
    day_mode, day_mode_reasons, day_mode_metrics = _derive_day_mode(
        rules=day_mode_rules,
        real_odds_available_rate=real_odds_coverage,
        missing_feature_rate=missing_feature_rate,
        today_races=today_races_count,
        race_coverage=round(predicted_race_count / today_races_count, 4) if today_races_count > 0 else None,
    )
    race_yosou_view = buildRaceYosouViewModel(date_from, venue)
    race_yosou_meta = dict(race_yosou_view.get("meta") or {})
    recent30_trifecta = trifecta_recent30
    if recent30_trifecta:
        if recent30_trifecta.get("hit_rate") is not None:
            race_yosou_meta["hitRate"] = round(float(recent30_trifecta.get("hit_rate")) * 100.0, 1)
        if recent30_trifecta.get("roi") is not None:
            race_yosou_meta["recoveryRate"] = round(float(recent30_trifecta.get("roi")) * 100.0, 1)
        race_yosou_meta["recent30WindowRaces"] = int(recent30_trifecta.get("window_races") or recent30_trifecta.get("buy") or 0)
    if latest_prediction_date:
        race_yosou_meta["updatedAt"] = f"{latest_prediction_date} 更新"
    race_yosou_view["meta"] = race_yosou_meta
    race_yosou_source = str(race_yosou_view.get("source") or "legacy")
    latest_refresh = (ops_health.get("pipeline") or {}).get("generated_at") or datetime.now().isoformat(timespec="seconds")
    latest_guard = (ops_health.get("guard") or {}).get("generated_at")
    latest_error_reason = ""
    for reasons in [
        (ops_health.get("guard") or {}).get("reasons") or [],
        (ops_health.get("compare") or {}).get("reasons") or [],
    ]:
        if isinstance(reasons, list):
            for reason in reasons:
                text = str(reason or "").strip()
                if text:
                    latest_error_reason = text
                    break
        if latest_error_reason:
            break
    if not latest_error_reason:
        top_gate_reason = next(iter(sorted((gate_health.get("reason_keyword_counts") or {}).items(), key=lambda kv: kv[1], reverse=True)), None)
        if top_gate_reason:
            latest_error_reason = str(top_gate_reason[0])

    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_refresh": latest_refresh,
        "latest_guard": latest_guard,
        "latest_error_reason": latest_error_reason,
        "latest_prediction_date": latest_prediction_date,
        "latest_source_date": latest_source_date,
        "prediction_staleness_days": staleness_days,
        "target_races": target_races,
        "result_available_races": result_available_races,
        "real_odds_available_races": real_odds_available_races,
        "pending_unpublished_races": pending_unpublished_races,
        "predictions_total": total,
        "decision_counts": dict(decision_counts),
        "buy_count": buy_count,
        "watch_count": watch_count,
        "skip_count": skip_count,
        "buy_rate": round(buy_count / total, 4) if total else None,
        "watch_rate": round(watch_count / total, 4) if total else None,
        "skip_rate": round(skip_count / total, 4) if total else None,
        "buy_avg_ev": avg_net_ev,
        "buy_avg_first_win_proba": avg_first_win,
        "buy_avg_approx_prob": avg_approx_prob,
        "buy_avg_odds": avg_odds,
        "official_odds_rows": official_odds_rows,
        "fallback_odds_rows": fallback_odds_rows,
        "missing_odds_rows": missing_odds_rows,
        "real_odds_coverage": real_odds_coverage,
        "real_odds_rate": real_odds_coverage,
        "skip_reason_counts": dict(skip_reason_counts),
        "top_skip_reasons": top_skip_reasons,
        "buy_candidates_top": buy_candidates_top,
        "day_mode": day_mode,
        "day_mode_label": {"normal": "通常", "reduced": "縮小", "stop": "停止"}.get(day_mode, "未判定"),
        "day_mode_reasons": day_mode_reasons,
        "day_mode_metrics": day_mode_metrics,
        "predicted_race_count": predicted_race_count,
        "top_venue": top_venue,
        "strategy_mode": strategy_mode,
        "effective_strategy_mode": effective_strategy_mode,
        "mode_flags": mode_flags,
        "roi_filter_rules": roi_filter_rules,
        "roi_filter_summary": roi_filter_summary,
        "auto_filter_rules": auto_filter_rules,
        "auto_filter_summary": auto_filter_summary,
        "auto_filter_live_note": auto_filter_live_note,
        "calibration_artifact": calibration_artifact,
        "calibration_summary": calibration_summary,
        "gate_health": gate_health,
        "ops_health": ops_health,
        "upstream_health": upstream_health,
        "strategy_config": strategy_config,
        "bet_management": {
            "bankroll": _to_float(bet_management.get("bankroll")),
            "max_kelly_fraction": _to_float(bet_management.get("max_kelly_fraction")),
        },
        "kelly_total_bet": kelly_total_bet,
        "pre_race_block_rows": pre_race_block_rows,
        "pre_race_priority_rows": pre_race_priority_rows,
        "pre_race_boost_rows": pre_race_boost_rows,
        "pre_race_avg_score": pre_race_avg_score,
        "first_place_block_rows": first_place_block_rows,
        "first_place_priority_rows": first_place_priority_rows,
        "first_place_boost_rows": first_place_boost_rows,
        "first_place_avg_score": first_place_avg_score,
        "second_place_block_rows": second_place_block_rows,
        "second_place_priority_rows": second_place_priority_rows,
        "second_place_boost_rows": second_place_boost_rows,
        "second_place_avg_score": second_place_avg_score,
        "third_place_block_rows": third_place_block_rows,
        "third_place_priority_rows": third_place_priority_rows,
        "third_place_boost_rows": third_place_boost_rows,
        "third_place_avg_score": third_place_avg_score,
        "race_block_rows": race_block_rows,
        "race_watch_rows": race_watch_rows,
        "race_priority_rows": race_priority_rows,
        "race_avg_score": race_avg_score,
        "latest_dual_mode": dual,
        "recent30_trifecta": {
            "buy": trifecta_buy,
            "hits": trifecta_hits,
            "hit_rate": trifecta_hit_rate,
            "hit_rate_ci": [round(trifecta_ci_low, 4), round(trifecta_ci_high, 4)],
            "roi": trifecta_recent30.get("trifecta_roi"),
            "confidence": trifecta_quality,
            "window_races": int(trifecta_recent30.get("races") or 0),
            "window_days": int(trifecta_recent30.get("days") or 0),
            "sample_note": trifecta_recent30.get("sample_note") or "",
        },
        "recent30_exacta": {
            "buy": exacta_buy,
            "hits": exacta_hits,
            "hit_rate": exacta_hit_rate,
            "hit_rate_ci": [round(exacta_ci_low, 4), round(exacta_ci_high, 4)],
            "roi": exacta_recent30.get("exacta_roi"),
            "confidence": exacta_quality,
        },
        "race_yosou_source": race_yosou_source,
        "race_yosou_view": race_yosou_view,
    }


def _json_response(payload: dict | list, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        mimetype="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _load_repo_audit_progress() -> dict[str, Any]:
    if not FINAL_GOAL_PROGRESS_JSON.exists():
        return {}
    try:
        payload = json.loads(FINAL_GOAL_PROGRESS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_uploaded_live_odds(file_storage) -> dict:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ValueError("CSVファイルが選択されていません")

    df = pd.read_csv(file_storage)
    if df.empty:
        raise ValueError("CSVが空です")

    alias_map = {
        "race_id": ["race_id", "レースID", "race"],
        "trifecta": ["trifecta", "recommended_trifecta", "買い目", "3連単", "組み合わせ"],
        "odds": ["odds", "オッズ", "払戻", "odds_trifecta"],
    }
    source_cols: dict[str, str] = {}
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for target, aliases in alias_map.items():
        match = None
        for alias in aliases:
            if alias in df.columns:
                match = alias
                break
            alias_key = str(alias).strip().lower()
            if alias_key in lowered:
                match = lowered[alias_key]
                break
        if match is None:
            raise ValueError(f"必須列がありません: {target}")
        source_cols[target] = match

    out = pd.DataFrame({
        "race_id": df[source_cols["race_id"]].astype(str).str.strip(),
        "trifecta": df[source_cols["trifecta"]].astype(str).map(_normalize_trifecta_text),
        "odds": pd.to_numeric(df[source_cols["odds"]], errors="coerce"),
    })
    out = out.dropna(subset=["race_id", "odds"]).copy()
    out = out[out["trifecta"].astype(str).str.len() > 0].copy()
    out["odds"] = out["odds"].astype(float)
    out = out.drop_duplicates(subset=["race_id", "trifecta"], keep="last").reset_index(drop=True)
    if out.empty:
        raise ValueError("有効なオッズ行がありません")

    LIVE_ODDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(LIVE_ODDS_CSV, index=False, encoding="utf-8-sig")
    return {
        "path": str(LIVE_ODDS_CSV),
        "rows": int(len(out)),
        "race_count": int(out["race_id"].nunique()),
        "updated_at": _iso_now(),
    }


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_ops_state() -> dict:
    with _OPS_RUN_LOCK:
        proc = _OPS_RUN_PROCESS
        state = dict(_OPS_RUN_STATE)
    if proc is not None:
        code = proc.poll()
        if code is None:
            state["status"] = "running"
            state["returncode"] = None
        else:
            state["status"] = "ok" if int(code) == 0 else "failed"
            state["returncode"] = int(code)
    return state


def _start_ops_run(mode: str) -> tuple[bool, dict]:
    global _OPS_RUN_PROCESS

    with _OPS_RUN_LOCK:
        if _OPS_RUN_PROCESS is not None and _OPS_RUN_PROCESS.poll() is None:
            running = dict(_OPS_RUN_STATE)
            running["status"] = "running"
            running["message"] = "already running"
            return False, running

        if not OPS_PIPELINE_BAT.exists():
            _OPS_RUN_STATE.update(
                {
                    "status": "failed",
                    "mode": mode,
                    "started_at": _iso_now(),
                    "finished_at": _iso_now(),
                    "returncode": 127,
                    "message": f"runner not found: {OPS_PIPELINE_BAT}",
                }
            )
            return False, dict(_OPS_RUN_STATE)

        _OPS_RUN_STATE.update(
            {
                "status": "running",
                "mode": mode,
                "started_at": _iso_now(),
                "finished_at": None,
                "returncode": None,
                "message": "started",
            }
        )
        _OPS_RUN_PROCESS = subprocess.Popen(
            [str(OPS_PIPELINE_BAT), mode],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        return True, dict(_OPS_RUN_STATE)


def _finalize_ops_state_if_done() -> None:
    global _OPS_RUN_PROCESS
    with _OPS_RUN_LOCK:
        proc = _OPS_RUN_PROCESS
        if proc is None:
            return
        code = proc.poll()
        if code is None:
            return
        _OPS_RUN_STATE.update(
            {
                "status": "ok" if int(code) == 0 else "failed",
                "finished_at": _iso_now(),
                "returncode": int(code),
                "message": "completed" if int(code) == 0 else "failed",
            }
        )
        _OPS_RUN_PROCESS = None


@app.get("/api/health")
def api_health() -> Response:
    return _json_response({"ok": True})


@app.get("/api/ops/status")
def api_ops_status() -> Response:
    _finalize_ops_state_if_done()
    return _json_response(_get_ops_state())


@app.get("/api/ops/report")
def api_ops_report() -> Response:
    data = _load_json_file(OPS_DAILY_PIPELINE, {})
    if not data:
        return _json_response({"ok": False, "error": "report not found"}, status=404)
    return _json_response({"ok": True, "report": data})


@app.post("/api/ops/run")
def api_ops_run() -> Response:
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "predict")).strip().lower()
    allowed = {
        "predict",
        "pre-race",
        "odds-refresh",
        "post-race",
        "backtest",
        "guard",
        "full",
        "weekly",
        "weekly-promote",
    }
    if mode not in allowed:
        return _json_response(
            {"ok": False, "error": f"unsupported mode: {mode}", "allowed": sorted(allowed)},
            status=400,
        )
    started, state = _start_ops_run(mode)
    return _json_response({"ok": started, "state": state}, status=202 if started else 409)


@app.post("/api/odds/upload")
def api_odds_upload() -> Response:
    try:
        result = _save_uploaded_live_odds(request.files.get("file"))
        return _json_response({"ok": True, "result": result})
    except Exception as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)


@app.get("/api/summary")
def api_summary() -> Response:
    date_from = request.args.get("date_from") or None
    venue = request.args.get("venue") or None
    return _json_response(build_summary(date_from=date_from, venue=venue))


@app.get("/api/predictions")
def api_predictions() -> Response:
    decision = (request.args.get("decision", "") or "").upper() or None
    date_from = request.args.get("date_from") or None
    venue = request.args.get("venue") or None
    try:
        limit = int(request.args.get("limit", "300"))
    except Exception:
        limit = 300
    limit = max(1, min(limit, 5000))
    return _json_response(load_predictions(limit=limit, decision=decision, date_from=date_from, venue=venue))


@app.get("/api/venues")
def api_venues() -> Response:
    return _json_response({"venues": list_venues()})


@app.get("/api/venue_summary")
def api_venue_summary() -> Response:
    window = (request.args.get("window", "all") or "all").lower()
    if window not in {"all", "30", "100", "300"}:
        window = "all"
    date_from = request.args.get("date_from") or None
    return _json_response(build_venue_summary(window=window, date_from=date_from))


@app.get("/api/performance_breakdown")
def api_performance_breakdown() -> Response:
    window = (request.args.get("window", "all") or "all").lower()
    if window not in {"all", "30", "100", "300"}:
        window = "all"
    date_from = request.args.get("date_from") or None
    venue = request.args.get("venue") or None
    return _json_response(build_performance_breakdown(window=window, date_from=date_from, venue=venue))


@app.get("/api/experiments")
def api_experiments() -> Response:
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 1000))
    return _json_response(load_experiments(limit=limit))


@app.get("/api/raceyosou")
def api_raceyosou() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = str(request.args.get("jcd") or "01").zfill(2)
    path = ROOT / "data" / "ui" / date_value / f"raceyosou_{jcd}.json"
    if path.exists():
        try:
            return _json_response(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    payload = {
        "date": f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]}",
        "venue": JCD_TO_VENUE.get(jcd, ""),
        "event": JCD_TO_VENUE.get(jcd, ""),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "source": {"url": "", "fetchedAt": "", "stage": "missing", "model_version": "mvp-baseline"},
        "dataStatus": "unavailable",
        "warnings": [],
        "races": [],
    }
    return _json_response(payload)


def _date_for_daily_dir(date_value: str) -> str:
    text = re.sub(r"[^0-9]", "", str(date_value or ""))
    if len(text) != 8:
        text = datetime.now().strftime("%Y%m%d")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _daily_artifact(date_value: str, filename: str) -> Path:
    daily_root = ROOT / "reports" / "daily"
    hyphen = _date_for_daily_dir(date_value)
    compact = hyphen.replace("-", "")
    for day in [hyphen, compact]:
        path = daily_root / day / filename
        if path.exists():
            return path
    return daily_root / hyphen / filename


def _boat_no_span(combo: str) -> str:
    return "-".join([part for part in re.findall(r"[1-6]", str(combo or ""))[:3]])


def _confidence_label(prob: float | None, rank: int) -> str:
    if prob is not None and prob >= 0.04:
        return "A"
    if rank <= 3:
        return "B"
    return "C"


def build_venue_ai_yosou(date_value: str, jcd: str) -> dict:
    jcd = _normalize_jcd(jcd)
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    venue = JCD_TO_VENUE.get(jcd, jcd)

    cand_path = _daily_artifact(compact, "trifecta_candidates.csv")
    features_path = _daily_artifact(compact, "today_features.csv")
    proba_path = _daily_artifact(compact, "today_win_proba.csv")
    targets_path = _daily_artifact(compact, "race_targets.csv")

    candidates = pd.read_csv(cand_path) if cand_path.exists() else pd.DataFrame()
    features = pd.read_csv(features_path) if features_path.exists() else pd.DataFrame()
    proba = pd.read_csv(proba_path) if proba_path.exists() else pd.DataFrame()
    targets = pd.read_csv(targets_path) if targets_path.exists() else pd.DataFrame()

    if not candidates.empty and "race_id" in candidates.columns:
        candidates["jcd_norm"] = candidates["race_id"].astype(str).str.extract(r"^\d{8}-(\d{2})-")[0]
        candidates["race_no_num"] = pd.to_numeric(candidates["race_id"].astype(str).str.extract(r"-(\d{2})$")[0], errors="coerce")
        candidates = candidates[candidates["jcd_norm"] == jcd].copy()

    if not features.empty and "race_id" in features.columns:
        features["jcd_norm"] = features["jcd"].map(_normalize_jcd) if "jcd" in features.columns else features["race_id"].astype(str).str.extract(r"^\d{8}-(\d{2})-")[0]
        features["race_no_num"] = pd.to_numeric(features["race_no"], errors="coerce") if "race_no" in features.columns else pd.to_numeric(features["race_id"].astype(str).str.extract(r"-(\d{2})$")[0], errors="coerce")
        features = features[features["jcd_norm"] == jcd].copy()

    if not proba.empty and "race_id" in proba.columns:
        proba["jcd_norm"] = proba["race_id"].astype(str).str.extract(r"^\d{8}-(\d{2})-")[0]
        proba["race_no_num"] = pd.to_numeric(proba["race_id"].astype(str).str.extract(r"-(\d{2})$")[0], errors="coerce")
        proba = proba[proba["jcd_norm"] == jcd].copy()

    if not targets.empty:
        targets["jcd_norm"] = targets["jcd"].map(_normalize_jcd) if "jcd" in targets.columns else ""
        targets = targets[targets["jcd_norm"] == jcd].copy()

    source_warnings: list[str] = []
    if cand_path.exists() and candidates.empty:
        source_warnings.append("trifecta_candidates.csv は空です")
    elif not cand_path.exists():
        source_warnings.append("trifecta_candidates.csv がありません")
    if features_path.exists() and features.empty:
        source_warnings.append("today_features.csv は空です")
    elif not features_path.exists():
        source_warnings.append("today_features.csv がありません")
    if proba_path.exists() and proba.empty:
        source_warnings.append("today_win_proba.csv は空です")
    elif not proba_path.exists():
        source_warnings.append("today_win_proba.csv がありません")
    if targets_path.exists() and targets.empty:
        source_warnings.append("race_targets.csv は空です")
    elif not targets_path.exists():
        source_warnings.append("race_targets.csv がありません")

    races: list[dict[str, Any]] = []
    for race_no in range(1, 13):
        race_candidates = candidates[candidates["race_no_num"] == race_no].copy() if not candidates.empty else pd.DataFrame()
        if not race_candidates.empty:
            race_candidates = race_candidates.sort_values("approx_prob", ascending=False).head(5)
        race_features = features[features["race_no_num"] == race_no].copy() if not features.empty else pd.DataFrame()
        race_proba = proba[proba["race_no_num"] == race_no].copy() if not proba.empty else pd.DataFrame()

        boats = []
        for lane in range(1, 7):
            feature_row = race_features[race_features["lane"].astype(str) == str(lane)].head(1) if not race_features.empty and "lane" in race_features.columns else pd.DataFrame()
            proba_row = race_proba[race_proba["lane"].astype(str) == str(lane)].head(1) if not race_proba.empty and "lane" in race_proba.columns else pd.DataFrame()
            f = feature_row.iloc[0] if not feature_row.empty else {}
            p = proba_row.iloc[0] if not proba_row.empty else {}
            boats.append({
                "lane": lane,
                "racerId": _text_or_empty(f.get("racer_id", "")) if hasattr(f, "get") else "",
                "class": _text_or_empty(f.get("racer_class", "")) if hasattr(f, "get") else "",
                "avgSt": _to_float(f.get("avg_st")) if hasattr(f, "get") else None,
                "national2Rate": _to_float(f.get("national_2ren_rate")) if hasattr(f, "get") else None,
                "local2Rate": _to_float(f.get("local_2ren_rate")) if hasattr(f, "get") else None,
                "motor2Rate": _to_float(f.get("motor_2ren_rate")) if hasattr(f, "get") else None,
                "boat2Rate": _to_float(f.get("boat_2ren_rate")) if hasattr(f, "get") else None,
                "winProba": _to_float(p.get("final_win_proba")) if hasattr(p, "get") else None,
                "rank": int(_to_float(p.get("final_rank")) or lane) if hasattr(p, "get") else lane,
            })

        predictions = []
        for idx, (_, row) in enumerate(race_candidates.iterrows(), start=1):
            approx = _to_float(row.get("approx_prob"))
            decision = _text_or_empty(row.get("decision") or row.get("final_decision") or row.get("decision_label") or "")
            predictions.append({
                "rank": idx,
                "combo": _boat_no_span(_text_or_empty(row.get("trifecta"))),
                "confidence": _confidence_label(approx, idx),
                "decision": decision,
                "approxProb": approx,
                "mainScore": _to_float(row.get("main_score")),
                "odds": _to_float(row.get("odds")),
                "ev": _to_float(row.get("ev") or row.get("expected_value")),
                "stake": _to_float(row.get("stake") or row.get("bet_amount")),
                "stopReason": _text_or_empty(row.get("stop_reason") or row.get("skip_reason") or row.get("reason")),
                "reason": _text_or_empty(row.get("reason")),
                "firstLane": int(_to_float(row.get("first_lane")) or 0),
                "conditionalMode": _to_bool(row.get("conditional_mode")),
            })

        target_row = targets[targets["race_no"].astype(str).str.lstrip("0") == str(race_no)].head(1) if not targets.empty and "race_no" in targets.columns else pd.DataFrame()
        deadline = _text_or_empty(target_row.iloc[0].get("deadline", "")) if not target_row.empty else ""
        race_warnings: list[str] = []
        if race_candidates.empty:
            race_warnings.append("予想候補なし")
        if race_features.empty:
            race_warnings.append("出走データ不足")
        if race_proba.empty:
            race_warnings.append("確率未反映")
        race_status = "available" if predictions else ("pending" if (not features.empty or not proba.empty or not targets.empty) else "missing")
        races.append({
            "raceNo": race_no,
            "raceTitle": f"{race_no}R",
            "deadline": deadline,
            "predictions": predictions,
            "boats": boats,
            "dataStatus": race_status,
            "warnings": race_warnings,
        })

    any_predictions = any((race.get("predictions") or []) for race in races)
    if any_predictions:
        data_status = "available"
    elif not candidates.empty or not features.empty or not proba.empty or not targets.empty:
        data_status = "pending"
    else:
        data_status = "missing"

    return {
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": venue,
        "event": "独自AI予想",
        "modelVersion": "boatrace-ai-mvp",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "dataStatus": data_status,
        "warnings": source_warnings,
        "sourceFiles": {
            "trifectaCandidates": str(cand_path),
            "todayFeatures": str(features_path),
            "todayWinProba": str(proba_path),
            "raceTargets": str(targets_path),
        },
        "races": races,
    }


@app.get("/api/venue-ai-yosou")
def api_venue_ai_yosou() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = str(request.args.get("jcd") or "12").zfill(2)
    return _json_response(build_venue_ai_yosou(date_value, jcd))


PUBLIC_RACELIST_URL = "https://www.boatrace.jp/owpc/pc/race/racelist?hd={date8}&jcd={jcd}&rno={rno}"
PUBLIC_BEFOREINFO_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?hd={date8}&jcd={jcd}&rno={rno}"
PUBLIC_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp-local-ui/1.0; +https://www.boatrace.jp/)"


def _public_fetch_html(url: str, timeout_sec: float) -> tuple[str, str]:
    try:
        response = requests.get(url, timeout=timeout_sec, headers={"User-Agent": PUBLIC_USER_AGENT})
        response.raise_for_status()
        return response.text, "live"
    except Exception as exc:
        return "", f"error:{exc}"


def _boat_no_from_public_boat(boat: dict[str, Any]) -> int:
    return int(_to_float(boat.get("boat_no") or boat.get("no") or boat.get("lane")) or 0)


def _public_boat_view(boat: dict[str, Any], before_boat: dict[str, Any]) -> dict[str, Any]:
    lane = _boat_no_from_public_boat(boat) or _boat_no_from_public_boat(before_boat)
    return {
        "lane": lane,
        "racerName": _text_or_empty(boat.get("racer_name") or boat.get("name")),
        "racerId": _text_or_empty(boat.get("racer_id")),
        "avgSt": _to_float(boat.get("avg_st")),
        "national2Rate": _to_float(boat.get("national_2rate") or boat.get("national_2ren_rate")),
        "local2Rate": _to_float(boat.get("local_2rate") or boat.get("local_2ren_rate")),
        "motor2Rate": _to_float(boat.get("motor_2rate") or boat.get("motor_2ren_rate")),
        "boat2Rate": _to_float(boat.get("boat_2rate") or boat.get("boat_2ren_rate")),
        "exhibitionTime": _to_float(before_boat.get("exhibitionTime") or before_boat.get("exhibition_time")),
        "startExhibitionSt": _to_float(before_boat.get("startExhibitionSt") or before_boat.get("exhibition_st")),
        "startExhibitionCourse": _to_float(before_boat.get("startExhibitionCourse")),
    }


def _public_status_text(value: object, fallback: str = "cache") -> str:
    if isinstance(value, dict):
        parts = [f"{key}={val}" for key, val in value.items() if val not in (None, "")]
        return ",".join(parts) if parts else fallback
    text = _text_or_empty(value)
    return text or fallback


def _public_snapshot_from_ui_payload(
    *,
    payload: dict[str, Any],
    source_path: Path,
    date_dir: str,
    compact: str,
    jcd: str,
    race_no: int,
) -> dict[str, Any]:
    races = payload.get("races") if isinstance(payload.get("races"), list) else []
    race = {}
    for item in races:
        if not isinstance(item, dict):
            continue
        item_no = int(_to_float(item.get("raceNo") or item.get("raceNumber")) or 0)
        if item_no == race_no:
            race = item
            break
    if not race and len(races) == 1 and isinstance(races[0], dict):
        race = races[0]

    boats = []
    for boat in race.get("boats") or []:
        if not isinstance(boat, dict):
            continue
        lane = _boat_no_from_public_boat(boat)
        boats.append({
            "lane": lane,
            "racerName": _text_or_empty(boat.get("racer_name") or boat.get("racerName") or boat.get("name")),
            "racerId": _text_or_empty(boat.get("racer_id") or boat.get("racerId")),
            "avgSt": _to_float(boat.get("avg_st") or boat.get("avgSt")),
            "national2Rate": _to_float(boat.get("national_2rate") or boat.get("national2Rate") or boat.get("national_2ren_rate")),
            "local2Rate": _to_float(boat.get("local_2rate") or boat.get("local2Rate") or boat.get("local_2ren_rate")),
            "motor2Rate": _to_float(boat.get("motor_2rate") or boat.get("motor2Rate") or boat.get("motor_2ren_rate")),
            "boat2Rate": _to_float(boat.get("boat_2rate") or boat.get("boat2Rate") or boat.get("boat_2ren_rate")),
            "exhibitionTime": _to_float(boat.get("exhibitionTime") or boat.get("exhibition_time")),
            "startExhibitionSt": _to_float(boat.get("startExhibitionSt") or boat.get("exhibition_st")),
            "startExhibitionCourse": _to_float(boat.get("startExhibitionCourse")),
        })

    return {
        "status": "cache_public_ui",
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "raceTitle": _text_or_empty(race.get("raceTitle")),
        "deadline": _text_or_empty(race.get("deadline")),
        "weather": race.get("weather") if isinstance(race.get("weather"), dict) else {},
        "startExhibition": race.get("startExhibition") if isinstance(race.get("startExhibition"), list) else [],
        "boats": boats,
        "statuses": {
            "racelist": _public_status_text(race.get("dataStatus") or payload.get("dataStatus")),
            "beforeinfo": _public_status_text(((race.get("beforeInfo") or {}).get("dataStatus") if isinstance(race.get("beforeInfo"), dict) else None)
            or race.get("dataStatus")
            or payload.get("dataStatus")
            or "cache"),
        },
        "source": "local_public_ui_cache",
        "sourcePath": str(source_path),
        "sourceUrls": {},
        "fetchStatus": {
            "racelist": "cache",
            "beforeinfo": "cache",
        },
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    }


def load_public_race_snapshot(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    jcd = _normalize_jcd(jcd)
    race_no = max(1, min(int(race_no or 1), 12))
    cache_key = (compact, jcd, race_no)
    now = datetime.now()
    cached_at = _PUBLIC_RACE_CACHE_TS.get(cache_key)
    if cached_at and (now - cached_at).total_seconds() < 45:
        return _PUBLIC_RACE_CACHE[cache_key]

    racelist_url = PUBLIC_RACELIST_URL.format(date8=compact, jcd=jcd, rno=race_no)
    beforeinfo_url = PUBLIC_BEFOREINFO_URL.format(date8=compact, jcd=jcd, rno=race_no)
    racelist_html, racelist_fetch = _public_fetch_html(racelist_url, 4.0)
    beforeinfo_html, beforeinfo_fetch = _public_fetch_html(beforeinfo_url, 4.0)

    if not racelist_html and not beforeinfo_html:
        cached_payload, cached_path = _load_official_raceyosou_payload(compact, jcd)
        if isinstance(cached_payload, dict) and cached_path is not None:
            payload = _public_snapshot_from_ui_payload(
                payload=cached_payload,
                source_path=cached_path,
                date_dir=date_dir,
                compact=compact,
                jcd=jcd,
                race_no=race_no,
            )
            payload["fetchStatus"] = {
                "racelist": racelist_fetch,
                "beforeinfo": beforeinfo_fetch,
                "fallback": "local_public_ui_cache",
            }
            _PUBLIC_RACE_CACHE[cache_key] = payload
            _PUBLIC_RACE_CACHE_TS[cache_key] = now
            return payload

    racelist = parse_racelist_html(racelist_html, target_date=date_dir, jcd=jcd, race_no=race_no) if racelist_html else {}
    beforeinfo = parse_beforeinfo_html(beforeinfo_html, date_dir, jcd, race_no) if beforeinfo_html else {}
    racelist_boats = {
        _boat_no_from_public_boat(boat): boat
        for boat in racelist.get("boats", [])
        if isinstance(boat, dict) and _boat_no_from_public_boat(boat)
    }
    before_boats = {
        _boat_no_from_public_boat(boat): boat
        for boat in beforeinfo.get("boats", [])
        if isinstance(boat, dict) and _boat_no_from_public_boat(boat)
    }
    lanes = sorted(set(racelist_boats) | set(before_boats) | set(range(1, 7)))
    boats = [_public_boat_view(racelist_boats.get(lane, {}), before_boats.get(lane, {})) for lane in lanes]
    before_info = beforeinfo.get("beforeInfo") if isinstance(beforeinfo.get("beforeInfo"), dict) else {}

    statuses = {
        "racelist": racelist.get("dataStatus") or ("unavailable" if not racelist_html else "unknown"),
        "beforeinfo": beforeinfo.get("dataStatus") or ("unavailable" if not beforeinfo_html else "unknown"),
    }
    payload = {
        "status": "ok" if racelist_html or beforeinfo_html else "unavailable",
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "raceTitle": _text_or_empty(racelist.get("raceTitle")),
        "deadline": _text_or_empty(racelist.get("deadline")),
        "weather": before_info.get("weather") or beforeinfo.get("weather") or {},
        "startExhibition": before_info.get("startExhibition") or beforeinfo.get("startExhibition") or [],
        "boats": boats,
        "statuses": statuses,
        "source": "boatrace_official_public",
        "sourceUrls": {
            "racelist": racelist_url,
            "beforeinfo": beforeinfo_url,
        },
        "fetchStatus": {
            "racelist": racelist_fetch,
            "beforeinfo": beforeinfo_fetch,
        },
        "fetchedAt": now.isoformat(timespec="seconds"),
    }
    _PUBLIC_RACE_CACHE[cache_key] = payload
    _PUBLIC_RACE_CACHE_TS[cache_key] = now
    return payload


@app.get("/api/public-race-snapshot")
def api_public_race_snapshot() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = str(request.args.get("jcd") or "12").zfill(2)
    try:
        race_no = int(request.args.get("race") or "1")
    except Exception:
        race_no = 1
    return _json_response(load_public_race_snapshot(date_value, jcd, race_no))


HAMANAKO_HOME_URL = "https://www.boatrace-hamanako.jp/"


def _join_nonempty(parts: list[str], sep: str = " / ") -> str:
    return sep.join([p for p in parts if p])


def _clean_dom_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _parse_hamanako_home(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    date_info = _clean_dom_text(soup.select_one(".c_header_date").get_text(" ", strip=True)) if soup.select_one(".c_header_date") else ""
    open_time = _clean_dom_text(soup.select_one(".kaimon_time").get_text(" ", strip=True)) if soup.select_one(".kaimon_time") else ""
    series_title = _clean_dom_text(soup.select_one(".par-racetitle").get_text(" ", strip=True)) if soup.select_one(".par-racetitle") else ""
    stage = _clean_dom_text(soup.select_one(".raceinfo_countdownday .par-icon_area").get_text(" ", strip=True)) if soup.select_one(".raceinfo_countdownday .par-icon_area") else ""

    current_day = ""
    current_day_node = soup.select_one(".day.current")
    if current_day_node:
        current_day = _clean_dom_text(current_day_node.get_text(" ", strip=True))

    race_tabs = []
    for item in soup.select("#js-tab_category_menu .par-tab_item_cell"):
        text = _clean_dom_text(item.get_text(" ", strip=True))
        if text:
            race_tabs.append(text)

    sales_blocks = []
    for block in soup.select(".c_header_hatsubai .hatsubai_block"):
        title = _clean_dom_text(block.select_one(".item_title").get_text(" ", strip=True)) if block.select_one(".item_title") else ""
        kaimon = _clean_dom_text(block.select_one(".item_kaimon").get_text(" ", strip=True)) if block.select_one(".item_kaimon") else ""
        venues = []
        for venue in block.select(".jo_grade_area"):
            text = _clean_dom_text(venue.get_text(" ", strip=True))
            if text:
                venues.append(text)
        if title or venues:
            sales_blocks.append({"title": title, "openTime": kaimon, "venues": venues[:10]})

    news = []
    for anchor in soup.select("a[href]"):
        text = _clean_dom_text(anchor.get_text(" ", strip=True))
        href = str(anchor.get("href") or "")
        if not text or len(text) < 8:
            continue
        if any(word in text for word in ["お知らせ", "OPEN", "杯", "キャンペーン", "横断幕", "Mooovi"]):
            if href.startswith("/"):
                href = HAMANAKO_HOME_URL.rstrip("/") + href
            news.append({"title": text, "url": href})
        if len(news) >= 5:
            break

    return {
        "status": "ok",
        "source": "boatrace_hamanako_official_home",
        "sourceUrl": HAMANAKO_HOME_URL,
        "dateInfo": date_info,
        "openTime": open_time,
        "stage": stage,
        "currentDay": current_day,
        "seriesTitle": series_title,
        "raceTabs": race_tabs,
        "salesBlocks": sales_blocks,
        "news": news,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    }


def load_hamanako_current_info() -> dict[str, Any]:
    global _HAMANAKO_HOME_CACHE, _HAMANAKO_HOME_CACHE_TS
    now = datetime.now()
    if _HAMANAKO_HOME_CACHE and _HAMANAKO_HOME_CACHE_TS and (now - _HAMANAKO_HOME_CACHE_TS).total_seconds() < 300:
        return _HAMANAKO_HOME_CACHE
    html, fetch_status = _public_fetch_html(HAMANAKO_HOME_URL, 8.0)
    if not html:
        return {
            "status": "unavailable",
            "source": "boatrace_hamanako_official_home",
            "sourceUrl": HAMANAKO_HOME_URL,
            "fetchStatus": fetch_status,
            "fetchedAt": now.isoformat(timespec="seconds"),
        }
    payload = _parse_hamanako_home(html)
    payload["fetchStatus"] = fetch_status
    _HAMANAKO_HOME_CACHE = payload
    _HAMANAKO_HOME_CACHE_TS = now
    return payload


@app.get("/api/hamanako-current-info")
def api_hamanako_current_info() -> Response:
    return _json_response(load_hamanako_current_info())


def _nikkan_venue_slug(jcd: str) -> str:
    return JCD_TO_VENUE_SLUG.get(_normalize_jcd(jcd), "")


def _normalize_display_combo(value: object) -> str:
    parts = re.findall(r"[1-6]", str(value or ""))
    return "-".join(parts[:3]) if len(parts) >= 3 else ""


def _parse_nikkan_ai_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    predictions: list[dict[str, Any]] = []
    for table in soup.select("#ai .exp_ai table.exp_table"):
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            combo_parts = [
                str(node.get("num"))
                for node in cells[1].find_all("i")
                if str(node.get("num") or "") in {"1", "2", "3", "4", "5", "6"}
            ]
            combo = "-".join(combo_parts[:3])
            if not combo:
                combo = _normalize_display_combo(cells[1].get_text(" ", strip=True))
            if not combo:
                continue
            rank_num = _to_float(cells[0].get_text(" ", strip=True))
            predictions.append({
                "rank": int(rank_num) if rank_num is not None else len(predictions) + 1,
                "combo": combo,
                "confidence": _text_or_empty(cells[2].get_text(" ", strip=True)),
                "result": _text_or_empty(cells[3].get_text(" ", strip=True)) if len(cells) >= 4 else "",
            })
        if predictions:
            break
    return predictions


def load_nikkan_ai_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    jcd = _normalize_jcd(jcd)
    race_no = max(1, min(int(race_no or 1), 12))
    cache_key = (compact, jcd, race_no)
    if cache_key in _NIKKAN_AI_CACHE:
        return _NIKKAN_AI_CACHE[cache_key]

    slug = _nikkan_venue_slug(jcd)
    url = f"https://nikkansports.raceyosou.jp/boatrace/{slug}/{compact}/{race_no}" if slug else ""
    predictions: list[dict[str, Any]] = []
    status = "missing"
    error = ""
    if url:
        try:
            html, fetch_status = _public_fetch_html(url, 20.0)
            if not html:
                raise RuntimeError(fetch_status)
            predictions = _parse_nikkan_ai_html(html)
            if not predictions:
                tables = pd.read_html(StringIO(html))
                for table in tables:
                    columns = [str(c) for c in table.columns]
                    if {"推奨順", "買い目", "自信度"}.issubset(set(columns)):
                        for _, row in table.iterrows():
                            combo = _normalize_display_combo(row.get("買い目"))
                            if not combo:
                                continue
                            rank_num = _to_float(row.get("推奨順"))
                            predictions.append({
                                "rank": int(rank_num) if rank_num is not None else len(predictions) + 1,
                                "combo": combo,
                                "confidence": _text_or_empty(row.get("自信度")),
                                "result": _text_or_empty(row.get("結果")),
                            })
                        break
            status = "ok" if predictions else "not_published"
        except Exception as exc:
            error = str(exc)
            status = "fetch_error"
    else:
        status = "not_supported"

    payload = {
        "status": status,
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "source": "nikkan_sports_ai",
        "sourceUrl": url,
        "predictions": predictions,
        "error": error,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    }
    _NIKKAN_AI_CACHE[cache_key] = payload
    return payload


def _parse_official_pcexpect_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    predictions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in soup.select(".numberSet2_row"):
        label = re.sub(r"\s+", "", row.get_text(" ", strip=True))
        if not label or not re.search(r"[-=]", label):
            continue
        parts = re.findall(r"[1-6]", label)
        if len(parts) < 3:
            continue
        combo = "-".join(parts[:3])
        if combo in seen:
            continue
        seen.add(combo)
        predictions.append({
            "rank": len(predictions) + 1,
            "combo": combo,
            "confidence": "",
            "label": label,
        })
    return predictions


def load_official_pcexpect_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    jcd = _normalize_jcd(jcd)
    race_no = max(1, min(int(race_no or 1), 12))
    url = f"https://www.boatrace.jp/owpc/pc/race/pcexpect?rno={race_no}&jcd={jcd}&hd={compact}"
    predictions: list[dict[str, Any]] = []
    status = "missing"
    error = ""
    try:
        html = _external_fetch_html(url, 20.0)
        predictions = _parse_official_pcexpect_html(html)
        status = "ok" if predictions else "not_parseable"
    except Exception as exc:
        status = "fetch_error"
        error = str(exc)
    return {
        "status": status,
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "source": "official_pcexpect",
        "sourceUrl": url,
        "predictions": predictions,
        "error": error,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/nikkan-ai-yosou")
def api_nikkan_ai_yosou() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = str(request.args.get("jcd") or "12").zfill(2)
    try:
        race_no = int(request.args.get("race") or "1")
    except Exception:
        race_no = 1
    return _json_response(load_nikkan_ai_yosou(date_value, jcd, race_no))


ACTIVE_PORTAL_JCDS = ["06", "08", "12", "13", "16", "18", "21", "24"]
NIKKAN_AUTO_JCDS = {"12", "13", "16", "18", "21"}


def _compact_prediction_items(items: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    compact_items: list[dict[str, Any]] = []
    for item in items[:limit]:
        compact_items.append({
            "rank": item.get("rank"),
            "combo": item.get("combo") or item.get("trifecta") or "",
            "confidence": item.get("confidence") or "",
            "approxProb": item.get("approxProb"),
        })
    return compact_items


def _external_fetch_html(url: str, timeout_sec: float = 12.0, force_utf8: bool = False) -> str:
    if url in _EXTERNAL_HTML_CACHE:
        return _EXTERNAL_HTML_CACHE[url]
    headers = {"User-Agent": PUBLIC_USER_AGENT}
    res = requests.get(url, timeout=timeout_sec, headers=headers)
    res.raise_for_status()
    if force_utf8:
        res.encoding = "utf-8"
    text = res.text
    _EXTERNAL_HTML_CACHE[url] = text
    return text


def _prediction_payload(
    source_id: str,
    name: str,
    status: str,
    url: str,
    predictions: list[dict[str, Any]] | None = None,
    note: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {
        "source": source_id,
        "name": name,
        "status": status,
        "sourceUrl": url,
        "predictions": predictions or [],
        "note": note,
        "error": error,
    }


def _parse_official_result_html(html: str) -> dict[str, Any]:
    lines = [
        line.strip()
        for line in BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    if "3連単" not in lines:
        return {"status": "not_published", "actualTrifecta": "", "payout": None, "popularity": None}
    idx = lines.index("3連単")
    window = lines[idx + 1: idx + 16]
    nums = [item for item in window if item in {"1", "2", "3", "4", "5", "6"}]
    combo = "-".join(nums[:3]) if len(nums) >= 3 else ""
    payout = None
    popularity = None
    for item in window:
        if item.startswith("¥"):
            payout = _to_float(item.replace("¥", "").replace(",", ""))
            break
    if payout is not None:
        pay_idx = window.index(next(item for item in window if item.startswith("¥")))
        if pay_idx + 1 < len(window):
            popularity = int(_to_float(window[pay_idx + 1]) or 0) or None
    return {
        "status": "ok" if combo else "not_published",
        "actualTrifecta": combo,
        "payout": payout,
        "popularity": popularity,
    }


def load_race_result(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    jcd = _normalize_jcd(jcd)
    race_no = max(1, min(int(race_no or 1), 12))
    cache_key = (compact, jcd, race_no)
    if cache_key in _RACE_RESULT_CACHE:
        return _RACE_RESULT_CACHE[cache_key]

    local_path = ROOT / "data" / "raw" / "official" / compact / jcd / f"result_{race_no:02d}.html"
    url = f"https://www.boatrace.jp/owpc/pc/race/raceresult?rno={race_no}&jcd={jcd}&hd={compact}"
    html = ""
    source = "official_web"
    error = ""
    try:
        if local_path.exists():
            html = local_path.read_text(encoding="utf-8", errors="ignore")
            source = str(local_path)
        else:
            html, fetch_status = _public_fetch_html(url, 20.0)
            if not html:
                raise RuntimeError(fetch_status)
        result = _parse_official_result_html(html)
    except Exception as exc:
        error = str(exc)
        result = {"status": "pending", "actualTrifecta": "", "payout": None, "popularity": None}

    result.update({
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "source": source,
        "sourceUrl": url,
        "error": error,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    })
    _RACE_RESULT_CACHE[cache_key] = result
    return result


def _settle_external_sources(sources: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    actual = str(result.get("actualTrifecta") or "")
    payout = result.get("payout")
    result_ready = result.get("status") == "ok" and bool(actual)
    settled = []
    for source in sources:
        row = dict(source)
        predictions = [dict(item) for item in row.get("predictions") or []]
        hit = False
        for item in predictions:
            combo = _normalize_display_combo(item.get("combo") or item.get("trifecta"))
            item["hit"] = bool(result_ready and combo == actual)
            if item["hit"]:
                hit = True
        row["predictions"] = predictions
        row["settlement"] = {
            "status": "settled" if result_ready and predictions else "pending" if not result_ready else "no_prediction",
            "actualTrifecta": actual,
            "hit": hit if result_ready and predictions else None,
            "returnYen": int(payout or 0) if hit else 0 if result_ready and predictions else None,
        }
        settled.append(row)
    return settled


def _save_external_yosou_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = str(payload.get("dateCompact") or "").replace("-", "")
    jcd = _normalize_jcd(payload.get("jcd") or "")
    race_no = int(payload.get("raceNo") or 0)
    if not compact or not jcd or not race_no:
        return payload

    out_dir = ROOT / "data" / "external_predictions" / compact
    race_dir = out_dir / jcd
    race_dir.mkdir(parents=True, exist_ok=True)
    race_path = race_dir / f"race_{race_no:02d}.json"

    payload = dict(payload)
    source_saved_paths: dict[str, str] = {}
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source") or "unknown")
        source_dir = out_dir / "sources" / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir / f"{jcd}_race_{race_no:02d}.json"
        source_payload = {
            "date": payload.get("date"),
            "dateCompact": compact,
            "jcd": jcd,
            "venue": payload.get("venue"),
            "raceNo": race_no,
            "result": payload.get("result"),
            "source": source,
            "savedAt": datetime.now().isoformat(timespec="seconds"),
        }
        source_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        source_saved_paths[source_id] = str(source_path)

    payload["savedPath"] = str(race_path)
    payload["sourceSavedPaths"] = source_saved_paths
    payload["savedAt"] = datetime.now().isoformat(timespec="seconds")
    race_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_external_yosou_summary(date_value: str, jcd: str | None = None) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    target_jcd = _normalize_jcd(jcd) if jcd else ""
    base_dir = ROOT / "data" / "external_predictions" / compact
    by_source: dict[str, dict[str, Any]] = {}
    race_files = [
        path
        for path in base_dir.glob("*/*.json")
        if path.parent.name != "sources"
        and path.name.startswith("race_")
        and (not target_jcd or path.parent.name == target_jcd)
    ] if base_dir.exists() else []

    total_races = 0
    settled_races = 0
    for path in sorted(race_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        total_races += 1
        result = payload.get("result") or {}
        if result.get("status") == "ok":
            settled_races += 1
        for source in payload.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("source") or "unknown")
            row = by_source.setdefault(source_id, {
                "source": source_id,
                "name": source.get("name") or source_id,
                "raceCount": 0,
                "predictionRaceCount": 0,
                "predictionCount": 0,
                "settledPredictionRaceCount": 0,
                "hitCount": 0,
                "top1HitCount": 0,
                "top3HitCount": 0,
                "top5HitCount": 0,
                "returnYen": 0,
                "statusCounts": {},
            })
            predictions = source.get("predictions") or []
            settlement = source.get("settlement") or {}
            row["raceCount"] += 1
            row["predictionCount"] += len(predictions)
            row["statusCounts"][source.get("status") or "missing"] = row["statusCounts"].get(source.get("status") or "missing", 0) + 1
            if predictions:
                row["predictionRaceCount"] += 1
            if settlement.get("status") == "settled" and predictions:
                row["settledPredictionRaceCount"] += 1
                hit_ranks = [
                    int(item.get("rank") or idx)
                    for idx, item in enumerate(predictions, start=1)
                    if item.get("hit") is True
                ]
                if hit_ranks:
                    row["hitCount"] += 1
                if any(rank <= 1 for rank in hit_ranks):
                    row["top1HitCount"] += 1
                if any(rank <= 3 for rank in hit_ranks):
                    row["top3HitCount"] += 1
                if any(rank <= 5 for rank in hit_ranks):
                    row["top5HitCount"] += 1
                row["returnYen"] += int(settlement.get("returnYen") or 0)

    rows = []
    for row in by_source.values():
        settled_n = int(row["settledPredictionRaceCount"] or 0)
        row["hitRate"] = round(row["hitCount"] / settled_n, 4) if settled_n else None
        row["top1HitRate"] = round(row["top1HitCount"] / settled_n, 4) if settled_n else None
        row["top3HitRate"] = round(row["top3HitCount"] / settled_n, 4) if settled_n else None
        row["top5HitRate"] = round(row["top5HitCount"] / settled_n, 4) if settled_n else None
        row["roi"] = round(row["returnYen"] / (settled_n * 100), 4) if settled_n else None
        rows.append(row)
    rows.sort(key=lambda item: (-int(item.get("predictionRaceCount") or 0), str(item.get("name") or "")))
    return {
        "status": "ok",
        "date": date_dir,
        "dateCompact": compact,
        "requestedJcd": target_jcd,
        "baseDir": str(base_dir),
        "raceFileCount": total_races,
        "settledRaceCount": settled_races,
        "sources": rows,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def reconcile_saved_external_yosou(date_value: str, jcd: str | None = None, max_races: int | None = None) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    target_jcd = _normalize_jcd(jcd) if jcd else ""
    base_dir = ROOT / "data" / "external_predictions" / compact
    race_files = [
        path
        for path in base_dir.glob("*/*.json")
        if path.parent.name != "sources"
        and path.name.startswith("race_")
        and (not target_jcd or path.parent.name == target_jcd)
    ] if base_dir.exists() else []
    updated = []
    pending = []
    errors = []
    limit = int(max_races or 0)
    for path in sorted(race_files):
        if limit and len(updated) + len(pending) + len(errors) >= limit:
            break
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            race_jcd = _normalize_jcd(payload.get("jcd") or path.parent.name)
            race_no = int(payload.get("raceNo") or path.stem.split("_")[-1])
            result = load_race_result(compact, race_jcd, race_no)
            payload["result"] = result
            payload["sources"] = _settle_external_sources(payload.get("sources") or [], result)
            payload["reconciledAt"] = datetime.now().isoformat(timespec="seconds")
            _save_external_yosou_payload(payload)
            row = {
                "jcd": race_jcd,
                "venue": JCD_TO_VENUE.get(race_jcd, race_jcd),
                "raceNo": race_no,
                "resultStatus": result.get("status"),
                "actualTrifecta": result.get("actualTrifecta") or "",
                "savedPath": str(path),
            }
            if result.get("status") == "ok":
                updated.append(row)
            else:
                pending.append(row)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
    summary = build_external_yosou_summary(compact, jcd=target_jcd or None)
    return {
        "status": "ok" if not errors else "partial",
        "date": date_dir,
        "dateCompact": compact,
        "requestedJcd": target_jcd,
        "updatedCount": len(updated),
        "pendingCount": len(pending),
        "errorCount": len(errors),
        "updated": updated,
        "pending": pending,
        "errors": errors,
        "summary": summary,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def _combo_predictions_from_parts(parts: list[str], limit: int = 10) -> list[dict[str, Any]]:
    predictions = []
    seen = set()
    for raw in parts:
        combo = _normalize_display_combo(raw)
        if not combo or combo in seen:
            continue
        seen.add(combo)
        predictions.append({"rank": len(predictions) + 1, "combo": combo, "confidence": ""})
        if len(predictions) >= limit:
            break
    return predictions


def _parse_nihonkando_race(html: str, venue: str, race_no: int) -> list[dict[str, Any]]:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    start = text.find(f"{venue} 第{race_no}R")
    if start < 0:
        return []
    next_start = text.find(f"{venue} 第{race_no + 1}R", start + 1)
    block = text[start: next_start if next_start > start else start + 5000]
    marker = "《3連単 買い目》"
    pos = block.find(marker)
    if pos < 0:
        return []
    lines = [line.strip() for line in block[pos + len(marker):].splitlines() if line.strip()]
    return _combo_predictions_from_parts(lines, 10)


def _load_nihonkando_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    dt = datetime.strptime(date_dir, "%Y-%m-%d")
    venue = JCD_TO_VENUE.get(jcd, jcd)
    archive_url = f"https://nihonkando.jp/archive/category/{quote(venue)}"
    try:
        archive_html = _external_fetch_html(archive_url)
        soup = BeautifulSoup(archive_html, "html.parser")
        date_label = f"{dt.month}/{dt.day}"
        page_url = ""
        for link in soup.find_all("a", href=True):
            text = link.get_text(" ", strip=True)
            if date_label in text and venue in text:
                page_url = str(link["href"])
                break
        if not page_url:
            return _prediction_payload("nihonkando", "競艇AIプロ", "not_published", archive_url, note="当日ページが見つかりません")
        page_html = _external_fetch_html(page_url)
        predictions = _parse_nihonkando_race(page_html, venue, race_no)
        return _prediction_payload(
            "nihonkando",
            "競艇AIプロ",
            "ok" if predictions else "not_published",
            page_url,
            predictions,
            note="公開HTMLから3連単買い目を取得",
        )
    except Exception as exc:
        return _prediction_payload("nihonkando", "競艇AIプロ", "fetch_error", archive_url, error=str(exc))


def _parse_acemotorz_race(html: str, venue: str, race_no: int) -> list[dict[str, Any]]:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    pattern = re.compile(rf"{re.escape(venue)}\s*{race_no}R予想・出走表")
    match = pattern.search(text)
    if not match:
        return []
    next_match = re.search(rf"{re.escape(venue)}\s*{race_no + 1}R予想・出走表", text[match.end():])
    block_end = match.end() + next_match.start() if next_match else match.end() + 7000
    block = text[match.end():block_end]
    combos = ["-".join(m.groups()) for m in re.finditer(r"\b([1-6])\s+([1-6])\s+([1-6])\b", block)]
    return _combo_predictions_from_parts(combos, 10)


def _load_acemotorz_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    url = "https://www.kyotei-acemotorz.net/daily-race/"
    venue = JCD_TO_VENUE.get(jcd, jcd)
    try:
        html = _external_fetch_html(url)
        predictions = _parse_acemotorz_race(html, venue, race_no)
        return _prediction_payload(
            "acemotorz",
            "エースモーターズ",
            "ok" if predictions else "not_published",
            url,
            predictions,
            note="daily-race公開HTMLから3連単買い目を取得",
        )
    except Exception as exc:
        return _prediction_payload("acemotorz", "エースモーターズ", "fetch_error", url, error=str(exc))


def _parse_asokabu_race(html: str, venue: str, race_no: int) -> list[dict[str, Any]]:
    lines = [
        line.strip()
        for line in BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    race_label = f"{race_no}R"
    for idx in range(len(lines) - 1):
        if lines[idx] != venue or lines[idx + 1] != race_label:
            continue
        window = lines[idx: idx + 90]
        try:
            marker = window.index("3連単 予想")
        except ValueError:
            continue
        nums = [item for item in window[marker + 1: marker + 12] if item in {"1", "2", "3", "4", "5", "6"}]
        if len(nums) >= 3:
            return [{"rank": 1, "combo": "-".join(nums[:3]), "confidence": ""}]
    return []


def _load_asokabu_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    url = "https://a-so-ka.work/boatrace/boatrace_forecast.html"
    venue = JCD_TO_VENUE.get(jcd, jcd)
    try:
        html = _external_fetch_html(url, timeout_sec=18.0, force_utf8=True)
        predictions = _parse_asokabu_race(html, venue, race_no)
        return _prediction_payload(
            "asokabu",
            "あそかぶ予想",
            "ok" if predictions else "not_published",
            url,
            predictions,
            note="iframe公開HTMLから3連単予想を取得",
        )
    except Exception as exc:
        return _prediction_payload("asokabu", "あそかぶ予想", "fetch_error", url, error=str(exc))


def _load_boatrace_simulator_yosou(date_value: str, jcd: str, race_no: int) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    url = f"https://www.boatrace-simulator.com/races/{jcd}/{date_dir}/{race_no}"
    try:
        html = _external_fetch_html(url)
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        predictions = []
        if "3連単" in text:
            pos = text.find("3連単")
            predictions = _combo_predictions_from_parts(text[pos: pos + 500].splitlines(), 5)
        return _prediction_payload(
            "simulator",
            "BoatraceSimulator",
            "ok" if predictions else "not_parseable",
            url,
            predictions,
            note="静的HTMLに買い目が無い場合は未反映",
        )
    except Exception as exc:
        return _prediction_payload("simulator", "BoatraceSimulator", "fetch_error", url, error=str(exc))


def load_external_yosou(date_value: str, jcd: str, race_no: int, fetch_result: bool = True) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    jcd = _normalize_jcd(jcd)
    race_no = max(1, min(int(race_no or 1), 12))
    cache_key = (compact, jcd, race_no, bool(fetch_result))
    if cache_key in _EXTERNAL_YOSOU_CACHE:
        return _EXTERNAL_YOSOU_CACHE[cache_key]

    nikkan = load_nikkan_ai_yosou(compact, jcd, race_no)
    official = load_official_pcexpect_yosou(compact, jcd, race_no)
    sources = [
        _prediction_payload(
            "official",
            "公式コンピュータ",
            official.get("status") or "missing",
            official.get("sourceUrl") or "",
            official.get("predictions") or [],
            note="BOATRACE公式pcexpectから自動取得",
            error=official.get("error") or "",
        ),
        _prediction_payload(
            "nikkan",
            "日刊スポーツAI",
            nikkan.get("status") or "missing",
            nikkan.get("sourceUrl") or "",
            nikkan.get("predictions") or [],
            note="公開HTMLから自動取得",
            error=nikkan.get("error") or "",
        ),
        _prediction_payload(
            "teinavi",
            "艇ナビ",
            "not_parseable",
            "https://teinavi.com/tools/search/",
            note="公開静的HTMLにレース別買い目がありません",
        ),
        _load_boatrace_simulator_yosou(compact, jcd, race_no),
        _load_acemotorz_yosou(compact, jcd, race_no),
        _load_nihonkando_yosou(compact, jcd, race_no),
        _load_asokabu_yosou(compact, jcd, race_no),
    ]
    result = load_race_result(compact, jcd, race_no) if fetch_result else {
        "status": "pending",
        "actualTrifecta": "",
        "payout": None,
        "popularity": None,
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "source": "skipped_batch_save",
        "sourceUrl": "",
        "error": "",
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
    }
    sources = _settle_external_sources(sources, result)
    payload = {
        "status": "ok",
        "date": date_dir,
        "dateCompact": compact,
        "jcd": jcd,
        "venue": JCD_TO_VENUE.get(jcd, jcd),
        "raceNo": race_no,
        "result": result,
        "sources": sources,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    payload = _save_external_yosou_payload(payload)
    _EXTERNAL_YOSOU_CACHE[cache_key] = payload
    return payload


@app.get("/api/external-yosou")
def api_external_yosou() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = str(request.args.get("jcd") or "12").zfill(2)
    try:
        race_no = int(request.args.get("race") or "1")
    except Exception:
        race_no = 1
    return _json_response(load_external_yosou(date_value, jcd, race_no))


def build_external_yosou_batch(date_value: str, jcd: str | None = None, max_races: int | None = None) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    target_jcds = [_normalize_jcd(jcd)] if jcd else ACTIVE_PORTAL_JCDS
    target_jcds = [item for item in target_jcds if item]
    saved = []
    errors = []
    limit = int(max_races or 0)
    for target_jcd in target_jcds:
        for race_no in range(1, 13):
            if limit and len(saved) >= limit:
                break
            try:
                payload = load_external_yosou(compact, target_jcd, race_no, fetch_result=False)
                saved.append({
                    "jcd": target_jcd,
                    "venue": JCD_TO_VENUE.get(target_jcd, target_jcd),
                    "raceNo": race_no,
                    "savedPath": payload.get("savedPath"),
                    "predictionSources": sum(1 for source in payload.get("sources") or [] if source.get("predictions")),
                })
            except Exception as exc:
                errors.append({
                    "jcd": target_jcd,
                    "venue": JCD_TO_VENUE.get(target_jcd, target_jcd),
                    "raceNo": race_no,
                    "error": str(exc),
                })
        if limit and len(saved) >= limit:
            break
    summary = build_external_yosou_summary(compact)
    return {
        "status": "ok" if not errors else "partial",
        "date": date_dir,
        "dateCompact": compact,
        "requestedJcd": _normalize_jcd(jcd) if jcd else "",
        "savedCount": len(saved),
        "errorCount": len(errors),
        "saved": saved,
        "errors": errors,
        "summary": summary,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


@app.post("/api/external-yosou-batch")
@app.get("/api/external-yosou-batch")
def api_external_yosou_batch() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = request.args.get("jcd") or None
    try:
        max_races = int(request.args.get("maxRaces") or "0") or None
    except Exception:
        max_races = None
    return _json_response(build_external_yosou_batch(date_value, jcd=jcd, max_races=max_races))


@app.post("/api/external-yosou-reconcile")
@app.get("/api/external-yosou-reconcile")
def api_external_yosou_reconcile() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = request.args.get("jcd") or None
    try:
        max_races = int(request.args.get("maxRaces") or "0") or None
    except Exception:
        max_races = None
    return _json_response(reconcile_saved_external_yosou(date_value, jcd=jcd, max_races=max_races))


@app.get("/api/external-yosou-summary")
def api_external_yosou_summary() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    jcd = request.args.get("jcd") or None
    return _json_response(build_external_yosou_summary(date_value, jcd=jcd))


def build_today_active_prediction_compare(date_value: str) -> dict[str, Any]:
    date_dir = _date_for_daily_dir(date_value)
    compact = date_dir.replace("-", "")
    venues = []
    for jcd in ACTIVE_PORTAL_JCDS:
        own_payload = build_venue_ai_yosou(compact, jcd)
        venue_races = []
        for race in own_payload.get("races", []):
            race_no = int(race.get("raceNo") or 0)
            own_predictions = _compact_prediction_items(race.get("predictions") or [], 3)
            official_payload = load_official_pcexpect_yosou(compact, jcd, race_no) if race_no else {
                "status": "missing",
                "sourceUrl": "",
                "predictions": [],
            }
            nikkan_payload = (
                load_nikkan_ai_yosou(compact, jcd, race_no)
                if jcd in NIKKAN_AUTO_JCDS and race_no
                else {
                    "status": "not_supported",
                    "sourceUrl": f"https://nikkansports.raceyosou.jp/boatrace/{JCD_TO_VENUE_SLUG.get(jcd, '')}/{compact}/{race_no}",
                    "predictions": [],
                }
            )
            venue_races.append({
                "raceNo": race_no,
                "deadline": race.get("deadline") or "",
                "own": own_predictions,
                "official": _compact_prediction_items(official_payload.get("predictions") or [], 3),
                "officialStatus": official_payload.get("status") or "",
                "officialSourceUrl": official_payload.get("sourceUrl") or "",
                "nikkan": _compact_prediction_items(nikkan_payload.get("predictions") or [], 3),
                "nikkanStatus": nikkan_payload.get("status") or "",
                "nikkanSourceUrl": nikkan_payload.get("sourceUrl") or "",
            })
        venues.append({
            "jcd": jcd,
            "slug": JCD_TO_VENUE_SLUG.get(jcd, ""),
            "venue": JCD_TO_VENUE.get(jcd, jcd),
            "races": venue_races,
        })
    return {
        "status": "ok",
        "date": date_dir,
        "dateCompact": compact,
        "venues": venues,
        "sources": {
            "own": "boatrace-ai-mvp",
            "official": "boatrace.jp pcexpect",
            "nikkan": "nikkansports.raceyosou.jp auto when available",
            "manual": [
                "艇ナビ",
                "BoatraceSimulator",
                "エースモーターズ",
                "競艇AIプロ",
                "あそかぶ予想",
            ],
        },
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/today-active-prediction-compare")
def api_today_active_prediction_compare() -> Response:
    date_value = str(request.args.get("date") or datetime.now().strftime("%Y%m%d"))
    return _json_response(build_today_active_prediction_compare(date_value))


@app.get("/api/prediction-sheet")
def api_prediction_sheet() -> Response:
    requested_date = request.args.get("date") or None
    return _json_response(resolve_prediction_sheet(requested_date))


@app.get("/api/prediction-sheet/latest")
def api_prediction_sheet_latest() -> Response:
    return _json_response(resolve_prediction_sheet(None))


@app.get("/api/consensus-sheet")
def api_consensus_sheet() -> Response:
    requested_date = request.args.get("date") or None
    return _json_response(resolve_consensus_sheet(requested_date))


@app.get("/api/consensus-sheet/latest")
def api_consensus_sheet_latest() -> Response:
    return _json_response(resolve_consensus_sheet(None))


def _prediction_review_path(date_text: str) -> Path:
    normalized = date_text.replace("-", "")
    return ROOT / "reports" / "predictions" / f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}" / "prediction_review.json"


def _load_prediction_review(requested_date: str | None = None) -> dict:
    resolved = resolve_prediction_sheet(requested_date)
    requested = str(resolved.get("requestedDate") or requested_date or "")
    source_date = str(resolved.get("sourceDate") or "")
    fallback_reason = str(resolved.get("fallbackReason") or "")
    if not source_date:
        return {
            "status": "missing",
            "requestedDate": requested,
            "sourceDate": "",
            "fallbackReason": fallback_reason,
            "data": None,
        }
    review_path = _prediction_review_path(source_date)
    payload = _load_json_file(review_path, {})
    if payload:
        return {
            "status": str(payload.get("status") or "ok"),
            "requestedDate": requested,
            "sourceDate": source_date,
            "fallbackReason": fallback_reason,
            "data": payload,
        }
    return {
        "status": "missing",
        "requestedDate": requested,
        "sourceDate": source_date,
        "fallbackReason": fallback_reason,
        "data": None,
    }


def _ops_board_path(date_text: str) -> Path:
    normalized = re.sub(r"\D", "", date_text)
    if len(normalized) != 8:
        normalized = datetime.now().strftime("%Y%m%d")
    return OFFICIAL_UI_DIR / normalized / "ops_board.json"


def _load_ops_board(requested_date: str | None = None) -> dict:
    requested = str(requested_date or datetime.now().strftime("%Y-%m-%d"))
    path = _ops_board_path(requested)
    payload = _load_json_file(path, {})
    if payload:
        return {
            "status": "ok",
            "requestedDate": requested,
            "sourceDate": requested,
            "fallbackReason": "ops_board_json",
            "data": payload,
        }
    return {
        "status": "unavailable",
        "requestedDate": requested,
        "sourceDate": requested,
        "fallbackReason": "ops_board_missing",
        "data": None,
    }


@app.get("/api/prediction-review")
def api_prediction_review() -> Response:
    requested_date = request.args.get("date") or None
    return _json_response(_load_prediction_review(requested_date))


@app.get("/api/ops-goal-board")
def api_ops_goal_board() -> Response:
    requested_date = request.args.get("date") or None
    return _json_response(_load_ops_board(requested_date))


@app.get("/api/final-goal-progress")
def api_final_goal_progress() -> Response:
    payload = _load_repo_audit_progress()
    return _json_response({"status": "ok" if payload else "missing", "data": payload})


@app.get("/predictions")
@app.get("/predictions.html")
def predictions_page() -> Response:
    return send_from_directory(STATIC_DIR, "predictions.html")


@app.get("/ops-board")
@app.get("/ops-board.html")
def ops_board_page() -> Response:
    return send_from_directory(STATIC_DIR, "ops_board.html", max_age=0)


@app.get("/boatrace/portal/")
@app.get("/boatrace/portal")
@app.get("/portal")
def nikkan_portal() -> Response:
    return send_from_directory(STATIC_DIR, "portal.html", max_age=0)


@app.get("/boatrace/suminoe/")
@app.get("/boatrace/suminoe")
@app.get("/boatrace/suminoe/<date_value>")
@app.get("/boatrace/suminoe/<date_value>/<race_no>")
def suminoe_ai_page(date_value: str | None = None, race_no: str | None = None) -> Response:
    return send_from_directory(STATIC_DIR, "suminoe_ai.html", max_age=0)


@app.get("/boatrace/<venue_slug>/")
@app.get("/boatrace/<venue_slug>")
@app.get("/boatrace/<venue_slug>/<date_value>")
@app.get("/boatrace/<venue_slug>/<date_value>/<race_no>")
def venue_ai_page(
    venue_slug: str,
    date_value: str | None = None,
    race_no: str | None = None,
) -> Response:
    if venue_slug == "portal":
        return send_from_directory(STATIC_DIR, "portal.html", max_age=0)
    if venue_slug not in VENUE_SLUG_TO_JCD:
        return send_from_directory(STATIC_DIR, "portal.html", max_age=0)
    return send_from_directory(STATIC_DIR, "suminoe_ai.html", max_age=0)


@app.get("/")
@app.get("/index.html")
def index() -> Response:
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str) -> Response:
    return send_from_directory(STATIC_DIR, filename)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve web UI for boatrace predictions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--open", action="store_true", help="Open browser automatically")
    args = parser.parse_args()

    if not STATIC_DIR.exists():
        raise FileNotFoundError(f"static dir not found: {STATIC_DIR}")

    url = f"http://{args.host}:{args.port}"
    print(f"[web] serving at {url}")
    print("[web] press Ctrl+C to stop")

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True, load_dotenv=False)
    except KeyboardInterrupt:
        pass
    finally:
        print("[web] stopped")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
