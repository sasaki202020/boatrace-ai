from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable
from urllib import error, request


ROOT_DIR = Path(__file__).resolve().parents[1]
OFFICIAL_ROOT = ROOT_DIR / "data" / "raw" / "official"
MANIFEST_PATH = OFFICIAL_ROOT / "logs" / "download_manifest.json"
USER_AGENT = "Mozilla/5.0 (compatible; BoatRaceAIMVPDownloader/1.0)"


@dataclass(frozen=True)
class DatasetSpec:
    dataset: str
    save_dir: Path
    official_index_url: str
    cadence: str

    def build_url(self, target_key: str) -> str:
        if self.dataset == "results":
            dt = datetime.strptime(target_key, "%Y-%m-%d").date()
            return f"https://www1.mbrace.or.jp/od2/K/{dt.strftime('%Y%m')}/k{dt.strftime('%y%m%d')}.lzh"
        if self.dataset == "entries":
            dt = datetime.strptime(target_key, "%Y-%m-%d").date()
            return f"https://www1.mbrace.or.jp/od2/B/{dt.strftime('%Y%m')}/b{dt.strftime('%y%m%d')}.lzh"
        if self.dataset == "fanbook":
            month = datetime.strptime(target_key, "%Y-%m").date()
            return f"https://boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan{month.strftime('%y%m')}.lzh"
        raise ValueError(f"Unsupported dataset: {self.dataset}")

    def build_filename(self, target_key: str) -> str:
        if self.dataset == "results":
            dt = datetime.strptime(target_key, "%Y-%m-%d").date()
            return f"k{dt.strftime('%y%m%d')}.lzh"
        if self.dataset == "entries":
            dt = datetime.strptime(target_key, "%Y-%m-%d").date()
            return f"b{dt.strftime('%y%m%d')}.lzh"
        if self.dataset == "fanbook":
            month = datetime.strptime(target_key, "%Y-%m").date()
            return f"fan{month.strftime('%y%m')}.lzh"
        raise ValueError(f"Unsupported dataset: {self.dataset}")


DATASET_SPECS = {
    "results": DatasetSpec(
        dataset="results",
        save_dir=OFFICIAL_ROOT / "results",
        official_index_url="https://www1.mbrace.or.jp/od2/K/dindex.html",
        cadence="daily",
    ),
    "entries": DatasetSpec(
        dataset="entries",
        save_dir=OFFICIAL_ROOT / "entries",
        official_index_url="https://www1.mbrace.or.jp/od2/B/dindex.html",
        cadence="daily",
    ),
    "fanbook": DatasetSpec(
        dataset="fanbook",
        save_dir=OFFICIAL_ROOT / "fanbook",
        official_index_url="https://boatrace.jp/owpc/pc/extra/data/download.html",
        cadence="monthly",
    ),
}


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_month(value: str) -> date:
    return datetime.strptime(f"{value}-01", "%Y-%m-%d").date()


def iter_days(start_day: date, end_day: date) -> Iterable[str]:
    if end_day < start_day:
        raise ValueError("end day must be on or after start day")
    current = start_day
    while current <= end_day:
        yield current.strftime("%Y-%m-%d")
        current = current.fromordinal(current.toordinal() + 1)


def iter_months(start_month: date, end_month: date) -> Iterable[str]:
    if end_month < start_month:
        raise ValueError("end month must be on or after start month")
    year = start_month.year
    month = start_month.month
    while (year, month) <= (end_month.year, end_month.month):
        yield f"{year:04d}-{month:02d}"
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def ensure_layout() -> None:
    for spec in DATASET_SPECS.values():
        spec.save_dir.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists():
        write_manifest({"version": 1, "records": []})


def load_manifest() -> dict:
    ensure_layout()
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write_manifest(payload: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=MANIFEST_PATH.parent) as tmp_file:
        json.dump(payload, tmp_file, ensure_ascii=True, indent=2)
        tmp_name = tmp_file.name
    Path(tmp_name).replace(MANIFEST_PATH)


