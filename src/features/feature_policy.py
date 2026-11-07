from __future__ import annotations

STAGE_FEATURES = {
    "pre_race": {
        "course_adjustment",
        "national_win_rate",
        "local_win_rate",
        "motor_2rate",
        "boat_2rate",
        "avg_st",
        "f_penalty",
        "l_penalty",
        "missing_penalty",
    },
    "beforeinfo": {
        "course_adjustment",
        "national_win_rate",
        "local_win_rate",
        "motor_2rate",
        "boat_2rate",
        "avg_st",
        "exhibition_time",
        "exhibition_st",
        "f_penalty",
        "l_penalty",
        "missing_penalty",
    },
    "odds": {
        "course_adjustment",
        "national_win_rate",
        "local_win_rate",
        "motor_2rate",
        "boat_2rate",
        "avg_st",
        "exhibition_time",
        "exhibition_st",
        "f_penalty",
        "l_penalty",
        "missing_penalty",
    },
    "result": {
        "course_adjustment",
        "national_win_rate",
        "local_win_rate",
        "motor_2rate",
        "boat_2rate",
        "avg_st",
        "exhibition_time",
        "exhibition_st",
        "f_penalty",
        "l_penalty",
        "missing_penalty",
    },
}


def allowed_features(stage: str) -> set[str]:
    return set(STAGE_FEATURES.get(str(stage or "pre_race").lower(), STAGE_FEATURES["pre_race"]))
