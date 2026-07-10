from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


DEFAULT_SKIP_DECISIONS = Path("data/strategy_outputs/skip_decisions.csv")
DEFAULT_DAILY_REPORTS_DIR = Path("reports/daily")
DEFAULT_OUTPUT_DIR = Path("reports/monitoring")
DEFAULT_CHANGE_LOG = Path("reports/monitoring/change_log.csv")


@dataclass
class MonitorConfig:
    skip_decisions_path: Path
    daily_reports_dir: Path
    output_dir: Path
    change_log_path: Path
    lookback_days: int = 7
    min_hold_days: int = 2
    improvement_rank_target: int = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily monitoring and improvement loop report generator."
    )
    parser.add_argument(
        "--skip-decisions",
        type=Path,
        default=DEFAULT_SKIP_DECISIONS,
        help="Path to skip_decisions.csv",
    )
    parser.add_argument(
        "--daily-reports-dir",
        type=Path,
        default=DEFAULT_DAILY_REPORTS_DIR,
        help="Directory like reports/daily/YYYY-MM-DD/",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for monitoring reports",
    )
    parser.add_argument(
        "--change-log",
        type=Path,
        default=DEFAULT_CHANGE_LOG,
        help="CSV log of parameter changes",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Number of recent days to include",
    )
    parser.add_argument(
        "--min-hold-days",
        type=int,
        default=2,
        help="Minimum days to hold before next change",
    )
    return parser.parse_args()


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_skip_decisions(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"skip_decisions not found: {path}")

    df = pd.read_csv(path)
    if "date" not in df.columns:
        raise ValueError("skip_decisions.csv must contain a 'date' column")
    if "decision" not in df.columns:
        raise ValueError("skip_decisions.csv must contain a 'decision' column")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).copy()

    if "hit" in df.columns:
        df["hit"] = pd.to_numeric(df["hit"], errors="coerce").fillna(0).astype(int)
    else:
        df["hit"] = 0

    if "odds" in df.columns:
        df["odds"] = pd.to_numeric(df["odds"], errors="coerce")
    else:
        df["odds"] = pd.NA

    return df


def latest_n_dates(df: pd.DataFrame, n: int) -> list[pd.Timestamp]:
    unique_dates = sorted(df["date"].dropna().unique())
    return list(unique_dates[-n:])


