import pandas as pd
import itertools
import json
import os
from pathlib import Path

import joblib


class TrifectaGenerator:
    """
    1着確率から3連単の組み合わせと確率を近似生成する。
    """
    def __init__(self, config_path="config/strategy_config.json"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)
        self.candidate_cfg = self.config.get("candidate_generation", {})
        self.candidate_generation_mode = str(
            self.candidate_cfg.get(
                "candidate_generation_mode",
                self.config.get("candidate_generation_mode", "legacy"),
            )
            or "legacy"
        ).strip().lower()
        self.first_prob_relative_threshold = float(
            self.candidate_cfg.get(
                "first_prob_relative_threshold",
                self.config.get("first_prob_relative_threshold", 0.72),
            )
        )
        self.place_feats = ["local_2ren_rate", "national_2ren_rate", "boat_2ren_rate"]
        self.win_feat = "national_win_rate"
        self.win_beta = 0.2
        self.tiebreak_thresh = 0.01
        self.tiebreak_weight = 0.001
        self.use_conditional_place_models = bool(self.candidate_cfg.get("use_conditional_place_models", False))
        self.use_conditional_place_model_p2 = bool(self.candidate_cfg.get("use_conditional_place_model_p2", False))
        self.use_conditional_place_tables = bool(self.candidate_cfg.get("use_conditional_place_tables", True))
        self.conditional_scale_factor = float(self.candidate_cfg.get("conditional_scale_factor", 20.0))
        self.conditional_lane_feature_bias = float(self.candidate_cfg.get("conditional_lane_feature_bias", 0.15))
        self.conditional_table_path = Path(self.candidate_cfg.get("conditional_table_path", "models/conditional_place_tables.json"))
        self.conditional_table_min_support = int(self.candidate_cfg.get("conditional_table_min_support", 20))
        self.conditional_laplace_alpha = float(self.candidate_cfg.get("conditional_laplace_alpha", 0.5))
        self.order_adjustment_alpha = float(self.candidate_cfg.get("order_adjustment_alpha", 0.1))
        self.place2_model_path = Path(self.candidate_cfg.get("place2_model_path", "models/place2_model.joblib"))
        self.place3_model_path = Path(self.candidate_cfg.get("place3_model_path", "models/place3_model.joblib"))
        self.conditional_tables = self._load_conditional_tables(self.conditional_table_path)
        self.place2_bundle = self._load_model_bundle(self.place2_model_path)
        self.place3_bundle = self._load_model_bundle(self.place3_model_path)
        self.top_n_win = int(self.candidate_cfg.get("top_n_win", 6))
        self.max_trifecta_combinations = int(self.candidate_cfg.get("max_trifecta_combinations", 60))
        self.pair_base_cols = [
            "lane_num",
            "course_no",
            "waku_no",
            "avg_st",
            "national_win_rate",
            "national_2ren_rate",
            "local_2ren_rate",
            "motor_2ren_rate",
            "boat_2ren_rate",
            "racer_rank",
            "season_num",
            "weather_num",
            "win_rate_diff_to_avg",
            "st_diff_to_min",
            "start_timing",
            "start_timing_diff_to_avg",
            "start_timing_rank_in_race",
            "start_timing_fast_flag",
            "inside_course_flag",
            "middle_course_flag",
            "outside_course_flag",
            "lane_win_rate_prior",
            "lane_top3_rate_prior",
            "nige_rate_prior",
            "sashi_rate_prior",
            "makuri_rate_prior",
            "low_motor_flag",
            "low_boat_flag",
            "jcd_low_motor_flag",
            "jcd_low_boat_flag",
            "recent3_avg_finish",
            "recent3_win_rate",
            "recent3_top3_rate",
            "recent3_avg_st",
            "recent6_avg_finish",
            "recent6_win_rate",
            "recent6_top3_rate",
            "recent6_avg_st",
        ]

    @staticmethod
    def _load_model_bundle(path: Path):
        if not path.exists():
            return None
        try:
            bundle = joblib.load(path)
            if isinstance(bundle, dict) and "feature_columns" in bundle and (
                "model" in bundle or "models" in bundle
            ):
                return bundle
        except Exception:
            return None
        return None

    @staticmethod
    def _load_feature_frame(win_proba_path: str | os.PathLike[str]) -> pd.DataFrame:
        requested = Path(win_proba_path)
        feature_candidates: list[Path] = []
        if "train" in requested.name:
            feature_candidates.append(Path("data/features/train_features.csv"))
            feature_candidates.append(Path("data/features/today_features.csv"))
        else:
            feature_candidates.append(Path("data/features/today_features.csv"))
            feature_candidates.append(Path("data/features/train_features.csv"))

        frames: list[pd.DataFrame] = []
        seen: set[str] = set()
        for feature_path in feature_candidates:
            key = str(feature_path)
            if key in seen or not feature_path.exists():
                continue
            seen.add(key)
            try:
                feat_df = pd.read_csv(feature_path)
            except Exception:
                continue
            if not {"race_id", "lane"}.issubset(feat_df.columns):
                continue
            feat_df = feat_df.copy()
            feat_df["race_id"] = feat_df["race_id"].astype(str).str.strip()
            feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
            feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
            feat_df["lane"] = feat_df["lane"].astype(int)
            frames.append(feat_df)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False).drop_duplicates(subset=["race_id", "lane"], keep="first")

    @staticmethod
    def _load_conditional_tables(path: Path):
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                tables = json.load(f)
            if isinstance(tables, dict) and "p2" in tables and "p3" in tables:
                return tables
        except Exception:
            return None
        return None

    @staticmethod
    def _clean_numeric(v):
        try:
            if pd.isna(v):
                return 0.0
        except Exception:
            pass
        try:
            return float(v)
        except Exception:
            return 0.0

    def _make_conditional_row(self, cand_row, first_row, second_row=None):
        row = {}
        cand_lane = int(self._clean_numeric(cand_row.get("lane")))
        first_lane = int(self._clean_numeric(first_row.get("lane")))
        row["candidate_lane"] = cand_lane
        row["first_lane"] = first_lane
        for col in self.pair_base_cols:
            cand_val = self._clean_numeric(cand_row.get(col))
            first_val = self._clean_numeric(first_row.get(col))
            row[f"cand_{col}"] = cand_val
            row[f"first_{col}"] = first_val
            row[f"diff_{col}"] = cand_val - first_val
        if second_row is not None:
            second_lane = int(self._clean_numeric(second_row.get("lane")))
            row["second_lane"] = second_lane
            for col in self.pair_base_cols:
                second_val = self._clean_numeric(second_row.get(col))
                row[f"second_{col}"] = second_val
                row[f"diff_first_{col}"] = self._clean_numeric(cand_row.get(col)) - self._clean_numeric(first_row.get(col))
                row[f"diff_second_{col}"] = self._clean_numeric(cand_row.get(col)) - second_val
        return row

    @staticmethod
    def _predict_conditional_probabilities(model_bundle, rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        model = model_bundle["model"]
        feature_columns = model_bundle["feature_columns"]
        df = pd.DataFrame(rows)
        for col in feature_columns:
            if col not in df.columns:
                df[col] = 0.0
        X = df[feature_columns]
        probs = model.predict_proba(X)[:, 1]
        df["raw_prob"] = pd.to_numeric(probs, errors="coerce").fillna(0.0)
        return df

    @staticmethod
    def _predict_second_lane_conditional_probabilities(model_bundle, rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        feature_columns = model_bundle["feature_columns"]
        models = model_bundle.get("models", {})
        global_model = model_bundle.get("global_model")
        df = pd.DataFrame(rows)
        for col in feature_columns:
            if col not in df.columns:
                df[col] = 0.0
        raw_probs = []
        for _, row in df.iterrows():
            first_lane = int(row.get("first_lane", 0) or 0)
            model = None
            if isinstance(models, dict):
                model = models.get(first_lane)
            if model is None:
                model = global_model or model_bundle.get("model")
            if model is None:
                raw_probs.append(0.0)
                continue
            X = pd.DataFrame([{col: row[col] for col in feature_columns}])
            try:
                prob = float(model.predict_proba(X)[:, 1][0])
            except Exception:
                prob = 0.0
            raw_probs.append(prob)
        df["raw_prob"] = pd.Series(raw_probs, dtype="float64").fillna(0.0)
        return df

    @staticmethod
    def _select_top60_per_first_pattern(
        race_results,
        min_per_first=12,
        fill_mode="global",
        ensure_min_distinct_first_lanes=3,
    ):
        lanes = sorted({int(row["first_lane"]) for row in race_results})
        if not lanes:
            return []

        selected = []
        selected_keys = set()
        grouped = {lane: [] for lane in lanes}
        for row in race_results:
            grouped[int(row["first_lane"])].append(row)

        for lane in lanes:
            for row in grouped[lane][:max(int(min_per_first), 0)]:
                key = str(row["trifecta"])
                if key in selected_keys:
                    continue
                selected.append(row)
                selected_keys.add(key)
                if len(selected) >= 60:
                    return selected[:60]

        if str(fill_mode) == "lane_round_robin":
            lane_idx = 0
            cursor = {lane: max(int(min_per_first), 0) for lane in lanes}
            while len(selected) < 60:
                lane = lanes[lane_idx % len(lanes)]
                lane_idx += 1
                idx = cursor[lane]
                if idx >= len(grouped[lane]):
                    if all(cursor[l] >= len(grouped[l]) for l in lanes):
                        break
                    continue
                row = grouped[lane][idx]
                cursor[lane] += 1
                key = str(row["trifecta"])
                if key in selected_keys:
                    continue
                selected.append(row)
                selected_keys.add(key)
        else:
            for row in race_results:
                key = str(row["trifecta"])
                if key in selected_keys:
                    continue
                selected.append(row)
                selected_keys.add(key)
                if len(selected) >= 60:
                    break

        if str(fill_mode) == "global" and int(ensure_min_distinct_first_lanes) > 0:
            selected_first_lanes = {int(row["first_lane"]) for row in selected}
            missing_lanes = [lane for lane in lanes if lane not in selected_first_lanes]
            needed = max(int(ensure_min_distinct_first_lanes) - len(selected_first_lanes), 0)
            if needed > 0 and missing_lanes:
                missing_rows = []
                for lane in missing_lanes:
                    lane_rows = [row for row in grouped[lane] if str(row["trifecta"]) not in selected_keys]
                    if not lane_rows:
                        continue
                    lane_rows.sort(key=lambda x: float(x.get("approx_prob", 0.0)), reverse=True)
                    missing_rows.append(lane_rows[0])
                missing_rows.sort(key=lambda x: float(x.get("approx_prob", 0.0)), reverse=True)
                for row in missing_rows[:needed]:
                    key = str(row["trifecta"])
                    if key in selected_keys:
                        continue
                    if len(selected) < 60:
                        selected.append(row)
                        selected_keys.add(key)
                        continue
                    worst_idx = min(
                        range(len(selected)),
                        key=lambda idx: float(selected[idx].get("approx_prob", 0.0)),
                    )
                    if float(row.get("approx_prob", 0.0)) > float(selected[worst_idx].get("approx_prob", 0.0)):
                        selected_keys.discard(str(selected[worst_idx]["trifecta"]))
                        selected[worst_idx] = row
                        selected_keys.add(key)
                    else:
                        selected.append(row)
                        selected_keys.add(key)

        return selected[:60]

    @staticmethod
    def _apply_tiebreak(race_results, thresh=0.01, weight=0.001):
        """
        main_score が thresh 以内の候補だけ place_score で微調整して返す。
        """
        if not race_results:
            return race_results

        max_score = max(float(row.get("main_score", 0.0)) for row in race_results)
        for row in race_results:
            main_score = float(row.get("main_score", 0.0))
            place_score = float(row.get("place_score_scaled", 0.0))
            if (max_score - main_score) <= thresh:
                row["approx_prob"] = main_score + (weight * place_score)
            else:
                row["approx_prob"] = main_score
        return sorted(race_results, key=lambda x: x["approx_prob"], reverse=True)

    @staticmethod
    def _renormalize_race_probabilities(race_results):
        """
        place_score 補正後の approx_prob をレース内で再正規化する。
        TASK-6: max(approx_prob) <= 1.0 を担保。
        """
        if not race_results:
            return race_results

        probs = [max(0.0, float(row.get("approx_prob", 0.0))) for row in race_results]
        total = float(sum(probs))
        if total <= 0:
            return race_results

        for row, p in zip(race_results, probs):
            row["approx_prob_raw"] = float(p)
            row["normalized_prob"] = float(p / total)
            row["approx_prob"] = float(p / total)
        return sorted(race_results, key=lambda x: x["approx_prob"], reverse=True)

    def _context_key_from_group(self, group):
        if "jcd" in group.columns:
            jcd_val = self._clean_numeric(group["jcd"].iloc[0])
            if jcd_val not in (None, 0.0):
                return str(int(jcd_val))
        return "__global__"

    def _table_counts_for(self, tables_section, context_key, key_parts):
        context_keys = [context_key]
        if context_key != "__global__":
            context_keys.append("__global__")
        for key in context_keys:
            entry = tables_section.get(key)
            if not isinstance(entry, dict):
                continue
            support = int(entry.get("support", 0) or 0)
            if support < self.conditional_table_min_support and key != "__global__":
                continue
            counts_root = entry.get("counts", {})
            if not isinstance(counts_root, dict):
                continue
            if len(key_parts) == 1:
                counts = counts_root.get(str(int(self._clean_numeric(key_parts[0]))), {})
            elif len(key_parts) == 2:
                first_key = str(int(self._clean_numeric(key_parts[0])))
                second_key = str(int(self._clean_numeric(key_parts[1])))
                counts = counts_root.get(first_key, {}).get(second_key, {})
            else:
                counts = {}
            if isinstance(counts, dict):
                return counts
        return {}

    def _build_table_based_maps(self, group, lanes, top_boats, context_key):
        p2_map = {}
        p3_map = {}
        if not self.conditional_tables:
            return p2_map, p3_map

        p2_section = self.conditional_tables.get("p2", {})
        p3_section = self.conditional_tables.get("p3", {})

        for first_lane in top_boats:
            counts = self._table_counts_for(p2_section, context_key, [first_lane])
            raw_scores = {}
            for second_lane in lanes:
                if int(second_lane) == int(first_lane):
                    continue
                raw_scores[int(second_lane)] = float(counts.get(str(int(second_lane)), 0)) + self.conditional_laplace_alpha
            total = sum(raw_scores.values())
            if total <= 0:
                raw_scores = {int(second_lane): 1.0 for second_lane in lanes if int(second_lane) != int(first_lane)}
                total = sum(raw_scores.values())
            for second_lane, raw in raw_scores.items():
                p2_map[(int(first_lane), int(second_lane))] = float(raw / total)

        for first_lane in top_boats:
            for second_lane in lanes:
                if int(second_lane) == int(first_lane):
                    continue
                counts = self._table_counts_for(p3_section, context_key, [first_lane, second_lane])
                raw_scores = {}
                for third_lane in lanes:
                    if int(third_lane) in (int(first_lane), int(second_lane)):
                        continue
                    raw_scores[int(third_lane)] = float(counts.get(str(int(third_lane)), 0)) + self.conditional_laplace_alpha
                total = sum(raw_scores.values())
                if total <= 0:
                    raw_scores = {
                        int(third_lane): 1.0
                        for third_lane in lanes
                        if int(third_lane) not in (int(first_lane), int(second_lane))
                    }
                    total = sum(raw_scores.values())
                for third_lane, raw in raw_scores.items():
                    p3_map[(int(first_lane), int(second_lane), int(third_lane))] = float(raw / total)

        return p2_map, p3_map

    def _make_second_lane_model_row(self, cand_row, first_row):
        row = {
            "race_id": str(cand_row.get("race_id", "")),
            "first_lane": int(self._clean_numeric(first_row.get("lane"))),
            "second_lane": int(self._clean_numeric(cand_row.get("lane"))),
        }
        for col in [
            "start_timing",
            "course_win_rate",
        ]:
            row[col] = self._clean_numeric(cand_row.get(col))
        return row

    def _select_first_lanes(self, sorted_boats: pd.DataFrame) -> list[int]:
        top_lanes: list[int] = []
        if sorted_boats.empty:
            return top_lanes

        top_prob = float(pd.to_numeric(sorted_boats["win_proba_norm"], errors="coerce").fillna(0.0).iloc[0])
        relative_floor = max(0.0, min(1.0, float(self.first_prob_relative_threshold)))

        for _, row in sorted_boats.iterrows():
            lane_i = int(row["lane"])
            if lane_i in top_lanes:
                continue
            prob = float(pd.to_numeric(row.get("win_proba_norm"), errors="coerce") or 0.0)
            if self.candidate_generation_mode == "expanded":
                include = len(top_lanes) < self.top_n_win or prob >= (top_prob * relative_floor)
                if include:
                    top_lanes.append(lane_i)
            else:
                if len(top_lanes) < self.top_n_win:
                    top_lanes.append(lane_i)

        if self.candidate_generation_mode == "expanded" and len(top_lanes) < self.top_n_win:
            for lane in sorted_boats["lane"].tolist():
                lane_i = int(lane)
                if lane_i in top_lanes:
                    continue
                top_lanes.append(lane_i)
                if len(top_lanes) >= self.top_n_win:
                    break

        return top_lanes

    def generate(self, win_proba_path, ignore_race_candidate_limit: bool = False):
        df = pd.read_csv(win_proba_path)
        feat_df = self._load_feature_frame(win_proba_path)
        if not feat_df.empty and {"race_id", "lane"}.issubset(feat_df.columns):
            feat_df = feat_df.copy()
            feat_df["race_id"] = feat_df["race_id"].astype(str).str.strip()
            df["race_id"] = df["race_id"].astype(str).str.strip()
            df = df.merge(feat_df, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
            if {"race_id", "lane"}.issubset(df.columns):
                df = df.drop_duplicates(subset=["race_id", "lane"], keep="last")
        results = []

        scale_cols = [self.win_feat] + self.place_feats
        for col in scale_cols:
            if col not in df.columns:
                df[f"{col}_scaled"] = 0.0
                continue
            col_series = pd.to_numeric(df[col], errors="coerce")
            col_min = col_series.min()
            col_max = col_series.max()
            df[f"{col}_scaled"] = ((col_series - col_min) / (col_max - col_min + 1e-9)).fillna(0.0)

        for race_id, group in df.groupby("race_id"):
            if "win_proba_norm" not in group.columns:
                raise ValueError("today_win_proba.csv must contain win_proba_norm")

            group = group.copy()
            group["win_proba_norm"] = pd.to_numeric(group["win_proba_norm"], errors="coerce").fillna(0.0)
            total_prob = group["win_proba_norm"].sum()
            if total_prob <= 0:
                continue
            group["win_proba_norm"] = group["win_proba_norm"] / total_prob

            # 1着確率でソート
            sorted_boats = group.sort_values("win_proba_norm", ascending=False)

            # 1着候補の選び方を legacy / expanded で切り替える
            top_boats = self._select_first_lanes(sorted_boats)
            top_first_prob = float(pd.to_numeric(sorted_boats["win_proba_norm"], errors="coerce").fillna(0.0).iloc[0])
            legacy_first_lane_count = min(self.top_n_win, len(sorted_boats))
            
            # 3連単 (1-2-3位) の全組み合わせを生成
            lanes = group["lane"].tolist()
            probs = group.set_index("lane")["win_proba_norm"].to_dict()
            win_scores = group.set_index("lane")[f"{self.win_feat}_scaled"].to_dict()
            place_cols = [f"{feat}_scaled" for feat in self.place_feats if f"{feat}_scaled" in group.columns]
            if place_cols:
                place_avg = group[place_cols].fillna(0.0).mean(axis=1)
                place_scores = group.set_index("lane").assign(place_avg_scaled=place_avg)["place_avg_scaled"].to_dict()
            else:
                place_scores = {int(lane): 0.0 for lane in lanes}
            
            race_results = []
            conditional_table_ready = self.use_conditional_place_tables and self.conditional_tables is not None
            p2_model_ready = self.use_conditional_place_model_p2 and self.place2_bundle is not None
            conditional_ready = conditional_table_ready or p2_model_ready

            p2_map = {}
            p3_map = {}
            context_key = self._context_key_from_group(group)
            lane_rows = {int(row["lane"]): row for _, row in group.iterrows()}

            if p2_model_ready:
                p2_rows = []
                for first_lane in top_boats:
                    first_row = lane_rows.get(int(first_lane))
                    if first_row is None:
                        continue
                    for second_lane in lanes:
                        if int(second_lane) == int(first_lane):
                            continue
                        cand_row = lane_rows[int(second_lane)]
                        rec = self._make_second_lane_model_row(cand_row, first_row)
                        p2_rows.append(rec)

                p2_df = self._predict_second_lane_conditional_probabilities(self.place2_bundle, p2_rows)
                use_model_p2 = False
                if not p2_df.empty:
                    p2_df["p2_cond"] = p2_df.groupby(["race_id", "first_lane"])["raw_prob"].transform(
                        lambda x: x / x.sum() if x.sum() > 0 else 0.0
                    )
                    p2_total = float(p2_df["p2_cond"].sum())
                    p2_max = float(p2_df["p2_cond"].max()) if not p2_df.empty else 0.0
                    if p2_total > 0 and p2_max > 0:
                        use_model_p2 = True
                        for _, r in p2_df.iterrows():
                            p2_map[(int(r["first_lane"]), int(r["second_lane"]))] = float(r["p2_cond"])
                if not use_model_p2 and conditional_table_ready:
                    p2_map, _ = self._build_table_based_maps(group, lanes, top_boats, context_key)
            elif conditional_table_ready:
                p2_map, p3_map = self._build_table_based_maps(group, lanes, top_boats, context_key)

            if conditional_table_ready:
                if not p3_map:
                    _, p3_map = self._build_table_based_maps(group, lanes, top_boats, context_key)

            combos = list(itertools.permutations(lanes, 3))
            for c in combos:
                if c[0] not in top_boats:
                    continue

                p1 = probs[c[0]]
                if p1 <= 0:
                    continue

                if conditional_ready:
                    p2 = p2_map.get((int(c[0]), int(c[1])), 0.0)
                    p3 = p3_map.get((int(c[0]), int(c[1]), int(c[2])), 0.0)
                    base_prob = p1 * p2 * p3
                    approx_prob = base_prob * self.conditional_scale_factor
                else:
                    eps = 1e-10
                    remain_after_first = sum(probs[lane] for lane in lanes if lane != c[0])
                    remain_after_second = sum(probs[lane] for lane in lanes if lane not in (c[0], c[1]))
                    if remain_after_first <= eps or remain_after_second <= eps:
                        continue
                    p2 = probs[c[1]] / remain_after_first
                    p3 = probs[c[2]] / remain_after_second
                    base_prob = p1 * p2 * p3
                    approx_prob = base_prob + (self.win_beta * float(win_scores.get(c[0], 0.0)))

                second_place_score_raw = place_scores.get(c[1], 0.0)
                third_place_score_raw = place_scores.get(c[2], 0.0)
                place_raw = (second_place_score_raw + third_place_score_raw) / 2.0
                place_score = float(pd.to_numeric(place_raw, errors="coerce"))
                if pd.isna(place_score):
                    place_score = 0.0
                second_place_score = float(pd.to_numeric(second_place_score_raw, errors="coerce"))
                third_place_score = float(pd.to_numeric(third_place_score_raw, errors="coerce"))
                if pd.isna(second_place_score):
                    second_place_score = 0.0
                if pd.isna(third_place_score):
                    third_place_score = 0.0
                second_prob = float(p2)
                third_prob = float(p3)
                order_gap = second_prob - third_prob
                place_gap = second_place_score - third_place_score
                if place_score <= 0:
                    place_score = max(0.0, order_gap)
                # 2着/3着の並びを全候補で弱く反映する。
                # 既存の tiebreak は race 内上位近傍にしか効かないため、
                # 同一1着候補内の順序が崩れやすかった。
                # ここでは平均 place_score ではなく、2着候補と3着候補の
                # 差分を少し使い、2着/3着の順序そのものにだけ効かせる。
                # 1着ロジックは崩さず、同一1着内の並びだけを弱く整える。
                order_signal = (0.50 * order_gap) + (0.05 * place_gap) + (0.10 * place_score)
                order_adjustment = self.conditional_scale_factor * self.order_adjustment_alpha * order_signal
                place_score_adjustment = approx_prob * (self.conditional_lane_feature_bias * place_score)
                main_score = approx_prob + place_score_adjustment + order_adjustment
                if main_score <= 0:
                    continue
                race_results.append({
                    "race_id": race_id,
                    "date": group["date"].iloc[0] if "date" in group.columns else None,
                    "candidate_generation_mode": self.candidate_generation_mode,
                    "trifecta": f"{c[0]}-{c[1]}-{c[2]}",
                    "first_lane": c[0],
                    "second_lane": c[1],
                    "third_lane": c[2],
                    "first_prob": float(p1),
                    "first_win_proba": probs[c[0]],
                    "second_score": second_prob,
                    "second_win_proba": second_prob,
                    "third_score": third_prob,
                    "third_win_proba": third_prob,
                    "win_score_scaled": float(win_scores.get(c[0], 0.0)),
                    "place_score_scaled": place_score,
                    "place_score_adjustment": place_score_adjustment,
                    "order_adjustment": order_adjustment,
                    "approx_prob_base": base_prob,
                    "main_score": main_score,
                    "approx_prob": main_score,
                    "conditional_mode": bool(conditional_ready),
                })

            candidate_count_before_cap = len(race_results)

            # 個数制限 (確率が高い順)
            race_results = self._apply_tiebreak(
                race_results,
                thresh=self.tiebreak_thresh,
                weight=self.tiebreak_weight,
            )
            race_results = self._renormalize_race_probabilities(race_results)

            selection_mode = str(self.candidate_cfg.get("selection_mode", "baseline_top60"))
            if selection_mode == "per_first_m12_global":
                min_per_first = int(self.candidate_cfg.get("per_first_min_per_first", 12))
                fill_mode = str(self.candidate_cfg.get("per_first_fill_mode", "global"))
                race_results = self._select_top60_per_first_pattern(
                    race_results,
                    min_per_first=min_per_first,
                    fill_mode=fill_mode,
                    ensure_min_distinct_first_lanes=int(self.candidate_cfg.get("per_first_min_distinct_first_lanes", 3)),
                )
            else:
                if ignore_race_candidate_limit:
                    race_results = race_results
                else:
                    race_results = race_results[:self.max_trifecta_combinations]
            candidate_count_after_cap = len(race_results)
            first_lane_pool_count = len(top_boats)
            for row in race_results:
                row["top_first_prob"] = top_first_prob
                row["first_prob_relative_threshold"] = self.first_prob_relative_threshold
                row["legacy_first_lane_count"] = legacy_first_lane_count
                row["expanded_first_lane_count"] = first_lane_pool_count
                row["candidate_count_before_cap"] = candidate_count_before_cap
                row["candidate_count_after_cap"] = candidate_count_after_cap
                row["candidate_pool_delta_vs_legacy"] = candidate_count_after_cap - legacy_first_lane_count
            results.extend(race_results)
                
        return pd.DataFrame(results)

if __name__ == "__main__":
    generator = TrifectaGenerator()
    os.makedirs("data/strategy_outputs", exist_ok=True)
    input_path = "data/model_outputs/today_win_proba.csv"
    output_path = "data/strategy_outputs/trifecta_candidates.csv"

    trifectas = generator.generate(input_path)
    trifectas.to_csv(output_path, index=False)
    print(f"Trifecta candidates generated: {output_path}")
