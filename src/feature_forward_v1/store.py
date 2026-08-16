from __future__ import annotations
import hashlib,json,sqlite3
from pathlib import Path
from typing import Any
def stable_hash(value:Any)->str:return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
class FeatureStore:
 F=("snapshot_id","race_date","jcd","race_no","source_type","source_location","fetched_at_utc","fetched_at_jst","deadline_jst","seconds_before_deadline","parser_version","contract_version","raw_sha256","schema_sha256","provenance_sha256","status","reasons_json","capture_timestamp_verified","research_eligible","commercial_use_allowed")
 def __init__(self,root:Path):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);(self.root/"raw").mkdir(exist_ok=True);(self.root/"dead-letter").mkdir(exist_ok=True);self.connection=sqlite3.connect(self.root/"feature_forward.sqlite3");self.connection.row_factory=sqlite3.Row;self._init()
 def _init(self):
  self.connection.executescript("""CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,race_date TEXT,jcd TEXT,race_no INTEGER,source_type TEXT,source_location TEXT,fetched_at_utc TEXT,fetched_at_jst TEXT,deadline_jst TEXT,seconds_before_deadline REAL,parser_version TEXT,contract_version TEXT,raw_sha256 TEXT,schema_sha256 TEXT,provenance_sha256 TEXT,status TEXT,reasons_json TEXT,capture_timestamp_verified INTEGER,research_eligible INTEGER,commercial_use_allowed INTEGER,record_hash TEXT,UNIQUE(race_date,jcd,race_no,source_type));CREATE TABLE IF NOT EXISTS feature_records(id TEXT PRIMARY KEY,snapshot_id TEXT,boat_no INTEGER,feature_group TEXT,payload_json TEXT,parse_status TEXT,missing_reason TEXT,record_hash TEXT,UNIQUE(snapshot_id,boat_no,feature_group));CREATE TABLE IF NOT EXISTS ledger_chain(sequence INTEGER PRIMARY KEY AUTOINCREMENT,record_type TEXT,record_id TEXT,previous_hash TEXT,record_hash TEXT UNIQUE);""")
  for table in ("snapshots","feature_records","ledger_chain"):
   self.connection.execute(f"CREATE TRIGGER IF NOT EXISTS no_update_{table} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'append_only_update_prohibited'); END");self.connection.execute(f"CREATE TRIGGER IF NOT EXISTS no_delete_{table} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append_only_delete_prohibited'); END")
  self.connection.commit()
 def existing(self,snapshot_id):return self.connection.execute("SELECT * FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
 def existing_race(self,race_date,jcd,race_no,source_type):return self.connection.execute("SELECT * FROM snapshots WHERE race_date=? AND jcd=? AND race_no=? AND source_type=?",(race_date,jcd,race_no,source_type)).fetchone()
 def _chain(self,kind,rid,payload_hash):
  row=self.connection.execute("SELECT record_hash FROM ledger_chain ORDER BY sequence DESC LIMIT 1").fetchone();previous=row[0] if row else "0"*64;current=stable_hash({"type":kind,"id":rid,"payloadHash":payload_hash,"previousHash":previous});self.connection.execute("INSERT INTO ledger_chain(record_type,record_id,previous_hash,record_hash) VALUES(?,?,?,?)",(kind,rid,previous,current))
 def append(self,meta,records):
  rh=stable_hash(meta);meta={**meta,"record_hash":rh}
  with self.connection:
   self.connection.execute("INSERT INTO snapshots VALUES("+",".join("?"*21)+")",tuple(meta[k] for k in self.F+("record_hash",)));self._chain("snapshot",meta["snapshot_id"],rh)
   for rec in records:
    rr=stable_hash(rec);rid=stable_hash({"snapshotId":meta["snapshot_id"],"boatNo":rec["boat_no"],"featureGroup":rec["feature_group"]});self.connection.execute("INSERT INTO feature_records VALUES(?,?,?,?,?,?,?,?)",(rid,meta["snapshot_id"],rec["boat_no"],rec["feature_group"],json.dumps(rec["payload"],sort_keys=True,separators=(",",":")),rec["parse_status"],rec["missing_reason"],rr));self._chain("feature_record",rid,rr)
 def verify_integrity(self):
  for row in self.connection.execute("SELECT * FROM snapshots"):
   if row["record_hash"]!=stable_hash({k:row[k] for k in self.F}):return {"valid":False,"reason":"snapshot_payload"}
   raw=self.root/"raw"/row["race_date"]/row["jcd"]/str(row["race_no"])/(row["snapshot_id"]+".json")
   if not raw.exists() or hashlib.sha256(raw.read_bytes()).hexdigest()!=row["raw_sha256"]:return {"valid":False,"reason":"raw_payload"}
  for row in self.connection.execute("SELECT * FROM feature_records"):
   value={"boat_no":row["boat_no"],"feature_group":row["feature_group"],"payload":json.loads(row["payload_json"]),"parse_status":row["parse_status"],"missing_reason":row["missing_reason"]}
   if row["record_hash"]!=stable_hash(value):return {"valid":False,"reason":"feature_payload"}
  previous="0"*64;seen=set()
  for row in self.connection.execute("SELECT * FROM ledger_chain ORDER BY sequence"):
   if row["previous_hash"]!=previous:return {"valid":False,"reason":"previous_hash"}
   table="snapshots" if row["record_type"]=="snapshot" else "feature_records" if row["record_type"]=="feature_record" else None
   if not table:return {"valid":False,"reason":"record_type"}
   key=(row["record_type"],row["record_id"])
   if key in seen:return {"valid":False,"reason":"duplicate_chain"}
   seen.add(key);source=self.connection.execute(f"SELECT record_hash FROM {table} WHERE "+("snapshot_id" if table=="snapshots" else "id")+"=?",(row["record_id"],)).fetchone()
   if not source:return {"valid":False,"reason":"source_missing"}
   if stable_hash({"type":row["record_type"],"id":row["record_id"],"payloadHash":source[0],"previousHash":previous})!=row["record_hash"]:return {"valid":False,"reason":"record_hash"}
   previous=row["record_hash"]
  count=self.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]+self.connection.execute("SELECT COUNT(*) FROM feature_records").fetchone()[0];chain=self.connection.execute("SELECT COUNT(*) FROM ledger_chain").fetchone()[0]
  return {"valid":count==chain,"recordCount":chain,"tailHash":previous}
