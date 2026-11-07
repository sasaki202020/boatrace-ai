from __future__ import annotations

from src.pipeline.daily_report import daily_report


def summarize_daily(*, date: str, jcd: str = "all") -> dict:
    return daily_report(target_date=date, jcd=jcd)
