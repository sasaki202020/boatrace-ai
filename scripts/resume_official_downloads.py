from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import io
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

try:
    import lhafile
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise ImportError("pip install lhafile を実行してください") from exc


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_PATH = LOG_DIR / "resume_downloads_state.json"


def url_b(ds: str) -> str:
    yyyymm = ds[:6]
    yymmdd = ds[2:]
    return f"http://www1.mbrace.or.jp/od2/B/{yyyymm}/b{yymmdd}.lzh"


def url_k(ds: str) -> str:
    yyyymm = ds[:6]
    yymmdd = ds[2:]
    return f"http://www1.mbrace.or.jp/od2/K/{yyyymm}/k{yymmdd}.lzh"


def to_text(data: bytes) -> str:
    for enc in ("shift_jis", "utf-8", "cp932"):
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def log_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line.rstrip("\n") + "\n")


def parse_yyyymmdd(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


@dataclass(frozen=True)
class MonthWindow:
    start: date
    end: date


def month_windows(start: date, end: date) -> list[MonthWindow]:
    windows: list[MonthWindow] = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        month_end = date(cur.year, cur.month, last_day)
        windows.append(MonthWindow(start=max(start, cur), end=min(end, month_end)))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return windows


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"months": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def count_files(root: Path, prefix: str) -> int:
    target = root / "data" / "raw" / prefix
    if not target.exists():
        return 0
    return sum(1 for p in target.glob("*.txt") if p.is_file())


def latest_date(root: Path, prefix: str) -> str | None:
    target = root / "data" / "raw" / prefix
    if not target.exists():
        return None
    dates: list[str] = []
    for path in target.glob("*.txt"):
        stem = path.stem
        if len(stem) == 8 and stem.isdigit():
            dates.append(stem)
    return max(dates) if dates else None


def existing_for_day(root: Path, ds: str) -> bool:
    return (root / "data" / "raw" / "B" / f"{ds}.txt").exists() and (
        root / "data" / "raw" / "K" / f"{ds}.txt"
    ).exists()


def fetch_one(prefix: str, ds: str, out_dir: Path) -> tuple[str, str, str | None]:
    save = out_dir / f"{ds}.txt"
    if save.exists():
        return prefix, ds, "exists"

    url = url_b(ds) if prefix == "B" else url_k(ds)
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 404:
            return prefix, ds, "404"
        resp.raise_for_status()

        lha_cls = getattr(lhafile, "LhaFile", None) or getattr(lhafile, "Lhafile", None)
        if lha_cls is None:
            return prefix, ds, "error: lhafile class not found"

        lzh = lha_cls(io.BytesIO(resp.content))
        names = lzh.namelist()
        if not names:
            return prefix, ds, "error: empty archive"

        data = lzh.read(names[0])
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(to_text(data), encoding="utf-8")
        return prefix, ds, "ok"
    except Exception as exc:  # noqa: BLE001
        return prefix, ds, f"error: {exc}"


def month_tasks(window: MonthWindow) -> list[tuple[str, str]]:
    tasks: list[tuple[str, str]] = []
    cur = window.start
    while cur <= window.end:
        ds = cur.strftime("%Y%m%d")
        tasks.append(("B", ds))
        tasks.append(("K", ds))
        cur += timedelta(days=1)
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume monthly BOATRACE official downloads for B/K")
    parser.add_argument("--start", default="20230303", help="Start date YYYYMMDD")
    parser.add_argument("--end", default="20241231", help="End date YYYYMMDD")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers inside a month")
    parser.add_argument("--log-file", default="", help="Optional explicit log file path")
    args = parser.parse_args()

    start = parse_yyyymmdd(args.start)
    end = parse_yyyymmdd(args.end)
    if start > end:
        raise ValueError("start must be <= end")
    if args.workers < 1:
        raise ValueError("workers must be >= 1")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = Path(args.log_file) if args.log_file else LOG_DIR / f"resume_downloads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    state = load_state()
    state.setdefault("range", {"start": args.start, "end": args.end})
    state.setdefault("months", {})
    save_state(state)

    log_line(log_file, f"[START] {datetime.now().isoformat(timespec='seconds')} from={args.start} to={args.end}")

    windows = month_windows(start, end)
    for window in windows:
        key = f"{window.start:%Y-%m}"
        before_b = count_files(ROOT, "B")
        before_k = count_files(ROOT, "K")
        missing_days = [ds for _, ds in month_tasks(window) if not existing_for_day(ROOT, ds)]

        if not missing_days:
            state["months"][key] = {
                "start": window.start.strftime("%Y%m%d"),
                "end": window.end.strftime("%Y%m%d"),
                "status": "done",
                "returncode": 0,
                "files_before": {"B": before_b, "K": before_k},
                "files_after": {"B": before_b, "K": before_k},
                "skipped_existing": True,
            }
            save_state(state)
            log_line(log_file, f"[SKIP] {key} already complete")
            continue

        log_line(log_file, f"[RUN] {window.start:%Y%m%d} -> {window.end:%Y%m%d}")
        log_line(log_file, f"[BEFORE] B={before_b} K={before_k} missing_days={len(missing_days)} workers={args.workers}")

        results: list[tuple[str, str, str | None]] = []
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            future_map = {}
            for prefix, ds in month_tasks(window):
                out_dir = ROOT / "data" / "raw" / prefix
                future = ex.submit(fetch_one, prefix, ds, out_dir)
                future_map[future] = (prefix, ds)
            for future in cf.as_completed(future_map):
                results.append(future.result())

        after_b = count_files(ROOT, "B")
        after_k = count_files(ROOT, "K")
        errors = [r for r in results if r[2] and r[2].startswith("error")]
        month_status = "done" if not errors else "failed"
        state["months"][key] = {
            "start": window.start.strftime("%Y%m%d"),
            "end": window.end.strftime("%Y%m%d"),
            "status": month_status,
            "returncode": 0 if not errors else 1,
            "files_before": {"B": before_b, "K": before_k},
            "files_after": {"B": after_b, "K": after_k},
            "downloaded": sum(1 for _, _, status in results if status == "ok"),
            "skipped_existing": sum(1 for _, _, status in results if status == "exists"),
            "not_found": sum(1 for _, _, status in results if status == "404"),
            "errors": [f"{prefix}:{ds}:{status}" for prefix, ds, status in errors],
        }
        save_state(state)

        log_line(log_file, f"[AFTER] B={after_b} K={after_k} ok={state['months'][key]['downloaded']} 404={state['months'][key]['not_found']} errors={len(errors)}")
        log_line(log_file, f"[MONTH] {key} status={month_status}")

        if errors:
            log_line(log_file, f"[STOP] {key} failed, can resume from this month")
            return 1

    final_b = latest_date(ROOT, "B")
    final_k = latest_date(ROOT, "K")
    total_b = count_files(ROOT, "B")
    total_k = count_files(ROOT, "K")
    log_line(log_file, f"[DONE] latest_B={final_b} latest_K={final_k} count_B={total_b} count_K={total_k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
