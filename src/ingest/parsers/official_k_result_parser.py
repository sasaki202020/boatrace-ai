from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ingest.official_fetcher import JCD_TO_VENUE


_BLOCK_BEGIN_RE = re.compile(r"^(\d{2})KBGN$")
_BLOCK_END_RE = re.compile(r"^(\d{2})KEND$")
_RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})R\s+(.+?)\s+H1800m\s+(.*)$")
_TRIFECTA_RE = re.compile(r"(?:3|３)連単\s+([1-6][\-\=→＝\s]{1,8}[1-6][\-\=→＝\s]{1,8}[1-6])\s+([¥￥]?\s*[\d,]+)\s*(?:円)?(?:\s*人気\s*(\d+))?")
_REFUND_MARKERS = ("返還",)
_CANCEL_MARKERS = ("中止", "開催中止")
_NO_CONTEST_MARKERS = ("不成立",)
_INVALID_PAYOUT_MARKERS = {"-", "--", "―", "／"}


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _to_text(value: Any) -> str | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value))
    return text or None


def _to_int(value: Any) -> int | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value)).replace(",", "").replace("¥", "").replace("￥", "")
    if text in _INVALID_PAYOUT_MARKERS:
        return None
    m = re.search(r"[-+]?\d+", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value)).replace(",", "")
    if text in _INVALID_PAYOUT_MARKERS:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _normalize_combo(value: Any) -> str | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value))
    if any(marker in text for marker in ("欠場", "不成立", "返還")):
        return None
    if text in {"", "-", "--", "―", "／"}:
        return None
    digits = re.findall(r"[1-6]", text)
    if len(digits) >= 3:
        return "-".join(digits[:3])
    compact = re.sub(r"[^1-6]", "", text)
    if len(compact) == 3:
        return "-".join(compact)
    return None


