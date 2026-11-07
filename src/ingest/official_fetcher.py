from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from src.ingest.parsers.beforeinfo_parser import parse_beforeinfo_html
from src.ingest.parsers.odds3t_parser import parse_odds3t_document, parse_odds3t_html
from src.ingest.parsers.result_parser import parse_result_html
from src.ingest.parsers.racelist_parser import parse_racelist_html
from src.ingest.browser_fetcher import fetch_html_with_browser
from src.utils.race_id import canonical_race_id


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

INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date8}"
RACELIST_URL = "https://www.boatrace.jp/owpc/pc/race/racelist?hd={date8}&jcd={jcd}&rno={rno}"
BEFOREINFO_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?hd={date8}&jcd={jcd}&rno={rno}"
ODDS3T_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t?hd={date8}&jcd={jcd}&rno={rno}"
PAY_URL = "https://www.boatrace.jp/owpc/pc/race/pay?hd={date8}"
RACERESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?hd={date8}&jcd={jcd}&rno={rno}"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"
MODEL_VERSION = "baseline_rule_v1"
RAW_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "official"


@dataclass(frozen=True)
class FetchTarget:
    date: str
    jcd: str
    race_no: int
    race_id: str


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


def _normalize_date(date_text: str) -> str:
    digits = re.sub(r"\D", "", str(date_text))
    if len(digits) != 8:
        raise ValueError(f"expected YYYYMMDD or YYYY-MM-DD, got {date_text!r}")
    return digits


def _fetch_html(url: str, *, timeout: float, retries: int, retry_sleep: float) -> tuple[str, str]:
    session = _session()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text, "live"
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt >= retries:
                break
            if retry_sleep > 0:
                time.sleep(retry_sleep * (attempt + 1))
    return "", f"error:{last_error}" if last_error else "unavailable"


def _save_raw_html(target_date: str, jcd: str, name: str, html: str) -> Path:
    date8 = _normalize_date(target_date)
    out_dir = RAW_ROOT / date8 / f"{int(jcd):02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.html"
    path.write_text(html or "", encoding="utf-8")
    return path


def _cached_raw_html(target_date: str, jcd: str, name: str) -> tuple[str, Path] | tuple[None, Path]:
    date8 = _normalize_date(target_date)
    path = RAW_ROOT / date8 / f"{int(jcd):02d}" / f"{name}.html"
    if not path.exists():
        return None, path
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return None, path
    if not html.strip():
        return None, path
    return html, path


def _discover_targets(target_date: str, jcd: str = "all", races: Iterable[int] | None = None) -> list[FetchTarget]:
    date8 = _normalize_date(target_date)
    race_nos = list(races) if races is not None else list(range(1, 13))
    if jcd != "all":
        jcds = [f"{int(jcd):02d}"]
    else:
        jcds = [f"{idx:02d}" for idx in range(1, 25)]

    html, _ = _fetch_html(INDEX_URL.format(date8=date8), timeout=10.0, retries=1, retry_sleep=0.5)
    discovered: dict[tuple[str, int], FetchTarget] = {}
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            if "racelist" not in href or "jcd=" not in href or "rno=" not in href:
                continue
            m_jcd = re.search(r"jcd=(\d{1,2})", href)
            m_rno = re.search(r"rno=(\d{1,2})", href)
            if not m_jcd or not m_rno:
                continue
            discovered_jcd = f"{int(m_jcd.group(1)):02d}"
            discovered_rno = int(m_rno.group(1))
            if discovered_jcd not in jcds or discovered_rno not in race_nos:
                continue
            race_id = canonical_race_id(target_date, discovered_jcd, discovered_rno)
            discovered[(discovered_jcd, discovered_rno)] = FetchTarget(target_date, discovered_jcd, discovered_rno, race_id)
    if discovered:
        return sorted(discovered.values(), key=lambda item: (item.jcd, item.race_no))
    return [
        FetchTarget(target_date, target_jcd, race_no, canonical_race_id(target_date, target_jcd, race_no))
        for target_jcd in jcds
        for race_no in race_nos
    ]


