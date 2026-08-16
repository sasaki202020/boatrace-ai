from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def build_scorecard(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {"status": "NOT_STARTED", "internalOnlyPredictions": 0, "externallyCommittedPredictions": 0, "lateOrInvalidPredictions": 0, "revealedPredictions": 0, "resultMatchedPredictions": 0, "verifiedProspectiveDays": 0, "verifiedProspectiveRaces": 0, "performance": None, "roiCalculated": False}
    connection = sqlite3.connect(ledger_path)
    scalar = lambda query: int(connection.execute(query).fetchone()[0])
    external = scalar("SELECT COUNT(DISTINCT p.race_date) FROM prediction_packages p JOIN external_anchors a ON a.package_id=p.id WHERE a.status='EXTERNALLY_COMMITTED'")
    verified_races = scalar("SELECT COUNT(DISTINCT r.race_id) FROM prediction_rows r JOIN prediction_packages p ON p.id=r.package_id JOIN external_anchors a ON a.package_id=p.id WHERE a.status='EXTERNALLY_COMMITTED'")
    return {"status": "NOT_STARTED" if scalar("SELECT COUNT(*) FROM prediction_packages") == 0 else "IN_PROGRESS", "internalOnlyPredictions": scalar("SELECT COUNT(*) FROM prediction_packages WHERE id NOT IN (SELECT package_id FROM external_anchors)"), "externallyCommittedPredictions": external, "lateOrInvalidPredictions": scalar("SELECT COUNT(*) FROM external_anchors WHERE status!='EXTERNALLY_COMMITTED'"), "revealedPredictions": scalar("SELECT COUNT(*) FROM reveals"), "resultMatchedPredictions": scalar("SELECT COUNT(*) FROM result_rows"), "verifiedProspectiveDays": external, "verifiedProspectiveRaces": verified_races, "performance": None, "roiCalculated": False}
