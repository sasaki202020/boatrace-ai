import subprocess
import os
import sys

def run_step(name, script_path):
    print(f"--- Running Step: {name} ---")
    try:
        # PYTHONPATH を現在のディレクトリに設定
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()

        result = subprocess.run(
            [sys.executable, script_path],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FAILED: {name}")
        print(e.stdout)
        print(e.stderr)
        return False

def main():
    steps = [
        ("Gate 2.5: Alias Audit", "src/ingest/inspect_raw_columns.py"),
        ("Gate 2: Ingestion", "src/ingest/build_processed.py"),
        ("Gate 3: Feature Engineering", "src/features/build_features.py"),
        ("Gate 4: Training", "src/models/train_win_model.py"),
        ("Gate 4: Prediction", "src/models/predict_win_proba.py"),
        ("Gate 5: Trifecta Generation", "src/strategy/generate_trifecta_candidates.py"),
        ("Gate 5: EV Evaluation", "src/strategy/evaluate_ev_and_skip.py"),
        ("Gate 6: Reporting", "src/report/build_daily_report.py")
    ]

    for name, cmd in steps:
        if not run_step(name, cmd):
            print("Pipeline aborted due to errors.")
            break
    else:
        print("Pipeline COMPLETED successfully.")

if __name__ == "__main__":
    main()
