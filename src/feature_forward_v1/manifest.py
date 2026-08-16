from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone

def _hash(values):
 return hashlib.sha256(json.dumps(sorted(values),separators=(",",":")).encode()).hexdigest()
def build_daily_manifest(date,rows,created_at_utc=None):
 return {"schemaVersion":1,"date":date,"snapshotCount":len(rows),"snapshotSetSha256":_hash([r["snapshot_id"]+r["raw_sha256"] for r in rows]),"schemaSetSha256":_hash([r["schema_sha256"] for r in rows]),"createdAtUtc":created_at_utc or datetime.now(timezone.utc).isoformat()}