def fetch_racelist_html(
    *,
    target_date: str,
    jcd: str,
    race_no: int,
    timeout: float = 8.0,
    retries: int = 0,
    retry_sleep: float = 0.0,
) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    url = RACELIST_URL.format(date8=date8, jcd=f"{int(jcd):02d}", rno=int(race_no))
    cached_html, cached_path = _cached_raw_html(target_date, jcd, f"racelist_{int(race_no):02d}")
    if cached_html is not None:
        parsed = parse_racelist_html(cached_html, target_date=target_date, jcd=f"{int(jcd):02d}", race_no=int(race_no))
        empty_html = not cached_html or "データがありません" in cached_html
        data_status = "missing" if empty_html or parsed.get("dataStatus") != "available" else "ok"
        missing_reason = parsed.get("missingReason") or ([] if data_status == "ok" else ["racelist_unavailable"])
        return {
            "url": url,
            "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            "fetchStatus": "cache",
            "dataStatus": data_status,
            "missingReason": missing_reason,
            "html": cached_html,
            "parsed": parsed,
            "rawHtmlPath": str(cached_path),
        }
    html, fetch_status = _fetch_html(url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    raw_path = _save_raw_html(target_date, jcd, f"racelist_{int(race_no):02d}", html)
    parsed = parse_racelist_html(html, target_date=target_date, jcd=f"{int(jcd):02d}", race_no=int(race_no))
    empty_html = not html or "データがありません" in html
    data_status = "missing" if empty_html or parsed.get("dataStatus") != "available" else "ok"
    missing_reason = parsed.get("missingReason") or ([] if data_status == "available" else ["racelist_unavailable"])
    fetched_at = datetime.now().isoformat(timespec="seconds")
    return {
        "url": url,
        "fetchedAt": fetched_at,
        "fetchStatus": fetch_status if html else "unavailable",
        "dataStatus": data_status,
        "missingReason": missing_reason,
        "html": html,
        "parsed": parsed,
        "rawHtmlPath": str(raw_path),
    }


def fetch_beforeinfo_html(
    *,
    target_date: str,
    jcd: str,
    race_no: int,
    timeout: float = 8.0,
    retries: int = 0,
    retry_sleep: float = 0.0,
) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    url = BEFOREINFO_URL.format(date8=date8, jcd=f"{int(jcd):02d}", rno=int(race_no))
    cached_html, cached_path = _cached_raw_html(target_date, jcd, f"beforeinfo_{int(race_no):02d}")
    today8 = datetime.now().strftime("%Y%m%d")
    if cached_html is not None:
        parsed = parse_beforeinfo_html(cached_html, target_date, f"{int(jcd):02d}", int(race_no))
        data_status = parsed.get("dataStatus") or "ok"
        if data_status == "ok" or date8 < today8:
            return {
                "url": url,
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                "fetchStatus": "cache",
                "dataStatus": data_status,
                "dataStatusReason": parsed.get("dataStatusReason") or parsed.get("missingReason") or [],
                "missingReason": parsed.get("missingReason") or [],
                "parseWarnings": parsed.get("parseWarnings") or [],
                "fallbackUsed": False,
                "html": cached_html,
                "parsed": parsed,
                "beforeInfo": parsed.get("beforeInfo") or {},
                "rawHtmlPath": str(cached_path),
                "beforeinfoRawPath": str(cached_path),
                "beforeinfoFallbackUsed": False,
            }
    html, fetch_status = _fetch_html(url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    raw_path = _save_raw_html(target_date, jcd, f"beforeinfo_{int(race_no):02d}", html)
    parsed = parse_beforeinfo_html(html, target_date, f"{int(jcd):02d}", int(race_no))
    data_status = parsed.get("dataStatus") or ("ok" if html else "unavailable")
    missing_reason = parsed.get("missingReason") or []
    parse_warnings = parsed.get("parseWarnings") or []
    fallback_used = False
    if (not html or data_status != "ok") and date8 >= today8:
        try:
            browser_result = fetch_html_with_browser(url, timeout=max(timeout, 30.0), output_path=raw_path)
            if browser_result.html and browser_result.html != html:
                html = browser_result.html
                fetch_status = browser_result.fetch_status or fetch_status
                fallback_used = True
                parsed = parse_beforeinfo_html(html, target_date, f"{int(jcd):02d}", int(race_no))
                data_status = parsed.get("dataStatus") or "ok"
                missing_reason = parsed.get("missingReason") or []
                parse_warnings = parsed.get("parseWarnings") or []
        except Exception as exc:
            parse_warnings.append(f"browser_fallback:{exc}")
    return {
        "url": url,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "fetchStatus": fetch_status if html else "unavailable",
        "dataStatus": data_status,
        "dataStatusReason": parsed.get("dataStatusReason") or missing_reason,
        "missingReason": missing_reason,
        "parseWarnings": parse_warnings,
        "fallbackUsed": fallback_used,
        "html": html,
        "parsed": parsed,
        "beforeInfo": parsed.get("beforeInfo") or {},
        "rawHtmlPath": str(raw_path),
        "beforeinfoRawPath": str(raw_path),
        "beforeinfoFallbackUsed": fallback_used,
    }


def fetch_odds3t_html(
    *,
    target_date: str,
    jcd: str,
    race_no: int,
    race_id: str,
    timeout: float = 8.0,
    retries: int = 0,
    retry_sleep: float = 0.0,
) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    url = ODDS3T_URL.format(date8=date8, jcd=f"{int(jcd):02d}", rno=int(race_no))
    cached_html, cached_path = _cached_raw_html(target_date, jcd, f"odds3t_{int(race_no):02d}")
    today8 = datetime.now().strftime("%Y%m%d")
    if cached_html is not None:
        parsed_doc = parse_odds3t_document(cached_html, race_id)
        parsed: dict[str, Any] = dict(parsed_doc.get("parsed") or {})
        data_status = parsed_doc.get("dataStatus") or "missing"
        if data_status == "available" or date8 < today8:
            return {
                "url": url,
                "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                "fetchStatus": "cache",
                "dataStatus": data_status,
                "missingReason": [] if data_status == "available" else [parsed_doc.get("errorType") or "odds3t_unavailable"],
                "parseWarnings": [],
                "html": cached_html,
                "parsed": parsed,
                "rawHtmlPath": str(cached_path),
                "fallbackUsed": False,
                "errorType": parsed_doc.get("errorType") or "",
                "errorMessage": parsed_doc.get("errorMessage") or "",
                "containsOddsKeyword": parsed_doc.get("htmlContainsOddsKeyword", False),
                "parsedOddsCount": parsed_doc.get("parsedOddsCount", len(parsed)),
                "sampleCombos": parsed_doc.get("sampleCombos", []),
                "tableCount": parsed_doc.get("tableCount", 0),
                "rawHtmlLength": parsed_doc.get("rawHtmlLength", len(cached_html or "")),
            }
    html, fetch_status = _fetch_html(url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    fallback_used = False
    raw_path = _save_raw_html(target_date, jcd, f"odds3t_{int(race_no):02d}", html)
    parsed_doc = parse_odds3t_document(html, race_id)
    parsed: dict[str, Any] = dict(parsed_doc.get("parsed") or {})
    data_status = parsed_doc.get("dataStatus") or "missing"
    parse_warnings: list[str] = []
    error_type = parsed_doc.get("errorType") or ""
    error_message = parsed_doc.get("errorMessage") or ""
    if not html or data_status != "available":
        try:
            browser_result = fetch_html_with_browser(url, timeout=max(timeout, 30.0), output_path=raw_path)
            if browser_result.html and browser_result.html != html:
                html = browser_result.html
                fetch_status = browser_result.fetch_status or fetch_status
                fallback_used = True
                parsed_doc = parse_odds3t_document(html, race_id)
                parsed = dict(parsed_doc.get("parsed") or {})
                data_status = parsed_doc.get("dataStatus") or "missing"
                error_type = parsed_doc.get("errorType") or ""
                error_message = parsed_doc.get("errorMessage") or ""
        except Exception as exc:
            parse_warnings.append(f"browser_fallback:{exc}")
    return {
        "url": url,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "fetchStatus": fetch_status if html else "unavailable",
        "dataStatus": data_status,
        "missingReason": [] if data_status == "available" else [error_type or (parse_warnings[0] if parse_warnings else "odds3t_unavailable")],
        "parseWarnings": parse_warnings,
        "html": html,
        "parsed": parsed,
        "rawHtmlPath": str(raw_path),
        "fallbackUsed": fallback_used,
        "errorType": error_type,
        "errorMessage": error_message,
        "containsOddsKeyword": parsed_doc.get("htmlContainsOddsKeyword", False),
        "parsedOddsCount": parsed_doc.get("parsedOddsCount", len(parsed)),
        "sampleCombos": parsed_doc.get("sampleCombos", []),
        "tableCount": parsed_doc.get("tableCount", 0),
        "rawHtmlLength": parsed_doc.get("rawHtmlLength", len(html or "")),
    }


def fetch_result_html(
    *,
    target_date: str,
    jcd: str,
    race_no: int,
    race_id: str,
    timeout: float = 8.0,
    retries: int = 0,
    retry_sleep: float = 0.0,
) -> dict[str, Any]:
    date8 = _normalize_date(target_date)
    url = RACERESULT_URL.format(date8=date8, jcd=f"{int(jcd):02d}", rno=int(race_no))
    raw_path = RAW_ROOT / date8 / f"{int(jcd):02d}" / f"result_{int(race_no):02d}.html"
    if raw_path.exists() and raw_path.stat().st_size > 0:
        try:
            cached_html = raw_path.read_text(encoding="utf-8")
        except Exception:
            cached_html = ""
        if cached_html:
            cached_parsed = parse_result_html(cached_html, race_id)
            cached_status = cached_parsed.get("dataStatus") or "missing"
            if cached_status in {"available", "missing", "pending", "parse_error", "refund", "canceled", "no_contest", "available_without_trifecta"}:
                return {
                    "url": url,
                    "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                    "fetchStatus": "cache",
                    "dataStatus": cached_status,
                    "missingReason": [] if cached_status in {"available", "available_without_trifecta", "refund", "canceled", "no_contest"} else cached_parsed.get("missingReason") or ["result_unavailable"],
                    "parseWarnings": list(cached_parsed.get("parseWarnings") or []),
                    "html": cached_html,
                    "parsed": cached_parsed,
                    "rawHtmlPath": str(raw_path),
                    "resultRawPath": str(raw_path),
                    "resultFallbackUsed": False,
                    "fallbackUsed": False,
                    "errorType": "",
                    "errorMessage": "",
                }
    html = ""
    fetch_status = "unavailable"
    fallback_used = False
    parsed: dict[str, Any] = {}
    parse_warnings: list[str] = []
    error_type = ""
    error_message = ""
    if raw_path.exists() and raw_path.stat().st_size > 0:
        try:
            cached_html = raw_path.read_text(encoding="utf-8")
        except Exception:
            cached_html = ""
        if cached_html:
            cached_parsed = parse_result_html(cached_html, race_id)
            cached_status = cached_parsed.get("dataStatus") or "missing"
            if cached_status in {"available", "missing", "pending", "parse_error", "refund", "cancelled", "invalid"}:
                return {
                    "url": url,
                    "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                    "fetchStatus": "cache",
                    "dataStatus": cached_status,
                    "missingReason": [] if cached_status == "available" else cached_parsed.get("missingReason") or ["result_unavailable"],
                    "parseWarnings": list(cached_parsed.get("parseWarnings") or []),
                    "html": cached_html,
                    "parsed": cached_parsed,
                    "rawHtmlPath": str(raw_path),
                    "resultRawPath": str(raw_path),
                    "resultFallbackUsed": False,
                    "fallbackUsed": False,
                    "errorType": "",
                    "errorMessage": "",
                }
    today8 = datetime.now().strftime("%Y%m%d")
    if date8 < today8:
        return {
            "url": url,
            "fetchedAt": datetime.now().isoformat(timespec="seconds"),
            "fetchStatus": "cache-miss",
            "dataStatus": "missing",
            "missingReason": ["result_unavailable_expected"],
            "parseWarnings": ["result_cache_missing"],
            "html": "",
            "parsed": {},
            "rawHtmlPath": str(raw_path),
            "resultRawPath": str(raw_path),
            "resultFallbackUsed": False,
            "fallbackUsed": False,
            "errorType": "result_before_publish",
            "errorMessage": "historical result cache missing",
        }
    html, fetch_status = _fetch_html(url, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(html or "", encoding="utf-8")
    parsed = parse_result_html(html, race_id) if html else {}
    data_status = parsed.get("dataStatus") or ("available" if html else "unavailable")
    parse_warnings = list(parsed.get("parseWarnings") or [])
    if not html or data_status != "available":
        try:
            browser_result = fetch_html_with_browser(url, timeout=max(timeout, 30.0), output_path=raw_path)
            if browser_result.html and browser_result.html != html:
                html = browser_result.html
                fetch_status = browser_result.fetch_status or fetch_status
                fallback_used = True
                parsed = parse_result_html(html, race_id)
                data_status = parsed.get("dataStatus") or "available"
                parse_warnings = list(parsed.get("parseWarnings") or [])
        except Exception as exc:
            parse_warnings.append(f"browser_fallback:{exc}")
            if not error_message:
                error_message = str(exc)
                error_type = "result_browser_fallback_error"
    return {
        "url": url,
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "fetchStatus": fetch_status if html else "unavailable",
        "dataStatus": data_status,
        "missingReason": [] if data_status == "available" else parsed.get("missingReason") or ["result_unavailable"],
        "parseWarnings": parse_warnings,
        "html": html,
        "parsed": parsed,
        "rawHtmlPath": str(raw_path),
        "resultRawPath": str(raw_path),
        "resultFallbackUsed": fallback_used,
        "fallbackUsed": fallback_used,
        "errorType": error_type or parsed.get("errorType") or "",
        "errorMessage": error_message or parsed.get("errorMessage") or "",
    }


def fetch_day(
    *,
    target_date: str,
    jcd: str = "all",
    races: Iterable[int] | None = None,
    stage: str = "pre_race",
    timeout: float = 8.0,
    retries: int = 0,
    retry_sleep: float = 0.0,
) -> list[dict[str, Any]]:
    targets = _discover_targets(target_date, jcd=jcd, races=races)
    rows: list[dict[str, Any]] = []

    def _fetch_target(target: FetchTarget) -> dict[str, Any]:
        racelist = fetch_racelist_html(
            target_date=target.date,
            jcd=target.jcd,
            race_no=target.race_no,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
        )
        beforeinfo = {"dataStatus": "pending", "missingReason": ["beforeinfo_unavailable"], "parsed": {}, "html": ""}
        odds3t = {"dataStatus": "pending", "missingReason": ["odds3t_unavailable"], "parsed": {}, "html": ""}
        result = {"dataStatus": "pending", "missingReason": ["result_unavailable"], "parsed": {}, "html": ""}
        if stage == "beforeinfo":
            beforeinfo = fetch_beforeinfo_html(
                target_date=target.date,
                jcd=target.jcd,
                race_no=target.race_no,
                timeout=timeout,
                retries=retries,
                retry_sleep=retry_sleep,
            )
        elif stage == "odds":
            odds3t = fetch_odds3t_html(
                target_date=target.date,
                jcd=target.jcd,
                race_no=target.race_no,
                race_id=target.race_id,
                timeout=timeout,
                retries=retries,
                retry_sleep=retry_sleep,
            )
        elif stage == "result":
            result = fetch_result_html(
                target_date=target.date,
                jcd=target.jcd,
                race_no=target.race_no,
                race_id=target.race_id,
                timeout=timeout,
                retries=retries,
                retry_sleep=retry_sleep,
            )
        return {
            "date": target.date,
            "jcd": target.jcd,
            "venue_name": JCD_TO_VENUE.get(target.jcd, target.jcd),
            "race_no": target.race_no,
            "race_id": target.race_id,
            "race_title": racelist.get("parsed", {}).get("raceTitle") or f"{target.race_no}R",
            "deadline": racelist.get("parsed", {}).get("deadline") or "",
            "racelist": racelist,
            "beforeinfo": beforeinfo,
            "odds3t": odds3t,
            "result": result,
            "stage": stage,
            "source": {
                "racelistUrl": racelist["url"],
                "racelistFetchedAt": racelist["fetchedAt"],
                "racelistHttpStatus": racelist.get("fetchStatus", "unavailable"),
                "beforeinfoUrl": beforeinfo.get("url"),
                "beforeinfoFetchedAt": beforeinfo.get("fetchedAt"),
                "beforeinfoHttpStatus": beforeinfo.get("fetchStatus"),
                "beforeinfoFallbackUsed": beforeinfo.get("fallbackUsed", False),
                "beforeinfoRawPath": beforeinfo.get("rawHtmlPath"),
                "odds3tUrl": odds3t.get("url"),
                "odds3tFetchedAt": odds3t.get("fetchedAt"),
                "odds3tHttpStatus": odds3t.get("fetchStatus"),
                "odds3tFallbackUsed": odds3t.get("fallbackUsed", False),
                "resultUrl": result.get("url"),
                "resultFetchedAt": result.get("fetchedAt"),
                "resultHttpStatus": result.get("fetchStatus"),
                "resultFallbackUsed": result.get("fallbackUsed", False),
                "resultRawPath": result.get("rawHtmlPath"),
                "stage": stage,
                "modelVersion": MODEL_VERSION,
            },
        }

    if len(targets) > 1:
        max_workers = 8 if stage == "result" else 4
        with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
            future_map = {executor.submit(_fetch_target, target): target for target in targets}
            fetched_rows = [future.result() for future in as_completed(future_map)]
        rows.extend(sorted(fetched_rows, key=lambda item: (item["jcd"], item["race_no"])))
        return rows

    for target in targets:
        rows.append(_fetch_target(target))
    return rows
