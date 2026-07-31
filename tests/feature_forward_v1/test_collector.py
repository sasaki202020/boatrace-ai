from __future__ import annotations
import hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from src.feature_forward_v1.collector import CollectorConfig, FeatureCollector
from src.feature_forward_v1.store import FeatureStore

JST=timezone(timedelta(hours=9))

def envelope(now=None):
    now=now or datetime.now(timezone.utc)
    deadline=now.astimezone(JST)+timedelta(minutes=20)
    boats=[]
    for boat in range(1,7):
        boats.append({"boatNo":boat,"groups":{
            "A":{"courseEntry":boat,"startExhibition":0.1+boat/100,"exhibitionTime":6.7+boat/100,"tilt":0.0,"bodyWeight":50+boat},
            "B":{"weather":"fine","airTemp":30.0,"waterTemp":27.0,"windDirection":"N","windSpeed":2.0,"waveHeight":2.0},
            "C":{"racerRecentStarts":5,"racerRecentAvgSt":0.16,"motorRecentRate":0.35,"boatRecentRate":0.34,"sampleCount":10}}})
    return {"schemaVersion":1,"sourceType":"LOCAL_APPROVED_SNAPSHOT","sourceLocation":"fixture://pre-race",
        "fetchedAtUtc":now.isoformat(),"fetchedAtJst":now.astimezone(JST).isoformat(),
        "raceDeadlineJst":deadline.isoformat(),"clockDriftSeconds":0.2,"raceDate":deadline.date().isoformat(),
        "jcd":"01","raceNo":1,"boats":boats}

def collector(tmp_path):
    cfg=CollectorConfig(store_root=tmp_path/"store",allowed_source_types=("LOCAL_APPROVED_SNAPSHOT",),
        parser_version="fixture-v1",contract_version="feature-forward-v1")
    return FeatureCollector(cfg)

def test_capture_is_append_only_idempotent_and_hashes_reproduce(tmp_path):
    item=envelope(); raw=json.dumps(item,sort_keys=True,separators=(",",":")).encode()
    first=collector(tmp_path).capture(raw); second=collector(tmp_path).capture(raw)
    assert first.status=="CAPTURED" and second.status=="ALREADY_CAPTURED"
    assert first.snapshot_id==second.snapshot_id
    assert first.raw_payload_sha256==hashlib.sha256(raw).hexdigest()
    assert FeatureStore(tmp_path/"store").verify_integrity()["valid"] is True
    assert len(list((tmp_path/"store"/"raw").rglob("*.json")))==1

@pytest.mark.parametrize("mutation,reason",[
    (lambda x:x.update(raceDeadlineJst=x["fetchedAtJst"]),"POST_DEADLINE"),
    (lambda x:x.update(clockDriftSeconds=6.0),"CLOCK_DRIFT"),
    (lambda x:x.update(sourceType="UNAPPROVED"),"SOURCE_NOT_APPROVED"),
    (lambda x:x["boats"][0]["groups"]["A"].update(result=1),"RESULT_LEAKAGE"),
    (lambda x:x["boats"].pop(),"SCHEMA_MISMATCH")])
def test_fail_closed_inputs_are_recorded_not_eligible(tmp_path,mutation,reason):
    item=envelope();mutation(item);result=collector(tmp_path).capture(json.dumps(item).encode())
    assert result.status=="REJECTED" and reason in result.reasons and result.research_eligible is False


def test_result_leakage_is_quarantined_outside_normal_raw_store(tmp_path):
    item=envelope();item["boats"][0]["groups"]["A"]["result"]=1
    result=collector(tmp_path).capture(json.dumps(item).encode())
    assert result.status=="REJECTED"
    assert list((tmp_path/"store"/"dead-letter").glob("*.json"))
    assert not list((tmp_path/"store"/"raw").rglob("*.json"))
    assert FeatureStore(tmp_path/"store").existing(result.snapshot_id) is None

def test_schema_drift_is_quarantined(tmp_path):
    item=envelope();item["schemaVersion"]=3
    result=collector(tmp_path).capture(json.dumps(item).encode())
    assert result.status=="QUARANTINED_SCHEMA_DRIFT"
    assert list((tmp_path/"store"/"dead-letter").glob("*.json"))

