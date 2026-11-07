from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_WEEKLY_DIR = Path("reports/weekly")
DEFAULT_MONITOR_DIR = Path("reports/monitoring")
DEFAULT_ANALYSIS_DIR = Path("reports/analysis")
DEFAULT_OUTPUT_DIR = Path("reports/recommendations")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Suggest the next single change based on weekly/monitoring/analysis reports"
    )
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--weekly-dir", type=Path, default=DEFAULT_WEEKLY_DIR)
    parser.add_argument("--monitor-dir", type=Path, default=DEFAULT_MONITOR_DIR)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def safe_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0] * len(df), dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def load_weekly_json(weekly_dir: Path, start_date: str, end_date: str) -> dict[str, Any]:
    exact_path = weekly_dir / f"weekly_report_{start_date}_{end_date}.json"
    if exact_path.exists():
        return json.loads(exact_path.read_text(encoding="utf-8"))

    candidates = sorted(weekly_dir.glob("weekly_report_*.json"))
    if candidates:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))

    return {
        "kpi": {
            "period_days": 0,
            "days_with_buy": 0,
            "weekly_buy_count": 0,
            "weekly_hit_count": 0,
            "weekly_hit_rate": 0.0,
            "weekly_roi": 0.0,
            "weekly_profit": 0,
            "avg_buy_per_day": 0.0,
            "avg_real_odds_available": 0.0,
            "avg_pending_unpublished": 0.0,
            "best_day": None,
            "worst_day": None,
            "top_improvement_report_item": None,
            "recommendation": "weekly report not found; fallback to monitoring-only mode",
        }
    }


def load_monitoring_csv(monitor_dir: Path) -> pd.DataFrame:
    path = monitor_dir / "daily_monitoring_summary.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = df["date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    df = df[df["date"] != "TOTAL"].copy()
    return df.sort_values("date").reset_index(drop=True)


