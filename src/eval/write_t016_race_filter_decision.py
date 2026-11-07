import argparse
import json
from pathlib import Path


INPUT_JSON = Path("reports/race_filter_comparison.json")
OUTPUT_MD = Path("reports/t016_race_filter_decision.md")
OUTPUT_JSON = Path("reports/t016_race_filter_decision.json")


def fmt_num(value, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_markdown(report: dict) -> str:
    methods = report.get("methods", {})
    diff_vs_no_filter = report.get("diff_vs_no_filter", {})
    thresholds = report.get("thresholds", {})
    sweep_best = report.get("threshold_sweep_best_by_roi", {})

    rows = []
    for name, metrics in methods.items():
        rows.append(
            "| {name} | {bought} | {result_rows} | {roi} | {dd} | {profit} | {hit_rate} | {missing} |".format(
                name=name,
                bought=metrics.get("bought_races", "n/a"),
                result_rows=metrics.get("result_available_rows", "n/a"),
                roi=fmt_num(metrics.get("roi")),
                dd=fmt_num(metrics.get("max_drawdown")),
                profit=fmt_num(metrics.get("profit")),
                hit_rate=fmt_num(metrics.get("hit_rate")),
                missing=metrics.get("missing_odds_rows", "n/a"),
            )
        )

    diff_rows = []
    for name, diff in diff_vs_no_filter.items():
        diff_rows.append(
            "| {name} | {roi} | {profit} | {dd} |".format(
                name=name,
                roi=fmt_num(diff.get("roi_diff")),
                profit=fmt_num(diff.get("profit_diff")),
                dd=fmt_num(diff.get("max_drawdown_diff")),
            )
        )

    best_by_roi = sweep_best.get("first_gap_filter", {})
    best_by_profit = report.get("threshold_sweep_best_by_profit", {}).get("first_gap_filter", {})
    best_by_score = report.get("threshold_sweep_best_by_score", {}).get("first_gap_filter", {})

    primary = "concentration_filter"
    secondary = "first_gap_filter"
    selected_reason = (
        "concentration_filter はこの単一スナップショットで ROI が最も高く、DD も最小だった。 "
        "first_gap_filter も no_filter 比で大きく改善しており、次点候補として残す価値がある。 "
        "top_score_gap_filter は改善するが上位2候補より弱い。 "
        "q70 付近は有望だが、単一スナップショットの閾値固定は避ける。"
    )

    md = f"""# TASK-016 Race Filter Decision

## 目的
`TASK-016` の race filter 比較結果を整理し、production BUY ルールにはまだ入れない前提で、shadow / candidate の判断材料を残す。

## 入力ファイル
- `reports/race_filter_comparison.json`
- `data/tmp/20260311_eval/today_win_proba.csv`
- `data/tmp/20260311_eval/today_features.csv`
- `reports/t016_backtest_race_results.csv`

## サマリ
- `total_races`: {report.get("methods", {}).get("no_filter", {}).get("result_available_rows", "n/a")}
- `candidate_mode_fixed`: {report.get("candidate_mode_fixed", "n/a")}
- `betting_mode_fixed`: {report.get("betting_mode_fixed", "n/a")}
- `thresholds`: first_gap_q60={fmt_num(thresholds.get("first_gap_q60"))}, top_score_gap_q60={fmt_num(thresholds.get("top_score_gap_q60"))}, concentration_top5_q60={fmt_num(thresholds.get("concentration_top5_q60"))}

## 比較表
| filter_name | bought_races | race_count | roi | max_drawdown | profit | hit_rate | missing_odds_rows |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

## no_filter との差分
| filter_name | roi_diff | profit_diff | max_drawdown_diff |
|---|---:|---:|---:|
{chr(10).join(diff_rows)}

## 最有力候補
- 第一候補: `{primary}`
- 第二候補: `{secondary}`
- 参考: `and_filter` は数値上さらに強いが、今回の decision は requested candidate の整理に留めるため shadow 注記のみとする。

## 採用判断
- 結論: `shadow_only_candidate` として扱う。
- `production_adoption`: `false`
- 理由: {selected_reason}

## 注意点
- 単一スナップショット `20260311` の結果であり、日付横断の妥当性は未検証。
- `q70` 近辺の閾値は有望でも、固定ルールとして production に入れる段階ではない。
- `concentration_filter` と `first_gap_filter` は candidate として残すが、本番 BUY ルールには反映しない。
- 現行日次 `today_*` の比較とは切り分ける。

## 次にやること
1. 同じ比較を複数日スナップショットで再実行する。
2. `concentration_filter` と `first_gap_filter` の安定性を確認する。
3. `q70` を固定化せず、日付横断での再現性を確認してから production 判断を行う。

## 参考
- best_by_roi: `first_gap_filter` at {best_by_roi.get("threshold_label", "n/a")}
- best_by_profit: `first_gap_filter` at {best_by_profit.get("threshold_label", "n/a")}
- best_by_score: `first_gap_filter` at {best_by_score.get("threshold_label", "n/a")}
"""
    return md


def build_json(report: dict) -> dict:
    methods = report.get("methods", {})
    risks = [
        "single snapshot evaluation",
        "possible overfitting",
        "threshold q70 not validated across dates",
    ]
    return {
        "task": "TASK-016",
        "status": "shadow_candidate_selected",
        "total_races": int(methods.get("no_filter", {}).get("result_available_rows", 0)),
        "primary_candidate": "concentration_filter",
        "secondary_candidate": "first_gap_filter",
        "production_adoption": False,
        "reason": (
            "concentration_filter had the strongest ROI and lowest max_drawdown among the requested candidates; "
            "first_gap_filter also improved ROI/DD materially and remains a useful backup shadow candidate."
        ),
        "risks": risks,
        "next_action": "run same comparison across multiple dates before production adoption",
        "input_files": [
            "reports/race_filter_comparison.json",
            "data/tmp/20260311_eval/today_win_proba.csv",
            "data/tmp/20260311_eval/today_features.csv",
            "reports/t016_backtest_race_results.csv",
        ],
        "thresholds": report.get("thresholds", {}),
        "methods": methods,
        "diff_vs_no_filter": report.get("diff_vs_no_filter", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Write TASK-016 race filter decision reports.")
    parser.add_argument("--input", default=str(INPUT_JSON))
    parser.add_argument("--output-md", default=str(OUTPUT_MD))
    parser.add_argument("--output-json", default=str(OUTPUT_JSON))
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    report = load_report(input_path)
    md_text = build_markdown(report)
    json_data = build_json(report)

    output_md = Path(args.output_md)
    output_json = Path(args.output_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(md_text, encoding="utf-8")
    output_json.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {output_md}")
    print(f"[saved] {output_json}")
    print(json.dumps(json_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
