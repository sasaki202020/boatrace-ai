import pandas as pd
import json
import os
from typing import List, Dict

class SchemaValidator:
    """
    Ingestion Contract に基づくデータバリデーション。
    """
    def __init__(self, contract_path="docs/ingestion_contract.md"):
        # 実装では契約内容をルール化
        self.required_cols = ["race_id", "date", "venue", "race_no", "lane", "racer_id", "racer_class"]
        self.fatal_errors = []
        self.warnings = []
        self.race_issues = []

    def reset(self) -> None:
        self.fatal_errors = []
        self.warnings = []
        self.race_issues = []

    def _to_int_series(self, series: pd.Series) -> pd.Series:
        return pd.to_numeric(series, errors="coerce").astype("Int64")

    def _record_race_issue(self, issue: Dict) -> None:
        self.race_issues.append(issue)

    def validate_dataframe(self, df: pd.DataFrame) -> bool:
        """
        致命的な不整合がないかチェック。
        """
        self.reset()

        # 1. 必須列の存在チェック
        missing_cols = [c for c in self.required_cols if c not in df.columns]
        if missing_cols:
            self.fatal_errors.append(f"Missing required columns: {missing_cols}")
            return False

        is_valid = True

        # 2. race 単位の 6艇完全性チェック
        lane_series = self._to_int_series(df["lane"])
        race_no_series = self._to_int_series(df["race_no"])
        racer_id_series = self._to_int_series(df["racer_id"])

        df_check = df.copy()
        df_check["_lane_int"] = lane_series
        df_check["_race_no_int"] = race_no_series
        df_check["_racer_id_int"] = racer_id_series

        for race_id, group in df_check.groupby("race_id", dropna=False, sort=False):
            lane_counts = group["_lane_int"].dropna().value_counts().sort_index()
            present_lanes = [int(lane) for lane in group["_lane_int"].dropna().tolist()]
            duplicate_lanes = [int(lane) for lane, count in lane_counts.items() if count > 1]
            missing_lanes = [lane for lane in range(1, 7) if lane not in lane_counts.index.tolist()]
            malformed_rows = group[
                group["_lane_int"].isna()
                | group["_race_no_int"].isna()
                | group["_racer_id_int"].isna()
            ].index.tolist()
            ordered_lanes = [int(lane) for lane in group["_lane_int"].dropna().tolist()]
            order_ok = ordered_lanes == sorted(ordered_lanes)

            issue = {
                "race_id": race_id,
                "race_no": int(group["_race_no_int"].dropna().iloc[0]) if not group["_race_no_int"].dropna().empty else None,
                "rows": int(len(group)),
                "present_lanes": present_lanes,
                "duplicate_lanes": duplicate_lanes,
                "missing_lanes": missing_lanes,
                "malformed_rows": malformed_rows,
                "order_ok": order_ok,
            }
            if duplicate_lanes or missing_lanes or malformed_rows or len(group) != 6:
                self._record_race_issue(issue)

            if duplicate_lanes:
                self.fatal_errors.append(f"{race_id}: duplicate lanes {duplicate_lanes}")
                is_valid = False
            if missing_lanes:
                self.fatal_errors.append(f"{race_id}: missing lanes {missing_lanes}")
                is_valid = False
            if malformed_rows:
                self.fatal_errors.append(f"{race_id}: malformed rows at indexes {malformed_rows}")
                is_valid = False
            if len(group) != 6:
                self.fatal_errors.append(f"{race_id}: expected 6 boats but found {len(group)}")
                is_valid = False
            if len(group) == 6 and not order_ok:
                self.warnings.append(f"{race_id}: lane order anomaly {ordered_lanes}")

        # 3. 重複チェック（race_id + lane）
        dupes = df.duplicated(subset=['race_id', 'lane'])
        if dupes.any():
            duplicate_races = df.loc[dupes, 'race_id'].astype(str).unique().tolist()
            self.fatal_errors.append(f"Duplicate race_id + lane found: {duplicate_races}")
            is_valid = False

        return is_valid and not self.fatal_errors

    def get_summary(self) -> Dict:
        return {
            "fatal_errors": self.fatal_errors,
            "warnings": self.warnings,
            "race_issues": self.race_issues,
            "status": "FAIL" if self.fatal_errors else "PASS"
        }