def test_store_detects_tamper_and_prohibits_update_delete(tmp_path):
    result=collector(tmp_path).capture(json.dumps(envelope()).encode())
    store=FeatureStore(tmp_path/"store")
    with pytest.raises(Exception):store.connection.execute("UPDATE snapshots SET status='x'")
    with pytest.raises(Exception):store.connection.execute("DELETE FROM snapshots")
    assert result.research_eligible is True and store.verify_integrity()["valid"] is True

def test_collector_lock_blocks_concurrent_run(tmp_path):
    c=collector(tmp_path)
    with c.lock():
        with pytest.raises(RuntimeError,match="collector_locked"):
            with c.lock():pass

def test_deterministic_rerun(tmp_path):
    raw=json.dumps(envelope(),sort_keys=True).encode()
    a=collector(tmp_path/"a").capture(raw);b=collector(tmp_path/"b").capture(raw)
    assert (a.snapshot_id,a.schema_sha256,a.provenance_sha256)==(b.snapshot_id,b.schema_sha256,b.provenance_sha256)


def test_second_payload_for_same_race_is_conflict(tmp_path):
 c=collector(tmp_path);first=envelope();second=envelope();second["boats"][0]["groups"]["A"]["tilt"]=0.5
 assert c.capture(json.dumps(first).encode()).status=="CAPTURED"
 conflict=c.capture(json.dumps(second).encode())
 assert conflict.status=="REJECTED" and "SNAPSHOT_CONFLICT" in conflict.reasons
 assert FeatureStore(tmp_path/"store").connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]==1

def test_payload_tamper_is_detected(tmp_path):
 c=collector(tmp_path);c.capture(json.dumps(envelope()).encode());store=FeatureStore(tmp_path/"store")
 store.connection.execute("DROP TRIGGER no_update_feature_records")
 store.connection.execute("UPDATE feature_records SET payload_json='{}'");store.connection.commit()
 assert store.verify_integrity()["valid"] is False


def test_stale_lock_is_recovered(tmp_path):
 c=collector(tmp_path);lock=tmp_path/"store"/"collector.lock";lock.write_text("99999999")
 with c.lock():assert lock.exists()
 with c.lock():assert lock.exists()


@pytest.mark.parametrize("field,value", [("raceDate", "../../escape"), ("jcd", "../x"), ("raceNo", 13)])
def test_invalid_race_identity_cannot_escape_store(tmp_path, field, value):
 item=envelope();item[field]=value
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "RACE_IDENTITY_INVALID" in result.reasons
 assert not list(tmp_path.parent.glob("escape*"))


@pytest.mark.parametrize("group,field,value", [("A","exhibitionTime",None),("B","windSpeed",float("inf")),("C","sampleCount",-1)])
def test_invalid_feature_values_are_not_eligible(tmp_path, group, field, value):
 item=envelope();item["boats"][0]["groups"][group][field]=value
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "FEATURE_VALUE_INVALID" in result.reasons


def test_stale_declared_fetch_time_is_rejected(tmp_path):
 item=envelope(datetime.now(timezone.utc)-timedelta(minutes=20))
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "CAPTURE_TIME_UNVERIFIED" in result.reasons


def test_source_prefix_requires_uri_boundary(tmp_path):
 cfg=CollectorConfig(store_root=tmp_path/"store",allowed_source_types=("LOCAL_APPROVED_SNAPSHOT",),parser_version="v",contract_version="v",allowed_source_location_prefixes=("https://approved.example/path",))
 item=envelope();item["sourceLocation"]="https://approved.example.attacker/path"
 result=FeatureCollector(cfg).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "SOURCE_LOCATION_NOT_APPROVED" in result.reasons


@pytest.mark.parametrize("value", [[], 1, "text", {"schemaVersion": 1, "boats": [None] * 6}])
def test_non_object_payloads_fail_closed_without_crashing(tmp_path, value):
 result=collector(tmp_path).capture(json.dumps(value).encode())
 assert result.status=="REJECTED"


def test_empty_lock_is_not_treated_as_stale(tmp_path):
 c=collector(tmp_path);lock=tmp_path/"store"/"collector.lock";lock.write_text("")
 with c.lock():assert lock.exists()


