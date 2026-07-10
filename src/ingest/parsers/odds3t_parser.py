from __future__ import annotations

import re
import unicodedata
from io import StringIO
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from src.odds.fetch_daily_trifecta_odds import ALL_TRIFECTA_COMBOS, normalize_combo, parse_trifecta_odds_table


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _normalize_odds_value(raw_text: str) -> tuple[float | None, str]:
    text = _normalize_text(raw_text).replace(",", "")
    if not text:
        return None, "missing"
    if text in {"-", "--", "―", "—", "欠場", "取消"}:
        return None, "not_offered"
    try:
        return float(text), "ok"
    except ValueError:
        return None, text


def _contains_odds_keywords(html: str) -> bool:
    text = _normalize_text(html)
    if not text:
        return False
    return any(keyword in text for keyword in ("オッズ", "3連単", "人気", "締切", "払戻"))


def _contains_before_sale_markers(html: str) -> bool:
    text = _normalize_text(html)
    markers = (
        "データがありません",
        "表示条件を変更してもう一度処理を行ってください",
        "発売前",
        "発売開始前",
        "発売前です",
        "オッズはまだありません",
    )
    return any(marker in text for marker in markers)


def _contains_after_close_markers(html: str) -> bool:
    text = _normalize_text(html)
    markers = (
        "発売締切",
        "締切済",
        "締切後",
        "終了しました",
        "確定",
        "払戻",
    )
    return any(marker in text for marker in markers)


def _parse_with_pandas(html: str, race_id: str) -> list[dict[str, Any]]:
    tables = pd.read_html(StringIO(html))
    if not tables:
        return []

    best: list[dict[str, Any]] = []
    best_count = -1
    for table in tables:
        if table.empty:
            continue
        try:
            text = " ".join(_normalize_text(value) for value in table.astype(str).fillna("").values.flatten().tolist())
        except Exception:
            text = ""
        if "オッズ" not in text and "3連単" not in text and table.shape[0] < 20:
            continue

        parsed: list[dict[str, Any]] = []
        if table.shape[1] >= 18 and table.shape[0] >= 20:
            header = [_normalize_text(v) for v in table.iloc[0].tolist()]
            first_boats = [cell for cell in header[0::2] if cell and cell.isdigit()]
            if len(first_boats) == 6:
                for row_idx in range(1, min(21, len(table))):
                    row = table.iloc[row_idx].tolist()
                    values = [_normalize_text(v) for v in row]
                    if len(values) >= 18:
                        for col in range(6):
                            first = first_boats[col]
                            second = values[col * 3]
                            third = values[col * 3 + 1]
                            odds_text = values[col * 3 + 2]
                            if len({first, second, third}) != 3:
                                continue
                            combo = normalize_combo((int(first), int(second), int(third)))
                            odds_value, odds_status = _normalize_odds_value(odds_text)
                            parsed.append(
                                {
                                    "race_id": race_id,
                                    "combo": combo,
                                    "odds": odds_value,
                                    "odds_status": odds_status,
                                    "raw_odds_text": odds_text,
                                }
                            )
                if len(parsed) > best_count:
                    best = parsed
                    best_count = len(parsed)

    return best


def parse_odds3t_document(html: str, race_id: str) -> dict[str, Any]:
    raw_html = _normalize_text(html)
    contains_odds_keyword = _contains_odds_keywords(raw_html)
    if not raw_html:
        return {
            "dataStatus": "missing",
            "missingReason": ["odds_fetch_empty_html"],
            "errorType": "odds_fetch_empty_html",
            "errorMessage": "empty html",
            "htmlContainsOddsKeyword": False,
            "parsed": {},
            "parsedOddsCount": 0,
            "sampleCombos": [],
            "tableCount": 0,
        }

    parsed_rows: list[dict[str, Any]] = []
    error_type = ""
    error_message = ""
    table_count = 0
    try:
        soup = BeautifulSoup(raw_html, "html.parser")
        table_count = len(soup.find_all("table"))
        parsed_map = parse_trifecta_odds_table(raw_html, race_id)
        parsed_rows = [
            {
                "race_id": race_id,
                "combo": row.get("combo") or row.get("trifecta") or "",
                "odds": row.get("odds"),
                "odds_status": row.get("odds_status", "ok"),
                "raw_odds_text": row.get("raw_odds_text", ""),
            }
            for row in parsed_map
        ]
    except Exception as exc:
        error_message = str(exc)
        error_lower = error_message.lower()
        if "odds table not found" in error_lower:
            error_type = "odds_parse_no_table"
        elif "unexpected row count" in error_lower or "header first-boat count" in error_lower or "block" in error_lower:
            error_type = "odds_parse_partial"
        else:
            error_type = "odds_unknown_error"
        try:
            fallback_rows = _parse_with_pandas(raw_html, race_id)
            if fallback_rows:
                parsed_rows = fallback_rows
                if error_type == "odds_parse_no_table":
                    error_type = "odds_parse_partial"
        except Exception as fallback_exc:
            if not error_message:
                error_message = str(fallback_exc)

    odds_map: dict[str, float] = {}
    sample_combos: list[str] = []
    for row in parsed_rows:
        combo = _normalize_text(row.get("combo") or row.get("trifecta") or "")
        if not combo:
            continue
        odds_value = row.get("odds")
        try:
            if odds_value in (None, "", "-", "--", "―", "—"):
                continue
            odds_map[combo] = float(odds_value)
            if len(sample_combos) < 8:
                sample_combos.append(combo)
        except Exception:
            continue

    parsed_count = len(odds_map)
    if parsed_count == 0 and not error_type:
        if _contains_before_sale_markers(raw_html):
            error_type = "odds_fetch_before_sale"
        elif _contains_after_close_markers(raw_html):
            error_type = "odds_fetch_after_close"
        elif table_count == 0:
            error_type = "odds_parse_no_table"
        else:
            error_type = "odds_parse_zero_count"

    if parsed_count == 0 and not error_message:
        if error_type == "odds_fetch_before_sale":
            error_message = "odds page indicates before sale / unpublished"
        elif error_type == "odds_fetch_after_close":
            error_message = "odds page indicates after close / finished"
        elif error_type == "odds_parse_no_table":
            error_message = "odds table not found"
        elif error_type == "odds_parse_zero_count":
            error_message = "parsed zero odds rows"
        else:
            error_message = "odds parse failed"

    if parsed_count > 0 and parsed_count < len(ALL_TRIFECTA_COMBOS) and error_type == "":
        error_type = "odds_parse_partial"
        error_message = error_message or f"parsed {parsed_count} odds rows"

    data_status = "available" if parsed_count > 0 else "missing"
    missing_reason = [] if parsed_count > 0 else [error_type or "odds_unknown_error"]
    return {
        "dataStatus": data_status,
        "missingReason": missing_reason,
        "errorType": error_type or "",
        "errorMessage": error_message,
        "htmlContainsOddsKeyword": contains_odds_keyword,
        "parsed": odds_map,
        "parsedOddsCount": parsed_count,
        "sampleCombos": sample_combos,
        "tableCount": table_count,
        "rawHtmlLength": len(raw_html),
    }


def parse_odds3t_html(html: str, race_id: str) -> dict[str, float | None]:
    parsed = parse_odds3t_document(html, race_id)
    return dict(parsed.get("parsed") or {})
