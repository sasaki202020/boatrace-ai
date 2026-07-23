from __future__ import annotations
import hashlib,json,math,os,re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit
from .store import FeatureStore,stable_hash

FORBIDDEN=("result","winner","finish","payout","refund","odds","着順","払戻","結果","確定")
GROUP_FIELDS={"A":("courseEntry","startExhibition","exhibitionTime","tilt","bodyWeight"),"B":("weather","airTemp","waterTemp","windDirection","windSpeed","waveHeight"),"C":("racerRecentStarts","racerRecentAvgSt","motorRecentRate","boatRecentRate","sampleCount")}
SCHEMA_SHA256=stable_hash({"schemaVersion":1,"groups":GROUP_FIELDS})
LIVE_GROUP_FIELDS={
 "course_and_start_exhibition":("courseEntry","startExhibition","tilt","bodyWeight"),
 "exhibition_time":("exhibitionTime",),
 "weather_and_water":("weather","airTemp","waterTemp","windDirection","windSpeed","waveHeight"),
}
LIVE_SCHEMA_SHA256=stable_hash({"schemaVersion":2,"groups":LIVE_GROUP_FIELDS})
RACE_DATE=re.compile(r"^\d{4}-\d{2}-\d{2}$")
JCD=re.compile(r"^\d{2}$")

@dataclass(frozen=True)
class CollectorConfig:
 store_root:Path
 allowed_source_types:tuple[str,...]
 parser_version:str
 contract_version:str
 max_clock_drift_seconds:float=5.0
 max_capture_age_seconds:float=600.0
 allowed_source_location_prefixes:tuple[str,...]=()

@dataclass(frozen=True)
class CaptureResult:
 status:str;snapshot_id:str;raw_payload_sha256:str;schema_sha256:str;provenance_sha256:str;research_eligible:bool;reasons:tuple[str,...]

