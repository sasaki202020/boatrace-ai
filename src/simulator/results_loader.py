from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.pipeline.pipeline_utils import ROOT


RESULTS_DIR = ROOT / "data" / "raw" / "official" / "results"
HISTORICAL_PATH = ROOT / "data" / "processed" / "historical_races.csv"

_RE_SECTION_BEGIN = re.compile(r"^(\d{2})KBGN$")
_RE_SECTION_END = re.compile(r"^(\d{2})KEND$")
_RE_RACE_HEADER = re.compile(r"^\s*(\d{1,2})R\b")
_RE_FINISH_ROW = re.compile(r"^\s*(\d{2})\s+(\d)\s+(\d{4})\b")
_RE_THREERACE = re.compile(r"３連単\s+(\d-\d-\d)\s+(\d+)")


@dataclass(frozen=True)
class ResultsLoadResult:
    date: str
    source_path: str
    rows: int
    status: str
    warning: str | None = None


def _normalize_date(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return pd.to_datetime(text).date().isoformat()
    except Exception:
        return text


def _file_date8(target_date: str | date | datetime) -> str:
    date_str = _normalize_date(target_date)
    if not date_str:
        raise ValueError("target_date is required")
    return date_str.replace("-", "")


def _date_from_result_filename(path: Path) -> str:
    stem = path.stem.upper()
    m = re.match(r"^K(\d{6})$", stem)
    if not m:
        return ""
    yymmdd = m.group(1)
    return f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"


def _normalize_race_key(race_id: str | None) -> str | None:
    if race_id is None or pd.isna(race_id):
        return None
    text = str(race_id).strip()
    if not text:
        return None

    m = re.match(r"^(\d{8})-(\d{2})-(\d{2})$", text)
    if m:
        date8, venue, race_no = m.groups()
        return f"d{date8}-c{int(venue):02d}-r{int(race_no):02d}"

    return text


def results_txt_path(target_date: str | date | datetime, results_dir: Path | None = None) -> Path:
    root = results_dir or RESULTS_DIR
    return root / f"K{_file_date8(target_date)[2:]}.TXT"


def _build_winning_ticket(race_rows: pd.DataFrame) -> str | None:
    if race_rows.empty:
        return None
    work = race_rows.copy()
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    top3 = work[work["finish_position"].isin([1, 2, 3])].dropna(subset=["lane", "finish_position"])
    if len(top3) < 3:
        return None
    top3 = top3.sort_values("finish_position").head(3)
    return "-".join(str(int(v)) for v in top3["lane"].tolist())


def parse_results_txt(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "race_id",
                "winning_ticket",
                "venue_code",
                "race_no",
                "source_path",
                "raw_rows",
            ]
        )

    date_str = _date_from_result_filename(path)

    try:
        lines = path.read_bytes().splitlines()
    except Exception:
        return pd.DataFrame()

    rows: list[dict] = []
    current_venue_code: str | None = None
    current_race_no: int | None = None
    current_race_rows: list[dict] = []
    in_race = False

    def flush_current() -> None:
        nonlocal current_race_rows, current_race_no, current_venue_code
        if not current_venue_code or not current_race_no:
            current_race_rows = []
            return
        race_df = pd.DataFrame(current_race_rows)
        winning_ticket = _build_winning_ticket(race_df)
        if not winning_ticket:
            current_race_rows = []
            return
        rows.append(
            {
                "date": date_str,
                "race_id": f"{date_str.replace('-', '')}-{current_venue_code}-{current_race_no:02d}",
                "race_key": f"d{date_str.replace('-', '')}-c{int(current_venue_code):02d}-r{current_race_no:02d}",
                "winning_ticket": winning_ticket,
                "venue_code": current_venue_code,
                "race_no": current_race_no,
                "source_path": str(path),
                "raw_rows": int(len(race_df)),
            }
        )
        current_race_rows = []

    for raw_line in lines:
        line = raw_line.decode("cp932", errors="replace")
        if _RE_SECTION_BEGIN.match(line.strip()):
            current_venue_code = _RE_SECTION_BEGIN.match(line.strip()).group(1)
            current_race_no = None
            current_race_rows = []
            in_race = False
            continue
        if _RE_SECTION_END.match(line.strip()):
            flush_current()
            current_race_no = None
            current_race_rows = []
            in_race = False
            continue

        race_header = _RE_RACE_HEADER.match(line)
        if race_header:
            flush_current()
            current_race_no = int(race_header.group(1))
            current_race_rows = []
            in_race = True
            continue

        if in_race and current_race_no is not None:
            finish_row = _RE_FINISH_ROW.match(line)
            if finish_row:
                try:
                    current_race_rows.append(
                        {
                            "finish_position": int(finish_row.group(1)),
                            "lane": int(finish_row.group(2)),
                            "racer_id": finish_row.group(3),
                            "line_text": line,
                        }
                    )
                except Exception:
                    pass
                continue

            if _RE_THREERACE.search(line):
                # parseable but unused here; kept as evidence of a complete race block
                continue

    flush_current()
    out = pd.DataFrame(rows)
    if not out.empty:
        out["date"] = out["date"].astype(str)
        out["race_id"] = out["race_id"].astype(str)
        out["race_key"] = out["race_id"].apply(_normalize_race_key)
        out["winning_ticket"] = out["winning_ticket"].astype(str)
        out["race_no"] = pd.to_numeric(out["race_no"], errors="coerce").astype("Int64")
    return out


