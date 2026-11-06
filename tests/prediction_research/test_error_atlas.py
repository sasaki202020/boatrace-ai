import hashlib, json, sqlite3
from src.prediction_research.error_atlas import build_error_atlas

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def make_ledger(path,settled):
    c=sqlite3.connect(path); c.executescript("CREATE TABLE prediction_packages(id TEXT PRIMARY KEY, package_json TEXT NOT NULL); CREATE TABLE result_rows(id TEXT PRIMARY KEY, package_id TEXT, race_id TEXT, winning_lane INTEGER, record_hash TEXT);")
    ps=["0.40","0.25","0.15","0.10","0.06","0.04"]; rows=[{"raceId":"secret-race-id","raceNumber":4,"venue":"secret-venue","lane":i,"predictedProbability":p} for i,p in enumerate(ps,1)]
    c.execute("INSERT INTO prediction_packages VALUES(?,?)",("pkg",json.dumps({"raceDate":"2026-07-20","predictions":rows})))
    if settled: c.execute("INSERT INTO result_rows VALUES(?,?,?,?,?)",("r","pkg","secret-race-id",2,"h"))
    c.commit(); c.close()
def test_error_atlas_is_read_only_and_redacts_race_level_values(tmp_path):
    d=tmp_path/"2026-07-20"; d.mkdir(); ledger=d/"shadow.sqlite3"; make_ledger(ledger,True); before=digest(ledger); a=build_error_atlas(tmp_path)
    assert digest(ledger)==before and a["settledRaces"]==1 and a["winnerRankDistribution"]=={"2":1} and a["tree15Correct"]=={"correct":0,"incorrect":1}
    assert a["status"]=="DIAGNOSTIC_ONLY_INSUFFICIENT_SETTLEMENTS" and a["featureMissingness"]=="UNAVAILABLE_IN_LEDGER"
    s=json.dumps(a); assert "secret-race-id" not in s and "secret-venue" not in s and "0.40" not in s
def test_unsettled_predictions_are_counted_without_diagnostic_rows(tmp_path):
    d=tmp_path/"2026-07-20"; d.mkdir(); make_ledger(d/"shadow.sqlite3",False); a=build_error_atlas(tmp_path)
    assert (a["predictedRaces"],a["settledRaces"],a["pendingRaces"],a["segments"])==(1,0,1,[])