def _normalize_payout(value: Any) -> int | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value)).replace(",", "").replace("¥", "").replace("￥", "").replace("円", "")
    if text in _INVALID_PAYOUT_MARKERS:
        return None
    m = re.search(r"\d+", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _normalize_venue_name(value: str | None, jcd: str) -> str:
    if value:
        token = _collapse_spaces(_norm(value))
        token = token.replace("［成績］", "").replace("[成績]", "").strip()
        token = token.split(" ")[0] if " " in token else token
        token = token.replace(" ", "")
        if token:
            return token
    return JCD_TO_VENUE.get(jcd.zfill(2), jcd.zfill(2))


def _parse_weather(header_tail: str) -> dict[str, Any]:
    tail = _collapse_spaces(_norm(header_tail))
    weather: dict[str, Any] = {
        "sky": None,
        "temperature": None,
        "windDirection": None,
        "windSpeed": None,
        "waveHeight": None,
        "water": {"temperature": None},
        "waterCondition": None,
    }
    m = re.search(r"(?P<sky>.+?)\s+風\s+(?P<wind_dir>\S+)\s+(?P<wind_speed>\d+)m\s+波\s+(?P<wave>\d+)cm", tail)
    if m:
        weather["sky"] = _to_text(m.group("sky"))
        weather["windDirection"] = _to_text(m.group("wind_dir"))
        weather["windSpeed"] = _to_int(m.group("wind_speed"))
        weather["waveHeight"] = _to_int(m.group("wave"))
    else:
        if tail:
            weather["sky"] = _to_text(tail)
    return weather


def _parse_boat_row(line: str) -> dict[str, Any] | None:
    text = _collapse_spaces(_norm(line))
    if not re.match(r"^\d{2}\s+[1-6]\s+\d{3,4}\s+", text):
        return None
    tokens = text.split()
    if len(tokens) < 9:
        return None
    row_no = _to_int(tokens[0])
    boat_no = _to_int(tokens[1])
    racer_id = _to_text(tokens[2])
    tail = tokens[-6:]
    name_tokens = tokens[3:-6]
    if row_no is None or boat_no is None or racer_id is None or len(tail) < 6:
        return None
    racer_name = "".join(name_tokens).strip() or None
    age = _to_int(tail[0])
    weight = _to_float(tail[1])
    exhibition_time = _to_float(tail[2])
    course = _to_int(tail[3])
    start_timing_raw = tail[4]
    start_timing = _to_float(start_timing_raw)
    if start_timing is None:
        start_timing = _to_text(start_timing_raw)
    race_time = _to_text(tail[5])
    accident_flag = bool(race_time and any(marker in race_time for marker in ("転", "失", "欠", "妨", "不")))
    decision = "accident" if accident_flag else None
    return {
        "finishPosition": row_no,
        "boat_no": boat_no,
        "racer_id": racer_id,
        "racer_name": racer_name,
        "age": age,
        "weight": weight,
        "exhibitionTime": exhibition_time,
        "course": course,
        "startTiming": start_timing,
        "decision": decision,
        "accidentFlag": accident_flag,
        "raceTime": race_time,
    }


def _parse_trifecta_line(lines: list[str]) -> tuple[str | None, int | None, int | None, dict[str, Any], list[str]]:
    parse_warnings: list[str] = []
    payouts: dict[str, Any] = {}
    for line in lines:
        text = _collapse_spaces(_norm(line))
        if "3連単" not in text and "三連単" not in text:
            continue
        m = _TRIFECTA_RE.search(text)
        if not m:
            continue
        combo = _normalize_combo(m.group(1))
        payout = _normalize_payout(m.group(2))
        popularity = _to_int(m.group(3))
        if combo:
            payouts["trifecta"] = {"combo": combo, "payout": payout, "popularity": popularity, "betType": "3連単"}
            return combo, payout, popularity, payouts, parse_warnings
        parse_warnings.append("result_txt_invalid_combo")
    parse_warnings.append("result_txt_no_trifecta")
    return None, None, None, payouts, parse_warnings


def _parse_race_section(
    *,
    date8: str,
    jcd: str,
    venue_name: str,
    rno: int,
    header_line: str,
    lines: list[str],
    source_path: str,
) -> dict[str, Any]:
    header_text = _collapse_spaces(_norm(header_line))
    header_match = _RACE_HEADER_RE.match(header_text)
    race_title = _to_text(header_match.group(2)) if header_match else ""
    weather = _parse_weather(header_match.group(3) if header_match else "")
    boat_results: list[dict[str, Any]] = []
    parse_warnings: list[str] = []
    for line in lines:
        boat = _parse_boat_row(line)
        if boat:
            boat_results.append(boat)
    finish_order = [int(item["finishPosition"]) for item in sorted(boat_results, key=lambda item: int(item.get("finishPosition") or 0)) if isinstance(item.get("finishPosition"), int)]
    trifecta_combo, trifecta_payout, trifecta_popularity, payouts, trifecta_warnings = _parse_trifecta_line(lines)
    parse_warnings.extend(trifecta_warnings)
    block_text = _collapse_spaces(_norm("\n".join([header_line, *lines])))
    if any(marker in block_text for marker in _CANCEL_MARKERS):
        race_status = "canceled"
    elif any(marker in block_text for marker in _REFUND_MARKERS):
        race_status = "refund"
    elif any(marker in block_text for marker in _NO_CONTEST_MARKERS):
        race_status = "no_contest"
    elif trifecta_combo and trifecta_payout is not None:
        race_status = "ok"
    elif boat_results:
        race_status = "available_without_trifecta"
        parse_warnings.append("finish_order_missing")
    else:
        race_status = "not_held"
        parse_warnings.append("k_race_empty")
    if trifecta_combo and trifecta_payout is not None and not finish_order:
        parse_warnings.append("finish_order_missing")
    if any(item.get("finishPosition") is None for item in boat_results):
        parse_warnings.append("result_parse_partial")
    result = {
        "date": date8,
        "jcd": jcd,
        "venueName": venue_name,
        "venue_name": venue_name,
        "rno": rno,
        "raceNo": rno,
        "raceTitle": race_title or "",
        "race_title": race_title or "",
        "raceStatus": race_status,
        "race_status": race_status,
        "finishOrder": finish_order,
        "finish_order": finish_order,
        "boatResults": boat_results,
        "boat_results": boat_results,
        "trifectaCombo": trifecta_combo,
        "trifecta_combo": trifecta_combo,
        "trifectaPayout": trifecta_payout,
        "trifecta_payout": trifecta_payout,
        "trifectaPopularity": trifecta_popularity,
        "trifecta_popularity": trifecta_popularity,
        "resultSource": "official_txt_k",
        "result_source": "official_txt_k",
        "parseWarnings": sorted(dict.fromkeys(parse_warnings)),
        "payouts": payouts,
        "weather": weather,
        "beforeInfo": {},
        "startExhibition": [],
        "source": {
            "resultSource": "official_txt_k",
            "resultSourceType": "official_txt_k",
            "kFilePath": source_path,
        },
        "dataStatus": "ok" if race_status == "ok" else race_status,
        "missingReason": [] if race_status == "ok" else ([f"result_txt_{race_status}"] if race_status != "available_without_trifecta" else ["result_txt_available_without_trifecta"]),
        "resultPublishedAt": f"{date8[:4]}/{int(date8[4:6]):d}/{int(date8[6:8]):d}",
        "blockFound": True,
        "raceFound": True,
    }
    return result


def _iter_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_jcd: str | None = None
    current_lines: list[str] = []
    for line in lines:
        text = line.strip()
        begin = _BLOCK_BEGIN_RE.match(text)
        end = _BLOCK_END_RE.match(text)
        if begin:
            if current_jcd is not None:
                blocks.append((current_jcd, current_lines))
            current_jcd = begin.group(1)
            current_lines = []
            continue
        if end:
            if current_jcd is not None:
                blocks.append((current_jcd, current_lines))
            current_jcd = None
            current_lines = []
            continue
        if current_jcd is not None:
            current_lines.append(line)
    if current_jcd is not None:
        blocks.append((current_jcd, current_lines))
    return blocks


def parse_official_k_result_text(*, text: str, source_path: str = "", date8: str = "") -> dict[str, Any]:
    lines = text.splitlines()
    blocks = _iter_blocks(lines)
    records: list[dict[str, Any]] = []
    parse_warnings: list[str] = []
    if not blocks:
        parse_warnings.append("result_txt_no_blocks")
        return {
            "date": date8,
            "sourceType": "official_txt_k",
            "sourcePath": source_path,
            "resultSource": "official_txt_k",
            "blocks": [],
            "races": [],
            "raceCount": 0,
            "resultTxtOkCount": 0,
            "resultTxtParseErrorCount": 0,
            "resultTxtMissingCount": 0,
            "parseWarnings": parse_warnings,
        }

    for jcd, block_lines in blocks:
        venue_name = _normalize_venue_name(JCD_TO_VENUE.get(jcd), jcd)
        race_header_indexes = [idx for idx, line in enumerate(block_lines) if _RACE_HEADER_RE.match(_collapse_spaces(_norm(line)))]
        if not race_header_indexes:
            parse_warnings.append("result_txt_block_no_races")
            records.append(
                {
                    "date": date8,
                    "jcd": jcd,
                    "venueName": venue_name,
                    "venue_name": venue_name,
                    "rno": None,
                    "raceNo": None,
                    "raceStatus": "not_held",
                    "race_status": "not_held",
                    "finishOrder": [],
                    "boatResults": [],
                    "trifectaCombo": None,
                    "trifectaPayout": None,
                    "trifectaPopularity": None,
                    "resultSource": "official_txt_k",
                    "result_source": "official_txt_k",
                    "parseWarnings": ["k_block_no_races"],
                    "payouts": {},
                    "weather": None,
                    "source": {"resultSource": "official_txt_k", "kFilePath": source_path},
                    "dataStatus": "not_held",
                    "missingReason": ["result_txt_not_held"],
                    "resultPublishedAt": None,
                    "blockFound": True,
                    "raceFound": False,
                }
            )
            continue

        for idx in race_header_indexes:
            header_line = block_lines[idx]
            header_match = _RACE_HEADER_RE.match(_collapse_spaces(_norm(header_line)))
            if not header_match:
                continue
            rno = int(header_match.group(1))
            section_lines: list[str] = []
            for next_idx in range(idx + 1, len(block_lines)):
                candidate = block_lines[next_idx]
                candidate_text = _collapse_spaces(_norm(candidate))
                if _RACE_HEADER_RE.match(candidate_text):
                    break
                section_lines.append(candidate)
            race_record = _parse_race_section(
                date8=date8,
                jcd=jcd,
                venue_name=venue_name,
                rno=rno,
                header_line=header_line,
                lines=section_lines,
                source_path=source_path,
            )
            if race_record.get("raceStatus") == "ok":
                pass
            else:
                parse_warnings.extend(race_record.get("parseWarnings") or [])
            records.append(race_record)

    ok_count = sum(1 for record in records if str(record.get("raceStatus") or "").lower() == "ok")
    parse_error_count = sum(1 for record in records if str(record.get("raceStatus") or "").lower() == "parse_error")
    missing_count = sum(1 for record in records if str(record.get("raceStatus") or "").lower() in {"missing", "not_held", "pending", "unavailable"})
    return {
        "date": date8,
        "sourceType": "official_txt_k",
        "sourcePath": source_path,
        "resultSource": "official_txt_k",
        "blocks": blocks,
        "races": records,
        "raceCount": len(records),
        "resultTxtOkCount": ok_count,
        "resultTxtParseErrorCount": parse_error_count,
        "resultTxtMissingCount": missing_count,
        "parseWarnings": sorted(dict.fromkeys(parse_warnings)),
    }


def parse_official_k_result_file(path: str | Path, *, date8: str = "") -> dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="cp932", errors="replace")
    inferred_date = date8
    if not inferred_date:
        m = re.search(r"K(\d{6})\.TXT$", file_path.name, re.I)
        if m:
            yy = int(m.group(1)[:2])
            inferred_date = f"20{yy:02d}{m.group(1)[2:]}"
    return parse_official_k_result_text(text=text, source_path=str(file_path), date8=inferred_date)

