#!/usr/bin/env python3
"""
BOAT RACE official site scraper + simple prediction pipeline.

What this script does
---------------------
1. Discovers today's active venues from the official BOAT RACE "本日のレース" page.
2. Fetches, for each venue/race:
   - 出走表      /owpc/pc/race/racelist
   - 3連単オッズ /owpc/pc/race/odds3t
   - 直前情報    /owpc/pc/race/beforeinfo
   - コンピューター予想 /owpc/pc/race/pcexpect
3. Parses the official text into structured JSON/CSV.
4. Builds a simple Plackett-Luce style model from lane bias + racer stats + display info.
5. Compares model probability vs market odds and outputs EV-ranked bets.
6. Emits JSON shaped to feed the user's RaceYosouView.jsx component.

Notes
-----
- This is intentionally fail-soft. Official page markup can change, so every parser is defensive.
- The model is a heuristic baseline, not a proven profitable strategy.
- For historic dates, venue discovery from the daily index is not guaranteed. Use --jcds to force venues.

Usage
-----
python boatrace_official_pipeline.py --date 2026-04-19 --out-dir data --top-n 5
python boatrace_official_pipeline.py --date 2026-04-19 --jcds 02,05,08

Dependencies
------------
pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
import logging
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils.race_id import canonical_race_id

BASE = "https://www.boatrace.jp"
TODAY_INDEX = f"{BASE}/owpc/pc/race/index"
RACELIST_URL = f"{BASE}/owpc/pc/race/racelist"
ODDS3T_URL = f"{BASE}/owpc/pc/race/odds3t"
BEFOREINFO_URL = f"{BASE}/owpc/pc/race/beforeinfo"
PCEXPECT_URL = f"{BASE}/owpc/pc/race/pcexpect"

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

LANE_BIAS = {
    1: 1.34,
    2: 1.10,
    3: 1.04,
    4: 0.98,
    5: 0.92,
    6: 0.86,
}

GRADE_THRESHOLDS = [(0.18, "A"), (0.10, "B"), (0.0, "C")]


@dataclass
class VenueInfo:
    jcd: str
    venue: str
    live_race_no: Optional[int] = None
    title: Optional[str] = None
    status: Optional[str] = None


@dataclass
class BoatStats:
    no: int
    reg_no: Optional[str] = None
    rank: Optional[str] = None
    name: Optional[str] = None
    branch: Optional[str] = None
    hometown: Optional[str] = None
    age: Optional[int] = None
    weight: Optional[float] = None
    f_count: Optional[str] = None
    l_count: Optional[str] = None
    avg_st: Optional[float] = None
    nat_win: Optional[float] = None
    nat_2ren: Optional[float] = None
    nat_3ren: Optional[float] = None
    local_win: Optional[float] = None
    local_2ren: Optional[float] = None
    local_3ren: Optional[float] = None
    motor_no: Optional[int] = None
    motor_2ren: Optional[float] = None
    motor_3ren: Optional[float] = None
    boat_no: Optional[int] = None
    boat_2ren: Optional[float] = None
    boat_3ren: Optional[float] = None
    exhibition_time: Optional[float] = None
    tilt: Optional[float] = None
    parts: list[str] = field(default_factory=list)
    start_display_st: Optional[str] = None

    def to_ui_dict(self) -> dict[str, Any]:
        fl = f"{self.f_count or 'F0'}{self.l_count or 'L0'}"
        confidence = boat_confidence_label(self)
        compi = int(round(boat_raw_score(self) * 10))
        return {
            "no": self.no,
            "name": self.name or f"{self.no}号艇",
            "rank": self.rank or "B1",
            "branch": self.branch or "-",
            "age": self.age or 0,
            "weight": self.weight or 0.0,
            "motorNo": self.motor_no or 0,
            "motorRate": round(self.motor_2ren or 0.0, 1),
            "boatNo": self.boat_no or 0,
            "boatRate": round(self.boat_2ren or 0.0, 1),
            "avgSt": round(self.avg_st or 0.0, 2),
            "natRate": round(self.nat_2ren or 0.0, 1),
            "localRate": round(self.local_2ren or 0.0, 1),
            "fl": fl,
            "confidence": confidence,
            "compi": compi,
        }


@dataclass
class RaceMeta:
    date: str
    race_id: str
    jcd: str
    venue: str
    race_no: int
    event_title: Optional[str] = None
    race_title: Optional[str] = None
    deadline: Optional[str] = None


@dataclass
class BeforeInfo:
    weather_sky: Optional[str] = None
    wind_speed_m: Optional[float] = None
    wave_cm: Optional[int] = None
    water_temp_c: Optional[float] = None
    air_temp_c: Optional[float] = None
    start_exhibition: dict[int, str] = field(default_factory=dict)
    data_point_race_no: Optional[int] = None


@dataclass
class PredictionInfo:
    focus_lines: list[str] = field(default_factory=list)
    official_boat_mentions: Counter = field(default_factory=Counter)


@dataclass
class RaceBundle:
    meta: RaceMeta
    boats: dict[int, BoatStats] = field(default_factory=dict)
    before: BeforeInfo = field(default_factory=BeforeInfo)
    pcexpect: PredictionInfo = field(default_factory=PredictionInfo)
    odds: dict[str, float] = field(default_factory=dict)
    odds_updated_at: Optional[str] = None


class BoatraceOfficialClient:
    def __init__(self, sleep_sec: float = 0.8, timeout: tuple[int, int] = (10, 25)) -> None:
        self.sleep_sec = sleep_sec
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(HEADERS)

    def get(self, url: str, params: Optional[dict[str, Any]] = None) -> str:
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        time.sleep(self.sleep_sec)
        return resp.text


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch BOAT RACE official data and build prediction outputs.")
    p.add_argument("--date", default=jst_today_str(), help="Target date in YYYY-MM-DD or YYYYMMDD. Default: JST today")
    p.add_argument("--out-dir", default="data", help="Output root directory")
    p.add_argument("--top-n", type=int, default=5, help="Top N combos to keep per race in UI/prediction ranking")
    p.add_argument("--sleep", type=float, default=0.8, help="Sleep between requests")
    p.add_argument("--jcds", default="", help="Comma-separated venue codes to force, e.g. 02,05,08")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def jst_today_str() -> str:
    now = dt.datetime.utcnow() + dt.timedelta(hours=9)
    return now.strftime("%Y-%m-%d")


def normalize_date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    raise ValueError(f"Unsupported date format: {value}")


def ymd_no_dash(date_str: str) -> str:
    return date_str.replace("-", "")


def normalize_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_query_params(href: str) -> dict[str, str]:
    parsed = urlparse(href)
    q = parse_qs(parsed.query)
    return {k: v[0] for k, v in q.items() if v}


def discover_today_venues(client: BoatraceOfficialClient, target_date: str) -> list[VenueInfo]:
    html = client.get(TODAY_INDEX)
    soup = BeautifulSoup(html, "html.parser")
    venues: dict[str, VenueInfo] = {}

    for a in soup.find_all("a", href=True):
        href = urljoin(BASE, a["href"])
        if "/owpc/pc/race/odds3t" not in href:
            continue
        params = parse_query_params(href)
        hd = params.get("hd")
        jcd = params.get("jcd")
        rno = params.get("rno")
        if not hd or not jcd:
            continue
        if hd != ymd_no_dash(target_date):
            continue
        venue = JCD_TO_VENUE.get(jcd, jcd)
        info = venues.get(jcd)
        if not info:
            info = VenueInfo(jcd=jcd, venue=venue)
            venues[jcd] = info
        if rno and rno.isdigit():
            info.live_race_no = int(rno)

    if venues:
        return sorted(venues.values(), key=lambda x: x.jcd)

    # Fallback: if discovery failed, probe all 24 venues.
    logging.warning("Venue discovery from daily index failed; falling back to all venues.")
    return [VenueInfo(jcd=jcd, venue=venue) for jcd, venue in sorted(JCD_TO_VENUE.items())]


def extract_between(text: str, start_pat: str, end_pat: str) -> str:
    start = re.search(start_pat, text, re.MULTILINE)
    if not start:
        return ""
    end = re.search(end_pat, text[start.end():], re.MULTILINE)
    if end:
        return text[start.end(): start.end() + end.start()].strip()
    return text[start.end():].strip()


def find_first(text: str, pattern: str, flags: int = 0, group: int = 1, default: Any = None) -> Any:
    m = re.search(pattern, text, flags)
    if not m:
        return default
    return m.group(group)


def to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.replace(",", "").strip()
    if not value or value == "-":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_int(value: Optional[str]) -> Optional[int]:
    v = to_float(value)
    return None if v is None else int(v)


def parse_meta_from_text(text: str, date_str: str, jcd: str, race_no: int) -> RaceMeta:
    venue = JCD_TO_VENUE.get(jcd, jcd)
    race_id = canonical_race_id(date_str, jcd, race_no)
    event_title = find_first(text, r"##\s*([^\n]+)")
    race_title = find_first(text, r"###\s*([^\n]+)")
    deadline = None
    m = re.search(r"締切予定時刻\s+([^\n]+)", text)
    if m:
        times = re.findall(r"\d{2}:\d{2}", m.group(1))
        if len(times) >= race_no:
            deadline = times[race_no - 1]
    return RaceMeta(
        date=date_str,
        race_id=race_id,
        jcd=jcd,
        venue=venue,
        race_no=race_no,
        event_title=event_title,
        race_title=race_title,
        deadline=deadline,
    )


def parse_racelist(html: str, date_str: str, jcd: str, race_no: int) -> tuple[RaceMeta, dict[int, BoatStats]]:
    text = normalize_text(html)
    meta = parse_meta_from_text(text, date_str, jcd, race_no)

    # Extract only the racer stats block to reduce false positives from side menus.
    block = extract_between(
        text,
        r"枠\s+ボートレーサー\s+全国\s+当地\s+モーター\s+ボート",
        r"今節成績|表の見方について|投票\b",
    )

    boats: dict[int, BoatStats] = {}
    lines = [unicodedata.normalize("NFKC", ln).strip() for ln in block.splitlines() if ln.strip()]

    # The official page now renders the racer block one value per line.
    # Parse that representation directly instead of relying on the older
    # single-regex layout, which is brittle across markup revisions.
    for idx, line in enumerate(lines):
        if not re.fullmatch(r"[1-6]", line):
            continue
        section = lines[idx + 1 : idx + 22]
        if len(section) < 21:
            continue
        if not re.fullmatch(r"\d{4}", section[0]):
            continue
        if section[1] != "/":
            continue
        if not re.fullmatch(r"[AB]\d", section[2]):
            continue
        age_match = re.fullmatch(r"(\d+)歳/([0-9.]+)kg", section[5])
        if not age_match:
            continue

        hometown_parts = section[4].split("/", 1)
        branch = hometown_parts[0].strip() if hometown_parts else ""
        hometown = hometown_parts[1].strip() if len(hometown_parts) > 1 else ""

        boats[int(line)] = BoatStats(
            no=int(line),
            reg_no=section[0],
            rank=section[2],
            name=section[3].strip(),
            branch=branch or None,
            hometown=hometown or None,
            age=to_int(age_match.group(1)),
            weight=to_float(age_match.group(2)),
            f_count=section[6],
            l_count=section[7],
            avg_st=to_float(section[8]),
            nat_win=to_float(section[9]),
            nat_2ren=to_float(section[10]),
            nat_3ren=to_float(section[11]),
            local_win=to_float(section[12]),
            local_2ren=to_float(section[13]),
            local_3ren=to_float(section[14]),
            motor_no=to_int(section[15]),
            motor_2ren=to_float(section[16]),
            motor_3ren=to_float(section[17]),
            boat_no=to_int(section[18]),
            boat_2ren=to_float(section[19]),
            boat_3ren=to_float(section[20]),
        )

    # Fallback: if no full stat blocks were parsed, at least extract names from the odds header style.
    if not boats:
        logging.debug("racelist parser yielded no boats for jcd=%s rno=%s", jcd, race_no)
    return meta, boats


def parse_beforeinfo(html: str, boats: dict[int, BoatStats]) -> BeforeInfo:
    text = normalize_text(html)
    before = BeforeInfo()

    racer_lines = extract_between(text, r"枠\s+写真\s+ボートレーサー", r"###\s*部品交換凡例|スタート展示")
    for line in racer_lines.splitlines():
        line = line.strip()
        m = re.match(r"^([1-6])\s+(.+?)\s+([0-9.]+)kg\s+([0-9.]+)\s+(-?[0-9.]+)$", line)
        if m:
            no = int(m.group(1))
            boat = boats.setdefault(no, BoatStats(no=no))
            boat.name = boat.name or m.group(2).strip()
            boat.weight = to_float(m.group(3))
            boat.exhibition_time = to_float(m.group(4))
            boat.tilt = to_float(m.group(5))

    # Parts change lines can appear immediately after a racer line. Capture lightweightly.
    current_no: Optional[int] = None
    for line in racer_lines.splitlines():
        line = line.strip()
        start_match = re.match(r"^([1-6])\s+", line)
        if start_match:
            current_no = int(start_match.group(1))
            continue
        if not current_no or not line.startswith("*"):
            continue
        boats.setdefault(current_no, BoatStats(no=current_no)).parts.append(line.replace("*", "").strip())

    start_block = extract_between(text, r"スタート展示", r"水面気象情報|スタンド")
    for line in start_block.splitlines():
        line = line.strip()
        m = re.match(r"^([1-6])\s+([FL]?\.?[0-9]{1,2}|F\.[0-9]{2}|L\.[0-9]{2}|\.\d{2}|0\.0)$", line)
        if m:
            no = int(m.group(1))
            st = m.group(2)
            boats.setdefault(no, BoatStats(no=no)).start_display_st = st
            before.start_exhibition[no] = st

    before.data_point_race_no = to_int(find_first(text, r"水面気象情報\s+(\d+)R時点"))
    before.air_temp_c = to_float(find_first(text, r"気温\s+([0-9.]+)℃"))
    before.weather_sky = find_first(text, r"気温\s+[0-9.]+℃\s+([^\n\d]+)\s+風速", default=None)
    before.wind_speed_m = to_float(find_first(text, r"風速\s+([0-9.]+)m"))
    before.water_temp_c = to_float(find_first(text, r"水温\s+([0-9.]+)℃"))
    before.wave_cm = to_int(find_first(text, r"波高\s+([0-9.]+)cm"))
    return before


def parse_pcexpect(html: str) -> PredictionInfo:
    text = normalize_text(html)
    focus_block = extract_between(text, r"###\s*予想フォーカス", r"この予想に対する自信度は|###\s*進入予想")
    info = PredictionInfo()
    for line in focus_block.splitlines():
        line = line.strip()
        if not re.search(r"[1-6]", line):
            continue
        if not re.search(r"[-=]", line):
            continue
        cleaned = re.sub(r"\s+", "", line)
        info.focus_lines.append(cleaned)
        for n in re.findall(r"[1-6]", cleaned):
            info.official_boat_mentions[int(n)] += 1
    return info


def parse_odds3t(html: str) -> tuple[dict[str, float], Optional[str]]:
    text = normalize_text(html)
    updated_at = find_first(text, r"オッズ更新時間\s+([0-9:]{5})", default=None)
    odds_block = extract_between(text, r"###\s*3連単オッズ", r"ボートレースガイドはこちら|締切時オッズは")
    lines = [ln.strip() for ln in odds_block.splitlines() if ln.strip()]
    if not lines:
        return {}, updated_at

    # First line is often the boat-name header. Everything after that should be rows of 6 triples.
    data_lines = lines[1:] if re.search(r"1\s+.+2\s+.+3\s+", lines[0]) else lines
    odds: dict[str, float] = {}
    for line in data_lines:
        triples = re.findall(r"([1-6])\s+([1-6])\s+([0-9]+(?:\.[0-9]+)?)", line)
        if not triples:
            continue
        for idx, (second, third, odd) in enumerate(triples, start=1):
            combo = f"{idx}-{second}-{third}"
            odds[combo] = float(odd)
    return odds, updated_at


def boat_raw_score(boat: BoatStats) -> float:
    avg_st_component = 0.0
    if boat.avg_st is not None:
        avg_st_component = max(0.0, 0.25 - boat.avg_st) * 100.0

    exhibition_component = 0.0
    if boat.exhibition_time is not None:
        exhibition_component = max(0.0, 7.10 - boat.exhibition_time) * 60.0

    score = 0.0
    score += LANE_BIAS.get(boat.no, 1.0) * 10.0
    score += (boat.nat_2ren or 0.0) * 0.55
    score += (boat.local_2ren or 0.0) * 0.25
    score += (boat.motor_2ren or 0.0) * 0.35
    score += (boat.boat_2ren or 0.0) * 0.15
    score += avg_st_component * 0.60
    score += exhibition_component
    if (boat.f_count or "").startswith("F1"):
        score *= 0.93
    if (boat.f_count or "").startswith("F2"):
        score *= 0.88
    return max(score, 1.0)


def boat_confidence_label(boat: BoatStats) -> str:
    s = boat_raw_score(boat)
    if s >= 48:
        return "A"
    if s >= 36:
        return "B"
    return "C"


def score_boats(boats: dict[int, BoatStats], official_mentions: Counter) -> dict[int, float]:
    scores = {no: boat_raw_score(boat) for no, boat in boats.items()}
    for no in list(scores):
        scores[no] += official_mentions.get(no, 0) * 2.5

    # relative display-time bonus
    display_pairs = [(no, b.exhibition_time) for no, b in boats.items() if b.exhibition_time is not None]
    if display_pairs:
        sorted_times = sorted(display_pairs, key=lambda x: x[1])
        bonus = [4.5, 2.0, 1.0]
        for idx, (no, _t) in enumerate(sorted_times[:3]):
            scores[no] += bonus[idx]
    return scores


def plackett_luce_combo_probs(scores: dict[int, float]) -> dict[str, float]:
    boats = sorted(scores)
    probs: dict[str, float] = {}
    total = sum(scores.values())
    if total <= 0:
        return probs
    for a in boats:
        p1 = scores[a] / total
        rem1 = total - scores[a]
        if rem1 <= 0:
            continue
        for b in boats:
            if b == a:
                continue
            p2 = scores[b] / rem1
            rem2 = rem1 - scores[b]
            if rem2 <= 0:
                continue
            for c in boats:
                if c in (a, b):
                    continue
                p3 = scores[c] / rem2
                probs[f"{a}-{b}-{c}"] = p1 * p2 * p3
    # normalize after any numerical drift
    z = sum(probs.values())
    if z > 0:
        probs = {k: v / z for k, v in probs.items()}
    return probs


def apply_focus_boost(probs: dict[str, float], focus_lines: list[str]) -> dict[str, float]:
    boosted = dict(probs)
    # Exact ordered lines get the strongest boost.
    for line in focus_lines:
        if re.fullmatch(r"[1-6]-[1-6]-[1-6]", line):
            if line in boosted:
                boosted[line] *= 1.18

    # Unordered/tied focus: boost combos containing same set/order cues.
    for line in focus_lines:
        nums = re.findall(r"[1-6]", line)
        if len(nums) < 2:
            continue
        key_set = set(nums)
        for combo in list(boosted):
            parts = combo.split("-")
            if key_set.issubset(set(parts[: len(key_set)])):
                boosted[combo] *= 1.04
            elif key_set.issubset(set(parts)):
                boosted[combo] *= 1.015

    z = sum(boosted.values())
    if z > 0:
        boosted = {k: v / z for k, v in boosted.items()}
    return boosted


def rank_predictions(bundle: RaceBundle, top_n: int = 5) -> list[dict[str, Any]]:
    scores = score_boats(bundle.boats, bundle.pcexpect.official_boat_mentions)
    probs = plackett_luce_combo_probs(scores)
    probs = apply_focus_boost(probs, bundle.pcexpect.focus_lines)

    ranked: list[dict[str, Any]] = []
    for combo, prob in probs.items():
        market_odds = bundle.odds.get(combo)
        fair_odds = (1.0 / prob) if prob > 0 else None
        ev = (prob * market_odds) if market_odds is not None else None
        ranked.append(
            {
                "date": bundle.meta.date,
                "race_id": bundle.meta.race_id,
                "jcd": bundle.meta.jcd,
                "venue": bundle.meta.venue,
                "race_no": bundle.meta.race_no,
                "combo": combo,
                "model_prob": prob,
                "fair_odds": fair_odds,
                "market_odds": market_odds,
                "expected_value": ev,
            }
        )

    def _rank_key(row: dict[str, Any]) -> tuple[bool, float, float]:
        ev = row.get("expected_value")
        prob = float(row.get("model_prob") or 0.0)
        # When market odds are unavailable, use model probability as the ordering signal.
        # The previous fallback kept the original permutation order, which biased the
        # output toward the first generated combo (typically 1-2-3).
        return (ev is not None, float(ev) if ev is not None else prob, prob)

    ranked.sort(key=_rank_key, reverse=True)
    for i, row in enumerate(ranked, start=1):
        row["ev_rank"] = i
        row["is_recommended"] = bool(row["expected_value"] and row["expected_value"] >= 1.10)
    return ranked[:max(top_n, 120)]


def grade_for_prob(prob: float) -> str:
    for threshold, label in GRADE_THRESHOLDS:
        if prob >= threshold:
            return label
    return "C"


def build_reporter_comment(bundle: RaceBundle, top_rows: list[dict[str, Any]]) -> str:
    if not top_rows:
        return "有力艇の材料が薄く、見送り寄り。"
    top_combo = top_rows[0]["combo"].split("-")
    axis = top_combo[0]
    second = top_combo[1]
    score_map = score_boats(bundle.boats, bundle.pcexpect.official_boat_mentions)
    sorted_boats = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    alert = ""
    if len(sorted_boats) >= 2 and sorted_boats[1][0] != int(axis):
        alert = f" 相手は{sorted_boats[1][0]}号艇を厚め。"
    weather = bundle.before.weather_sky or "水面"
    wind = bundle.before.wind_speed_m if bundle.before.wind_speed_m is not None else 0
    return f"{weather}・風速{wind}m想定。{axis}号艇を軸、{second}号艇が相手本線。{alert}".strip()


def build_ui_payload_for_venue(date_str: str, venue: str, event: str, race_rows: list[tuple[RaceBundle, list[dict[str, Any]]]]) -> dict[str, Any]:
    races_payload = []
    for bundle, ranked in sorted(race_rows, key=lambda x: x[0].meta.race_no):
        top5 = ranked[:5]
        ai_predictions = []
        for idx, row in enumerate(top5, start=1):
            combo = [int(x) for x in row["combo"].split("-")]
            ai_predictions.append({
                "rank": idx,
                "combo": combo,
                "grade": grade_for_prob(row["model_prob"]),
            })
        start_exh = []
        for no in range(1, 7):
            st = bundle.before.start_exhibition.get(no)
            start_exh.append({
                "no": no,
                "type": "D" if st and st.startswith("F") else "S",
                "time": round(bundle.boats.get(no, BoatStats(no=no)).exhibition_time or 0.0, 2),
            })
        races_payload.append(
            {
                "raceId": bundle.meta.race_id,
                "raceNo": bundle.meta.race_no,
                "grade": bundle.meta.race_title or "一般",
                "deadline": bundle.meta.deadline or "--:--",
                "weather": {
                    "sky": bundle.before.weather_sky or "-",
                    "wind": int(round(bundle.before.wind_speed_m or 0)),
                    "wave": int(bundle.before.wave_cm or 0),
                },
                "aiConfidence": int(round((top5[0]["model_prob"] if top5 else 0.0) * 100)),
                "aiPredictions": ai_predictions,
                "reporterComment": build_reporter_comment(bundle, top5),
                "reporterBets": [row["combo"].replace("-", "－") for row in top5[:2]],
                "boats": [bundle.boats.get(no, BoatStats(no=no)).to_ui_dict() for no in range(1, 7)],
                "startExhibition": start_exh,
            }
        )
    return {
        "date": date_str,
        "venue": venue,
        "event": event,
        "races": races_payload,
    }


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    if not rows:
        with path.open("w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_race_bundle(client: BoatraceOfficialClient, date_str: str, venue: VenueInfo, race_no: int) -> Optional[RaceBundle]:
    params = {"hd": ymd_no_dash(date_str), "jcd": venue.jcd, "rno": str(race_no)}
    try:
        racelist_html = client.get(RACELIST_URL, params=params)
    except requests.HTTPError as e:
        logging.debug("racelist fetch failed for %s %sR: %s", venue.jcd, race_no, e)
        return None

    racelist_text = normalize_text(racelist_html)
    if "出走表" not in racelist_text:
        return None

    meta, boats = parse_racelist(racelist_html, date_str, venue.jcd, race_no)
    meta.venue = venue.venue
    meta.event_title = meta.event_title or venue.title or venue.venue

    try:
        before_html = client.get(BEFOREINFO_URL, params=params)
        before = parse_beforeinfo(before_html, boats)
    except Exception as e:  # noqa: BLE001
        logging.warning("beforeinfo parse failed for %s %sR: %s", venue.jcd, race_no, e)
        before = BeforeInfo()

    try:
        pcexpect_html = client.get(PCEXPECT_URL, params=params)
        pcexpect = parse_pcexpect(pcexpect_html)
    except Exception as e:  # noqa: BLE001
        logging.warning("pcexpect parse failed for %s %sR: %s", venue.jcd, race_no, e)
        pcexpect = PredictionInfo()

    try:
        odds_html = client.get(ODDS3T_URL, params=params)
        odds, odds_updated_at = parse_odds3t(odds_html)
    except Exception as e:  # noqa: BLE001
        logging.warning("odds3t parse failed for %s %sR: %s", venue.jcd, race_no, e)
        odds, odds_updated_at = {}, None

    return RaceBundle(
        meta=meta,
        boats=boats,
        before=before,
        pcexpect=pcexpect,
        odds=odds,
        odds_updated_at=odds_updated_at,
    )


def build_output_paths(out_dir: Path, date_str: str) -> dict[str, Path]:
    ymd = ymd_no_dash(date_str)
    return {
        "odds_csv": out_dir / "odds" / ymd / "all_trifecta_odds.csv",
        "pred_csv": out_dir / "predictions" / ymd / "all_race_predictions.csv",
        "top_ev_csv": out_dir / "predictions" / ymd / "top_ev_races.csv",
        "bundles_json": out_dir / "predictions" / ymd / "race_bundles.json",
        "ui_dir": out_dir / "ui" / ymd,
        "summary_json": out_dir / "predictions" / ymd / "summary.json",
    }


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    date_str = normalize_date(args.date)
    out_dir = Path(args.out_dir)
    paths = build_output_paths(out_dir, date_str)
    client = BoatraceOfficialClient(sleep_sec=args.sleep)

    if args.jcds.strip():
        venues = [VenueInfo(jcd=j.strip(), venue=JCD_TO_VENUE.get(j.strip(), j.strip())) for j in args.jcds.split(",") if j.strip()]
    else:
        venues = discover_today_venues(client, date_str)

    logging.info("Target date: %s", date_str)
    logging.info("Venues: %s", ", ".join(f"{v.jcd}:{v.venue}" for v in venues))

    all_odds_rows: list[dict[str, Any]] = []
    all_pred_rows: list[dict[str, Any]] = []
    top_ev_rows: list[dict[str, Any]] = []
    bundles_for_json: list[dict[str, Any]] = []
    ui_by_venue: dict[str, list[tuple[RaceBundle, list[dict[str, Any]]]]] = defaultdict(list)
    event_title_by_venue: dict[str, str] = {}

    fetched_races = 0
    for venue in venues:
        logging.info("Fetching venue %s %s", venue.jcd, venue.venue)
        for race_no in range(1, 13):
            bundle = fetch_race_bundle(client, date_str, venue, race_no)
            if bundle is None:
                continue
            if not bundle.boats:
                logging.debug("Skipping %s %sR due to empty boats", venue.venue, race_no)
            fetched_races += 1
            event_title_by_venue.setdefault(venue.venue, bundle.meta.event_title or venue.venue)

            ranked = rank_predictions(bundle, top_n=max(args.top_n, 30))
            ui_by_venue[venue.venue].append((bundle, ranked))

            for combo, odd in bundle.odds.items():
                all_odds_rows.append(
                    {
                        "date": bundle.meta.date,
                        "race_id": bundle.meta.race_id,
                        "jcd": bundle.meta.jcd,
                        "venue": bundle.meta.venue,
                        "race_no": bundle.meta.race_no,
                        "combo": combo,
                        "odds": odd,
                        "odds_updated_at": bundle.odds_updated_at,
                    }
                )

            for row in ranked:
                pred_row = {
                    **row,
                    "event_title": bundle.meta.event_title,
                    "race_title": bundle.meta.race_title,
                    "deadline": bundle.meta.deadline,
                    "odds_updated_at": bundle.odds_updated_at,
                }
                all_pred_rows.append(pred_row)
            top_ev_rows.extend([r for r in ranked[: args.top_n] if r.get("market_odds") is not None])

            bundles_for_json.append(
                {
                    "meta": dataclasses.asdict(bundle.meta),
                    "before": dataclasses.asdict(bundle.before),
                    "pcexpect": {
                        "focus_lines": bundle.pcexpect.focus_lines,
                        "official_boat_mentions": dict(bundle.pcexpect.official_boat_mentions),
                    },
                    "boats": {str(k): dataclasses.asdict(v) for k, v in bundle.boats.items()},
                    "odds_updated_at": bundle.odds_updated_at,
                    "odds": bundle.odds,
                }
            )

    top_ev_rows.sort(key=lambda r: r.get("expected_value") or -1.0, reverse=True)

    write_csv(paths["odds_csv"], all_odds_rows)
    write_csv(paths["pred_csv"], all_pred_rows)
    write_csv(paths["top_ev_csv"], top_ev_rows)
    write_json(paths["bundles_json"], bundles_for_json)

    ui_index: dict[str, str] = {}
    for venue, race_rows in ui_by_venue.items():
        payload = build_ui_payload_for_venue(date_str, venue, event_title_by_venue.get(venue, venue), race_rows)
        venue_code = next((bundle.meta.jcd for bundle, _ in race_rows), venue)
        ui_path = paths["ui_dir"] / f"raceyosou_{venue_code}.json"
        write_json(ui_path, payload)
        ui_index[venue] = str(ui_path)

    summary = {
        "date": date_str,
        "venue_count": len(ui_by_venue),
        "fetched_race_count": fetched_races,
        "odds_row_count": len(all_odds_rows),
        "prediction_row_count": len(all_pred_rows),
        "recommended_combo_count": sum(1 for r in all_pred_rows if r.get("is_recommended")),
        "ui_files": ui_index,
        "paths": {k: str(v) for k, v in paths.items() if k != "ui_dir"},
    }
    write_json(paths["summary_json"], summary)

    logging.info("Done. fetched_race_count=%s", fetched_races)
    logging.info("Odds CSV: %s", paths["odds_csv"])
    logging.info("Predictions CSV: %s", paths["pred_csv"])
    logging.info("Top EV CSV: %s", paths["top_ev_csv"])
    logging.info("Summary JSON: %s", paths["summary_json"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
