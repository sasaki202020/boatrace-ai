from __future__ import annotations

"""Backtest external prediction baselines saved by the local web API."""

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports" / "external" / "baseline_compare"


def _normalize_date(value: str) -> str:
    text = str(value or "").strip()
    if text == "today":
        return datetime.now().strftime("%Y-%m-%d")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(_normalize_date(start_date))
    end = date.fromisoformat(_normalize_date(end_date))
    if end < start:
        start, end = end, start
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _merge_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        dst = merged.setdefault(source, {
            "source": source,
            "name": row.get("name") or source,
            "raceCount": 0,
            "predictionRaceCount": 0,
            "predictionCount": 0,
            "settledPredictionRaceCount": 0,
            "hitCount": 0,
            "top1HitCount": 0,
            "top3HitCount": 0,
            "top5HitCount": 0,
            "returnYen": 0,
        })
        for key in [
            "raceCount",
            "predictionRaceCount",
            "predictionCount",
            "settledPredictionRaceCount",
            "hitCount",
            "top1HitCount",
            "top3HitCount",
            "top5HitCount",
            "returnYen",
        ]:
            dst[key] += int(row.get(key) or 0)

    result = []
    for row in merged.values():
        settled = int(row["settledPredictionRaceCount"] or 0)
        row["hitRate"] = round(row["hitCount"] / settled, 4) if settled else None
        row["top1HitRate"] = round(row["top1HitCount"] / settled, 4) if settled else None
        row["top3HitRate"] = round(row["top3HitCount"] / settled, 4) if settled else None
        row["top5HitRate"] = round(row["top5HitCount"] / settled, 4) if settled else None
        row["roi"] = round(row["returnYen"] / (settled * 100), 4) if settled else None
        result.append(row)
    result.sort(key=lambda item: (-int(item.get("settledPredictionRaceCount") or 0), str(item.get("name") or "")))
    return result


def _write_outputs(payload: dict[str, Any], out_dir: Path, stem: str) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "source",
        "name",
        "raceCount",
        "predictionRaceCount",
        "settledPredictionRaceCount",
        "hitCount",
        "hitRate",
        "top1HitRate",
        "top3HitRate",
        "top5HitRate",
        "roi",
        "returnYen",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("sources", []):
            writer.writerow({key: row.get(key) for key in columns})

    lines = [
        "# External baseline backtest",
        "",
        f"- 期間: {payload.get('startDate')} - {payload.get('endDate')}",
        f"- 日数: {payload.get('dateCount')}",
        f"- 生成: {payload.get('generatedAt')}",
        "",
        "| source | settled | hit | hitRate | top1 | top3 | top5 | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload.get("sources", []):
        def pct(value: Any) -> str:
            return "-" if value is None else f"{float(value) * 100:.1f}%"

        lines.append(
            "| {name} | {settled} | {hit} | {hit_rate} | {top1} | {top3} | {top5} | {roi} |".format(
                name=row.get("name") or row.get("source"),
                settled=row.get("settledPredictionRaceCount") or 0,
                hit=row.get("hitCount") or 0,
                hit_rate=pct(row.get("hitRate")),
                top1=pct(row.get("top1HitRate")),
                top3=pct(row.get("top3HitRate")),
                top5=pct(row.get("top5HitRate")),
                roi=pct(row.get("roi")),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "md": str(md_path)}


def backtest_external_baselines(
    *,
    start_date: str,
    end_date: str,
    jcd: str | None = None,
    max_races: int | None = None,
    fetch: bool = False,
    reconcile: bool = True,
) -> dict[str, Any]:
    dates = _date_range(start_date, end_date)
    day_summaries: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for day in dates:
        try:
            if fetch:
                web_app.build_external_yosou_batch(day, jcd=jcd, max_races=max_races)
            if reconcile:
                web_app.reconcile_saved_external_yosou(day, jcd=jcd, max_races=max_races)
            summary = web_app.build_external_yosou_summary(day, jcd=jcd)
            day_summaries.append(summary)
            for row in summary.get("sources") or []:
                source_rows.append(row)
        except Exception as exc:
            errors.append({"date": day, "error": str(exc)})

    payload = {
        "status": "ok" if not errors else "partial",
        "startDate": dates[0],
        "endDate": dates[-1],
        "dateCount": len(dates),
        "requestedJcd": web_app._normalize_jcd(jcd) if jcd else "all",
        "fetch": bool(fetch),
        "reconcile": bool(reconcile),
        "sources": _merge_source_rows(source_rows),
        "days": day_summaries,
        "errors": errors,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    stem = f"{dates[0].replace('-', '')}_{dates[-1].replace('-', '')}_external_baselines"
    payload["outputFiles"] = _write_outputs(payload, REPORT_ROOT, stem)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest saved external prediction baselines.")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD, YYYYMMDD, or today.")
    parser.add_argument("--end-date", help="YYYY-MM-DD, YYYYMMDD, or today. Defaults to --start-date.")
    parser.add_argument("--jcd", default=None, help="Venue code. Omit or use all for active portal venues.")
    parser.add_argument("--max-races", type=int, default=0)
    parser.add_argument("--fetch", action="store_true", help="Fetch and save external predictions before settling.")
    parser.add_argument("--no-reconcile", action="store_true", help="Skip result reconciliation.")
    args = parser.parse_args(argv)

    jcd = None if not args.jcd or str(args.jcd).lower() == "all" else str(args.jcd)
    result = backtest_external_baselines(
        start_date=args.start_date,
        end_date=args.end_date or args.start_date,
        jcd=jcd,
        max_races=args.max_races or None,
        fetch=bool(args.fetch),
        reconcile=not args.no_reconcile,
    )
    print(json.dumps({
        "status": result["status"],
        "startDate": result["startDate"],
        "endDate": result["endDate"],
        "sources": result["sources"],
        "outputFiles": result["outputFiles"],
        "errorCount": len(result["errors"]),
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
