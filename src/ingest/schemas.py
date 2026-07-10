from typing import List, Dict

# スキーマ定義の集中管理
CANONICAL_COLUMNS = {
    "historical": [
        "race_id", "date", "venue", "race_no", "lane", "racer_id", "racer_class",
        "avg_st", "national_win_rate", "national_2ren_rate", "local_2ren_rate",
        "motor_no", "motor_2ren_rate", "boat_no", "boat_2ren_rate", "season", "day_number",
        "exhibition_time", "body_weight", "tilt", "parts_change_flag", "propeller_new_flag",
        "prev_race_course", "prev_race_st", "prev_race_finish", "start_display_st",
        "wind_speed", "weather", "water_temp", "wave_height", "finish_position", "win_label", "odds_trifecta"
    ],
    "today": [
        "race_id", "date", "venue", "race_no", "lane", "racer_id", "racer_class",
        "avg_st", "national_win_rate", "national_2ren_rate", "local_2ren_rate",
        "motor_no", "motor_2ren_rate", "boat_no", "boat_2ren_rate", "season", "day_number",
        "exhibition_time", "body_weight", "tilt", "parts_change_flag", "propeller_new_flag",
        "prev_race_course", "prev_race_st", "prev_race_finish", "start_display_st",
        "wind_speed", "weather", "water_temp", "wave_height", "odds_trifecta"
    ]
}
