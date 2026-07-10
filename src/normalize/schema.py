from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


MODEL_VERSION = "baseline_rule_v1"


def _default_data_status() -> dict[str, str]:
    return {
        "racelist": "pending",
        "odds3t": "pending",
        "beforeinfo": "pending",
        "result": "pending",
    }


@dataclass
class Boat:
    boat_no: int
    racer_name: str | None = None
    racer_id: str | None = None
    branch: str | None = None
    cls: str | None = None
    age: int | None = None
    weight: float | None = None
    avg_st: float | None = None
    national_win_rate: float | None = None
    national_2rate: float | None = None
    national_3rate: float | None = None
    local_win_rate: float | None = None
    local_2rate: float | None = None
    local_3rate: float | None = None
    motor_no: int | None = None
    motor_2rate: float | None = None
    boat_no_equipment: int | None = None
    boat_2rate: float | None = None
    start_exhibition_course: int | None = None
    start_exhibition_st: float | str | None = None
    tilt: float | None = None
    propeller: str | None = None
    parts_exchange: list[str] = field(default_factory=list)
    weight_adjustment: str | None = None
    f_count: int | None = None
    l_count: int | None = None
    exhibition_time: float | None = None
    exhibition_st: float | None = None
    boat_score: float | None = None
    score_rank: int | None = None
    score_reason: str = ""
    data_status: str = "missing"
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Prediction:
    combo: str
    prob: float
    odds: float | None
    expected_value: float | None
    edge: float | None
    rank: int
    grade: str
    decision: str
    reason: str
    prob_rank: int | None = None
    ev_rank: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RaceSnapshot:
    date: str
    jcd: str
    venue_name: str
    rno: int
    deadline: str = ""
    race_title: str = ""
    stage: str = "pre_race"
    boats: list[Boat] = field(default_factory=list)
    before_info: dict[str, Any] | None = None
    weather: dict[str, Any] | None = None
    start_exhibition: list[dict[str, Any]] = field(default_factory=list)
    odds3t: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    data_status: dict[str, str] = field(default_factory=_default_data_status)
    data_status_reason: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    predictions: list[Prediction] = field(default_factory=list)
    model_version: str = MODEL_VERSION
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["boats"] = [boat.to_dict() if hasattr(boat, "to_dict") else boat for boat in self.boats]
        data["predictions"] = [pred.to_dict() if hasattr(pred, "to_dict") else pred for pred in self.predictions]
        return data
