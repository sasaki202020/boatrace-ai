"""Read-only prospective error aggregation."""
import hashlib, json, math, sqlite3
from collections import Counter
from pathlib import Path

def _digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def build_error_atlas(root: Path):
    ledgers=sorted(root.glob("*/shadow.sqlite3")); before={str(p):_digest(p) for p in ledgers}; races={}; results={}
    for path in ledgers:
        con=sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True)
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "prediction_packages" in tables:
            cols={r[1] for r in con.execute("PRAGMA table_info(prediction_packages)")}
            col=next((x for x in ("package_json","canonical_package_json","payload_json") if x in cols),None)
            if col:
                for (raw,) in con.execute(f"SELECT {col} FROM prediction_packages"):
                    package=json.loads(raw); grouped={}
                    for row in package.get("predictions",[]): grouped.setdefault(str(row["raceId"]),[]).append(row)
                    for race_id,rows in grouped.items():
                        if len(rows)==6:
                            probs=[float(r["predictedProbability"]) for r in rows]
                            if all(math.isfinite(p) and 0<=p<=1 for p in probs) and math.isclose(sum(probs),1,abs_tol=1e-6):
                                races[race_id]=sorted(rows,key=lambda r:float(r["predictedProbability"]),reverse=True)
        if "result_rows" in tables:
            cols={r[1] for r in con.execute("PRAGMA table_info(result_rows)")}
            rc=next((x for x in ("race_id","raceId","race_key") if x in cols),None); wc=next((x for x in ("winning_lane","winner_lane","winnerLane","winner") if x in cols),None)
            if rc and wc: results.update({str(r):int(w) for r,w in con.execute(f"SELECT {rc},{wc} FROM result_rows")})
        con.close()
    ranks=[]
    for race_id,rows in races.items():
        if race_id in results:
            rank=next((i for i,row in enumerate(rows,1) if int(row["lane"])==results[race_id]),None)
            if rank: ranks.append(rank)
    settled=len(ranks)
    return {"status":"DIAGNOSTIC_READY" if settled>=300 else "DIAGNOSTIC_ONLY_INSUFFICIENT_SETTLEMENTS","predictedRaces":len(races),"settledRaces":settled,"pendingRaces":len(races)-settled,"winnerRankDistribution":{str(k):v for k,v in sorted(Counter(ranks).items())},"tree15Correct":{"correct":sum(r==1 for r in ranks),"incorrect":sum(r!=1 for r in ranks)},"featureMissingness":"UNAVAILABLE_IN_LEDGER","segments":[],"trainingAllowed":False,"thresholdSelectionAllowed":False,"databaseHashesUnchanged":before=={str(p):_digest(p) for p in ledgers}}

