from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from src.data.parse_official_entries_html import parse_official_entries_html


_KNOWN_EMPTY_MARKERS = ("データがありません", "表示条件を変更してもう一度処理を行ってください")


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).strip()))
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).strip())
    except Exception:
        return None


def _to_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _extract_title_and_deadline(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    texts = [soup.title.get_text(" ", strip=True)] if soup.title else []
    for tag_name in ("h1", "h2", "p"):
        for tag in soup.find_all(tag_name):
            text = tag.get_text(" ", strip=True)
            if text:
                texts.append(text)
    title = next((text for text in texts if text and "BOAT RACE" not in text and not any(m in text for m in _KNOWN_EMPTY_MARKERS)), "")
    deadline = ""
    m = re.search(r"(\d{1,2}:\d{2})", soup.get_text(" ", strip=True))
    if m:
        deadline = m.group(1)
    return title, deadline


def _placeholder_boats() -> list[dict[str, Any]]:
    return [
        {
            "boat_no": boat_no,
            "racer_name": None,
            "racer_id": None,
            "branch": None,
            "class": None,
            "age": None,
            "weight": None,
            "avg_st": None,
            "national_win_rate": None,
            "national_2rate": None,
            "national_3rate": None,
            "local_win_rate": None,
            "local_2rate": None,
            "local_3rate": None,
            "motor_no": None,
            "motor_2rate": None,
            "boat_no_equipment": None,
            "boat_2rate": None,
            "f_count": None,
            "l_count": None,
            "data_status": "missing",
            "source": {"kind": "racelist_html", "missing": True},
        }
        for boat_no in range(1, 7)
    ]


def _normalize_record(record: dict[str, Any], default_boat_no: int) -> dict[str, Any]:
    boat_no = _to_int(record.get("boat_no") or record.get("lane") or record.get("no")) or default_boat_no
    return {
        "boat_no": boat_no,
        "racer_name": _to_str(record.get("racer_name")),
        "racer_id": _to_str(record.get("racer_id")),
        "branch": _to_str(record.get("branch")),
        "class": _to_str(record.get("racer_class") or record.get("class") or record.get("rank")),
        "age": _to_int(record.get("age")),
        "weight": _to_float(record.get("weight")),
        "avg_st": _to_float(record.get("avg_st") or record.get("start_display_st")),
        "national_win_rate": _to_float(record.get("national_win_rate")),
        "national_2rate": _to_float(record.get("national_2ren_rate") or record.get("national_2rate")),
        "national_3rate": _to_float(record.get("national_3ren_rate") or record.get("national_3rate")),
        "local_win_rate": _to_float(record.get("local_win_rate")),
        "local_2rate": _to_float(record.get("local_2ren_rate") or record.get("local_2rate")),
        "local_3rate": _to_float(record.get("local_3ren_rate") or record.get("local_3rate")),
        "motor_no": _to_int(record.get("motor_no")),
        "motor_2rate": _to_float(record.get("motor_2ren_rate") or record.get("motor_2rate")),
        "boat_no_equipment": _to_int(record.get("boat_no_equipment") or record.get("boat_no")),
        "boat_2rate": _to_float(record.get("boat_2ren_rate") or record.get("boat_2rate")),
        "f_count": _to_int(record.get("f_count")),
        "l_count": _to_int(record.get("l_count")),
        "data_status": "available",
        "source": {"kind": "racelist_html"},
    }


def parse_racelist_html(html: str, target_date: str, jcd: str, race_no: int) -> dict[str, Any]:
    title, deadline = _extract_title_and_deadline(html)
    empty_html = not html or any(marker in html for marker in _KNOWN_EMPTY_MARKERS)
    if empty_html:
        return {
            "dataStatus": "missing",
            "missingReason": ["racelist_unavailable"],
            "venueName": title,
            "raceTitle": title,
            "deadline": deadline,
            "boats": _placeholder_boats(),
        }

    frame = parse_official_entries_html(html, target_date=target_date, jcd=jcd, race_no=race_no)
    records = frame.to_dict(orient="records") if not frame.empty else []
    boats: list[dict[str, Any]] = []
    for boat_no in range(1, 7):
        record = next((row for row in records if _to_int(row.get("boat_no")) == boat_no or _to_int(row.get("lane")) == boat_no), None)
        if record is None:
            boats.append(_placeholder_boats()[boat_no - 1])
            continue
        boats.append(_normalize_record(record, boat_no))

    data_status = "available" if any(boat.get("racer_name") or boat.get("racer_id") for boat in boats) else "missing"
    if data_status != "available":
        boats = _placeholder_boats()

    return {
        "dataStatus": data_status,
        "missingReason": [] if data_status == "available" else ["racelist_unavailable"],
        "venueName": title,
        "raceTitle": title,
        "deadline": deadline,
        "boats": boats,
    }
