from __future__ import annotations

from datetime import datetime
from typing import Any

from src.normalize.schema import Boat, RaceSnapshot


def _normalize_data_status(value: dict[str, str] | str | None) -> dict[str, str]:
    if isinstance(value, dict):
        base = {
            "racelist": value.get("racelist", "pending"),
            "odds3t": value.get("odds3t", "pending"),
            "beforeinfo": value.get("beforeinfo", "pending"),
            "result": value.get("result", "pending"),
        }
        return base
    status = str(value or "pending")
    return {
        "racelist": status,
        "odds3t": "pending",
        "beforeinfo": "pending",
        "result": "pending",
    }


def build_race_snapshot(
    *,
    date: str,
    jcd: str,
    venue_name: str,
    rno: int,
    deadline: str = "",
    race_title: str = "",
    stage: str = "pre_race",
    boats: list[Boat] | list[dict[str, Any]] | None = None,
    before_info: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None,
    start_exhibition: list[dict[str, Any]] | None = None,
    odds3t: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    data_status: dict[str, str] | str | None = None,
    data_status_reason: dict[str, Any] | None = None,
    predictions: list[Any] | None = None,
    model_version: str = "baseline_rule_v1",
    updated_at: str | None = None,
) -> RaceSnapshot:
    boat_rows: list[Boat] = []
    for item in boats or []:
        if isinstance(item, Boat):
            boat_rows.append(item)
        elif isinstance(item, dict):
            boat_rows.append(Boat(**item))
    prediction_rows = []
    for item in predictions or []:
        if hasattr(item, "to_dict"):
            prediction_rows.append(item)
        elif isinstance(item, dict):
            from src.normalize.schema import Prediction

            prediction_rows.append(Prediction(**item))
    return RaceSnapshot(
        date=date,
        jcd=jcd,
        venue_name=venue_name,
        rno=rno,
        deadline=deadline,
        race_title=race_title,
        stage=stage,
        boats=boat_rows,
        before_info=dict(before_info or {}),
        weather=weather,
        start_exhibition=list(start_exhibition or []),
        odds3t=dict(odds3t or {}),
        result=dict(result or {}),
        data_status=_normalize_data_status(data_status),
        data_status_reason=dict(data_status_reason or {}),
        source=dict(source or {}),
        predictions=prediction_rows,
        model_version=model_version,
        updated_at=updated_at or datetime.now().isoformat(timespec="seconds"),
    )
