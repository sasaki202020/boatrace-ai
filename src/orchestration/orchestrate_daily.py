import subprocess
import os
import datetime
import shutil
import sys
import pandas as pd

def run_py(script_path):
    print(f"--- Executing: {script_path} ---")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run([sys.executable, script_path], env=env, text=True)
    if result.returncode != 0:
        print(f"ERROR in {script_path}")
        return False
    return True

def main():
    print(f"=== Daily Orchestration Start ===")

    # 1. Ingest (Today)
    if not run_py("src/ingest/build_processed.py"): sys.exit(1)
    
    # 処理結果から日付を特定する
    report_date = datetime.datetime.now().strftime("%Y%m%d")
    if os.path.exists("data/processed/today_races.csv"):
        df_tmp = pd.read_csv("data/processed/today_races.csv")
        if "date" in df_tmp.columns and not df_tmp.empty:
            report_date = str(df_tmp["date"].iloc[0])
            
    print(f"Target Date: {report_date}")

    # 2. Predict
    if not run_py("src/models/predict_win_proba.py"): sys.exit(1)

    # 3. Odds
    if not run_py("src/odds/fetch_today_odds3t.py"): sys.exit(1)

    # 4. Strategy
    if not run_py("src/strategy/generate_trifecta_candidates.py"): sys.exit(1)
    if not run_py("src/strategy/evaluate_ev_and_skip.py"): sys.exit(1)

    # 5. Report
    if not run_py("src/report/build_daily_report.py"): sys.exit(1)

    # 6. Archive
    archive_dir = f"archive/{report_date[:4]}/{report_date[4:6]}/{report_date}"
    os.makedirs(archive_dir, exist_ok=True)
    
    # 保存対象
    targets = [
        ("data/model_outputs/today_win_proba.csv", f"today_win_proba.csv"),
        ("reports/daily_report.md", f"{report_date}_daily_report.md"),
        ("data/strategy_outputs/skip_decisions.csv", "strategy_decisions.csv")
    ]
    
    for src, dst in targets:
        if os.path.exists(src):
            shutil.copy(src, os.path.join(archive_dir, dst))
            print(f"Archived: {dst}")

    print(f"=== All Daily Steps Completed Successfully ===")

if __name__ == "__main__":
    main()
