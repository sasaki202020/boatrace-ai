from __future__ import annotations

"""Parse BOATRACE official entry/race card HTML into row records.

This parser is intentionally tolerant:
- it prefers semantic HTML table parsing when possible
- it falls back to text-block heuristics for the official racelist page layout
"""

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from src.utils.race_id import canonical_race_id


_FULLWIDTH_DIGITS = str.maketrans("１２３４５６７８９０", "1234567890")
_RACE_NO_RE = re.compile(r"(?:^|\D)(\d{1,2})R(?:\D|$)")
_BOAT_START_RE = re.compile(r"^[1-6](?:\s+Image)?$", re.IGNORECASE)
_RACER_ID_CLASS_RE = re.compile(r"(\d{4})\s*/\s*([AB]\d)")
_AGE_WEIGHT_RE = re.compile(r"(\d{1,2})歳/(\d+(?:\.\d+)?)kg")
_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d+|\d+)")


@dataclass(frozen=True)
class OfficialEntryPageMeta:
    target_date: str
    jcd: str
    race_no: int
    race_id: str
    venue_name: str | None = None


def _normalize_fullwidth_digits(value: str) -> str:
    return str(value).translate(_FULLWIDTH_DIGITS)


def _parse_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        parsed = pd.to_numeric(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return float(parsed)
    except Exception:
        return None


def _extract_race_no_from_lines(lines: list[str]) -> int | None:
    for line in lines:
        text = _normalize_fullwidth_digits(line)
        match = _RACE_NO_RE.search(text)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                continue
    return None


def _split_boat_blocks(lines: list[str]) -> list[list[str]]:
    starts: list[int] = []
    for idx, line in enumerate(lines):
        normalized = _normalize_fullwidth_digits(line).strip()
        if _BOAT_START_RE.match(normalized):
            starts.append(idx)
    if not starts:
        return []
    starts.append(len(lines))
    blocks: list[list[str]] = []
    for left, right in zip(starts[:-1], starts[1:]):
        block = [line.strip() for line in lines[left:right] if line.strip()]
        if block:
            blocks.append(block)
    return blocks


def _extract_numeric_tokens(text: str) -> list[float]:
    tokens: list[float] = []
    for match in _FLOAT_RE.finditer(_normalize_fullwidth_digits(text)):
        try:
            tokens.append(float(match.group(0)))
        except Exception:
            continue
    return tokens


def _row_from_block(
    block: list[str],
    *,
    target_date: str,
    jcd: str,
    race_no: int,
    race_id: str,
    venue_name: str | None,
) -> dict[str, Any] | None:
    if not block:
        return None
    first_line = _normalize_fullwidth_digits(block[0]).strip()
    boat_match = re.match(r"([1-6])\b", first_line)
    if not boat_match:
        return None
    lane_no = int(boat_match.group(1))

    joined = "\n".join(block)
    racer_match = _RACER_ID_CLASS_RE.search(joined)
    age_match = _AGE_WEIGHT_RE.search(joined)
    name: str | None = None
    branch: str | None = None
    hometown: str | None = None
    racer_id: float | int | None = None
    racer_class: str | None = None
    if racer_match:
        racer_id = int(racer_match.group(1))
        racer_class = racer_match.group(2)
    for line in block:
        if " / " in line and "Image" not in line:
            # The line after the racer_id/class block usually contains the name.
            clean = re.sub(r"【\d+†|\】", "", line).strip()
            if clean and "/" not in clean and not clean.endswith("kg"):
                name = clean
                break
    for line in block:
        clean = re.sub(r"【\d+†|\】", "", line).strip()
        if "/" in clean and "kg" not in clean and clean.count("/") == 1:
            left, right = [part.strip() for part in clean.split("/", 1)]
            if left or right:
                branch = left or None
                hometown = right or None
                break
    if name is None:
        for line in block:
            if " / " not in line and "Image" not in line and line.strip() and not line.strip().startswith("F"):
                candidate = line.strip()
                if not candidate.isdigit():
                    name = candidate
                    break

    f_count = None
    l_count = None
    avg_st = None
    numeric_lines = [_normalize_fullwidth_digits(line) for line in block]
    for i, line in enumerate(numeric_lines):
        if re.fullmatch(r"F\d+", line.strip()):
            try:
                f_count = int(line.strip()[1:])
            except Exception:
                pass
            if i + 1 < len(numeric_lines) and re.fullmatch(r"L\d+", numeric_lines[i + 1].strip()):
                try:
                    l_count = int(numeric_lines[i + 1].strip()[1:])
                except Exception:
                    pass
                if i + 2 < len(numeric_lines):
                    avg_st = _parse_float(numeric_lines[i + 2].strip())
            break

    stat_text = " ".join(block)
    tokens = _extract_numeric_tokens(stat_text)
    stat_start_idx: int | None = None
    if avg_st is not None:
        for idx, token in enumerate(tokens):
            if abs(token - float(avg_st)) <= 1e-6:
                stat_start_idx = idx + 1
                break
    if stat_start_idx is None:
        stat_start_idx = 0
    stats = tokens[stat_start_idx : stat_start_idx + 12]
    # Expected order after avg_st:
    # national win / 2ren / 3ren / local win / 2ren / 3ren / motor no / motor 2ren / motor 3ren / boat no / boat 2ren / boat 3ren
    national_win_rate = stats[0] if len(stats) > 0 else None
    national_2ren_rate = stats[1] if len(stats) > 1 else None
    local_win_rate = stats[3] if len(stats) > 3 else None
    local_2ren_rate = stats[4] if len(stats) > 4 else None
    motor_no = int(stats[6]) if len(stats) > 6 and stats[6] is not None else None
    motor_2ren_rate = stats[7] if len(stats) > 7 else None
    equipment_boat_no = int(stats[9]) if len(stats) > 9 and stats[9] is not None else None
    boat_2ren_rate = stats[10] if len(stats) > 10 else None

    if racer_id is None or racer_class is None:
        return None

    return {
        "date": target_date,
        "jcd": jcd,
        "venue": venue_name,
        "race_no": race_no,
        "race_id": race_id,
        "union_key": f"{target_date.replace('-', '')}_{jcd}_{race_no:02d}",
        "lane": lane_no,
        "boat_no": equipment_boat_no,
        "racer_id": racer_id,
        "racer_class": racer_class,
        "racer_name": name,
        "branch": branch,
        "hometown": hometown,
        "age": int(age_match.group(1)) if age_match else None,
        "weight": float(age_match.group(2)) if age_match else None,
        "f_count": f_count,
        "l_count": l_count,
        "avg_st": avg_st,
        "start_display_st": avg_st,
        "national_win_rate": national_win_rate,
        "national_2ren_rate": national_2ren_rate,
        "local_win_rate": local_win_rate,
        "local_2ren_rate": local_2ren_rate,
        "motor_no": motor_no,
        "motor_2ren_rate": motor_2ren_rate,
        "boat_2ren_rate": boat_2ren_rate,
    }


def parse_official_entries_html(html: str, *, target_date: str, jcd: str, race_no: int) -> pd.DataFrame:
    """Parse one official racelist HTML page into a row-per-boat DataFrame."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for candidate in (soup.find("h1"), soup.find("title")):
        if candidate is not None:
            title = candidate.get_text(" ", strip=True)
            if title:
                break
    race_id = canonical_race_id(target_date, jcd, race_no)
    raw_lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in raw_lines if line]
    blocks = _split_boat_blocks(lines)
    records: list[dict[str, Any]] = []
    for block in blocks:
        row = _row_from_block(
            block,
            target_date=target_date,
            jcd=f"{int(jcd):02d}",
            race_no=int(race_no),
            race_id=race_id,
            venue_name=title or None,
        )
        if row is not None:
            records.append(row)
    return pd.DataFrame(records)
