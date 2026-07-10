from __future__ import annotations

from datetime import date, datetime
from pathlib import Path


def normalize_date_str(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        raise ValueError("date is empty")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return datetime.strptime(text, "%Y-%m-%d").date().isoformat()


def compact_date_str(value: str | date | datetime) -> str:
    return normalize_date_str(value).replace("-", "")


def parse_daily_dir_date(name: str) -> str | None:
    text = str(name).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except Exception:
        return None


def get_daily_report_dir(date_value: str | date | datetime, reports_root: Path) -> Path:
    return reports_root / normalize_date_str(date_value)


def find_existing_daily_report_dir(date_value: str | date | datetime, reports_root: Path) -> Path:
    normalized = normalize_date_str(date_value)
    compact = normalized.replace("-", "")
    preferred = reports_root / normalized
    if preferred.exists() and preferred.is_dir():
        return preferred
    legacy = reports_root / compact
    if legacy.exists() and legacy.is_dir():
        return legacy
    return preferred


def list_daily_report_dirs(reports_root: Path) -> list[Path]:
    if not reports_root.exists():
        return []
    dirs: dict[str, Path] = {}
    for p in reports_root.iterdir():
        if not p.is_dir():
            continue
        normalized = parse_daily_dir_date(p.name)
        if not normalized:
            continue
        if normalized not in dirs:
            dirs[normalized] = p
            continue
        if p.name.count("-") == 2 and dirs[normalized].name.count("-") != 2:
            dirs[normalized] = p
    return [dirs[key] for key in sorted(dirs.keys())]


def list_legacy_daily_dirs(reports_root: Path) -> list[str]:
    if not reports_root.exists():
        return []
    legacy: list[str] = []
    for p in reports_root.iterdir():
        if p.is_dir() and len(p.name) == 8 and p.name.isdigit():
            legacy.append(p.name)
    return sorted(legacy)
