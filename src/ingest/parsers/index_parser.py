from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from src.ingest.official_fetcher import JCD_TO_VENUE
from src.utils.race_id import canonical_race_id


def _venue_name_from_jcd(jcd: str, label: str) -> str:
    return JCD_TO_VENUE.get(jcd, label or jcd)


def parse_index_html(html: str, target_date: str) -> dict[str, Any]:
    if not html:
        return {"dataStatus": "missing", "venues": [], "missingReason": ["index_unavailable"]}

    soup = BeautifulSoup(html, "html.parser")
    venues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "racelist" not in href or "jcd=" not in href or "rno=" not in href:
            continue
        m_jcd = re.search(r"jcd=(\d{1,2})", href)
        m_rno = re.search(r"rno=(\d{1,2})", href)
        if not m_jcd or not m_rno:
            continue
        jcd = f"{int(m_jcd.group(1)):02d}"
        race_no = int(m_rno.group(1))
        if jcd in seen:
            continue
        seen.add(jcd)
        label = anchor.get_text(" ", strip=True) or jcd
        venues.append(
            {
                "jcd": jcd,
                "venueName": _venue_name_from_jcd(jcd, label),
                "isOpen": True,
                "sourceUrl": href if href.startswith("http") else f"https://www.boatrace.jp{href}",
                "fetchedAt": "",
                "raceNo": race_no,
                "raceId": canonical_race_id(target_date, jcd, race_no),
                "label": label,
            }
        )
    return {
        "dataStatus": "available" if venues else "missing",
        "venues": sorted(venues, key=lambda item: item["jcd"]),
        "missingReason": [] if venues else ["index_parse_failed"],
    }
