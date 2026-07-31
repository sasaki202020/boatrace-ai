from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import requests

from src.commercialization_v2.day1_readiness import validate_runtime_bfile
from src.ingest.parsers.beforeinfo_parser import parse_beforeinfo_html
from bs4 import BeautifulSoup

from .collector import CollectorConfig, FeatureCollector
from .source_policy import classify_response

JST = ZoneInfo("Asia/Tokyo")
BEFOREINFO_URL = (
    "https://www.boatrace.jp/owpc/pc/race/beforeinfo"
    "?hd={date8}&jcd={jcd}&rno={race_no}"
)


@dataclass(frozen=True)
class CaptureTarget:
    race_date: str
    jcd: str
    race_no: int
    deadline_jst: datetime

    @property
    def race_key(self) -> str:
        return f"{self.race_date.replace('-', '')}-{self.jcd}-{self.race_no:02d}"

    @property
    def url(self) -> str:
        return BEFOREINFO_URL.format(
            date8=self.race_date.replace("-", ""),
            jcd=self.jcd,
            race_no=self.race_no,
        )


class RequestLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests(
              race_key TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              requested_at_utc TEXT NOT NULL,
              status_code INTEGER,
              response_sha256 TEXT,
              outcome TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS no_update_requests
              BEFORE UPDATE ON requests BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_requests
              BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END;
            """
        )
        self.connection.commit()

    def requested(self, race_key: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM requests WHERE race_key=?", (race_key,)
        ).fetchone() is not None

    def last_requested_at(self) -> datetime | None:
        row = self.connection.execute(
            "SELECT requested_at_utc FROM requests ORDER BY requested_at_utc DESC LIMIT 1"
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def selected_venue(self, race_date: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM state WHERE key=?", (f"venue:{race_date}",)
        ).fetchone()
        return str(row[0]) if row else None

    def selected_venues(self, race_date: str) -> list[str]:
        row = self.connection.execute(
            "SELECT value FROM state WHERE key=?", (f"venues:{race_date}",)
        ).fetchone()
        selected = [value for value in str(row[0]).split(",") if value] if row else []
        legacy = self.selected_venue(race_date)
        if legacy and legacy not in selected:
            selected.insert(0, legacy)
        return selected

    def select_venue(self, race_date: str, jcd: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO state(key,value) VALUES(?,?)",
                (f"venue:{race_date}", jcd),
            )

    def select_venues(self, race_date: str, jcds: list[str]) -> None:
        values = ",".join(jcds)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO state(key,value) VALUES(?,?)",
                (f"venues:{race_date}", values),
            )

    def stopped(self) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM state WHERE key='stopped'"
        ).fetchone() is not None

    def stop(self, reason: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO state(key,value) VALUES('stopped',?)", (reason,)
            )

    def append(
        self,
        *,
        target: CaptureTarget,
        requested_at_utc: datetime,
        status_code: int,
        response_sha256: str,
        outcome: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?)",
                (
                    target.race_key,
                    target.url,
                    requested_at_utc.isoformat(),
                    status_code,
                    response_sha256,
                    outcome,
                ),
            )


def targets_from_bfile(path: Path, now: datetime) -> list[CaptureTarget]:
    entries = validate_runtime_bfile(path)
    rows = entries[["date", "jcd", "race_no", "deadline"]].drop_duplicates()
    targets: list[CaptureTarget] = []
    for row in rows.itertuples(index=False):
        deadline = datetime.fromisoformat(f"{row.date}T{row.deadline}:00").replace(tzinfo=JST)
        if deadline > now:
            targets.append(
                CaptureTarget(str(row.date), str(row.jcd).zfill(2), int(row.race_no), deadline)
            )
    return sorted(targets, key=lambda item: (item.deadline_jst, item.jcd, item.race_no))


def _verified_collection_days(store_root: Path) -> int:
    database = Path(store_root) / "feature_forward.sqlite3"
    if not database.is_file():
        return 0
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        count = int(connection.execute(
            "SELECT COUNT(DISTINCT race_date) FROM snapshots WHERE research_eligible=1"
        ).fetchone()[0])
        connection.close()
        return count
    except sqlite3.Error:
        return 0


def _venue_limit_for_days(collection_days: int) -> int:
    if collection_days >= 7:
        return 5
    if collection_days >= 3:
        return 2
    return 1


def _start_value(value: object) -> float | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text.startswith("F"):
        text = "-" + text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def build_envelope(target: CaptureTarget, html: str, captured_at_utc: datetime) -> dict:
    parsed = parse_beforeinfo_html(
        html, target.race_date.replace("-", ""), target.jcd, target.race_no
    )
    boats = {int(item["boat_no"]): item for item in parsed.get("boats", [])}
    starts = {int(item["no"]): item for item in parsed.get("startExhibition", [])}
    weather = parsed.get("weather") or {}
    water = weather.get("water") or {}
    output = []
    for boat_no in range(1, 7):
        boat = boats.get(boat_no, {})
        start = starts.get(boat_no, {})
        output.append(
            {
                "boatNo": boat_no,
                "groups": {
                    "course_and_start_exhibition": {
                        "courseEntry": int(start.get("course") or boat_no),
                        "startExhibition": _start_value(start.get("st")),
                        "tilt": boat.get("tilt"),
                        "bodyWeight": boat.get("bodyWeight"),
                    },
                    "exhibition_time": {"exhibitionTime": boat.get("exhibitionTime")},
                    "weather_and_water": {
                        "weather": weather.get("sky"),
                        "airTemp": weather.get("temperature"),
                        "waterTemp": water.get("temperature"),
                        "windDirection": weather.get("windDirection"),
                        "windSpeed": weather.get("windSpeed"),
                        "waveHeight": weather.get("waveHeight"),
                    },
                },
            }
        )
    captured_jst = captured_at_utc.astimezone(JST)
    return {
        "schemaVersion": 2,
        "sourceType": "OFFICIAL_PUBLIC_BEFOREINFO",
        "sourceLocation": target.url,
        "fetchedAtUtc": captured_at_utc.isoformat(),
        "fetchedAtJst": captured_jst.isoformat(),
        "raceDeadlineJst": target.deadline_jst.isoformat(),
        "clockDriftSeconds": 0.0,
        "raceDate": target.race_date,
        "jcd": target.jcd,
        "raceNo": target.race_no,
        "boats": output,
    }


@contextmanager
def _cycle_lock(store_root: Path):
    path = store_root / "live_capture.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _race_identity_matches(html: str, target: CaptureTarget) -> bool:
    soup = BeautifulSoup(html, "lxml")
    expected = {
        "hd": target.race_date.replace("-", ""),
        "jcd": target.jcd,
        "rno": str(target.race_no),
    }
    for link in soup.select("th > a[href*='/race/beforeinfo']"):
        parent_classes = set(link.parent.get("class") or [])
        if "is-thColor2" in parent_classes:
            continue
        query = parse_qs(urlsplit(str(link.get("href") or "")).query)
        actual = {key: values[0] for key, values in query.items() if values}
        if all(actual.get(key) == value for key, value in expected.items()):
            return True
    return False


def _run_capture_cycle_unlocked(
    *,
    b_file: Path,
    store_root: Path,
    now: datetime | None = None,
    opener=None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    now_jst = now.astimezone(JST)
    ledger = RequestLedger(store_root / "request_ledger.sqlite3")
    if ledger.stopped():
        return {"status": "FEATURE_COLLECTION_STOPPED", "networkRequests": 0}
    targets = targets_from_bfile(b_file, now_jst)
    selectable_targets = [
        target for target in targets
        if (target.deadline_jst - now_jst).total_seconds() >= 360
    ]
    race_date = now_jst.date().isoformat()
    venue_limit = _venue_limit_for_days(_verified_collection_days(store_root))
    selected_venues = ledger.selected_venues(race_date)
    if selectable_targets and not selected_venues:
        by_venue: dict[str, list[CaptureTarget]] = {}
        for target in selectable_targets:
            by_venue.setdefault(target.jcd, []).append(target)
        candidates = {
            key: values for key, values in by_venue.items() if len(values) >= 3
        } or by_venue
        selected_venues = sorted(
            candidates,
            key=lambda key: (candidates[key][0].deadline_jst, key),
        )[:venue_limit]
        ledger.select_venues(race_date, selected_venues)
    elif selectable_targets and len(selected_venues) < venue_limit:
        by_venue = {}
        for target in selectable_targets:
            by_venue.setdefault(target.jcd, []).append(target)
        candidates = sorted(
            by_venue,
            key=lambda key: (by_venue[key][0].deadline_jst, key),
        )
        selected_venues = [
            venue for venue in selected_venues if venue in by_venue
        ] + [
            venue for venue in candidates if venue not in selected_venues
        ]
        selected_venues = selected_venues[:venue_limit]
        if not ledger.connection.execute(
            "SELECT 1 FROM state WHERE key=?", (f"venues:{race_date}",)
        ).fetchone():
            ledger.select_venues(race_date, selected_venues)
    targets = [target for target in targets if target.jcd in selected_venues]
    due = [
        target for target in targets
        if 360 <= (target.deadline_jst - now_jst).total_seconds() <= 480
        and not ledger.requested(target.race_key)
    ]
    if not due:
        return {
            "status": "WAITING_FOR_CAPTURE_WINDOW",
            "networkRequests": 0,
            "selectedVenue": selected_venues[0] if selected_venues else None,
            "selectedVenues": selected_venues,
            "venueLimit": venue_limit,
        }
    previous = ledger.last_requested_at()
    if previous is not None and (now - previous).total_seconds() < 60:
        return {
            "status": "REQUEST_INTERVAL_BLOCKED",
            "networkRequests": 0,
            "selectedVenues": selected_venues,
            "venueLimit": venue_limit,
        }
    target = due[0]
    requested_at = datetime.now(timezone.utc)
    request = Request(target.url, headers={"User-Agent": "boatrace-ai-mvp/1.0"})
    try:
        if opener is None:
            response = requests.get(
                target.url,
                timeout=30,
                allow_redirects=True,
                headers={"User-Agent": "boatrace-ai-mvp/1.0"},
            )
            status_code = int(response.status_code)
            raw = response.content
            final_url = response.url
        else:
            with opener(request, timeout=30) as response:
                status_code = int(response.status)
                raw = response.read()
                final_url = response.geturl()
    except HTTPError as exc:
        status_code = int(exc.code)
        raw = exc.read()
        final_url = target.url
    except (requests.RequestException, OSError, TimeoutError) as exc:
        ledger.append(
            target=target,
            requested_at_utc=requested_at,
            status_code=0,
            response_sha256=hashlib.sha256(b"").hexdigest(),
            outcome=type(exc).__name__.upper(),
        )
        return {
            "status": "FEATURE_CAPTURE_NETWORK_ERROR",
            "networkRequests": 1,
            "raceKey": target.race_key,
            "reason": type(exc).__name__.upper(),
        }
    captured_at = datetime.now(timezone.utc)
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    classification = classify_response(status_code, text)
    if final_url.split("?", 1)[0] != target.url.split("?", 1)[0]:
        classification = type(classification)(True, "UNEXPECTED_REDIRECT")
    seconds_before_deadline = (
        target.deadline_jst - captured_at.astimezone(JST)
    ).total_seconds()
    if not classification.stop_collection and status_code == 200:
        if not _race_identity_matches(text, target):
            classification = type(classification)(True, "RACE_IDENTITY_MISMATCH")
        elif not 360 <= seconds_before_deadline <= 480:
            classification = type(classification)(True, "CAPTURE_WINDOW_MISSED")
    outcome = classification.reason if classification.stop_collection else "HTTP_OK"
    ledger.append(
        target=target,
        requested_at_utc=requested_at,
        status_code=status_code,
        response_sha256=digest,
        outcome=outcome,
    )
    if classification.stop_collection or status_code != 200:
        ledger.stop(outcome)
        return {"status": "FEATURE_COLLECTION_STOPPED", "networkRequests": 1, "reason": outcome}
    envelope = build_envelope(target, text, captured_at)
    raw_envelope = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    collector = FeatureCollector(
        CollectorConfig(
            store_root,
            ("OFFICIAL_PUBLIC_BEFOREINFO",),
            "official-beforeinfo-parser-v1",
            "feature-forward-v2",
            allowed_source_location_prefixes=(
                "https://www.boatrace.jp/owpc/pc/race/beforeinfo",
            ),
        )
    )
    result = collector.capture(raw_envelope)
    return {
        "status": result.status,
        "networkRequests": 1,
        "raceKey": target.race_key,
        "secondsBeforeDeadline": (
            target.deadline_jst - captured_at.astimezone(JST)
        ).total_seconds(),
        "rawPayloadSha256": result.raw_payload_sha256,
        "schemaSha256": result.schema_sha256,
        "provenanceSha256": result.provenance_sha256,
        "reasons": list(result.reasons),
    }


def run_capture_cycle(
    *,
    b_file: Path,
    store_root: Path,
    now: datetime | None = None,
    opener=None,
) -> dict:
    try:
        with _cycle_lock(store_root):
            return _run_capture_cycle_unlocked(
                b_file=b_file, store_root=store_root, now=now, opener=opener
            )
    except OSError:
        return {"status": "COLLECTOR_LOCKED", "networkRequests": 0}
