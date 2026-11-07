from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parents[2]
INPUT_JSON = ROOT / "reports" / "t017_race_filter_multiday_validation.json"
INPUT_MD = ROOT / "reports" / "t017_race_filter_multiday_validation.md"
INPUT_DIAG_JSON = ROOT / "reports" / "t017_missing_snapshot_diagnostics.json"
INPUT_DIAG_MD = ROOT / "reports" / "t017_missing_snapshot_diagnostics.md"
CONFIG_PATH = ROOT / "config" / "race_filter_promotion_gate.json"
OUT_MD = ROOT / "reports" / "t018_race_filter_promotion_gate.md"
OUT_JSON = ROOT / "reports" / "t018_race_filter_promotion_gate.json"

FILTERS = [
    "no_filter",
    "concentration_filter",
    "first_gap_filter",
    "top_score_gap_filter",
    "and_filter",
]


def as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value, digits: int = 4) -> str:
    num = as_float(value)
    if num is None:
        return "n/a"
    return f"{num:.{digits}f}"


def load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing required input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def metric_or_none(dct: dict[str, object], key: str):
    return as_float(dct.get(key))


def build_reasons(
    *,
    date_count: int,
    total_races: int,
    total_bets: int,
    positive_day_rate: float | None,
    mean_roi_delta: float | None,
    median_roi_delta: float | None,
    worst_roi: float | None,
    mean_drawdown_ratio: float | None,
    cfg: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    if date_count < int(cfg["min_date_count"]):
        reasons.append(f"date_count_below_min({date_count}<{cfg['min_date_count']})")
    if total_races < int(cfg["min_total_races"]):
        reasons.append(f"total_races_below_min({total_races}<{cfg['min_total_races']})")
    if total_bets < int(cfg["min_total_bets"]):
        reasons.append(f"total_bets_below_min({total_bets}<{cfg['min_total_bets']})")
    if positive_day_rate is None or positive_day_rate < float(cfg["min_positive_day_rate"]):
        reasons.append(
            f"positive_day_rate_below_min({fmt(positive_day_rate)}<{fmt(cfg['min_positive_day_rate'])})"
        )
    if mean_roi_delta is None or mean_roi_delta < float(cfg["min_mean_roi_delta_vs_no_filter"]):
        reasons.append(
            f"mean_roi_delta_below_min({fmt(mean_roi_delta)}<{fmt(cfg['min_mean_roi_delta_vs_no_filter'])})"
        )
    if median_roi_delta is None or median_roi_delta < float(cfg["min_median_roi_delta_vs_no_filter"]):
        reasons.append(
            f"median_roi_delta_below_min({fmt(median_roi_delta)}<{fmt(cfg['min_median_roi_delta_vs_no_filter'])})"
        )
    if worst_roi is None or worst_roi < float(cfg["max_worst_roi"]):
        reasons.append(f"tail_risk_too_high({fmt(worst_roi)}<{fmt(cfg['max_worst_roi'])})")
    if mean_drawdown_ratio is None or mean_drawdown_ratio > float(cfg["max_mean_drawdown_ratio_vs_no_filter"]):
        reasons.append(
            f"mean_drawdown_ratio_above_max({fmt(mean_drawdown_ratio)}>{fmt(cfg['max_mean_drawdown_ratio_vs_no_filter'])})"
        )
    return reasons


def choose_status(
    *,
    filter_name: str,
    reasons: list[str],
    cfg: dict[str, object],
) -> tuple[str, str]:
    if filter_name == "no_filter":
        return "BASELINE", "PASS"
    if filter_name in set(cfg.get("reference_only_filters", [])):
        return "REFERENCE_ONLY", "FAIL"
    if reasons:
        if any(r.startswith("date_count_below_min") for r in reasons):
            return "NEEDS_MORE_DATA", "NEEDS_MORE_DATA"
        if any(r.startswith("positive_day_rate_below_min") for r in reasons):
            return "FAIL_STABILITY", "FAIL"
        if any(r.startswith("median_roi_delta_below_min") for r in reasons):
            return "FAIL_MEDIAN_EDGE", "FAIL"
        if any(r.startswith("mean_roi_delta_below_min") for r in reasons):
            return "FAIL_OUTLIER_DEPENDENT", "FAIL"
        if any(r.startswith("tail_risk_too_high") for r in reasons):
            return "FAIL_TAIL_RISK", "FAIL"
        return "FAIL", "FAIL"
    return "PASS", "PASS"


def build_rows(summary: dict[str, object], cfg: dict[str, object]) -> list[dict[str, object]]:
    validated_dates = summary.get("validated_dates", [])
    date_count = len(validated_dates)
    day_rows = summary.get("day_rows", [])
    total_races = int(sum(int(row.get("total_races", 0) or 0) for row in day_rows))

    agg = summary["filter_aggregation"]
    diff = summary["no_filter_diffs"]
    no_filter = agg["no_filter"]
    no_filter_median = as_float(no_filter.get("median_roi"))
    no_filter_mean_dd = as_float(no_filter.get("mean_max_drawdown"))

    rows: list[dict[str, object]] = []
    for filter_name in FILTERS:
        metrics = agg[filter_name]
        if filter_name == "no_filter":
            total_bets = int(sum(int(row.get("no_filter_bet_count", 0) or 0) for row in day_rows))
            reasons = []
            status, gate_result = choose_status(filter_name=filter_name, reasons=reasons, cfg=cfg)
            rows.append(
                {
                    "filter_name": filter_name,
                    "status": status,
                    "gate_result": gate_result,
                    "date_count": date_count,
                    "total_races": total_races,
                    "total_bets": total_bets,
                    "mean_roi": as_float(metrics.get("mean_roi")),
                    "median_roi": as_float(metrics.get("median_roi")),
                    "worst_roi": as_float(metrics.get("worst_roi")),
                    "best_roi": as_float(metrics.get("best_roi")),
                    "positive_day_rate": as_float(metrics.get("positive_day_rate")),
                    "mean_roi_delta_vs_no_filter": 0.0,
                    "median_roi_delta_vs_no_filter": 0.0,
                    "mean_drawdown_ratio_vs_no_filter": 1.0,
                    "fail_reasons": [],
                }
            )
            continue

        total_bets_key = f"{filter_name}_bet_count"
        total_bets = int(sum(int(row.get(total_bets_key, 0) or 0) for row in day_rows))
        reasons = build_reasons(
            date_count=date_count,
            total_races=total_races,
            total_bets=total_bets,
            positive_day_rate=as_float(metrics.get("positive_day_rate")),
            mean_roi_delta=as_float(diff[filter_name].get("mean_roi_diff")),
            median_roi_delta=(
                (as_float(metrics.get("median_roi")) - no_filter_median)
                if no_filter_median is not None and as_float(metrics.get("median_roi")) is not None
                else None
            ),
            worst_roi=as_float(metrics.get("worst_roi")),
            mean_drawdown_ratio=(
                (as_float(metrics.get("mean_max_drawdown")) / no_filter_mean_dd)
                if no_filter_mean_dd and as_float(metrics.get("mean_max_drawdown")) is not None
                else None
            ),
            cfg=cfg,
        )
        status, gate_result = choose_status(filter_name=filter_name, reasons=reasons, cfg=cfg)
        rows.append(
            {
                "filter_name": filter_name,
                "status": status,
                "gate_result": gate_result,
                "date_count": date_count,
                "total_races": total_races,
                "total_bets": total_bets,
                "mean_roi": as_float(metrics.get("mean_roi")),
                "median_roi": as_float(metrics.get("median_roi")),
                "worst_roi": as_float(metrics.get("worst_roi")),
                "best_roi": as_float(metrics.get("best_roi")),
                "positive_day_rate": as_float(metrics.get("positive_day_rate")),
                "mean_roi_delta_vs_no_filter": as_float(diff[filter_name].get("mean_roi_diff")),
                "median_roi_delta_vs_no_filter": (
                    round(as_float(metrics.get("median_roi")) - no_filter_median, 4)
                    if no_filter_median is not None and as_float(metrics.get("median_roi")) is not None
                    else None
                ),
                "mean_drawdown_ratio_vs_no_filter": (
                    round(as_float(metrics.get("mean_max_drawdown")) / no_filter_mean_dd, 4)
                    if no_filter_mean_dd and as_float(metrics.get("mean_max_drawdown")) is not None
                    else None
                ),
                "fail_reasons": reasons,
            }
        )
    return rows


def choose_primary_candidate(rows: list[dict[str, object]]) -> str:
    candidates = [row for row in rows if row["filter_name"] not in {"no_filter", "and_filter"}]
    passes = [row for row in candidates if row["gate_result"] == "PASS"]
    if not passes:
        return "needs_more_data"
    best = max(
        passes,
        key=lambda row: (as_float(row["mean_roi"]) or float("-inf"), -(as_float(row["worst_roi"]) or 0.0)),
    )
    return best["filter_name"]


def render_markdown(summary: dict[str, object], diagnostics: dict[str, object], rows: list[dict[str, object]], cfg: dict[str, object]) -> str:
    validated_dates = summary.get("validated_dates", [])
    skipped_dates = summary.get("skipped_dates", [])
    total_races = int(sum(int(row.get("total_races", 0) or 0) for row in summary.get("day_rows", [])))
    concentration = next(row for row in rows if row["filter_name"] == "concentration_filter")
    first_gap = next(row for row in rows if row["filter_name"] == "first_gap_filter")
    and_filter = next(row for row in rows if row["filter_name"] == "and_filter")
    no_filter = next(row for row in rows if row["filter_name"] == "no_filter")

    table_lines = []
    for row in rows:
        fail_reasons = "; ".join(row["fail_reasons"]) if row["fail_reasons"] else "-"
        table_lines.append(
            "| {filter_name} | {status} | {date_count} | {mean_roi} | {median_roi} | {worst_roi} | {positive_day_rate} | {mean_roi_delta} | {median_roi_delta} | {dd_ratio} | {reasons} |".format(
                filter_name=row["filter_name"],
                status=row["status"],
                date_count=row["date_count"],
                mean_roi=fmt(row["mean_roi"]),
                median_roi=fmt(row["median_roi"]),
                worst_roi=fmt(row["worst_roi"]),
                positive_day_rate=fmt(row["positive_day_rate"]),
                mean_roi_delta=fmt(row["mean_roi_delta_vs_no_filter"]),
                median_roi_delta=fmt(row["median_roi_delta_vs_no_filter"]),
                dd_ratio=fmt(row["mean_drawdown_ratio_vs_no_filter"]),
                reasons=fail_reasons,
            )
        )

    doc = f"""# TASK-018 Race Filter Promotion Gate

## 目的
`TASK-017B` の結果をもとに、race filter を production BUY rule に入れてよいか判断するための採用基準を定義する。平均 ROI だけで採用せず、median ROI、positive_day_rate、tail risk、drawdown ratio を同時に見る。

## 入力ファイル
- `{INPUT_JSON.relative_to(ROOT)}`
- `{INPUT_MD.relative_to(ROOT)}`
- `{INPUT_DIAG_JSON.relative_to(ROOT)}`
- `{INPUT_DIAG_MD.relative_to(ROOT)}`
- `{CONFIG_PATH.relative_to(ROOT)}`

## promotion gate の基準
| key | value |
|---|---:|
| min_date_count | {cfg["min_date_count"]} |
| min_total_races | {cfg["min_total_races"]} |
| min_total_bets | {cfg["min_total_bets"]} |
| min_positive_day_rate | {fmt(cfg["min_positive_day_rate"])} |
| min_median_roi_delta_vs_no_filter | {fmt(cfg["min_median_roi_delta_vs_no_filter"])} |
| min_mean_roi_delta_vs_no_filter | {fmt(cfg["min_mean_roi_delta_vs_no_filter"])} |
| max_worst_roi | {fmt(cfg["max_worst_roi"])} |
| max_mean_drawdown_ratio_vs_no_filter | {fmt(cfg["max_mean_drawdown_ratio_vs_no_filter"])} |
| allow_production_adoption | false |

## filter 別の判定表
| filter_name | status | date_count | mean_roi | median_roi | worst_roi | positive_day_rate | mean_roi_delta_vs_no_filter | median_roi_delta_vs_no_filter | mean_drawdown_ratio_vs_no_filter | fail_reasons |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(table_lines)}

## concentration_filter の評価
- mean ROI は no_filter を上回るが、median ROI はマイナス寄りで、positive_day_rate も {fmt(concentration["positive_day_rate"])} に留まる。
- date_count は {concentration["date_count"]} で、gate の最小日数 {cfg["min_date_count"]} に届かない。
- worst ROI は {fmt(concentration["worst_roi"])} で tail risk が残る。
- したがって、現段階では `NEEDS_MORE_DATA` であり、本番採用には進めない。

## first_gap_filter の評価
- concentration_filter より mean_max_drawdown は小さいが、mean ROI は no_filter を上回らない。
- median ROI も弱く、positive_day_rate も {fmt(first_gap["positive_day_rate"])} に留まる。
- よって、こちらも `NEEDS_MORE_DATA` で固定し、production には入れない。

## and_filter の扱い
- 複合条件でサンプル数がさらに減り、過学習リスクが高い。
- reference_only として比較対象には残すが、primary_candidate にはしない。

## 判断
- `primary_candidate_after`: `{summary["primary_candidate_after"]}`
- `production_adoption`: `false`
- `decision`: {summary["decision"]}
- `reason`: {summary["decision_reason"]}

## 注意点
- 平均 ROI だけでは採用しない。
- `q70` を固定ルールにしない。
- `20260311` の単日好成績は採用根拠にしない。
- 本レポートは shadow gate であり、BUY rule には未反映。

## 次にやるべきこと
1. 10日以上の validated snapshot を集める。
2. 同じ gate を日付横断で再実行する。
3. `positive_day_rate` と median ROI が安定しない限り production 化しない。
"""
    return doc


def main() -> None:
    summary = load_json(INPUT_JSON)
    diagnostics = load_json(INPUT_DIAG_JSON)
    cfg = load_json(CONFIG_PATH)

    rows = build_rows(summary, cfg)
    primary_candidate_after = choose_primary_candidate(rows)
    production_adoption = False
    if primary_candidate_after == "needs_more_data":
        decision = "shadow gate retained; more data required"
        decision_reason = "The current sample is below the promotion gate and stability metrics are not sufficient."
    else:
        decision = "shadow gate blocked"
        decision_reason = "No filter reached the promotion gate, and production adoption remains disabled."

    report = {
        "task": "TASK-018",
        "inputs": {
            "race_filter_multiday_validation": str(INPUT_JSON.relative_to(ROOT)),
            "missing_snapshot_diagnostics": str(INPUT_DIAG_JSON.relative_to(ROOT)),
            "config": str(CONFIG_PATH.relative_to(ROOT)),
        },
        "gate_config": cfg,
        "validated_dates": summary.get("validated_dates", []),
        "skipped_dates": summary.get("skipped_dates", []),
        "rows": rows,
        "primary_candidate_after": primary_candidate_after,
        "production_adoption": production_adoption,
        "decision": decision,
        "decision_reason": decision_reason,
        "diagnostics_summary": {
            "dates_checked": len(diagnostics.get("per_date", [])),
            "reconstructed_snapshots": diagnostics.get("materialized_snapshot_count", 0),
        },
    }

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_markdown(summary, diagnostics, rows, cfg), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[saved] {OUT_MD}")
    print(f"[saved] {OUT_JSON}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
