from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


DATE_RE = re.compile(r"(?P<year>20\d{2})/\s*(?P<month>\d{1,2})/\s*(?P<day>\d{1,2})")
RACE_RE = re.compile(r"^\s*(?P<race_no>\d{1,2})R\b")
JCD_RE = re.compile(r"^\s*(?P<jcd>\d{2})[BK]BGN\b")
ENTRY_ROW_RE = re.compile(
    r"^(?P<lane>[1-6])\s+"
    r"(?P<toban>\d{4})"
    r"(?P<name>.+?)"
    r"(?P<age>\d{2})"
    r"(?P<grade>A1|A2|B1|B2)\s+"
    r"(?P<rest>.+)$"
)
RESULT_ROW_RE = re.compile(
    r"^\s*(?P<rank>\d{2})\s+"
    r"(?P<lane>[1-6])\s+"
    r"(?P<toban>\d{4})\s+"
    r"(?P<rest>.+)$"
)
PAYOUT_ROW_RE = re.compile(r"^\s*(?P<race_no>\d{1,2})R\s+(?P<combo>\d-\d-\d)\s+(?P<payout>\d+)")
RACE_ID_PREFIX_RE = re.compile(r"^(?P<prefix>[BK])(?P<date6>\d{6})$", re.IGNORECASE)


def read_text_lines(path: Path) -> list[str]:
    for encoding in ("utf-8", "cp932", "utf-8-sig"):
        try:
            return path.read_text(encoding=encoding, errors="replace").splitlines()
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def normalize_date(value: str | None, fallback_path: Path) -> str:
    if value:
        return value
    stem = fallback_path.stem
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"([BK])?(\d{2})(\d{2})(\d{2})", stem, re.IGNORECASE)
    if m:
        return f"20{m.group(2)}-{m.group(3)}-{m.group(4)}"
    return ""


def build_source_tag(path: Path, raw_prefix: str) -> str:
    stem = path.stem.strip()
    m = RACE_ID_PREFIX_RE.match(stem)
    if m:
        return stem.upper()
    m = re.search(r"(\d{8})", stem)
    if m:
        return f"{raw_prefix.upper()}{m.group(1)[2:]}"
    return f"{raw_prefix.upper()}{datetime.now().strftime('%y%m%d')}"


def build_race_id(date_value: str, source_tag: str, section_seq: int, race_no: int) -> str:
    date8 = date_value.replace("-", "")
    return f"{date8}-{source_tag}_s{section_seq:02d}-{race_no:02d}"


def build_normalized_race_key(date_value: str, section_seq: int, race_no: int) -> str:
    date8 = date_value.replace("-", "")
    return f"d{date8}-c{section_seq:02d}-r{race_no:02d}"