class FeatureCollector:
 def __init__(self,config):self.config=config;self.store=FeatureStore(config.store_root)
 @contextmanager
 def lock(self):
  path=Path(self.config.store_root)/"collector.lock";handle=path.open("a+b")
  try:
   handle.seek(0);handle.write(b"0");handle.flush();handle.seek(0)
   if os.name=="nt":
    import msvcrt
    try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise RuntimeError("collector_locked")
   else:
    import fcntl
    try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:raise RuntimeError("collector_locked")
   handle.seek(0);handle.truncate();handle.write(str(os.getpid()).encode());handle.flush();yield
  finally:
   try:
    handle.seek(0)
    if os.name=="nt":
     import msvcrt
     msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
     import fcntl
     fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
   except OSError:pass
   handle.close()
 def _contains_forbidden(self,value):
  if isinstance(value,dict):
   return any(any(token in str(k).lower() for token in FORBIDDEN) or self._contains_forbidden(v) for k,v in value.items())
  if isinstance(value,list):return any(self._contains_forbidden(v) for v in value)
  if isinstance(value,str):return any(token in value.lower() for token in FORBIDDEN)
  return False
 def _source_allowed(self,location):
  try:actual=urlsplit(str(location));actual_key=(actual.scheme,actual.hostname,actual.port)
  except (TypeError,ValueError):return False
  for prefix in self.config.allowed_source_location_prefixes:
   try:expected=urlsplit(prefix);expected_key=(expected.scheme,expected.hostname,expected.port)
   except (TypeError,ValueError):continue
   if actual_key!=expected_key:continue
   base=expected.path.rstrip("/")
   if actual.path==base or actual.path.startswith(base+"/"):return True
  return False
 def _feature_values_valid(self,group,payload):
  if not isinstance(payload,dict):return False
  def finite(name,low,high,integer=False):
   value=payload.get(name)
   if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return False
   return (not integer or isinstance(value,int)) and low<=value<=high
  if group=="A":return finite("courseEntry",1,6,True) and finite("startExhibition",-1,1) and finite("exhibitionTime",5,10) and finite("tilt",-1,3) and finite("bodyWeight",30,100)
  if group=="B":return isinstance(payload.get("weather"),str) and 0<len(payload["weather"])<=32 and isinstance(payload.get("windDirection"),str) and 0<len(payload["windDirection"])<=16 and finite("airTemp",-20,60) and finite("waterTemp",-5,45) and finite("windSpeed",0,60) and finite("waveHeight",0,500)
  if group=="course_and_start_exhibition":return finite("courseEntry",1,6,True) and finite("startExhibition",-1,1) and finite("tilt",-1,3) and finite("bodyWeight",30,100)
  if group=="exhibition_time":return finite("exhibitionTime",5,10)
  if group=="weather_and_water":return isinstance(payload.get("weather"),str) and 0<len(payload["weather"])<=32 and isinstance(payload.get("windDirection"),str) and 0<len(payload["windDirection"])<=16 and finite("airTemp",-20,60) and finite("waterTemp",-5,45) and finite("windSpeed",0,60) and finite("waveHeight",0,500)
  return finite("racerRecentStarts",0,10000,True) and finite("racerRecentAvgSt",-1,1) and finite("motorRecentRate",0,1) and finite("boatRecentRate",0,1) and finite("sampleCount",0,10000,True)
 def capture(self,raw:bytes):
  raw_hash=hashlib.sha256(raw).hexdigest()
  try:item=json.loads(raw)
  except Exception:
   path=Path(self.config.store_root)/"dead-letter"/f"{raw_hash}.json";path.write_bytes(raw);return CaptureResult("REJECTED",raw_hash,raw_hash,SCHEMA_SHA256,"",False,("INVALID_JSON",))
  if not isinstance(item,dict):
   path=Path(self.config.store_root)/"dead-letter"/f"{raw_hash}.json";path.write_bytes(raw);return CaptureResult("REJECTED",raw_hash,raw_hash,SCHEMA_SHA256,"",False,("SCHEMA_MISMATCH",))
  snapshot_id=stable_hash({"rawSha256":raw_hash,"raceDate":item.get("raceDate"),"jcd":item.get("jcd"),"raceNo":item.get("raceNo")})
  existing=self.store.existing(snapshot_id)
  if existing:return CaptureResult("ALREADY_CAPTURED",snapshot_id,raw_hash,existing["schema_sha256"],existing["provenance_sha256"],bool(existing["research_eligible"]),tuple(json.loads(existing["reasons_json"])))
  race_existing=self.store.existing_race(item.get("raceDate"),item.get("jcd"),item.get("raceNo"),item.get("sourceType"))
  if race_existing:return CaptureResult("REJECTED",snapshot_id,raw_hash,SCHEMA_SHA256,"",False,("SNAPSHOT_CONFLICT",))
  schema_version=item.get("schemaVersion")
  if schema_version not in {1,2}:
   (Path(self.config.store_root)/"dead-letter"/f"{snapshot_id}.json").write_bytes(raw);return CaptureResult("QUARANTINED_SCHEMA_DRIFT",snapshot_id,raw_hash,SCHEMA_SHA256,"",False,("SCHEMA_DRIFT",))
  group_fields=GROUP_FIELDS if schema_version==1 else LIVE_GROUP_FIELDS
  schema_sha256=SCHEMA_SHA256 if schema_version==1 else LIVE_SCHEMA_SHA256
  reasons=[]
  race_date=item.get("raceDate");jcd=item.get("jcd");race_no=item.get("raceNo")
  try:parsed_race_date=date.fromisoformat(race_date) if isinstance(race_date,str) and RACE_DATE.fullmatch(race_date) else None
  except ValueError:parsed_race_date=None
  identity_valid=parsed_race_date is not None and isinstance(jcd,str) and bool(JCD.fullmatch(jcd)) and 1<=int(jcd)<=24 and type(race_no) is int and 1<=race_no<=12
  if not identity_valid:reasons.append("RACE_IDENTITY_INVALID")
  if item.get("sourceType") not in self.config.allowed_source_types:reasons.append("SOURCE_NOT_APPROVED")
  if self.config.allowed_source_location_prefixes and not self._source_allowed(item.get("sourceLocation")):reasons.append("SOURCE_LOCATION_NOT_APPROVED")
  if self._contains_forbidden(item):reasons.append("RESULT_LEAKAGE")
  boats=item.get("boats")
  boat_numbers=[x.get("boatNo") for x in boats] if isinstance(boats,list) and all(isinstance(x,dict) for x in boats) else []
  if not isinstance(boats,list) or len(boats)!=6 or any(type(x) is not int for x in boat_numbers) or sorted(boat_numbers)!=list(range(1,7)):reasons.append("SCHEMA_MISMATCH")
  try:
   utc=datetime.fromisoformat(item["fetchedAtUtc"]);jst=datetime.fromisoformat(item["fetchedAtJst"]);deadline=datetime.fromisoformat(item["raceDeadlineJst"])
   if utc.tzinfo is None or jst.tzinfo is None or deadline.tzinfo is None:raise ValueError
   if utc.utcoffset()!=timedelta(0) or jst.utcoffset()!=timedelta(hours=9) or deadline.utcoffset()!=timedelta(hours=9):raise ValueError
   drift=abs((utc-jst).total_seconds());declared=float(item.get("clockDriftSeconds"))
   if not math.isfinite(declared) or declared<0 or declared>self.config.max_clock_drift_seconds or drift>self.config.max_clock_drift_seconds:reasons.append("CLOCK_DRIFT")
   age=(datetime.now(timezone.utc)-utc.astimezone(timezone.utc)).total_seconds()
   if age < -self.config.max_clock_drift_seconds or age > self.config.max_capture_age_seconds:reasons.append("CAPTURE_TIME_UNVERIFIED")
   seconds=(deadline-jst).total_seconds()
   if parsed_race_date is None or deadline.astimezone(jst.tzinfo).date()!=parsed_race_date:reasons.append("RACE_IDENTITY_INVALID")
   if seconds<=0:reasons.append("POST_DEADLINE")
  except Exception:seconds=float("-inf");reasons.append("TIMESTAMP_INVALID")
  records=[]
  if isinstance(boats,list) and len(boats)==6 and all(isinstance(x,dict) for x in boats):
   for boat in boats:
    groups=boat.get("groups",{});groups=groups if isinstance(groups,dict) else {}
    for group,fields in group_fields.items():
     payload=groups.get(group)
     if not isinstance(payload,dict) or set(payload)!=set(fields):reasons.append("SCHEMA_MISMATCH");payload=payload if isinstance(payload,dict) else {}
     elif not self._feature_values_valid(group,payload):reasons.append("FEATURE_VALUE_INVALID")
     records.append({"boat_no":boat.get("boatNo"),"feature_group":group,"payload":payload,"parse_status":"ok" if payload else "missing","missing_reason":"" if payload else "group_missing"})
  reasons=sorted(set(reasons));eligible=not reasons
  provenance=stable_hash({"sourceType":item.get("sourceType"),"sourceLocation":item.get("sourceLocation"),"fetchedAtUtc":item.get("fetchedAtUtc"),"fetchedAtJst":item.get("fetchedAtJst"),"deadlineJst":item.get("raceDeadlineJst"),"rawSha256":raw_hash,"schemaSha256":schema_sha256})
  if "RESULT_LEAKAGE" in reasons:
   dead=Path(self.config.store_root)/"dead-letter"/f"{snapshot_id}.json";dead.write_bytes(raw)
   return CaptureResult("REJECTED",snapshot_id,raw_hash,schema_sha256,provenance,False,tuple(reasons))
  if not identity_valid:
   dead=Path(self.config.store_root)/"dead-letter"/f"{snapshot_id}.json";dead.write_bytes(raw)
   return CaptureResult("REJECTED",snapshot_id,raw_hash,schema_sha256,provenance,False,tuple(reasons))
  raw_path=Path(self.config.store_root)/"raw"/race_date/jcd/str(race_no);raw_path.mkdir(parents=True,exist_ok=True);target=raw_path/f"{snapshot_id}.json";target.write_bytes(raw)
  meta={"snapshot_id":snapshot_id,"race_date":item.get("raceDate"),"jcd":item.get("jcd"),"race_no":item.get("raceNo"),"source_type":item.get("sourceType"),"source_location":item.get("sourceLocation"),"fetched_at_utc":item.get("fetchedAtUtc"),"fetched_at_jst":item.get("fetchedAtJst"),"deadline_jst":item.get("raceDeadlineJst"),"seconds_before_deadline":seconds,"parser_version":self.config.parser_version,"contract_version":self.config.contract_version,"raw_sha256":raw_hash,"schema_sha256":schema_sha256,"provenance_sha256":provenance,"status":"CAPTURED" if eligible else "REJECTED","reasons_json":json.dumps(reasons),"capture_timestamp_verified":int(not any(x in reasons for x in ("TIMESTAMP_INVALID","CLOCK_DRIFT","CAPTURE_TIME_UNVERIFIED"))),"research_eligible":int(eligible),"commercial_use_allowed":0}
  self.store.append(meta,records)
  return CaptureResult(meta["status"],snapshot_id,raw_hash,schema_sha256,provenance,eligible,tuple(reasons))