def append_manifest_record(record: dict) -> None:
    manifest = load_manifest()
    manifest.setdefault("version", 1)
    manifest.setdefault("records", [])
    manifest["records"].append(record)
    write_manifest(manifest)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_record(
    spec: DatasetSpec,
    target_key: str,
    url: str,
    status: str,
    saved_path: Path | None,
    http_status: int | None = None,
    byte_size: int | None = None,
    checksum_sha256: str | None = None,
    message: str | None = None,
) -> dict:
    return {
        "timestamp_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "dataset": spec.dataset,
        "cadence": spec.cadence,
        "target_key": target_key,
        "official_index_url": spec.official_index_url,
        "download_url": url,
        "status": status,
        "http_status": http_status,
        "saved_path": str(saved_path.relative_to(ROOT_DIR)) if saved_path else None,
        "byte_size": byte_size,
        "checksum_sha256": checksum_sha256,
        "message": message,
    }


def download_one(
    spec: DatasetSpec,
    target_key: str,
    force: bool = False,
    dry_run: bool = False,
    timeout_sec: float = 30.0,
) -> dict:
    ensure_layout()
    filename = spec.build_filename(target_key)
    url = spec.build_url(target_key)
    destination = spec.save_dir / filename

    if destination.exists() and not force:
        record = build_record(
            spec=spec,
            target_key=target_key,
            url=url,
            status="skipped_existing",
            saved_path=destination,
            byte_size=destination.stat().st_size,
            message="file already exists",
        )
        append_manifest_record(record)
        return record

    if dry_run:
        record = build_record(
            spec=spec,
            target_key=target_key,
            url=url,
            status="dry_run",
            saved_path=destination,
            message="download not executed",
        )
        append_manifest_record(record)
        return record

    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:
            content = response.read()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            record = build_record(
                spec=spec,
                target_key=target_key,
                url=url,
                status="success",
                saved_path=destination,
                http_status=getattr(response, "status", 200),
                byte_size=len(content),
                checksum_sha256=sha256_hex(content),
            )
            append_manifest_record(record)
            return record
    except error.HTTPError as exc:
        record = build_record(
            spec=spec,
            target_key=target_key,
            url=url,
            status="http_error",
            saved_path=destination if destination.exists() else None,
            http_status=exc.code,
            message=str(exc),
        )
        append_manifest_record(record)
        return record
    except error.URLError as exc:
        record = build_record(
            spec=spec,
            target_key=target_key,
            url=url,
            status="network_error",
            saved_path=destination if destination.exists() else None,
            message=str(exc.reason),
        )
        append_manifest_record(record)
        return record
    except Exception as exc:
        record = build_record(
            spec=spec,
            target_key=target_key,
            url=url,
            status="error",
            saved_path=destination if destination.exists() else None,
            message=str(exc),
        )
        append_manifest_record(record)
        return record


def download_many(
    dataset: str,
    target_keys: Iterable[str],
    force: bool = False,
    dry_run: bool = False,
    delay_sec: float = 1.0,
    timeout_sec: float = 30.0,
) -> list[dict]:
    spec = DATASET_SPECS[dataset]
    targets = list(target_keys)
    records: list[dict] = []
    for index, target_key in enumerate(targets):
        record = download_one(
            spec=spec,
            target_key=target_key,
            force=force,
            dry_run=dry_run,
            timeout_sec=timeout_sec,
        )
        records.append(record)
        if index + 1 < len(targets) and delay_sec > 0 and not dry_run:
            time.sleep(delay_sec)
    return records


def add_common_download_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", help="既存ファイルがあっても再取得する")
    parser.add_argument("--dry-run", action="store_true", help="保存せず manifest のみ記録する")
    parser.add_argument("--delay", type=float, default=1.0, help="リクエスト間隔（秒）")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP タイムアウト（秒）")


def summarize_records(records: list[dict]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
