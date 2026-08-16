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


def test_personal_manual_collection_does_not_require_commercial_evidence(tmp_path):
 inbox=tmp_path/"inbox";inbox.mkdir()
 approval=tmp_path/"approval.json";approval.write_text(json.dumps({
  "schemaVersion":2,"usageMode":"PERSONAL_RESEARCH_ONLY",
  "personalResearchAllowed":True,"localStorageAllowed":True,"localAnalysisAllowed":True,
  "personalModelTrainingAllowed":True,"personalPredictionUseAllowed":True,
  "manualIngestAllowed":True,"automatedNetworkFetchAllowed":False,"networkSafetyIntegrated":False,
  "manualInboxPath":str(inbox),
  "commercialUseAllowed":False,"redistributionAllowed":False,
  "publicReleaseAllowed":False,"paidServiceAllowed":False,
  "allowedSourceTypes":["LOCAL_PERSONAL_SNAPSHOT"],
  "allowedSourceLocationPrefixes":["file:///personal-inbox"],"allowedHttpsHosts":[],
  "minimumRequestIntervalSeconds":60,"requestsPerRace":1,"requestsPerDay":24,"retriesPerRace":0}))
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(inbox),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json")],cwd=ROOT,text=True,capture_output=True)
 payload=json.loads(result.stdout)
 assert payload["status"]=="WAITING_FOR_APPROVED_INPUT"
 assert payload["personalResearchStatus"]=="ALLOWED_WITH_RESTRICTIONS"
 assert payload["automatedFetchStatus"]=="PERSONAL_COLLECTION_MANUAL_ONLY"
 assert payload["commercialUseAllowed"] is False


def test_personal_manual_collection_rejects_unconfigured_inbox(tmp_path):
 configured=tmp_path/"configured";configured.mkdir();other=tmp_path/"other";other.mkdir()
 approval=tmp_path/"approval.json";approval.write_text(json.dumps({
  "schemaVersion":2,"usageMode":"PERSONAL_RESEARCH_ONLY","personalResearchAllowed":True,
  "localStorageAllowed":True,"localAnalysisAllowed":True,"personalModelTrainingAllowed":True,
  "personalPredictionUseAllowed":True,"manualIngestAllowed":True,"manualInboxPath":str(configured),
  "automatedNetworkFetchAllowed":False,"networkSafetyIntegrated":False,"allowedHttpsHosts":[],
  "commercialUseAllowed":False,"redistributionAllowed":False,"publicReleaseAllowed":False,
  "paidServiceAllowed":False,"allowedSourceTypes":["LOCAL_PERSONAL_SNAPSHOT"],
  "allowedSourceLocationPrefixes":["file:///personal-inbox"],"minimumRequestIntervalSeconds":60,
  "requestsPerRace":1,"requestsPerDay":24,"retriesPerRace":0}))
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(other),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json")],cwd=ROOT,text=True,capture_output=True)
 assert json.loads(result.stdout)["status"]=="FEATURE_COLLECTION_BLOCKED_SOURCE"
 assert not (tmp_path/"store").exists()


def test_personal_manual_collection_accepts_repo_relative_inbox(tmp_path):
 inbox=tmp_path/"data"/"research"/"feature_forward_v1"/"inbox";inbox.mkdir(parents=True)
 approval=tmp_path/"config"/"feature_forward_v1"/"approval.json";approval.parent.mkdir(parents=True)
 approval.write_text(json.dumps({
  "schemaVersion":2,"usageMode":"PERSONAL_RESEARCH_ONLY","personalResearchAllowed":True,
  "localStorageAllowed":True,"localAnalysisAllowed":True,"personalModelTrainingAllowed":True,
  "personalPredictionUseAllowed":True,"manualIngestAllowed":True,"manualInboxPath":"data/research/feature_forward_v1/inbox",
  "automatedNetworkFetchAllowed":False,"networkSafetyIntegrated":False,"allowedHttpsHosts":[],
  "commercialUseAllowed":False,"redistributionAllowed":False,"publicReleaseAllowed":False,
  "paidServiceAllowed":False,"allowedSourceTypes":["LOCAL_PERSONAL_SNAPSHOT"],
  "allowedSourceLocationPrefixes":["file:///personal-inbox"],"minimumRequestIntervalSeconds":60,
  "requestsPerRace":1,"requestsPerDay":24,"retriesPerRace":0}),encoding="utf-8")
 result=subprocess.run([sys.executable,str(SCRIPT),"--approval",str(approval),"--inbox",str(inbox),"--store",str(tmp_path/"store"),"--status",str(tmp_path/"status.json")],cwd=ROOT,text=True,capture_output=True)
 assert json.loads(result.stdout)["status"]=="WAITING_FOR_APPROVED_INPUT"
