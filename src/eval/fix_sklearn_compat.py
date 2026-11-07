import json
import pickle
from pathlib import Path

import joblib
import sklearn


MODEL_DIR = Path("models")
OUT_JSON = Path("reports/sklearn_compat_report.json")


def try_load_model(path: Path) -> dict:
    ext = path.suffix.lower()
    try:
        if ext == ".joblib":
            obj = joblib.load(path)
        else:
            with open(path, "rb") as f:
                obj = pickle.load(f)
        return {"status": "ok", "type": type(obj).__name__}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    report = {"sklearn_version": sklearn.__version__, "models": {}}
    model_files = sorted(MODEL_DIR.glob("**/*.pkl")) + sorted(MODEL_DIR.glob("**/*.joblib"))

    for model_path in model_files:
        report["models"][str(model_path.as_posix())] = try_load_model(model_path)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
