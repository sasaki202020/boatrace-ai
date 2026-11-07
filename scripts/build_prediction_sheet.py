from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.prediction_sheet import build_prediction_sheet


def _write_completion_report(result: dict[str, object]) -> tuple[Path, Path]:
    report_root = ROOT / "reports" / "repo_audit"
    report_root.mkdir(parents=True, exist_ok=True)
    md_path = report_root / "prediction_web_completion.md"
    json_path = report_root / "prediction_web_completion.json"
    summary = result.get("summary") or {}
    files = result.get("files") or {}
    lines = [
        "# Prediction Web Completion",
        "",
        f"- 対象日: {result.get('requestedDate') or ''}",
        f"- sourceDate: {result.get('sourceDate') or ''}",
        f"- BUY件数: {summary.get('buyCount', 0)}",
        f"- WATCH件数: {summary.get('watchCount', 0)}",
        f"- PAPER件数: {summary.get('paperCount', 0)}",
        f"- SKIP件数: {summary.get('skipCount', 0)}",
        f"- TOP候補: {files.get('top_watch_md', '')}",
        "",
        "## 生成ファイル",
    ]
    for key, value in files.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Web API URL",
            "- `/api/prediction-sheet?date=YYYY-MM-DD`",
            "- `/api/prediction-sheet/latest`",
            "",
            "## Web画面URL",
            "- `/predictions`",
            "",
            "## 注意",
            "- 紙上予想です。実賭けは禁止です。",
            "- BUY閾値変更なし",
            "- EV計算変更なし",
            "- 予想ロジック変更なし",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "status": result.get("status"),
        "requestedDate": result.get("requestedDate"),
        "sourceDate": result.get("sourceDate"),
        "summary": summary,
        "files": files,
        "webApiUrl": "/api/prediction-sheet?date=YYYY-MM-DD",
        "webLatestApiUrl": "/api/prediction-sheet/latest",
        "webPageUrl": "/predictions",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build paper prediction sheet.")
    parser.add_argument("--date", help="YYYY-MM-DD")
    args = parser.parse_args()
    result = build_prediction_sheet(args.date)
    md_path, json_path = _write_completion_report(result)
    result["completionReport"] = {"md": str(md_path), "json": str(json_path)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
