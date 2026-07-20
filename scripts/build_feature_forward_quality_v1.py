from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.feature_forward_v1.quality import build_quality_report
def main():
 p=argparse.ArgumentParser();p.add_argument("--store",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();payload=build_quality_report(a.store);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2),encoding="utf-8");print(json.dumps(payload))
if __name__=="__main__":main()
