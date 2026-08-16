from __future__ import annotations

"""Build research-only market reports from explicitly supplied local JSONL files.

This script never fetches data and never reads the prospective or production stores
implicitly. Missing inputs produce blocked reports instead of inferred values.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_evaluation_v1.market_baseline import calculate_market_probabilities
from src.market_evaluation_v1.odds_snapshots import (
    OddsSnapshotError,
    compute_ev_band_metrics,
    evaluate_odds_movement,
    validate_snapshot,
)


def _load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.exists():
        raise ValueError(f"input_not_found:{path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"input_json_invalid:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"input_row_not_object:{line_number}")
        rows.append(value)
    return rows


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, title: str, body: str) -> None:
    path.write_text(f"# {title}\n\n{body.rstrip()}\n", encoding="utf-8")


def _baseline_report(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    decision_rows = [row for row in snapshots if row["stage"] == "DECISION_TIME"]
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in decision_rows:
        grouped[(row["targetDate"], row["venue"], row["raceNo"])].append(row)
    races: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for race_key, rows in sorted(grouped.items()):
        try:
            probabilities = calculate_market_probabilities(
                [{"trifecta": row["trifecta"], "odds": row["odds"]} for row in rows]
            )
        except Exception as exc:
            blocked.append({"race": race_key, "reason": str(exc)})
            continue
        races.append({"race": race_key, "combinationCount": len(probabilities), "probabilitySum": sum(row["marketProbability"] for row in probabilities)})
    status = "OK" if races and not blocked else ("BLOCKED_INCOMPLETE_RACES" if blocked else "NO_LOCAL_DECISION_ODDS")
    return {
        "status": status,
        "researchOnly": True,
        "productionAdoptionAllowed": False,
        "decisionSnapshotRaceCount": len(grouped),
        "completeRaceCount": len(races),
        "blockedRaceCount": len(blocked),
        "races": races,
        "blockedRaces": blocked,
        "note": "市場確率は120通りの締切前DECISION_TIME oddsだけで計算する。1着確率から3連単確率は推測しない。",
    }


def build_reports(
    *, output_dir: Path, snapshots_path: Path | None, ev_path: Path | None
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[dict[str, Any]] = []
    input_error: str | None = None
    try:
        for raw in _load_jsonl(snapshots_path):
            snapshots.append(validate_snapshot(raw))
    except (ValueError, OddsSnapshotError) as exc:
        input_error = str(exc)
    if input_error:
        baseline = {
            "status": "BLOCKED_INVALID_SNAPSHOT_INPUT",
            "researchOnly": True,
            "productionAdoptionAllowed": False,
            "reason": input_error,
        }
        movement = {
            "status": "BLOCKED_INVALID_SNAPSHOT_INPUT",
            "researchOnly": True,
            "productionAdoptionAllowed": False,
            "reason": input_error,
        }
    elif snapshots_path is None:
        baseline = {
            "status": "BLOCKED_NO_LOCAL_SNAPSHOT_INPUT",
            "researchOnly": True,
            "productionAdoptionAllowed": False,
            "reason": "snapshot JSONL was not supplied; no local odds are inferred",
        }
        movement = dict(baseline)
    else:
        baseline = _baseline_report(snapshots)
        movement = evaluate_odds_movement(snapshots)
        movement.update({"researchOnly": True, "productionAdoptionAllowed": False})

    ev_rows = _load_jsonl(ev_path)
    if ev_path is None:
        ev_report = {
            "status": "BLOCKED_NO_SETTLED_EVALUATION_INPUT",
            "researchOnly": True,
            "productionAdoptionAllowed": False,
            "roiComputed": False,
            "reason": "settled evaluation rows were not supplied",
        }
    else:
        ev_report = compute_ev_band_metrics(ev_rows)
        ev_report.update({"researchOnly": True, "productionAdoptionAllowed": False, "roiComputed": ev_report.get("status") == "OK"})
    _write_json(output_dir / "market_baseline_report.json", baseline)
    _write_json(output_dir / "odds_movement_report.json", movement)
    _write_json(output_dir / "ev_band_report.json", ev_report)
    _write_markdown(
        output_dir / "market_baseline_report.md",
        "Market Baseline Report",
        f"status: `{baseline['status']}`\n\n{baseline.get('note', baseline.get('reason', ''))}\n",
    )
    _write_markdown(
        output_dir / "odds_movement_report.md",
        "Odds Movement Report",
        f"status: `{movement['status']}`\n\n決定時点と締切時点の両方がある組だけを評価する。\n",
    )
    _write_markdown(
        output_dir / "ev_band_report.md",
        "EV Band Report",
        f"status: `{ev_report['status']}`\n\nROIは払戻単位が明示的に検証された決済入力がある場合だけ計算する。\n",
    )
    return {"baseline": baseline, "movement": movement, "ev": ev_report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local-only market evaluation reports")
    parser.add_argument("--snapshots", type=Path, help="local odds snapshot JSONL")
    parser.add_argument("--ev-input", type=Path, help="local settled EV rows JSONL")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/market_evaluation_v1"))
    args = parser.parse_args()
    result = build_reports(output_dir=args.output_dir, snapshots_path=args.snapshots, ev_path=args.ev_input)
    print(json.dumps({key: value.get("status") for key, value in result.items()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
