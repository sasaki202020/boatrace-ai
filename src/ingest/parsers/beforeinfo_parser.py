from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from src.pipeline.boatrace_official_pipeline import BoatStats, parse_beforeinfo as parse_beforeinfo_detail


_EMPTY_MARKERS = ("データがありません", "表示条件を変更してもう一度処理を行ってください")


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "&nbsp;"):
            return None
        text = str(value).strip().replace(",", "")
        if text in {"", "-", "--", "―", "／"}:
            return None
        return float(text)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, "", "&nbsp;"):
            return None
        text = str(value).strip().replace(",", "")
        if text in {"", "-", "--", "―", "／"}:
            return None
        return int(float(text))
    except Exception:
        return None


def _to_text(value: Any) -> str | None:
    if value in (None, "", "&nbsp;"):
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    if text in {"", "-", "--", "―", "／"}:
        return None
    return text


def _find_first(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, text, re.S)
        if m:
            value = m.group(1)
            if isinstance(value, str):
                value = value.strip()
            return _to_text(value)
    return None


def _find_number_before(label: str, text: str) -> float | None:
    patterns = [
        rf"{re.escape(label)}\s*([+-]?\d+(?:\.\d+)?)",
        rf"{re.escape(label)}[^\d+\-]*([+-]?\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S)
        if m:
            return _to_float(m.group(1))
    return None


def _parse_weather(soup: BeautifulSoup, text: str) -> dict[str, Any] | None:
    units = soup.select(".weather1_bodyUnit")
    weather: dict[str, Any] = {
        "sky": None,
        "temperature": None,
        "windDirection": None,
        "windSpeed": None,
        "waveHeight": None,
        "water": {"temperature": None, "condition": None},
        "beforeInfoUpdatedAt": None,
    }
    if units:
        # The official page exposes weather as icon blocks, but blank values are possible.
        for unit in units:
            title = _to_text(unit.select_one(".weather1_bodyUnitLabelTitle").get_text(" ", strip=True) if unit.select_one(".weather1_bodyUnitLabelTitle") else None)
            data = _to_text(unit.select_one(".weather1_bodyUnitLabelData").get_text(" ", strip=True) if unit.select_one(".weather1_bodyUnitLabelData") else None)
            klass = " ".join(unit.get("class", []))
            if "is-direction" in klass:
                if data:
                    weather["sky"] = data
                img = unit.select_one(".weather1_bodyUnitImage")
                if img:
                    cls = " ".join(img.get("class", []))
                    m = re.search(r"is-direction(\d+)", cls)
                    if m:
                        weather["windDirection"] = m.group(1)
            elif title == "気温":
                weather["temperature"] = _to_float(data)
            elif title == "風速":
                weather["windSpeed"] = _to_float(data)
            elif "is-windDirection" in klass:
                img = unit.select_one(".weather1_bodyUnitImage")
                if img:
                    cls = " ".join(img.get("class", []))
                    m = re.search(r"is-windDirection(\d+)", cls)
                    if m:
                        weather["windDirection"] = m.group(1)
            elif title == "水温":
                weather["water"]["temperature"] = _to_float(data)
            elif title == "波高":
                weather["waveHeight"] = _to_float(data)

    # Fallback regexes for text-only pages.
    weather["temperature"] = weather["temperature"] if weather["temperature"] is not None else _find_number_before("気温", text)
    weather["windSpeed"] = weather["windSpeed"] if weather["windSpeed"] is not None else _find_number_before("風速", text)
    weather["water"]["temperature"] = (
        weather["water"]["temperature"] if weather["water"]["temperature"] is not None else _find_number_before("水温", text)
    )
    weather["waveHeight"] = weather["waveHeight"] if weather["waveHeight"] is not None else _find_number_before("波高", text)
    if weather["sky"] is None:
        weather["sky"] = _find_first(text, [r"気温\s+[0-9.]+℃\s+([^\n\d]+?)\s+風速", r"(晴れ|曇り|雨|小雨|雪)"])
    if weather["windDirection"] is None:
        weather["windDirection"] = _find_first(text, [r"風向\s+([^\n]+?)\s+風速", r"is-windDirection(\d+)"])
    if weather["water"]["condition"] is None:
        weather["water"]["condition"] = _find_first(text, [r"水面状態\s*[:：]\s*([^\n]+)", r"水面状況\s*[:：]\s*([^\n]+)"])
    if weather["beforeInfoUpdatedAt"] is None:
        weather["beforeInfoUpdatedAt"] = _find_first(text, [r"(?:更新|更新時刻|情報更新)\s*([0-9]{1,2}:[0-9]{2})"])

    if any(
        value is not None
        for value in (
            weather["sky"],
            weather["temperature"],
            weather["windDirection"],
            weather["windSpeed"],
            weather["waveHeight"],
            weather["water"]["temperature"],
            weather["water"]["condition"],
        )
    ):
        return weather
    return None


def _parse_start_exhibition(soup: BeautifulSoup, boats: dict[int, BoatStats], text: str) -> list[dict[str, Any]]:
    start_rows: list[dict[str, Any]] = []

    def _parse_st_token(value: str | None) -> str | None:
        if value is None:
            return None
        token = _to_text(value)
        if token in {None, "", "-", "--", "―", "／"}:
            return None
        return token

    # The official page renders a dedicated "スタート展示" table. When the page
    # is not yet published, the cells can be empty. We still try to preserve the
    # row order and any partial values.
    for tbl in soup.find_all("table"):
        header_text = tbl.get_text(" ", strip=True)
        if "スタート展示" not in header_text or "コース" not in header_text or "ST" not in header_text:
            continue

        rows = tbl.find_all("tr")
        for row in rows:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if not cells:
                continue
            joined = " ".join(cells)
            if "スタート展示" in joined and "コース" in joined and "ST" in joined:
                continue

            no_match = re.search(r"\b([1-6])\b", joined)
            no = _to_int(no_match.group(1)) if no_match else None
            if no is None:
                continue

            course_match = re.search(r"(?:コース|進入)\D*([1-6])", joined)
            course = _to_int(course_match.group(1)) if course_match else None

            st_match = re.search(r"([FL]?\d?\.\d{2}|[FL]\.\d{2}|0\.\d{2}|0\.0|--|-)", joined)
            st = _parse_st_token(st_match.group(1) if st_match else None)
            time_match = re.search(r"(\d+\.\d{2})", joined)
            time = _to_float(time_match.group(1)) if time_match else None
            if time is not None and abs(time) < 1e-9:
                time = None

            boat = boats.setdefault(no, BoatStats(no=no))
            if st is None:
                st = boat.start_display_st
            if time is None:
                time = boat.exhibition_time

            if st is not None:
                boat.start_display_st = st

            start_rows.append(
                {
                    "no": no,
                    "course": course,
                    "st": st,
                    "time": time,
                    "tilt": boat.tilt,
                }
            )

        if start_rows:
            break

    if not start_rows:
        # Fallback: preserve boats in lane order, even if only exhibition stats
        # are available from the main row table.
        for no in sorted(boats):
            boat = boats[no]
            if boat.exhibition_time is None and boat.start_display_st is None and boat.tilt is None:
                continue
            start_rows.append(
                {
                    "no": no,
                    "course": None,
                    "st": boat.start_display_st,
                    "time": None if boat.exhibition_time in (0, 0.0) else boat.exhibition_time,
                    "tilt": boat.tilt,
                }
            )

    return start_rows[:6]


def _boat_row(no: int, boat: BoatStats | None, parse_warnings: list[str]) -> dict[str, Any]:
    if boat is None:
        parse_warnings.append(f"boat_{no}_missing")
        return {
            "boat_no": no,
            "exhibitionTime": None,
            "startExhibitionCourse": None,
            "startExhibitionSt": None,
            "tilt": None,
            "propeller": None,
            "partsExchange": [],
            "weightAdjustment": None,
            "data_status": "missing",
        }

    if boat.exhibition_time is None:
        parse_warnings.append(f"boat_{no}_exhibition_time_missing")
    if boat.start_display_st is None:
        parse_warnings.append(f"boat_{no}_st_missing")
    if boat.tilt is None:
        parse_warnings.append(f"boat_{no}_tilt_missing")
    return {
        "boat_no": no,
        "racer_name": boat.name,
        "exhibitionTime": boat.exhibition_time if boat.exhibition_time not in (0, 0.0) else None,
        "startExhibitionCourse": None,
        "startExhibitionSt": boat.start_display_st,
        "tilt": boat.tilt,
        "propeller": None,
        "partsExchange": list(boat.parts or []),
        "weightAdjustment": None,
        "data_status": "available",
        "parts": list(boat.parts or []),
    }


def parse_beforeinfo_html(html: str, target_date: str, jcd: str, race_no: int) -> dict[str, Any]:
    parse_warnings: list[str] = []
    missing_reason: list[str] = []

    if not html:
        return {
            "dataStatus": "unavailable",
            "dataStatusReason": ["beforeinfo_unavailable"],
            "missingReason": ["beforeinfo_unavailable"],
            "parseWarnings": ["empty_html"],
            "beforeInfo": {},
            "weather": None,
            "startExhibition": [],
            "boats": [],
        }

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    if any(marker in text for marker in _EMPTY_MARKERS):
        return {
            "dataStatus": "unavailable",
            "dataStatusReason": ["beforeinfo_unavailable"],
            "missingReason": ["beforeinfo_unavailable"],
            "parseWarnings": ["empty_marker"],
            "beforeInfo": {},
            "weather": None,
            "startExhibition": [],
            "boats": [],
        }

    detail_boats: dict[int, BoatStats] = {}
    before = parse_beforeinfo_detail(html, detail_boats)

    weather = _parse_weather(soup, text)
    if weather is None:
        parse_warnings.append("weather_missing")
    else:
        if weather.get("temperature") is None:
            parse_warnings.append("weather_temperature_missing")
        if weather.get("windSpeed") is None:
            parse_warnings.append("weather_wind_speed_missing")
        if weather.get("water", {}).get("temperature") is None:
            parse_warnings.append("weather_water_temp_missing")
        if weather.get("waveHeight") is None:
            parse_warnings.append("weather_wave_height_missing")

    start_exhibition = _parse_start_exhibition(soup, detail_boats, text)
    if not start_exhibition:
        parse_warnings.append("beforeinfo_parse_zero_count")

    boat_rows = [_boat_row(no, detail_boats.get(no), parse_warnings) for no in range(1, 7)]

    for no, boat in detail_boats.items():
        if boat.exhibition_time is not None and boat.exhibition_time == 0:
            parse_warnings.append(f"boat_{no}_exhibition_time_zero")
        if boat.start_display_st is None and boat.exhibition_time is None:
            parse_warnings.append(f"boat_{no}_beforeinfo_missing")

    if not soup.find_all("table"):
        parse_warnings.append("beforeinfo_parse_no_table")

    if any(boat.get("exhibitionTime") is not None for boat in boat_rows) or weather is not None or start_exhibition:
        data_status = "ok"
    elif soup.find_all("table"):
        data_status = "pending"
        if any("beforeinfo_parse" in warning for warning in parse_warnings):
            missing_reason.append("beforeinfo_parse_partial")
        else:
            missing_reason.append("beforeinfo_before_publish")
    else:
        data_status = "parse_error"
        missing_reason.append("beforeinfo_parse_no_table")

    # Detect partial data explicitly.
    if data_status == "ok" and parse_warnings:
        if any(reason.endswith("_missing") for reason in parse_warnings):
            data_status = "pending"
            missing_reason.append("beforeinfo_parse_partial")

    before_info = {
        "weather": weather,
        "startExhibition": start_exhibition,
        "beforeInfoUpdatedAt": weather.get("beforeInfoUpdatedAt") if weather else None,
    }
    return {
        "dataStatus": data_status,
        "dataStatusReason": sorted(dict.fromkeys(missing_reason)),
        "missingReason": sorted(dict.fromkeys(missing_reason)),
        "parseWarnings": sorted(dict.fromkeys(parse_warnings)),
        "beforeInfo": before_info,
        "weather": weather,
        "startExhibition": start_exhibition,
        "boats": boat_rows,
    }
