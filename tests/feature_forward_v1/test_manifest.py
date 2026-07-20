from src.feature_forward_v1.manifest import build_daily_manifest
def test_daily_manifest_contains_hashes_and_counts_only():
 rows=[{"snapshot_id":"a","raw_sha256":"b"*64,"schema_sha256":"c"*64,"fetched_at_utc":"2026-07-21T03:00:00+00:00"}]
 result=build_daily_manifest("2026-07-21",rows)
 assert set(result)=={"schemaVersion","date","snapshotCount","snapshotSetSha256","schemaSetSha256","createdAtUtc"}
 text=str(result).lower()
 for forbidden in ("boat","venue","probability","racer","rawpayload"):assert forbidden not in text
 assert result["snapshotCount"]==1
