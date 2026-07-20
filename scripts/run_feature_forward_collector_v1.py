from __future__ import annotations
import argparse,hashlib,json,re,sys,uuid
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.feature_forward_v1.collector import CollectorConfig,FeatureCollector
from src.feature_forward_v1.quality import build_quality_report
HEX=re.compile(r"^[0-9a-f]{64}$")
def write_json(path,payload):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(path)
def append_log(path,payload):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open("a",encoding="utf-8") as handle:handle.write(json.dumps(payload,ensure_ascii=False,separators=(",",":"))+"\n")
def rights_valid(approval,path):
 evidence=approval.get("evidencePath");digest=approval.get("evidenceSha256");original=(path.parent/evidence).resolve() if isinstance(evidence,str) else None
 return approval.get("rightsStatus")=="INTERNAL_RESEARCH_APPROVED" and approval.get("writtenConfirmation") is True and isinstance(digest,str) and bool(HEX.fullmatch(digest)) and original is not None and original.suffix.lower() in {".eml",".pdf"} and original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest()==digest and approval.get("automatedCollectionAllowed") is True and approval.get("numericStorageAllowed") is True and approval.get("rawStorageAllowed") is True and type(approval.get("requestsPerRace")) is int and approval["requestsPerRace"]>0 and type(approval.get("requestsPerDay")) is int and approval["requestsPerDay"]>0 and approval.get("retriesPerRace")==0 and isinstance(approval.get("allowedSourceLocationPrefixes"),list) and bool(approval["allowedSourceLocationPrefixes"])
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("--approval",type=Path,required=True);p.add_argument("--inbox",type=Path,required=True);p.add_argument("--store",type=Path,required=True);p.add_argument("--status",type=Path,required=True);p.add_argument("--log",type=Path);p.add_argument("--dry-run",action="store_true");p.add_argument("--max-files",type=int,default=24);p.add_argument("--max-scan",type=int,default=240);args=p.parse_args(argv)
 approval=json.loads(args.approval.read_text(encoding="utf-8")) if args.approval.exists() else {};base={"runAtUtc":datetime.now(timezone.utc).isoformat(),"networkRequests":0,"productionWrites":0,"prospectiveWrites":0,"featureStoreWrites":0,"processedFiles":0,"capturedFiles":0,"rejectedFiles":0,"externalTimestampVerified":False,"commercialUseAllowed":False}
 basic=approval.get("schemaVersion")==1 and approval.get("collectionEnabled") is True and bool(approval.get("allowedSourceTypes"))
 files=sorted(args.inbox.glob("*.json"),key=lambda x:x.name,reverse=True)[:args.max_scan] if args.inbox.exists() else []
 if args.dry_run and basic:
  payload={**base,"status":"DRY_RUN","discoveredFiles":len(files)};print(json.dumps(payload));return 0
 if not basic or not rights_valid(approval,args.approval):
  payload={**base,"executionStatus":"BLOCKED","authorizationGateBlocks":1,"runtimeAttempts":0,"runtimeFailures":0,"status":"FEATURE_COLLECTION_BLOCKED_SOURCE","reason":"source_approval_missing_invalid_or_disabled"};write_json(args.status,payload);append_log(args.log or args.status.with_name("operations_log.jsonl"),payload);print(json.dumps(payload));return 0
 collector=FeatureCollector(CollectorConfig(args.store,tuple(approval["allowedSourceTypes"]),"feature-forward-parser-v1","feature-forward-v1",allowed_source_location_prefixes=tuple(approval["allowedSourceLocationPrefixes"])))
 if collector.store.verify_integrity().get("valid") is not True:
  payload={**base,"status":"BLOCKED_INTEGRITY","reason":"pre_run_integrity_failed"};write_json(args.status,payload);append_log(args.log or args.status.with_name("operations_log.jsonl"),payload);print(json.dumps(payload));return 2
 results=[];attempted=0
 with collector.lock():
  for path in files:
   result=collector.capture(path.read_bytes());results.append(result)
   processed=path.with_name(path.name+".processed."+result.raw_payload_sha256)
   if processed.exists():processed=path.with_name(processed.name+"."+uuid.uuid4().hex)
   path.replace(processed)
   if result.status!="ALREADY_CAPTURED":attempted+=1
   if attempted>=args.max_files:break
 quality=build_quality_report(args.store);valid=quality["integrity"].get("valid") is True
 captured=sum(r.status=="CAPTURED" for r in results);stored=sum(r.status!="ALREADY_CAPTURED" and collector.store.existing(r.snapshot_id) is not None for r in results);payload={**base,"status":"BLOCKED_INTEGRITY" if not valid else "WAITING_FOR_APPROVED_INPUT" if not files else "COLLECTION_COMPLETED","processedFiles":len(results),"capturedFiles":captured,"featureStoreWrites":stored,"rejectedFiles":sum(r.status in {"REJECTED","QUARANTINED_SCHEMA_DRIFT"} for r in results),"quality":quality}
 write_json(args.status,payload);append_log(args.log or args.status.with_name("operations_log.jsonl"),payload);print(json.dumps(payload));return 0 if valid else 2
if __name__=="__main__":raise SystemExit(main())
