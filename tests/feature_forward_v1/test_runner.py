import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/run_feature_forward_collector_v1.py"
def test_runner_blocks_unapproved_source_without_store(tmp_path):
 approval=tmp_path/"approval.json";approval.write_text(json.dumps({"schemaVersion":1,"collectionEnabled":False,"allowedSourceTypes":[]}))
 inbox=tmp_path/"inbox";inbox.mkdir()
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(inbox),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json")],cwd=ROOT,text=True,capture_output=True)
 assert result.returncode==0
 payload=json.loads(result.stdout)
 assert payload["status"]=="FEATURE_COLLECTION_BLOCKED_SOURCE"
 assert payload["executionStatus"]=="BLOCKED" and payload["authorizationGateBlocks"]==1
 assert payload["networkRequests"]==0 and payload["processedFiles"]==0
 assert not (tmp_path/"store").exists()
def test_dry_run_never_persists(tmp_path):
 approval=tmp_path/"approval.json";approval.write_text(json.dumps({"schemaVersion":1,"collectionEnabled":True,"allowedSourceTypes":["LOCAL_APPROVED_SNAPSHOT"]}))
 inbox=tmp_path/"inbox";inbox.mkdir();(inbox/"one.json").write_text("{}")
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(inbox),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json"),"--dry-run"],cwd=ROOT,text=True,capture_output=True)
 assert result.returncode==0 and json.loads(result.stdout)["status"]=="DRY_RUN"
 assert not (tmp_path/"store").exists()


def test_enabled_source_requires_original_evidence(tmp_path):
 approval=tmp_path/"approval.json";approval.write_text(json.dumps({"schemaVersion":1,"collectionEnabled":True,"allowedSourceTypes":["LOCAL_APPROVED_SNAPSHOT"],"rightsStatus":"INTERNAL_RESEARCH_APPROVED"}))
 inbox=tmp_path/"inbox";inbox.mkdir()
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(inbox),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json")],cwd=ROOT,text=True,capture_output=True)
 assert json.loads(result.stdout)["status"]=="FEATURE_COLLECTION_BLOCKED_SOURCE"
 assert not (tmp_path/"store").exists()
