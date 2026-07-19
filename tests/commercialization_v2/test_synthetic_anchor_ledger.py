from src.commercialization_v2.github_contents_anchor import canonical_synthetic_bytes
from src.commercialization_v2.synthetic_anchor_ledger import SyntheticAnchorLedger


def test_synthetic_ledger_is_one_to_one_idempotent_and_has_no_prediction_tables(tmp_path) -> None:
    package = {
        "schemaVersion": 2,
        "testType": "SYNTHETIC_EXTERNAL_ANCHOR",
        "commitment": "a" * 64,
        "candidateId": "tree_15",
        "modelSha256": "b" * 64,
        "featureSchemaSha256": "c" * 64,
        "syntheticRowCount": 6,
        "syntheticRaceCount": 1,
        "clientCreatedAt": "2026-07-19T00:00:00+00:00",
        "noProfitClaim": True,
        "realPrediction": False,
    }
    package_hash = __import__("hashlib").sha256(canonical_synthetic_bytes(package)).hexdigest()
    result = {
        "status": "CREATED", "package_sha256": package_hash, "readback_sha256": package_hash,
        "path": f"anchors/synthetic/{package['commitment']}.json", "object_sha": "blob",
        "commit_sha": "commit", "committed_at": "2026-07-18T00:00:00Z",
    }
    ledger = SyntheticAnchorLedger(tmp_path / "synthetic.sqlite3")
    assert ledger.record(package, result) == package_hash
    assert ledger.record(package, {**result, "status": "IDEMPOTENT"}) == package_hash
    assert ledger.verify() == {"valid": True, "sourceCount": 1, "commitCount": 1, "chainCount": 2}
    with __import__("pytest").raises(Exception):
        ledger.connection.execute("UPDATE synthetic_anchor_chain SET previous_hash='x'")
    with __import__("pytest").raises(Exception):
        ledger.connection.execute("DELETE FROM synthetic_anchor_chain")
    tables = {row[0] for row in ledger.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"prediction_packages", "prediction_rows", "reveals", "result_packages", "result_rows"} & tables