def load_recent_analysis_results(analysis_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(analysis_dir.glob("compare_*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        change = obj.get("change", {})
        before_summary = obj.get("before_summary", {})
        after_summary = obj.get("after_summary", {})
        effect = obj.get("effect_judgement", "")

        rows.append(
            {
                "file": str(path),
                "change_date": str(change.get("change_date", "")),
                "change_key": str(change.get("change_key", "")),
                "before_buy_count": before_summary.get("buy_count", 0),
                "after_buy_count": after_summary.get("buy_count", 0),
                "before_hit_rate": before_summary.get("hit_rate", 0),
                "after_hit_rate": after_summary.get("hit_rate", 0),
                "before_roi": before_summary.get("roi", 0),
                "after_roi": after_summary.get("roi", 0),
                "before_profit": before_summary.get("total_profit", 0),
                "after_profit": after_summary.get("total_profit", 0),
                "effect_judgement": effect,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "file",
                "change_date",
                "change_key",
                "before_buy_count",
                "after_buy_count",
                "before_hit_rate",
                "after_hit_rate",
                "before_roi",
                "after_roi",
                "before_profit",
                "after_profit",
                "effect_judgement",
            ]
        )

    df = pd.DataFrame(rows)
    df["change_date_norm"] = df["change_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    return df.sort_values(["change_date_norm", "change_key"]).reset_index(drop=True)


def filter_monitoring_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()
    return out.sort_values("date").reset_index(drop=True)


def summarize_monitoring(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "days": 0,
            "avg_real_odds_available": 0.0,
            "avg_pending_unpublished": 0.0,
            "avg_buy_count": 0.0,
            "avg_hit_rate": 0.0,
            "avg_odds": 0.0,
            "top_improvement_item": None,
        }

    top_item = None
    if "improvement_report_top1" in df.columns:
        vc = df["improvement_report_top1"].dropna().astype(str).value_counts()
        if not vc.empty:
            top_item = str(vc.index[0])

    return {
        "days": int(len(df)),
        "avg_real_odds_available": round(float(safe_num(df, "real_odds_available").mean()), 4),
        "avg_pending_unpublished": round(float(safe_num(df, "pending_unpublished").mean()), 4),
        "avg_buy_count": round(float(safe_num(df, "buy_count").mean()), 4),
        "avg_hit_rate": round(float(safe_num(df, "buy_hit_rate").mean()), 4),
        "avg_odds": round(float(safe_num(df, "avg_odds").mean()), 4),
        "top_improvement_item": top_item,
    }


def score_candidate(name: str, weekly_kpi: dict[str, Any], mon_summary: dict[str, Any]) -> tuple[float, str]:
    weekly_buy_count = float(weekly_kpi.get("weekly_buy_count", 0))
    weekly_hit_rate = float(weekly_kpi.get("weekly_hit_rate", 0))
    weekly_roi = float(weekly_kpi.get("weekly_roi", 0))
    avg_real_odds_available = float(mon_summary.get("avg_real_odds_available", 0))
    avg_pending_unpublished = float(mon_summary.get("avg_pending_unpublished", 0))
    top_item = mon_summary.get("top_improvement_item")

    score = 0.0
    reason_parts: list[str] = []

    if name == "odds_refresh_timing":
        if avg_real_odds_available <= 0:
            score += 9
            reason_parts.append("real_odds_available が未整備")
        if avg_pending_unpublished > 10:
            score += 8
            reason_parts.append("pending_unpublished が多い")
        if weekly_buy_count == 0:
            score += 4
            reason_parts.append("BUYが出ていない")
        return score, " / ".join(reason_parts) or "取得運用の改善余地"

    if name == "approx_prob":
        if weekly_hit_rate < 0.15:
            score += 8
            reason_parts.append("hit_rate が低い")
        if top_item and "approx" in str(top_item).lower():
            score += 6
            reason_parts.append("improvement_report で approx_prob 系が多い")
        if weekly_roi < 1.0:
            score += 3
            reason_parts.append("ROI も弱い")
        return score, " / ".join(reason_parts) or "確率推定の改善余地"

    if name == "2nd_3rd_rank_model":
        if weekly_hit_rate < 0.15:
            score += 7
            reason_parts.append("勝率が弱い")
        if top_item and any(k in str(top_item).lower() for k in ["rank", "2nd", "3rd", "second", "third"]):
            score += 7
            reason_parts.append("順位系改善の示唆あり")
        return score, " / ".join(reason_parts) or "2着3着順位精度の改善余地"

    if name == "max_buy_count":
        if weekly_buy_count < 5 and weekly_hit_rate >= 0.15:
            score += 6
            reason_parts.append("母数が少ないわりに勝率は壊れていない")
        if weekly_buy_count == 0:
            score += 2
            reason_parts.append("BUY不足")
        if weekly_roi >= 1.0:
            score += 1
            reason_parts.append("広げても耐えそう")
        return score, " / ".join(reason_parts) or "BUY件数拡張余地"

    if name == "buy_min_ev":
        if weekly_buy_count >= 5 and weekly_roi < 1.0:
            score += 6
            reason_parts.append("BUY母数はあるが ROI が弱い")
        if weekly_hit_rate < 0.15:
            score += 2
            reason_parts.append("勝率もやや弱い")
        return score, " / ".join(reason_parts) or "EV閾値見直し余地"

    if name == "buy_min_approx_prob":
        if weekly_buy_count >= 5 and weekly_hit_rate < 0.15:
            score += 5
            reason_parts.append("BUYはあるが勝率が弱い")
        if top_item and "approx" in str(top_item).lower():
            score += 2
            reason_parts.append("approx系の改善示唆")
        return score, " / ".join(reason_parts) or "確率閾値調整余地"

    return 0.0, ""


def build_candidates(weekly_kpi: dict[str, Any], mon_summary: dict[str, Any]) -> pd.DataFrame:
    candidates = [
        "odds_refresh_timing",
        "approx_prob",
        "2nd_3rd_rank_model",
        "max_buy_count",
        "buy_min_ev",
        "buy_min_approx_prob",
    ]

    rows: list[dict[str, Any]] = []
    for name in candidates:
        score, reason = score_candidate(name, weekly_kpi, mon_summary)
        rows.append({"candidate": name, "priority_score": round(score, 4), "reason": reason})

    df = pd.DataFrame(rows)
    df = df.sort_values(["priority_score", "candidate"], ascending=[False, True]).reset_index(drop=True)
    return df


def build_action_plan(top_candidate: str) -> dict[str, Any]:
    plans = {
        "odds_refresh_timing": {
            "what_to_change": "refresh 時間帯の固定運用を見直す",
            "how_to_change": "morning / late / final の採用phaseを直近3〜7日比較で再選定",
            "hold_days": 3,
            "success_metric": "real_odds_available 増加 / pending_unpublished 減少 / BUY件数増加",
        },
        "approx_prob": {
            "what_to_change": "approx_prob 計算ロジックの改善",
            "how_to_change": "特徴量・校正・候補順位との整合性を点検し、1点だけ修正",
            "hold_days": 3,
            "success_metric": "hit_rate 上昇 / ROI 改善 / improvement_report上位の変化",
        },
        "2nd_3rd_rank_model": {
            "what_to_change": "2着3着順位推定の改善",
            "how_to_change": "順位特徴量または順位ロジックを1点だけ変更",
            "hold_days": 3,
            "success_metric": "hit_rate 上昇 / top系順位精度改善",
        },
        "max_buy_count": {
            "what_to_change": "1日の最大BUY件数の緩和または縮小",
            "how_to_change": "例: 2→3 か 3→2 のように1段だけ動かす",
            "hold_days": 3,
            "success_metric": "BUY件数 / hit_rate / ROI の同時確認",
        },
        "buy_min_ev": {
            "what_to_change": "EV閾値の微調整",
            "how_to_change": "例: 0.10→0.15 のように1段だけ上げ下げ",
            "hold_days": 3,
            "success_metric": "ROI 改善 / 利益改善 / BUY件数の落ちすぎ回避",
        },
        "buy_min_approx_prob": {
            "what_to_change": "確率閾値の微調整",
            "how_to_change": "small step で 1回だけ変更",
            "hold_days": 3,
            "success_metric": "hit_rate 改善 / BUY件数の過度減少なし",
        },
    }
    return plans.get(
        top_candidate,
        {
            "what_to_change": "未定",
            "how_to_change": "1変更だけ実施",
            "hold_days": 3,
            "success_metric": "BUY件数 / hit_rate / ROI",
        },
    )


def build_final_recommendation(candidates_df: pd.DataFrame, recent_analysis_df: pd.DataFrame) -> dict[str, Any]:
    if candidates_df.empty:
        return {"top_candidate": None, "message": "候補なし", "action_plan": {}}

    top = candidates_df.iloc[0]
    top_candidate = str(top["candidate"])
    action_plan = build_action_plan(top_candidate)

    recent_same = recent_analysis_df[recent_analysis_df["change_key"] == top_candidate].copy()
    caution = None
    if not recent_same.empty:
        latest = recent_same.sort_values("change_date_norm").iloc[-1]
        effect = str(latest.get("effect_judgement", ""))
        if "悪化" in effect:
            caution = f"{top_candidate} は直近比較で悪化判定あり。再実施は慎重に。"
        elif "有望" in effect:
            caution = f"{top_candidate} は直近比較で有望判定あり。継続観測寄り。"

    message = f"次の1変更候補は {top_candidate}。理由: {top['reason']}"
    if caution:
        message += f" / 注意: {caution}"

    return {
        "top_candidate": top_candidate,
        "message": message,
        "action_plan": action_plan,
        "caution": caution,
    }


def override_weekly_kpi_from_monitoring(
    weekly_kpi: dict[str, Any], mon_summary: dict[str, Any]
) -> dict[str, Any]:
    out = dict(weekly_kpi)
    if float(out.get("weekly_buy_count", 0)) <= 0 and float(mon_summary.get("avg_buy_count", 0)) > 0:
        out["weekly_buy_count"] = int(round(float(mon_summary.get("avg_buy_count", 0))))
    if float(out.get("weekly_hit_rate", 0)) <= 0 and float(mon_summary.get("avg_hit_rate", 0)) > 0:
        out["weekly_hit_rate"] = float(mon_summary.get("avg_hit_rate", 0))
    if float(out.get("avg_real_odds_available", 0)) <= 0:
        out["avg_real_odds_available"] = float(mon_summary.get("avg_real_odds_available", 0))
    if float(out.get("avg_pending_unpublished", 0)) <= 0:
        out["avg_pending_unpublished"] = float(mon_summary.get("avg_pending_unpublished", 0))
    return out


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows_"
    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = ["" if pd.isna(row[c]) else str(row[c]) for c in cols]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def write_markdown_report(
    weekly_kpi: dict[str, Any],
    mon_summary: dict[str, Any],
    candidates_df: pd.DataFrame,
    recommendation: dict[str, Any],
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Next Change Recommendation")
    lines.append("")
    lines.append("## Weekly KPI Snapshot")
    lines.append("")
    for k, v in weekly_kpi.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Monitoring Snapshot")
    lines.append("")
    for k, v in mon_summary.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Ranked Candidates")
    lines.append("")
    lines.append(df_to_markdown(candidates_df))
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(f"- top_candidate: {recommendation.get('top_candidate')}")
    lines.append(f"- message: {recommendation.get('message')}")
    if recommendation.get("caution"):
        lines.append(f"- caution: {recommendation.get('caution')}")
    lines.append("")
    lines.append("## Action Plan")
    lines.append("")
    action_plan = recommendation.get("action_plan", {})
    for k, v in action_plan.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    start_date = normalize_date_str(args.start_date)
    end_date = normalize_date_str(args.end_date)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    weekly_obj = load_weekly_json(args.weekly_dir, start_date, end_date)
    monitoring_df = load_monitoring_csv(args.monitor_dir)
    monitoring_range_df = filter_monitoring_range(monitoring_df, start_date, end_date)
    mon_summary = summarize_monitoring(monitoring_range_df)

    weekly_kpi = weekly_obj.get("kpi", {})
    weekly_kpi = override_weekly_kpi_from_monitoring(weekly_kpi, mon_summary)

    recent_analysis_df = load_recent_analysis_results(args.analysis_dir)

    candidates_df = build_candidates(weekly_kpi, mon_summary)
    recommendation = build_final_recommendation(candidates_df, recent_analysis_df)

    base_name = f"next_change_{start_date}_{end_date}"
    csv_path = args.output_dir / f"{base_name}.csv"
    md_path = args.output_dir / f"{base_name}.md"
    json_path = args.output_dir / f"{base_name}.json"

    candidates_df.to_csv(csv_path, index=False, encoding="utf-8")

    payload = {
        "weekly_kpi": weekly_kpi,
        "monitoring_summary": mon_summary,
        "recommendation": recommendation,
        "ranked_candidates": candidates_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(weekly_kpi, mon_summary, candidates_df, recommendation, md_path)

    print("\n=== Next Change Recommendation ===")
    print(candidates_df.to_string(index=False))
    print("\n=== Recommendation ===")
    print(recommendation["message"])
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