def safe_mean(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    value = series.dropna().mean()
    return float(value) if pd.notna(value) else 0.0


def build_daily_decision_metrics(
    df: pd.DataFrame, target_dates: Iterable[pd.Timestamp]
) -> pd.DataFrame:
    rows: list[dict] = []

    for date in target_dates:
        day_df = df[df["date"] == date].copy()

        buy_df = day_df[day_df["decision"] == "BUY"].copy()
        pending_df = day_df[day_df["decision"] == "PENDING"].copy()
        skip_df = day_df[day_df["decision"] == "SKIP"].copy()

        buy_count = int(len(buy_df))
        buy_hit_count = int(buy_df["hit"].sum()) if not buy_df.empty else 0
        buy_hit_rate = (buy_hit_count / buy_count) if buy_count > 0 else 0.0
        avg_odds = safe_mean(buy_df["odds"]) if buy_count > 0 else 0.0

        rows.append(
            {
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "total_count": int(len(day_df)),
                "buy_count": buy_count,
                "buy_hit_count": buy_hit_count,
                "buy_hit_rate": round(buy_hit_rate, 4),
                "avg_odds": round(avg_odds, 4),
                "pending_count": int(len(pending_df)),
                "skip_count": int(len(skip_df)),
            }
        )

    return pd.DataFrame(rows)


def _read_csv_if_exists(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def find_metric_from_daily_report(day_dir: Path, metric_name: str) -> Optional[float]:
    candidates = list(day_dir.glob("*.csv")) + list(day_dir.glob("*.json"))

    for file_path in candidates:
        if file_path.suffix.lower() == ".csv":
            df = _read_csv_if_exists(file_path)
            if df is None or df.empty:
                continue

            if metric_name in df.columns:
                value = pd.to_numeric(df[metric_name], errors="coerce").dropna()
                if not value.empty:
                    return float(value.iloc[0])

            lowered = {str(c).strip().lower(): c for c in df.columns}
            if "key" in lowered and "value" in lowered:
                key_col = lowered["key"]
                value_col = lowered["value"]
                matched = df[df[key_col].astype(str) == metric_name]
                if not matched.empty:
                    values = pd.to_numeric(matched[value_col], errors="coerce").dropna()
                    if not values.empty:
                        return float(values.iloc[0])

        if file_path.suffix.lower() == ".json":
            try:
                obj = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if isinstance(obj, dict) and metric_name in obj:
                try:
                    return float(obj[metric_name])
                except Exception:
                    pass

    return None


def find_improvement_report_top1(day_dir: Path) -> Optional[str]:
    csv_candidates = sorted(day_dir.glob("*improvement*report*.csv"))
    json_candidates = sorted(day_dir.glob("*improvement*report*.json"))

    for file_path in csv_candidates:
        df = _read_csv_if_exists(file_path)
        if df is None or df.empty:
            continue

        rank_col = next(
            (c for c in df.columns if str(c).strip().lower() in {"rank", "順位"}),
            None,
        )
        item_col = next(
            (
                c
                for c in df.columns
                if str(c).strip().lower() in {"item", "metric", "name", "改善項目"}
            ),
            None,
        )

        if rank_col and item_col:
            tmp = df.copy()
            tmp[rank_col] = pd.to_numeric(tmp[rank_col], errors="coerce")
            top1 = tmp[tmp[rank_col] == 1]
            if not top1.empty:
                return str(top1.iloc[0][item_col])

        if item_col:
            return str(df.iloc[0][item_col])

    for file_path in json_candidates:
        try:
            obj = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(obj, dict):
            for key in ("top1", "rank1", "improvement_report_top1"):
                if key in obj:
                    return str(obj[key])

        if isinstance(obj, list) and obj:
            first = obj[0]
            if isinstance(first, dict):
                for key in ("item", "metric", "name", "改善項目"):
                    if key in first:
                        return str(first[key])

    return None


def build_daily_report_metrics(
    daily_reports_dir: Path, dates: Iterable[pd.Timestamp]
) -> pd.DataFrame:
    rows: list[dict] = []

    for date in dates:
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        day_dir = daily_reports_dir / date_str
        fixed_json = day_dir / "daily_metrics.json"

        if fixed_json.exists():
            try:
                obj = json.loads(fixed_json.read_text(encoding="utf-8"))
                rows.append(
                    {
                        "date": date_str,
                        "real_odds_available": obj.get("real_odds_available", pd.NA),
                        "pending_unpublished": obj.get("pending_unpublished", pd.NA),
                        "improvement_report_top1": obj.get("improvement_report_top1", pd.NA),
                    }
                )
                continue
            except Exception:
                pass

        real_odds_available = find_metric_from_daily_report(day_dir, "real_odds_available")
        pending_unpublished = find_metric_from_daily_report(day_dir, "pending_unpublished")
        improvement_report_top1 = find_improvement_report_top1(day_dir)

        rows.append(
            {
                "date": date_str,
                "real_odds_available": int(real_odds_available)
                if real_odds_available is not None
                else pd.NA,
                "pending_unpublished": int(pending_unpublished)
                if pending_unpublished is not None
                else pd.NA,
                "improvement_report_top1": improvement_report_top1
                if improvement_report_top1
                else pd.NA,
            }
        )

    return pd.DataFrame(rows)


def load_change_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "change_date",
                "change_key",
                "before_value",
                "after_value",
                "reason",
                "applied_by",
                "ticket",
            ]
        )

    df = pd.read_csv(path)
    if "change_date" not in df.columns:
        raise ValueError("change_log.csv must contain 'change_date' column")
    if "ticket" not in df.columns:
        df["ticket"] = ""

    df["change_date"] = pd.to_datetime(df["change_date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["change_date"]).copy()
    return df[
        [
            "change_date",
            "change_key",
            "before_value",
            "after_value",
            "reason",
            "applied_by",
            "ticket",
        ]
    ].copy()


def detect_multiple_changes_same_day(
    change_log_df: pd.DataFrame, target_date: str
) -> str | None:
    if change_log_df.empty:
        return None

    tmp = change_log_df.copy()
    tmp["change_date"] = pd.to_datetime(tmp["change_date"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    target = pd.to_datetime(target_date, errors="coerce").strftime("%Y-%m-%d")

    day_df = tmp[tmp["change_date"] == target]
    if len(day_df) <= 1:
        return None

    keys = ", ".join(day_df["change_key"].astype(str).tolist())
    return f"WARNING: multiple changes on same day ({len(day_df)}): {keys}"


def evaluate_change_hold(
    change_log_df: pd.DataFrame, latest_date: pd.Timestamp, min_hold_days: int
) -> dict:
    if change_log_df.empty:
        return {
            "can_change_today": True,
            "hold_days_elapsed": None,
            "last_change_date": None,
            "last_change_key": None,
            "hold_comment": "No prior changes logged. A single new change is allowed.",
        }

    latest_change = change_log_df.sort_values("change_date").iloc[-1]
    last_change_date = pd.Timestamp(latest_change["change_date"]).normalize()
    raw_days_elapsed = int((latest_date - last_change_date).days)
    hold_days_elapsed = max(0, raw_days_elapsed)
    can_change_today = hold_days_elapsed >= min_hold_days
    if raw_days_elapsed < 0:
        hold_comment = (
            "Latest change date is newer than latest metrics date. "
            "Hold is treated as 0 elapsed days."
        )
    else:
        hold_comment = (
            f"Hold satisfied ({hold_days_elapsed} days elapsed)."
            if can_change_today
            else f"Hold NOT satisfied ({hold_days_elapsed} days elapsed / need >= {min_hold_days})."
        )

    return {
        "can_change_today": can_change_today,
        "hold_days_elapsed": hold_days_elapsed,
        "last_change_date": last_change_date.strftime("%Y-%m-%d"),
        "last_change_key": str(latest_change.get("change_key", "")),
        "hold_comment": hold_comment,
    }


def build_total_row(df: pd.DataFrame) -> dict:
    buy_count = int(pd.to_numeric(df["buy_count"], errors="coerce").fillna(0).sum())
    buy_hit_count = int(pd.to_numeric(df["buy_hit_count"], errors="coerce").fillna(0).sum())

    weighted_avg_odds = 0.0
    if buy_count > 0:
        odds_numerator = (
            pd.to_numeric(df["avg_odds"], errors="coerce").fillna(0)
            * pd.to_numeric(df["buy_count"], errors="coerce").fillna(0)
        ).sum()
        weighted_avg_odds = float(odds_numerator / buy_count)

    real_odds_available_total = (
        pd.to_numeric(df["real_odds_available"], errors="coerce").fillna(0).sum()
    )
    pending_unpublished_total = (
        pd.to_numeric(df["pending_unpublished"], errors="coerce").fillna(0).sum()
    )

    return {
        "date": "TOTAL",
        "total_count": int(pd.to_numeric(df["total_count"], errors="coerce").fillna(0).sum()),
        "buy_count": buy_count,
        "buy_hit_count": buy_hit_count,
        "buy_hit_rate": round((buy_hit_count / buy_count), 4) if buy_count > 0 else 0.0,
        "avg_odds": round(weighted_avg_odds, 4),
        "pending_count": int(pd.to_numeric(df["pending_count"], errors="coerce").fillna(0).sum()),
        "skip_count": int(pd.to_numeric(df["skip_count"], errors="coerce").fillna(0).sum()),
        "real_odds_available": int(real_odds_available_total),
        "pending_unpublished": int(pending_unpublished_total),
        "improvement_report_top1": pd.NA,
    }


def decide_weekly_action(df: pd.DataFrame) -> str:
    total_row = build_total_row(df)
    buy_count = int(total_row["buy_count"])
    hit_rate = float(total_row["buy_hit_rate"])
    avg_odds = float(total_row["avg_odds"])

    if buy_count == 0:
        return "BUY is 0. Check real_odds_available and upstream candidate quality first."
    if buy_count < 5:
        return "BUY sample is still small. Keep fixed operation and collect more rows."
    if hit_rate >= 0.25 and avg_odds >= 4.0:
        return "Keep trifecta as main. Avoid large gate changes; focus on upstream quality."
    if hit_rate < 0.15:
        return "Hit rate is weak. Prioritize 2nd/3rd rank quality and approx_prob quality."
    return "Middle state. Keep one-change-per-day and re-evaluate after 2-3 days."


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except Exception:
        return df.to_string(index=False)


def write_markdown_report(
    merged_df: pd.DataFrame, hold_eval: dict, weekly_action: str, output_path: Path
) -> None:
    lines: list[str] = []
    lines.append("# Improvement Loop Monitoring Report")
    lines.append("")
    lines.append("## Daily Metrics")
    lines.append("")
    lines.append(dataframe_to_markdown(merged_df))
    lines.append("")
    lines.append("## Change Hold Evaluation")
    lines.append("")
    lines.append(f"- can_change_today: {hold_eval['can_change_today']}")
    lines.append(f"- hold_days_elapsed: {hold_eval['hold_days_elapsed']}")
    lines.append(f"- last_change_date: {hold_eval['last_change_date']}")
    lines.append(f"- last_change_key: {hold_eval['last_change_key']}")
    lines.append(f"- hold_comment: {hold_eval['hold_comment']}")
    lines.append("")
    lines.append("## Weekly Action")
    lines.append("")
    lines.append(weekly_action)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    config = MonitorConfig(
        skip_decisions_path=args.skip_decisions,
        daily_reports_dir=args.daily_reports_dir,
        output_dir=args.output_dir,
        change_log_path=args.change_log,
        lookback_days=args.lookback_days,
        min_hold_days=args.min_hold_days,
    )

    ensure_output_dir(config.output_dir)
    skip_df = load_skip_decisions(config.skip_decisions_path)
    target_dates = latest_n_dates(skip_df, config.lookback_days)
    if not target_dates:
        raise RuntimeError("No valid dates found in skip_decisions.csv.")

    daily_decisions_df = build_daily_decision_metrics(skip_df, target_dates)
    daily_reports_df = build_daily_report_metrics(config.daily_reports_dir, target_dates)

    merged_df = daily_decisions_df.merge(daily_reports_df, on="date", how="left")
    merged_df = merged_df.sort_values("date").reset_index(drop=True)

    total_row = build_total_row(merged_df)
    final_df = pd.concat([merged_df, pd.DataFrame([total_row])], ignore_index=True)

    latest_date = pd.Timestamp(target_dates[-1]).normalize()
    change_log_df = load_change_log(config.change_log_path)
    hold_eval = evaluate_change_hold(change_log_df, latest_date, config.min_hold_days)
    weekly_action = decide_weekly_action(merged_df)
    same_day_warning = detect_multiple_changes_same_day(
        change_log_df, latest_date.strftime("%Y-%m-%d")
    )

    csv_path = config.output_dir / "daily_monitoring_summary.csv"
    md_path = config.output_dir / "daily_monitoring_summary.md"
    json_path = config.output_dir / "daily_monitoring_summary.json"

    final_df.to_csv(csv_path, index=False, encoding="utf-8")
    write_markdown_report(final_df, hold_eval, weekly_action, md_path)

    payload = {
        "config": {
            "skip_decisions_path": str(config.skip_decisions_path),
            "daily_reports_dir": str(config.daily_reports_dir),
            "output_dir": str(config.output_dir),
            "change_log_path": str(config.change_log_path),
            "lookback_days": int(config.lookback_days),
            "min_hold_days": int(config.min_hold_days),
            "improvement_rank_target": int(config.improvement_rank_target),
        },
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "hold_evaluation": hold_eval,
        "same_day_warning": same_day_warning,
        "weekly_action": weekly_action,
        "rows": final_df.to_dict(orient="records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Daily Monitoring Summary ===")
    print(final_df.to_string(index=False))
    print("\n=== Hold Evaluation ===")
    print(json.dumps(hold_eval, ensure_ascii=False, indent=2))
    print("\n=== Weekly Action ===")
    print(weekly_action)
    if same_day_warning:
        print("\n=== Change Warning ===")
        print(same_day_warning)
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
