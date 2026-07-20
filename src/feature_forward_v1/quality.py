from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from .store import FeatureStore

def build_quality_report(store_root:Path):
 store=FeatureStore(store_root);rows=list(store.connection.execute("SELECT * FROM snapshots"));captured=len(rows);eligible=sum(bool(r["research_eligible"]) for r in rows);days=len({r["race_date"] for r in rows if r["research_eligible"]})
 reasons=Counter(x for r in rows for x in json.loads(r["reasons_json"]))
 groups={g:{"scheduledRaces":None,"capturedRaces":captured,"verifiedPreDeadlineRaces":eligible,"coverage":None,"missingReasons":dict(reasons),"majorMissingReason":"schedule_denominator_unavailable","postDeadlineCount":reasons["POST_DEADLINE"],"duplicateCount":0,"schemaDrift":reasons["SCHEMA_DRIFT"],"parserFailure":reasons["SCHEMA_MISMATCH"],"anchorSuccess":0,"clockDrift":reasons["CLOCK_DRIFT"],"consecutiveCollectionDays":days,"status":"FEATURE_SOURCE_NOT_READY"} for g in ("A","B","C")}
 return {"featureGroups":groups,"externalTimestampVerified":False,"commercialUseAllowed":False,"integrity":store.verify_integrity()}