def assign_normalized_race_keys(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "race_id" not in df.columns:
        return df

    out = df.copy()
    keys = pd.Series("", index=out.index, dtype=object)
    race_id = out["race_id"].astype(str)

    section_match = race_id.str.extract(
        r"^(?P<date8>\d{8})-(?P<source>[A-Z]\d{6})_s(?P<section>\d{2})-(?P<race>\d{2})$"
    )
    section_mask = section_match["date8"].notna()
    if section_mask.any():
        sec_df = section_match.loc[section_mask].copy()
        sec_df["section"] = pd.to_numeric(sec_df["section"], errors="coerce")
        sec_df["race"] = pd.to_numeric(sec_df["race"], errors="coerce")
        for date8, grp in sec_df.groupby("date8", sort=False):
            section_values = [int(v) for v in grp["section"].dropna().tolist()]
            section_order = []
            for v in section_values:
                if v not in section_order:
                    section_order.append(v)
            section_rank_map = {sec: idx + 1 for idx, sec in enumerate(section_order)}
            idx = out.index[section_mask & (section_match["date8"] == date8)]
            for i in idx:
                sec = int(float(section_match.at[i, "section"]))
                race_no = int(float(section_match.at[i, "race"]))
                keys.at[i] = f"d{date8}-c{section_rank_map.get(sec, sec):02d}-r{race_no:02d}"

    numeric_mask = keys.eq("")
    if numeric_mask.any():
        numeric_match = race_id.str.extract(r"^(?P<date8>\d{8})-(?P<venue>\d{2})-(?P<race>\d{2})$")
        numeric_rows = numeric_match.loc[numeric_mask & numeric_match["date8"].notna()].copy()
        for i in numeric_rows.index:
            keys.at[i] = f"d{numeric_match.at[i, 'date8']}-v{int(numeric_match.at[i, 'venue']):02d}-r{int(numeric_match.at[i, 'race']):02d}"

    fallback_mask = keys.eq("")
    if fallback_mask.any() and {"date", "jcd", "race_no"}.issubset(out.columns):
        fallback_dates = pd.to_datetime(out.loc[fallback_mask, "date"], errors="coerce").dt.strftime("%Y%m%d")
        fallback_jcd = pd.to_numeric(out.loc[fallback_mask, "jcd"], errors="coerce")
        fallback_rno = pd.to_numeric(out.loc[fallback_mask, "race_no"], errors="coerce")
        for idx in out.index[fallback_mask]:
            date8 = fallback_dates.loc[idx] if idx in fallback_dates.index else ""
            jcd_val = fallback_jcd.loc[idx] if idx in fallback_jcd.index else pd.NA
            race_val = fallback_rno.loc[idx] if idx in fallback_rno.index else pd.NA
            if pd.notna(date8) and pd.notna(jcd_val) and pd.notna(race_val):
                keys.at[idx] = f"d{date8}-v{int(jcd_val):02d}-r{int(race_val):02d}"

    out["normalized_race_key"] = keys.replace({"": pd.NA})
    return out


def extract_date(lines: list[str], fallback_path: Path) -> str:
    for line in lines[:20]:
        m = DATE_RE.search(line)
        if m:
            return f"{m.group('year')}-{int(m.group('month')):02d}-{int(m.group('day')):02d}"
    return normalize_date(None, fallback_path)


def extract_jcd(lines: list[str], fallback_path: Path) -> str:
    for line in lines[:5]:
        m = JCD_RE.match(line.strip())
        if m:
            return m.group("jcd")
    m = re.search(r"(\d{2})", fallback_path.stem)
    return m.group(1) if m else ""


def safe_float(text: str) -> str:
    txt = text.strip()
    if not txt or txt in {".", "....."}:
        return ""
    return txt


def parse_entry_rest(rest: str) -> dict[str, str]:
    tokens = rest.split()
    padded = tokens + [""] * max(0, 11 - len(tokens))
    padded = padded[:11]
    return {
        "win_rate_all": safe_float(padded[0]),
        "win_rate_venue": safe_float(padded[1]),
        "avg_st": safe_float(padded[2]),
        "in2_rate": safe_float(padded[3]),
        "motor_no": padded[4].strip(),
        "motor_win_rate": safe_float(padded[5]),
        "boat_no": padded[6].strip(),
        "boat_win_rate": safe_float(padded[7]),
        "f_count": padded[8].strip(),
        "l_count": padded[9].strip(),
        "weight": padded[10].strip(),
    }


def parse_b_file(path: Path) -> pd.DataFrame:
    lines = read_text_lines(path)
    file_date = extract_date(lines, path)
    source_tag = build_source_tag(path, "B")
    records: list[dict[str, str]] = []
    race_no = 1
    rows_in_race = 0
    current_jcd = ""
    section_seq = 1

    for line in lines:
        m_jcd = JCD_RE.match(line.strip())
        if m_jcd:
            current_jcd = m_jcd.group("jcd")
            race_no = 1
            rows_in_race = 0
            section_seq += 1
            continue

        m_row = ENTRY_ROW_RE.match(line)
        if not m_row:
            continue
        if rows_in_race >= 6:
            race_no += 1
            rows_in_race = 0

        rest_fields = parse_entry_rest(m_row.group("rest"))
        records.append(
            {
                "race_id": build_race_id(file_date, source_tag, section_seq, race_no),
                "date": file_date,
                "jcd": current_jcd or extract_jcd(lines, path),
                "race_no": race_no,
                "lane": m_row.group("lane"),
                "toban": m_row.group("toban"),
                "name": m_row.group("name").strip(),
                "tenji_time": "",
                "start_exhibition_st": "",
                "motor_no": rest_fields["motor_no"],
                "motor_win_rate": rest_fields["motor_win_rate"],
                "boat_no": rest_fields["boat_no"],
                "boat_win_rate": rest_fields["boat_win_rate"],
                "win_rate_all": rest_fields["win_rate_all"],
                "win_rate_venue": rest_fields["win_rate_venue"],
                "avg_st": rest_fields["avg_st"],
                "in2_rate": rest_fields["in2_rate"],
                "in3_rate": "",
                "f_count": rest_fields["f_count"],
                "l_count": rest_fields["l_count"],
                "grade": m_row.group("grade"),
                "age": m_row.group("age"),
                "weight": rest_fields["weight"],
            }
        )
        rows_in_race += 1

    return assign_normalized_race_keys(pd.DataFrame(records))


def parse_result_row(line: str) -> dict[str, str] | None:
    m = RESULT_ROW_RE.match(line)
    if not m:
        return None
    tokens = line.split()
    if len(tokens) < 6:
        return None

    rank = m.group("rank")
    lane = m.group("lane")
    toban = m.group("toban")
    st = tokens[-2] if len(tokens) >= 2 else ""
    return {
        "rank": rank,
        "lane": lane,
        "toban": toban,
        "st": safe_float(st),
    }


def parse_k_file(path: Path) -> pd.DataFrame:
    lines = read_text_lines(path)
    file_date = extract_date(lines, path)
    source_tag = build_source_tag(path, "K")
    records: list[dict[str, str]] = []
    race_no = 1
    rows_in_race = 0
    current_jcd = ""
    section_seq = 1
    payout_by_race: dict[str, tuple[str, str]] = {}

    for line in lines:
        m_jcd = JCD_RE.match(line.strip())
        if m_jcd:
            current_jcd = m_jcd.group("jcd")
            race_no = 1
            rows_in_race = 0
            section_seq += 1
            continue

        m_payout = PAYOUT_ROW_RE.match(line)
        if m_payout:
            payout_by_race[m_payout.group("race_no")] = (m_payout.group("combo"), m_payout.group("payout"))
            continue

    for line in lines:
        m_jcd = JCD_RE.match(line.strip())
        if m_jcd:
            current_jcd = m_jcd.group("jcd")
            race_no = 1
            rows_in_race = 0
            continue

        row = parse_result_row(line)
        if not row:
            continue
        if rows_in_race >= 6:
            race_no += 1
            rows_in_race = 0
        combo, payout = payout_by_race.get(str(race_no), ("", ""))
        hit = "Y" if combo and payout else "N"
        records.append(
            {
                "race_id": build_race_id(file_date, source_tag, section_seq, race_no),
                "date": file_date,
                "jcd": current_jcd or extract_jcd(lines, path),
                "race_no": race_no,
                "lane": row["lane"],
                "rank": row["rank"],
                "st": row["st"],
                "toban": row["toban"],
                "combo": combo,
                "payout": payout,
                "hit": hit,
            }
        )
        rows_in_race += 1

    return assign_normalized_race_keys(pd.DataFrame(records))


def collect_input_files(raw_dir: Path, prefix: str) -> list[Path]:
    if not raw_dir.exists():
        return []
    files = []
    for path in sorted(raw_dir.rglob("*.txt")):
        if path.name.lower().startswith(prefix.lower()):
            files.append(path)
    for path in sorted(raw_dir.rglob("*.TXT")):
        if path.name.lower().startswith(prefix.lower()) and path not in files:
            files.append(path)
    return files


def write_frame(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse BOAT RACE official B/K text files")
    parser.add_argument("--raw-b", required=True, help="番組表 raw ディレクトリ")
    parser.add_argument("--raw-k", required=True, help="競走成績 raw ディレクトリ")
    parser.add_argument("--output-dir", default="data/csv", help="CSV 出力先ルート")
    args = parser.parse_args()

    raw_b = Path(args.raw_b)
    raw_k = Path(args.raw_k)
    output_root = Path(args.output_dir)
    program_dir = output_root / "program"
    result_dir = output_root / "result"
    meta_path = output_root.parent / "run_meta.json"

    started_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    stats: dict[str, object] = {
        "started_at": started_at,
        "raw_b_dir": str(raw_b),
        "raw_k_dir": str(raw_k),
        "program_files": 0,
        "result_files": 0,
        "program_rows": 0,
        "result_rows": 0,
        "skipped_files": [],
        "errors": [],
    }

    program_input_files = list(collect_input_files(raw_b, "b"))
    result_input_files = list(collect_input_files(raw_k, "k"))

    program_frames: list[pd.DataFrame] = []
    for path in program_input_files:
        try:
            df = parse_b_file(path)
            if df.empty:
                stats["skipped_files"].append({"file": str(path), "reason": "empty"})
                continue
            program_frames.append(df)
            stats["program_files"] = int(stats["program_files"]) + 1
            stats["program_rows"] = int(stats["program_rows"]) + len(df)
        except Exception as exc:
            stats["errors"].append({"file": str(path), "error": str(exc)})

    result_frames: list[pd.DataFrame] = []
    for path in result_input_files:
        try:
            df = parse_k_file(path)
            if df.empty:
                stats["skipped_files"].append({"file": str(path), "reason": "empty"})
                continue
            result_frames.append(df)
            stats["result_files"] = int(stats["result_files"]) + 1
            stats["result_rows"] = int(stats["result_rows"]) + len(df)
        except Exception as exc:
            stats["errors"].append({"file": str(path), "error": str(exc)})

    program_df = pd.concat(program_frames, ignore_index=True) if program_frames else pd.DataFrame()
    result_df = pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame()

    if not program_df.empty:
        write_frame(program_df, program_dir / "program.csv")
    if not result_df.empty:
        write_frame(result_df, result_dir / "result.csv")

    finished_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    meta = {
        "schema_version": "1.0.0",
        "started_at": started_at,
        "finished_at": finished_at,
        "input_file_count": len(program_input_files) + len(result_input_files),
        "input_file_count_b": len(program_input_files),
        "input_file_count_k": len(result_input_files),
        "raw_b_files": stats["program_files"],
        "raw_k_files": stats["result_files"],
        "program_rows": stats["program_rows"],
        "result_rows": stats["result_rows"],
        "skipped_count": len(stats["skipped_files"]),
        "error_count": len(stats["errors"]),
        "skipped_files": stats["skipped_files"],
        "errors": stats["errors"],
        "outputs": {
            "program": str(program_dir / "program.csv"),
            "result": str(result_dir / "result.csv"),
        },
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