def load_results_for_date(target_date: str | date | datetime, results_dir: Path | None = None) -> tuple[pd.DataFrame, ResultsLoadResult]:
    date_str = _normalize_date(target_date)
    if not date_str:
        raise ValueError("target_date is required")
    base_dir = results_dir or RESULTS_DIR
    raw_path = results_txt_path(date_str, base_dir)
    if not raw_path.exists():
        return pd.DataFrame(), ResultsLoadResult(
            date=date_str,
            source_path=str(raw_path),
            rows=0,
            status="raw_missing",
            warning="missing raw results file",
        )

    df = parse_results_txt(raw_path)
    if df.empty:
        return df, ResultsLoadResult(
            date=date_str,
            source_path=str(raw_path),
            rows=0,
            status="raw_incomplete",
            warning="parsed raw results but no complete top3 races",
        )

    return df, ResultsLoadResult(
        date=date_str,
        source_path=str(raw_path),
        rows=int(len(df)),
        status="available",
        warning=None,
    )


def load_results_from_raw(target_date: str | date | datetime, results_dir: Path | None = None) -> pd.DataFrame:
    df, _ = load_results_for_date(target_date, results_dir)
    return df


def load_results_from_historical(historical_path: Path | None = None) -> pd.DataFrame:
    path = historical_path or HISTORICAL_PATH
    if not path.exists():
        return pd.DataFrame(columns=["date", "race_id", "race_key", "winning_ticket", "source_path", "raw_rows"])
    hist = pd.read_csv(path, low_memory=False)
    required = {"date", "race_id", "lane", "finish_position"}
    missing = required - set(hist.columns)
    if missing:
        raise ValueError(f"historical file missing columns: {sorted(missing)}")
    work = hist.copy()
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    rows: list[dict] = []
    for race_id, group in work.dropna(subset=["race_id", "lane", "finish_position"]).groupby("race_id", dropna=True):
        winning_ticket = _build_winning_ticket(group)
        if not winning_ticket:
            continue
        rows.append(
            {
                "date": str(group["date"].dropna().astype(str).iloc[0]) if not group["date"].dropna().empty else pd.NA,
                "race_id": str(race_id),
                "race_key": _normalize_race_key(str(race_id)),
                "winning_ticket": winning_ticket,
                "source_path": str(path),
                "raw_rows": int(len(group)),
            }
        )
    return pd.DataFrame(rows)