def test_negative_clock_drift_is_rejected_and_timestamp_flag_is_false(tmp_path):
 item=envelope();item["clockDriftSeconds"]=-0.1
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "CLOCK_DRIFT" in result.reasons
 row=FeatureStore(tmp_path/"store").existing(result.snapshot_id)
 assert row["capture_timestamp_verified"]==0


@pytest.mark.parametrize("mutation", [
 lambda x: x["boats"][0].update(boatNo="1"),
 lambda x: x["boats"][0].update(groups=[]),
 lambda x: x.update(raceDate="2026-02-30"),
 lambda x: x.update(jcd="99"),
 lambda x: x.update(raceDate="2000-01-01"),
])
def test_nested_schema_and_race_identity_fail_closed(tmp_path, mutation):
 item=envelope();mutation(item)
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED"


def test_invalid_uri_port_fails_closed(tmp_path):
 cfg=CollectorConfig(store_root=tmp_path/"store",allowed_source_types=("LOCAL_APPROVED_SNAPSHOT",),parser_version="v",contract_version="v",allowed_source_location_prefixes=("https://approved.example/path",))
 item=envelope();item["sourceLocation"]="https://approved.example:invalid/path"
 result=FeatureCollector(cfg).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "SOURCE_LOCATION_NOT_APPROVED" in result.reasons


@pytest.mark.parametrize("field,value", [
 ("fetchedAtUtc", "2026-07-21T12:00:00+09:00"),
 ("fetchedAtJst", "2026-07-21T03:00:00+00:00"),
 ("raceDeadlineJst", "2026-07-21T03:20:00+00:00"),
])
def test_timestamp_timezone_contract_is_strict(tmp_path, field, value):
 item=envelope();item[field]=value
 result=collector(tmp_path).capture(json.dumps(item).encode())
 assert result.status=="REJECTED" and "TIMESTAMP_INVALID" in result.reasons


def test_beforeinfo_v2_accepts_only_collected_feature_groups(tmp_path):
 now = datetime.now(timezone.utc)
 today = now.astimezone(JST).date().isoformat()
 item = {
  "schemaVersion": 2,
  "sourceType": "OFFICIAL_PUBLIC_BEFOREINFO",
  "sourceLocation": f"https://www.boatrace.jp/owpc/pc/race/beforeinfo?hd={today.replace('-', '')}&jcd=01&rno=1",
  "fetchedAtUtc": now.isoformat(),
  "fetchedAtJst": now.astimezone(JST).isoformat(),
  "raceDeadlineJst": (now.astimezone(JST) + timedelta(minutes=7)).isoformat(),
  "clockDriftSeconds": 0.0,
  "raceDate": today,
  "jcd": "01",
  "raceNo": 1,
  "boats": [{
   "boatNo": boat,
   "groups": {
    "course_and_start_exhibition": {
     "courseEntry": boat, "startExhibition": 0.10, "tilt": 0.0, "bodyWeight": 50.0,
    },
    "exhibition_time": {"exhibitionTime": 6.70},
    "weather_and_water": {
     "weather": "晴", "airTemp": 30.0, "waterTemp": 27.0,
     "windDirection": "北", "windSpeed": 2.0, "waveHeight": 2.0,
    },
   },
  } for boat in range(1, 7)],
 }
 cfg = CollectorConfig(
  store_root=tmp_path/"store",
  allowed_source_types=("OFFICIAL_PUBLIC_BEFOREINFO",),
  parser_version="beforeinfo-v1",
  contract_version="feature-forward-v2",
  allowed_source_location_prefixes=("https://www.boatrace.jp/owpc/pc/race/beforeinfo",),
 )
 result = FeatureCollector(cfg).capture(json.dumps(item, ensure_ascii=False).encode())
 assert result.status == "CAPTURED"
 groups = {
  row[0] for row in FeatureStore(tmp_path/"store").connection.execute(
   "SELECT DISTINCT feature_group FROM feature_records"
  )
 }
 assert groups == {
  "course_and_start_exhibition", "exhibition_time", "weather_and_water",
 }
