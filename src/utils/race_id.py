from __future__ import annotations

"""Shared helpers for canonical race IDs."""

import re


RACE_ID_RE = re.compile(r"^(?P<date>\d{8})[-_/](?P<jcd>\d{1,2})[-_/](?P<race_no>\d{1,2})$")
RACE_KEY_RE = re.compile(r"^[dD](?P<date>\d{8})[-_/]?[cC](?P<jcd>\d{1,2})[-_/]?[rR](?P<race_no>\d{1,2})$")
RACE_FALLBACK_RE = re.compile(r"(?P<date>\d{8}).*?(?P<jcd>\d{1,2}).*?(?P<race_no>\d{1,2})$")


def _normalize_date_digits(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"invalid date value: {value}")
    return digits[:8]


def canonical_race_id(date_value: object, jcd: object, race_no: object) -> str:
    """Return the canonical race identifier: YYYYMMDD-JJ-RR."""

    return f"{_normalize_date_digits(date_value)}-{int(jcd):02d}-{int(race_no):02d}"


def canonical_race_key(date_value: object, jcd: object, race_no: object) -> str:
    date8 = _normalize_date_digits(date_value)
    return f"d{date8}-c{int(jcd):02d}-r{int(race_no):02d}"


def split_race_id(value: object) -> tuple[str, int, int]:
    text = str(value).strip()
    if not text:
        raise ValueError("race_id is empty")

    match = RACE_ID_RE.match(text)
    if match:
        return match.group("date"), int(match.group("jcd")), int(match.group("race_no"))

    match = RACE_KEY_RE.match(text)
    if match:
        return match.group("date"), int(match.group("jcd")), int(match.group("race_no"))

    match = RACE_FALLBACK_RE.search(text)
    if match:
        return match.group("date"), int(match.group("jcd")), int(match.group("race_no"))

    raise ValueError(f"could not split race id: {value}")


def normalize_race_id(value: object) -> str:
    date8, jcd, race_no = split_race_id(value)
    return canonical_race_id(date8, jcd, race_no)


def race_key_from_race_id(value: object) -> str:
    date8, jcd, race_no = split_race_id(value)
    return canonical_race_key(date8, jcd, race_no)


def race_id_from_race_key(value: object) -> str:
    date8, jcd, race_no = split_race_id(value)
    return canonical_race_id(date8, jcd, race_no)
