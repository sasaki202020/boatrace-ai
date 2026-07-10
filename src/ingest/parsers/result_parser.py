from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from bs4 import BeautifulSoup


_RESULT_PENDING_MARKERS = ("結果未公開", "未公開", "発売中", "レース前", "投票受付中", "結果待ち")
_RESULT_REFUND_MARKERS = ("返還",)
_RESULT_CANCEL_MARKERS = ("中止", "開催中止")
_RESULT_NO_CONTEST_MARKERS = ("不成立",)
_RESULT_RESULTISH_MARKERS = ("3連単", "払戻金", "着", "レース結果", "勝式", "組番")
_INVALID_COMBO_MARKERS = ("欠場", "不成立", "返還", "-")
_INVALID_PAYOUT_MARKERS = ("-", "--", "返還", "不成立")


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _to_text(value: Any) -> str | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value))
    if text in {"", "-", "--", "―", "／"}:
        return None
    return text


def _to_int(value: Any) -> int | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = _collapse_spaces(_norm(value)).replace(",", "").replace("¥", "").replace("￥", "")
    if text in {"", "-", "--", "―", "／"}:
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
    if text in {"", "-", "--", "―", "／"}:
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
    if any(marker in text for marker in _INVALID_COMBO_MARKERS if marker != "-"):
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
    text = _collapse_spaces(_norm(value)).replace(",", "").replace("¥", "").replace("￥", "")
    if text in _INVALID_PAYOUT_MARKERS:
        return None
    m = re.search(r"\d+", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _extract_snippet(text: str, keyword: str, *, window: int = 120) -> str | None:
    idx = text.find(keyword)
    if idx < 0:
        return None
    start = max(0, idx - window)
    end = min(len(text), idx + window)
    return _collapse_spaces(text[start:end])


def _candidate_items(text: str, pattern: str, *, limit: int = 12) -> list[str]:
    try:
        candidates = re.findall(pattern, text, re.S)
    except Exception:
        return []
    out: list[str] = []
    for item in candidates:
        if isinstance(item, tuple):
            item = " ".join(str(v) for v in item if v)
        item = _collapse_spaces(_norm(item))
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _detect_status(text: str) -> str:
    if any(marker in text for marker in _RESULT_CANCEL_MARKERS):
        return "canceled"
    if any(marker in text for marker in _RESULT_NO_CONTEST_MARKERS):
        return "no_contest"
    if any(marker in text for marker in _RESULT_REFUND_MARKERS):
        return "refund"
    if any(marker in text for marker in _RESULT_PENDING_MARKERS):
        return "pending"
    return "available"


def _table_headers(table: Any) -> list[str]:
    headers = []
    for th in table.find_all("th"):
        text = _to_text(th.get_text(" ", strip=True))
        if text:
            headers.append(text)
    return headers


def _looks_like_finish_table(table: Any) -> bool:
    text = _norm(table.get_text(" ", strip=True))
    headers = _table_headers(table)
    header_text = " ".join(headers)
    if all(token in header_text for token in ("着", "枠", "ボートレーサー", "レースタイム")):
        return True
    if all(token in text for token in ("着", "枠", "ボートレーサー", "レースタイム")):
        return True
    return False


def _looks_like_payout_table(table: Any) -> bool:
    text = _norm(table.get_text(" ", strip=True))
    headers = _table_headers(table)
    header_text = " ".join(headers)
    if all(token in header_text for token in ("勝式", "組番", "払戻金", "人気")):
        return True
    if all(token in text for token in ("勝式", "組番", "払戻金", "人気")):
        return True
    return False


def _parse_finish_structured(soup: BeautifulSoup, parse_warnings: list[str]) -> tuple[list[int], list[dict[str, Any]], str]:
    boat_results: list[dict[str, Any]] = []
    finish_order: list[int] = []
    for table in soup.find_all("table"):
        if not _looks_like_finish_table(table):
            continue
        rows = table.find_all("tr")
        if not rows:
            continue
        for row in rows[1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 4:
                cells = row.find_all(["th", "td"])
            if len(cells) < 4:
                continue
            position_text = _to_text(cells[0].get_text(" ", strip=True))
            boat_no = _to_int(cells[1].get_text(" ", strip=True))
            racer_text = _to_text(cells[2].get_text(" ", strip=True))
            time_text = _to_text(cells[3].get_text(" ", strip=True))
            if boat_no is None:
                continue
            finish_position = _to_int(position_text)
            if finish_position is not None:
                finish_order.append(finish_position)
            accident_flag = bool(position_text and position_text not in {"1", "2", "3", "4", "5", "6"})
            racer_id = None
            racer_name = racer_text
            if racer_text:
                m = re.match(r"^(\d{3,4})\s+(.+)$", racer_text)
                if m:
                    racer_id = m.group(1)
                    racer_name = m.group(2)
            boat_results.append(
                {
                    "boat_no": boat_no,
                    "finishPosition": finish_position,
                    "racer_name": racer_name,
                    "racer_id": racer_id,
                    "startTiming": None,
                    "decision": "accident" if accident_flag else None,
                    "course": boat_no,
                    "accidentFlag": accident_flag,
                    "raceTime": time_text,
                }
            )
        return finish_order, boat_results, "structured_table"
    parse_warnings.append("result_parse_no_finish_order")
    return finish_order, boat_results, ""


def _parse_payout_structured(soup: BeautifulSoup, parse_warnings: list[str]) -> tuple[str | None, int | None, int | None, dict[str, Any], str]:
    trifecta_combo = None
    trifecta_payout = None
    trifecta_popularity = None
    payouts: dict[str, Any] = {}
    for table in soup.find_all("table"):
        if not _looks_like_payout_table(table):
            continue
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 4:
                cells = row.find_all(["th", "td"])
            if len(cells) < 4:
                continue
            bet_type = _to_text(cells[0].get_text(" ", strip=True))
            if bet_type != "3連単":
                continue
            combo = _normalize_combo(cells[1].get_text(" ", strip=True))
            payout = _normalize_payout(cells[2].get_text(" ", strip=True))
            popularity = _to_int(cells[3].get_text(" ", strip=True))
            if combo:
                trifecta_combo = combo
                trifecta_payout = payout
                trifecta_popularity = popularity
                payouts["trifecta"] = {
                    "combo": combo,
                    "payout": payout,
                    "popularity": popularity,
                    "betType": bet_type,
                }
                return trifecta_combo, trifecta_payout, trifecta_popularity, payouts, "structured_table"
    parse_warnings.append("result_parse_no_trifecta")
    return trifecta_combo, trifecta_payout, trifecta_popularity, payouts, ""


def _parse_finish_text(text: str) -> tuple[list[int], list[dict[str, Any]], str]:
    normalized = _collapse_spaces(_norm(text))
    finish_order: list[int] = []
    boat_results: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?:^|[\n\r])\s*([1-6１-６])\s+([1-6])\s+(\d{3,4})\s+(.+?)\s+([0-9][0-9'\".:\-]+|転|失|欠|妨|不)\b"
    )
    for idx, match in enumerate(pattern.finditer(normalized), start=1):
        position = _to_int(match.group(1))
        boat_no = _to_int(match.group(2))
        racer_id = _to_text(match.group(3))
        racer_name = _to_text(match.group(4))
        race_time = _to_text(match.group(5))
        if boat_no is None:
            continue
        if position is not None:
            finish_order.append(position)
        accident_flag = bool(race_time and race_time in {"転", "失", "欠", "妨", "不"})
        boat_results.append(
            {
                "boat_no": boat_no,
                "finishPosition": position,
                "racer_name": racer_name,
                "racer_id": racer_id,
                "startTiming": None,
                "decision": "accident" if accident_flag else None,
                "course": boat_no,
                "accidentFlag": accident_flag,
                "raceTime": race_time,
            }
        )
    if boat_results:
        return finish_order, boat_results, "text_fallback"
    return finish_order, boat_results, ""


def _parse_payout_text(text: str) -> tuple[str | None, int | None, int | None, dict[str, Any], str]:
    normalized = _collapse_spaces(_norm(text))
    payouts: dict[str, Any] = {}
    patterns = [
        re.compile(r"3連単\s*([1-6])\s*[-=→]\s*([1-6])\s*[-=→]\s*([1-6])\s*[¥￥]?\s*([\d,]+)\s*円?\s+([1-9]\d*)"),
        re.compile(r"3連単\s*([1-6])\s+([1-6])\s+([1-6])\s*[¥￥]?\s*([\d,]+)\s*円?\s+([1-9]\d*)"),
        re.compile(r"3連単\s*([1-6]=[1-6]=[1-6])\s*[¥￥]?\s*([\d,]+)\s*円?\s+([1-9]\d*)"),
    ]
    for pattern in patterns:
        m = pattern.search(normalized)
        if not m:
            continue
        if pattern.groups == 5:
            combo = _normalize_combo(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
            payout = _normalize_payout(m.group(4))
            popularity = _to_int(m.group(5))
        else:
            combo = _normalize_combo(m.group(1))
            payout = _normalize_payout(m.group(2))
            popularity = _to_int(m.group(3))
        if combo:
            payouts["trifecta"] = {"combo": combo, "payout": payout, "popularity": popularity, "betType": "3連単"}
            return combo, payout, popularity, payouts, "text_fallback"
    return None, None, None, payouts, ""


def _parse_script_payload(html: str) -> tuple[str | None, int | None, int | None, dict[str, Any], str]:
    payouts: dict[str, Any] = {}
    combo = None
    payout = None
    popularity = None
    patterns = [
        r'"(?:trifectaCombo|trifecta_combo|combo)"\s*:\s*"([^"]+)"',
        r'"(?:trifectaPayout|trifecta_payout|payout)"\s*:\s*"?([\d,]+)"?',
    ]
    m_combo = re.search(patterns[0], html, re.S)
    if m_combo:
        combo = _normalize_combo(m_combo.group(1))
    m_payout = re.search(patterns[1], html, re.S)
    if m_payout:
        payout = _normalize_payout(m_payout.group(1))
    if combo or payout is not None:
        payouts["trifecta"] = {"combo": combo, "payout": payout, "popularity": popularity, "betType": "3連単"}
        return combo, payout, popularity, payouts, "script_json"
    return None, None, None, payouts, ""


def parse_result_html(html: str, race_id: str) -> dict[str, Any]:
    if not html:
        return {
            "dataStatus": "unavailable",
            "resultStatus": "unavailable",
            "missingReason": ["result_unavailable"],
            "parseWarnings": ["empty_html"],
            "finishOrder": [],
            "trifectaCombo": None,
            "trifectaPayout": None,
            "trifectaPopularity": None,
            "raceStatus": "unavailable",
            "resultPublishedAt": None,
            "boatResults": [],
            "payouts": {},
            "detectedVariant": "empty_html",
            "parserPathUsed": "empty_html",
            "detectedTables": 0,
            "tableHeaders": [],
            "textSnippets": {},
        }

    soup = BeautifulSoup(html, "html.parser")
    raw_text = soup.get_text("\n", strip=True)
    text = _collapse_spaces(_norm(raw_text))
    parse_warnings: list[str] = []
    missing_reason: list[str] = []
    detected_tables = soup.find_all("table")
    table_headers = [_table_headers(table) for table in detected_tables[:8]]
    status_hint = _detect_status(text)
    detected_variant = "unknown"
    parser_path_used = ""

    # Structured tables.
    finish_order, boat_results, finish_path = _parse_finish_structured(soup, parse_warnings)
    trifecta_combo, trifecta_payout, trifecta_popularity, payouts, payout_path = _parse_payout_structured(soup, parse_warnings)
    parser_path_used = finish_path or payout_path
    if parser_path_used:
        detected_variant = "structured_table"

    # Text fallback.
    if not finish_order and not boat_results:
        finish_order, boat_results, finish_path = _parse_finish_text(raw_text)
        if finish_path:
            parser_path_used = parser_path_used or finish_path
            detected_variant = "text_fallback"
    if trifecta_combo is None and trifecta_payout is None and trifecta_popularity is None:
        combo2, payout2, popularity2, payouts2, payout_path = _parse_payout_text(raw_text)
        if combo2 or payout2 is not None or popularity2 is not None:
            trifecta_combo = combo2
            trifecta_payout = payout2
            trifecta_popularity = popularity2
            payouts.update(payouts2)
            parser_path_used = parser_path_used or payout_path
            detected_variant = "text_fallback"

    # Embedded script / JSON fallback.
    if trifecta_combo is None and trifecta_payout is None and trifecta_popularity is None:
        combo3, payout3, popularity3, payouts3, script_path = _parse_script_payload(html)
        if combo3 or payout3 is not None or popularity3 is not None:
            trifecta_combo = combo3
            trifecta_payout = payout3
            trifecta_popularity = popularity3
            payouts.update(payouts3)
            parser_path_used = parser_path_used or script_path
            detected_variant = "script_json"

    if not parser_path_used:
        parser_path_used = "status_only"

    # Derive result status.
    has_finish = any(item.get("finishPosition") is not None for item in boat_results)
    has_trifecta = trifecta_combo is not None and trifecta_payout is not None
    has_any_resultish = bool(detected_tables) or any(marker in text for marker in _RESULT_RESULTISH_MARKERS) or "結果" in text
    if status_hint in {"canceled", "refund", "no_contest"}:
        race_status = status_hint
    elif has_trifecta:
        race_status = "ok"
    elif has_finish and trifecta_combo is None and trifecta_payout is None:
        race_status = "available_without_trifecta"
    elif any(marker in text for marker in _RESULT_PENDING_MARKERS):
        race_status = "pending"
        missing_reason.append("result_before_publish")
    elif has_any_resultish:
        race_status = "parse_error"
        if not detected_tables:
            parse_warnings.append("result_parse_no_table")
            missing_reason.append("result_parse_no_table")
        if trifecta_combo is None:
            parse_warnings.append("result_parse_no_trifecta_combo")
            missing_reason.append("result_parse_no_trifecta_combo")
        if trifecta_payout is None:
            parse_warnings.append("result_parse_no_trifecta_payout")
            missing_reason.append("result_parse_no_trifecta_payout")
        if not has_finish:
            parse_warnings.append("result_parse_no_finish_order")
            missing_reason.append("result_parse_no_finish_order")
    else:
        race_status = "missing"
        missing_reason.append("result_missing")

    # Normalize result rows and derive finish order if possible.
    if not finish_order and boat_results:
        ordered_positions = sorted(
            [item.get("finishPosition") for item in boat_results if isinstance(item.get("finishPosition"), int)]
        )
        finish_order = [int(pos) for pos in ordered_positions]
    if has_trifecta and not finish_order:
        parse_warnings.append("finish_order_missing")

    if any(item.get("finishPosition") is None for item in boat_results):
        parse_warnings.append("result_parse_partial")

    if not has_trifecta and status_hint not in {"pending", "canceled", "refund", "no_contest"}:
        if race_status == "ok":
            race_status = "available_without_trifecta" if has_finish else "parse_error"
        if race_status == "parse_error":
            if trifecta_combo is None and "result_parse_no_trifecta_combo" not in parse_warnings:
                parse_warnings.append("result_parse_no_trifecta_combo")
            if trifecta_payout is None and "result_parse_no_trifecta_payout" not in parse_warnings:
                parse_warnings.append("result_parse_no_trifecta_payout")
        if trifecta_combo is None and "result_parse_no_trifecta_combo" not in missing_reason:
            missing_reason.append("result_parse_no_trifecta_combo")
        if trifecta_payout is None and "result_parse_no_trifecta_payout" not in missing_reason:
            missing_reason.append("result_parse_no_trifecta_payout")

    if has_trifecta:
        if race_status in {"parse_error", "missing", "available_without_trifecta", "refund", "pending"}:
            if status_hint == "refund" and race_status != "refund":
                parse_warnings.append("result_refund_marker_ignored")
            elif status_hint == "refund" and race_status == "refund":
                parse_warnings.append("result_refund_marker_ignored")
            race_status = "ok"

    if race_status == "ok":
        data_status = "ok"
    elif race_status in {"pending", "missing"}:
        data_status = race_status
    elif race_status in {"refund", "canceled", "no_contest", "available_without_trifecta"}:
        data_status = race_status
    elif race_status == "parse_error":
        data_status = "parse_error"
    else:
        data_status = race_status

    if not detected_tables:
        parse_warnings.append("result_parse_no_table")

    snippets = {
        "3連単": _extract_snippet(raw_text, "3連単"),
        "払戻金": _extract_snippet(raw_text, "払戻金"),
        "着": _extract_snippet(raw_text, "着"),
        "レース結果": _extract_snippet(raw_text, "レース結果"),
    }
    candidate_combos = _candidate_items(raw_text, r"3連単\s*([1-6][-=→\s＝]{1,3}[1-6][-=→\s＝]{1,3}[1-6])")
    candidate_payouts = _candidate_items(raw_text, r"3連単[^0-9]{0,16}[1-6][-=→\s＝]{1,3}[1-6][-=→\s＝]{1,3}[1-6][^0-9]{0,16}([¥￥]?\s*[\d,]+円?)")
    candidate_finish_numbers = _candidate_items(raw_text, r"(?:着順|着|艇番|1着|2着|3着)\s*[:：]?\s*([1-6](?:\s*[、,\-]\s*[1-6]){0,5})")

    result_published_at: str | None = None
    m_pub = re.search(r"([0-9]{4}/[0-9]{1,2}/[0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2})", raw_text)
    if m_pub:
        result_published_at = m_pub.group(1)

    return {
        "dataStatus": data_status,
        "resultStatus": data_status,
        "missingReason": sorted(dict.fromkeys(missing_reason)),
        "parseWarnings": sorted(dict.fromkeys(parse_warnings)),
        "finishOrder": finish_order,
        "finish_order": finish_order,
        "trifectaCombo": trifecta_combo,
        "trifecta_combo": trifecta_combo,
        "normalizedCombo": trifecta_combo,
        "trifectaPayout": trifecta_payout,
        "trifecta_payout": trifecta_payout,
        "normalizedPayout": trifecta_payout,
        "trifectaPopularity": trifecta_popularity,
        "trifecta_popularity": trifecta_popularity,
        "raceStatus": race_status,
        "race_status": race_status,
        "raceStatusNormalized": race_status,
        "resultPublishedAt": result_published_at,
        "result_published_at": result_published_at,
        "boatResults": boat_results,
        "boat_results": boat_results,
        "payouts": payouts,
        "detectedVariant": detected_variant,
        "parserPathUsed": parser_path_used,
        "detectedTables": len(detected_tables),
        "tableHeaders": table_headers,
        "textSnippets": snippets,
        "allTextLength": len(text),
        "containsTrifecta": "3連単" in text,
        "containsPayout": "払戻金" in text,
        "containsFinish": "着" in text or "着順" in text,
        "containsRefund": "返還" in text,
        "containsCancel": "中止" in text or "開催中止" in text,
        "candidateCombos": candidate_combos,
        "candidatePayouts": candidate_payouts,
        "candidateFinishNumbers": candidate_finish_numbers,
        "parserFailureReason": "none" if race_status == "ok" else (missing_reason[0] if missing_reason else "unknown"),
        "suggestedParserRoute": parser_path_used or ("text_fallback" if any(candidate_combos or candidate_payouts) else "status_only"),
    }
