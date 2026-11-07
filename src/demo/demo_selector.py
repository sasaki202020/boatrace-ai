from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


DECISION_LABELS = {
    "BUY": "購入候補",
    "WATCH": "様子見",
    "SKIP": "見送り",
    "PENDING": "様子見",
}

DECISION_PRIORITY = {
    "BUY": 0,
    "WATCH": 1,
    "PENDING": 1,
    "SKIP": 2,
}

QUALITY_DECISION_ORDER = ["buy_candidate", "watch_candidate", "weak_candidate"]
EXECUTION_STATUS_ORDER = ["tradable", "missing_odds", "suspicious_odds"]
FINAL_DECISION_LABELS = {
    ("buy_candidate", "tradable"): "BUY",
    ("buy_candidate", "missing_odds"): "BUY候補（未取得）",
    ("buy_candidate", "suspicious_odds"): "BUY候補（要確認）",
    ("watch_candidate", "tradable"): "WATCH",
    ("watch_candidate", "missing_odds"): "WATCH（未取得）",
    ("watch_candidate", "suspicious_odds"): "WATCH（要確認）",
    ("weak_candidate", "tradable"): "SKIP",
    ("weak_candidate", "missing_odds"): "SKIP",
    ("weak_candidate", "suspicious_odds"): "SKIP",
}

FINAL_REJECT_REASON_ORDER = [
    "missing_odds",
    "risk_flag",
    "below_min_win_proba",
    "below_min_ev",
    "above_max_ev",
]


@dataclass(frozen=True)
class DemoSummary:
    target_date: str
    generated_at: str
    model_name: str
    feature_set_name: str
    decision_source: str
    判定レース数: int
    購入候補数: int
    様子見数: int
    見送り数: int
    オッズ取得率: float
    データ警告: str


@dataclass(frozen=True)
class DemoDecisionConfig:
    buy_min_prob: float = 0.07
    buy_min_ev: float = 1.0
    buy_max_ev: float = 10.0
    watch_min_prob: float = 0.03
    watch_min_ev: float = 0.5
    watch_no_odds_min_prob: float = 0.05
    watch_no_odds_min_ev: float = 1.0


def _series_or_default(frame: pd.DataFrame, column_name: str, default: str = "") -> pd.Series:
    if column_name in frame.columns:
        return frame[column_name]
    return pd.Series(default, index=frame.index, dtype="object")


def _normalize_selection_work(ev_df: pd.DataFrame) -> pd.DataFrame:
    work = ev_df.copy()
    work = work.sort_values(
        ["race_id", "sort_score", "ev", "approx_prob"],
        ascending=[True, False, False, False],
    ).groupby("race_id", as_index=False).head(1).copy()

    work["approx_prob"] = pd.to_numeric(work.get("approx_prob"), errors="coerce").fillna(0.0)
    work["ev"] = pd.to_numeric(work.get("ev"), errors="coerce").fillna(0.0)
    work["sort_score"] = pd.to_numeric(work.get("sort_score"), errors="coerce").fillna(0.0)
    work["odds"] = pd.to_numeric(work.get("odds"), errors="coerce")
    work["has_real_odds"] = work.get("has_real_odds", False).fillna(False).astype(bool)
    work["risk_flag"] = work.get("risk_flag", False).fillna(False).astype(bool)
    return work


def _quality_decision(row: pd.Series, config: DemoDecisionConfig) -> str:
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)
    has_real_odds = bool(row.get("has_real_odds", False))

    if approx_prob >= config.buy_min_prob and ev_value >= config.buy_min_ev and ev_value <= config.buy_max_ev:
        return "buy_candidate"
    if has_real_odds:
        if approx_prob >= config.watch_min_prob or ev_value >= config.watch_min_ev:
            return "watch_candidate"
    else:
        if approx_prob >= config.watch_no_odds_min_prob and ev_value >= config.watch_no_odds_min_ev:
            return "watch_candidate"
    return "weak_candidate"


def _execution_status(row: pd.Series) -> str:
    if not bool(row.get("has_real_odds", False)):
        return "missing_odds"
    if bool(row.get("risk_flag", False)):
        return "suspicious_odds"
    return "tradable"


def _final_decision_label(quality_decision: str, execution_status: str) -> str:
    return FINAL_DECISION_LABELS.get((quality_decision, execution_status), "SKIP")


def _canonical_decision(final_decision: str) -> str:
    if final_decision.startswith("BUY"):
        return "BUY"
    if final_decision.startswith("WATCH"):
        return "WATCH"
    return "SKIP"


def _collect_buy_reject_reasons(row: pd.Series, config: DemoDecisionConfig) -> list[str]:
    reasons: list[str] = []
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)
    has_real_odds = bool(row.get("has_real_odds", False))
    risk_flag = bool(row.get("risk_flag", False))

    if not has_real_odds:
        reasons.append("missing_odds")
    if risk_flag:
        reasons.append("risk_flag")
    if approx_prob < config.buy_min_prob:
        reasons.append("below_min_win_proba")
    if ev_value < config.buy_min_ev:
        reasons.append("below_min_ev")
    if ev_value > config.buy_max_ev:
        reasons.append("above_max_ev")
    return reasons


def _final_reject_reason(row: pd.Series, config: DemoDecisionConfig) -> str:
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)
    has_real_odds = bool(row.get("has_real_odds", False))
    risk_flag = bool(row.get("risk_flag", False))

    if not has_real_odds:
        return "missing_odds"
    if risk_flag:
        return "risk_flag"
    if approx_prob < config.buy_min_prob:
        return "below_min_win_proba"
    if ev_value < config.buy_min_ev:
        return "below_min_ev"
    if ev_value > config.buy_max_ev:
        return "above_max_ev"
    return ""


def _parse_risk_labels(risk_labels: Any) -> list[str]:
    if risk_labels is None:
        return []
    if isinstance(risk_labels, list):
        return [str(item) for item in risk_labels if str(item).strip()]
    text = str(risk_labels).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(" / ") if part.strip()]


def _categorize_risk_reason(row: pd.Series) -> str:
    labels = set(_parse_risk_labels(row.get("risk_labels")))
    odds_status = str(row.get("odds_status", row.get("odds_raw_status", "")) or "").strip()
    odds_source = str(row.get("odds_source", row.get("odds_target_source", "")) or "").strip()

    if "実オッズ未取得" in labels or odds_status not in {"real", "real_odds", "available"}:
        return "risk_suspicious_odds"
    if "データ欠損あり" in labels or "DATA_MISSING" in labels:
        return "risk_missing_feature"
    if "高配当で変動大" in labels or "HIGH_ODDS_VOLATILE" in labels:
        return "risk_high_ev"
    if "学習根拠が弱い" in labels or "LOW_SAMPLE_MODEL" in labels:
        return "risk_other"
    if "予測信頼度低" in labels or "LOW_CONFIDENCE" in labels:
        return "risk_other"
    if odds_source and odds_source not in {"real"} and "実オッズ未取得" in labels:
        return "risk_suspicious_odds"
    return "risk_other"


def _decision_without_odds_gate(row: pd.Series, config: DemoDecisionConfig) -> str:
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)
    risk_flag = bool(row.get("risk_flag", False))
    if not risk_flag and approx_prob >= config.buy_min_prob and ev_value >= config.buy_min_ev and ev_value <= config.buy_max_ev:
        return "BUY"
    if approx_prob >= config.watch_min_prob or ev_value >= config.watch_min_ev:
        return "WATCH"
    return "SKIP"


def _contains_suspicious_odds_label(labels: list[str]) -> bool:
    normalized = {str(label).strip() for label in labels if str(label).strip()}
    return bool(
        normalized.intersection(
            {
                "高配当で変動大",
                "HIGH_ODDS_VOLATILE",
                "risk_suspicious_odds",
            }
        )
    )


def _decision_without_risk_suspicious_odds(row: pd.Series, config: DemoDecisionConfig) -> str:
    labels = _parse_risk_labels(row.get("risk_labels"))
    remaining_labels = [label for label in labels if label not in {"高配当で変動大", "HIGH_ODDS_VOLATILE", "risk_suspicious_odds"}]
    risk_flag = bool(remaining_labels)
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)
    has_real_odds = bool(row.get("has_real_odds", False))

    if has_real_odds and not risk_flag and approx_prob >= config.buy_min_prob and ev_value >= config.buy_min_ev and ev_value <= config.buy_max_ev:
        return "BUY"
    if not has_real_odds and approx_prob >= config.watch_no_odds_min_prob and ev_value >= config.watch_no_odds_min_ev:
        return "WATCH"
    if has_real_odds and (approx_prob >= config.watch_min_prob or ev_value >= config.watch_min_ev):
        return "WATCH"
    return "SKIP"


def _buy_promotion_diagnostics_reason(row: pd.Series, config: DemoDecisionConfig) -> str:
    has_real_odds = bool(row.get("has_real_odds", False))
    risk_flag = bool(row.get("risk_flag", False))
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)

    if not has_real_odds:
        return "missing_odds"
    if risk_flag:
        return "risk_flag"
    if approx_prob < config.buy_min_prob:
        return "score_below_buy_band"
    if ev_value < config.buy_min_ev:
        return "expected_value_not_strong_enough"
    if ev_value > config.buy_max_ev:
        return "above_max_ev"
    return "other"


def _buy_promotion_eligibility_score(row: pd.Series, config: DemoDecisionConfig) -> float:
    has_real_odds = bool(row.get("has_real_odds", False))
    risk_flag = bool(row.get("risk_flag", False))
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)

    prob_score = min(1.0, approx_prob / config.buy_min_prob) if config.buy_min_prob > 0 else 0.0
    ev_floor_score = min(1.0, ev_value / config.buy_min_ev) if config.buy_min_ev > 0 else 0.0
    ev_ceiling_score = min(1.0, config.buy_max_ev / ev_value) if ev_value > config.buy_max_ev and ev_value > 0 else 1.0
    odds_score = 1.0 if has_real_odds else 0.35
    risk_score = 1.0 if not risk_flag else 0.35
    return float((prob_score + ev_floor_score + ev_ceiling_score + odds_score + risk_score) / 5.0)


def _buy_promotion_counterfactual(row: pd.Series, config: DemoDecisionConfig) -> str:
    reason = _buy_promotion_diagnostics_reason(row, config)
    if reason == "missing_odds":
        return "odds_available_then_recheck"
    if reason == "risk_flag":
        return "remove_risk_flag_then_recheck"
    if reason == "score_below_buy_band":
        return f"raise_min_win_proba_to_{float(row.get('approx_prob', 0.0) or 0.0):.3f}"
    if reason == "expected_value_not_strong_enough":
        return f"lower_min_ev_to_{float(row.get('ev', 0.0) or 0.0):.3f}"
    if reason == "above_max_ev":
        return f"raise_max_ev_to_{float(row.get('ev', 0.0) or 0.0):.3f}"
    return "requires_multiple_relaxations"


def _scenario_decision(
    row: pd.Series,
    config: DemoDecisionConfig,
    *,
    include_missing_odds_gate: bool,
    include_risk_flag_gate: bool,
) -> str:
    has_real_odds = bool(row.get("has_real_odds", False))
    risk_flag = bool(row.get("risk_flag", False)) if include_risk_flag_gate else False
    approx_prob = float(row.get("approx_prob", 0.0) or 0.0)
    ev_value = float(row.get("ev", 0.0) or 0.0)

    if include_missing_odds_gate:
        if has_real_odds and not risk_flag and approx_prob >= config.buy_min_prob and ev_value >= config.buy_min_ev and ev_value <= config.buy_max_ev:
            return "BUY"
        if not has_real_odds and approx_prob >= config.watch_no_odds_min_prob and ev_value >= config.watch_no_odds_min_ev:
            return "WATCH"
        if has_real_odds and (approx_prob >= config.watch_min_prob or ev_value >= config.watch_min_ev):
            return "WATCH"
        return "SKIP"

    if not risk_flag and approx_prob >= config.buy_min_prob and ev_value >= config.buy_min_ev and ev_value <= config.buy_max_ev:
        return "BUY"
    if approx_prob >= config.watch_min_prob or ev_value >= config.watch_min_ev:
        return "WATCH"
    return "SKIP"


def build_demo_selector_gate_ablation(
    ev_df: pd.DataFrame,
    *,
    target_date: date,
    config: DemoDecisionConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if config is None:
        config = DemoDecisionConfig()

    work = _apply_demo_decisions(ev_df, config=config)
    scenarios = [
        ("current", True, True),
        ("without_missing_odds_gate", False, True),
        ("without_risk_flag_gate", True, False),
        ("without_missing_odds_and_risk_flag", False, False),
    ]

    scenario_results: list[dict[str, Any]] = []
    scenario_columns: dict[str, pd.Series] = {}
    current_decisions = work["decision"].astype(str)

    for scenario_name, include_missing_odds_gate, include_risk_flag_gate in scenarios:
        decisions = work.apply(
            lambda row: _scenario_decision(
                row,
                config,
                include_missing_odds_gate=include_missing_odds_gate,
                include_risk_flag_gate=include_risk_flag_gate,
            ),
            axis=1,
        ).astype(str)
        scenario_columns[scenario_name] = decisions
        buy_count = int((decisions == "BUY").sum())
        watch_count = int((decisions == "WATCH").sum())
        skip_count = int((decisions == "SKIP").sum())

        promoted_to_buy = work.loc[(current_decisions != "BUY") & (decisions == "BUY"), "race_id"].astype(str).tolist()
        promoted_to_watch = work.loc[(current_decisions == "SKIP") & (decisions == "WATCH"), "race_id"].astype(str).tolist()
        affected_race_ids = work.loc[current_decisions.ne(decisions), "race_id"].astype(str).tolist()

        scenario_results.append(
            {
                "scenario": scenario_name,
                "include_missing_odds_gate": include_missing_odds_gate,
                "include_risk_flag_gate": include_risk_flag_gate,
                "buy_count": buy_count,
                "watch_count": watch_count,
                "skip_count": skip_count,
                "promoted_to_buy_cases": promoted_to_buy,
                "promoted_to_watch_cases": promoted_to_watch,
                "affected_race_ids": affected_race_ids,
            }
        )

    ablation_df = pd.DataFrame(
        {
            "race_id": work["race_id"].astype(str),
            "race_label": work["race_id"].astype(str).str.extract(r"^\d{8}-(\d+)-(\d+)$").apply(
                lambda row: f"{int(row[0]):02d}R{int(row[1])}" if pd.notna(row[0]) and pd.notna(row[1]) else "",
                axis=1,
            ),
            "buy_combo": _series_or_default(work, "buy_combo", _series_or_default(work, "trifecta", "")).astype(str),
            "current_decision": current_decisions,
            "without_missing_odds_gate": scenario_columns["without_missing_odds_gate"],
            "without_risk_flag_gate": scenario_columns["without_risk_flag_gate"],
            "without_missing_odds_and_risk_flag": scenario_columns["without_missing_odds_and_risk_flag"],
        }
    )
    ablation_df = ablation_df.sort_values(["race_id"]).reset_index(drop=True)

    summary = {
        "target_date": target_date.isoformat(),
        "model_name": str(work["model_name"].iloc[0]) if "model_name" in work.columns and not work.empty else "",
        "feature_set_name": str(work["feature_set_name"].iloc[0]) if "feature_set_name" in work.columns and not work.empty else "",
        "decision_source": str(work["decision_source"].iloc[0]) if "decision_source" in work.columns and not work.empty else "",
        "scenarios": [item[0] for item in scenarios],
        "scenario_results": scenario_results,
        "notes": [
            "current は現行 selector の結果です。",
            "without_missing_odds_gate は実オッズ取得ゲートのみを無効化した反実仮想です。",
            "without_risk_flag_gate は risk_flag ゲートのみを無効化した反実仮想です。",
            "without_missing_odds_and_risk_flag は両ゲートを同時に無効化した反実仮想です。",
            "selector 本体の仕様は変更していません。",
        ],
    }
    return ablation_df, summary


def _apply_demo_decisions(
    ev_df: pd.DataFrame,
    *,
    config: DemoDecisionConfig,
) -> pd.DataFrame:
    work = _normalize_selection_work(ev_df)

    quality_decisions: list[str] = []
    execution_statuses: list[str] = []
    final_decisions: list[str] = []
    decisions: list[str] = []
    reasons: list[str] = []
    for row in work.itertuples(index=False):
        row_series = row._asdict() if hasattr(row, "_asdict") else row
        quality_decision = _quality_decision(row_series, config)
        execution_status = _execution_status(row_series)
        final_decision = _final_decision_label(quality_decision, execution_status)
        decision = _canonical_decision(final_decision)

        quality_decisions.append(quality_decision)
        execution_statuses.append(execution_status)
        final_decisions.append(final_decision)
        decisions.append(decision)
        reasons.append(
            _demo_reason(
                decision=decision,
                has_real_odds=bool(row.has_real_odds),
                risk_flag=bool(row.risk_flag),
                approx_prob=float(row.approx_prob),
                ev_value=float(row.ev),
            )
        )

    work["quality_decision"] = quality_decisions
    work["execution_status"] = execution_statuses
    work["final_decision"] = final_decisions
    work["decision"] = decisions
    work["decision_label"] = work["decision"].map(DECISION_LABELS).fillna("見送り")
    work["decision_priority"] = work["decision"].map(DECISION_PRIORITY).fillna(9)
    work["decision_reason"] = reasons
    work["reject_reasons"] = [
        _collect_buy_reject_reasons(row._asdict() if hasattr(row, "_asdict") else row, config)
        for row in work.itertuples(index=False)
    ]
    work["final_reject_reason"] = [
        _final_reject_reason(row._asdict() if hasattr(row, "_asdict") else row, config)
        for row in work.itertuples(index=False)
    ]
    work["総合評価"] = work["sort_score"].round(4)
    work["期待値"] = work["ev"].round(4)
    work["的中見込み"] = work["approx_prob"].round(4)
    work["推奨購入額"] = [
        _estimate_bet_amount(decision, float(prob), float(ev_value))
        for decision, prob, ev_value in zip(work["decision"], work["approx_prob"], work["ev"])
    ]
    work["オッズ"] = work["odds"].round(1)
    work["オッズ取得"] = work["has_real_odds"]
    risk_series = work["risk_labels"] if "risk_labels" in work.columns else pd.Series("", index=work.index, dtype="object")
    work["リスク"] = risk_series.fillna("").astype(str)
    work.loc[work["リスク"].eq(""), "リスク"] = "なし"
    work["場コード"] = work["race_id"].astype(str).str.extract(r"^\d{8}-(\d+)-", expand=False).fillna("")
    work["レース番号"] = pd.to_numeric(
        work["race_id"].astype(str).str.extract(r"-(\d+)$", expand=False),
        errors="coerce",
    ).fillna(0).astype(int)
    work["データ警告"] = ""
    work.loc[~work["オッズ取得"], "データ警告"] = "オッズ未取得"
    return work


def _localized_demo_frame(work: pd.DataFrame, *, target_date: date) -> pd.DataFrame:
    work = work.copy()
    work["予測日"] = target_date.isoformat()

    localized = pd.DataFrame(
        {
            "予測日": work["予測日"],
            "レースID": work["race_id"].astype(str),
            "場コード": work["場コード"],
            "レース番号": work["レース番号"],
            "normalized_win_probability": pd.to_numeric(
                work.get("normalized_win_probability", work.get("first_win_proba", work.get("p_win_norm", 0.0))),
                errors="coerce",
            ).fillna(0.0).round(6),
              "model_name": _series_or_default(work, "model_name").fillna("").astype(str),
              "feature_set_name": _series_or_default(work, "feature_set_name").fillna("").astype(str),
              "decision_source": _series_or_default(work, "decision_source").fillna("").astype(str),
              "quality_decision": _series_or_default(work, "quality_decision").fillna("").astype(str),
              "execution_status": _series_or_default(work, "execution_status").fillna("").astype(str),
              "final_decision": _series_or_default(work, "final_decision").fillna("").astype(str),
              "判定": work["decision_label"],
              "総合評価": work["総合評価"].round(4),
              "期待値": work["期待値"].round(4),
              "的中見込み": work["的中見込み"].round(4),
            "推奨購入額": work["推奨購入額"].round(0).astype(int),
            "オッズ": work["オッズ"].round(1),
            "オッズ取得": work["オッズ取得"].map(lambda v: "取得済み" if v else "未取得"),
            "リスク": work["リスク"],
            "推奨組番": work.get("trifecta", work.get("recommended_trifecta", "")).fillna("").astype(str),
            "1着候補": work.get("first_lane", "").fillna(""),
            "2着候補": work.get("second_lane", "").fillna(""),
            "3着候補": work.get("third_lane", "").fillna(""),
            "理由": work["decision_reason"],
            "データ警告": work["データ警告"],
        }
    )
    localized["_decision_priority"] = work["decision_priority"].to_numpy()
    localized = localized.sort_values(
        ["_decision_priority", "総合評価", "期待値", "的中見込み", "レースID"],
        ascending=[True, False, False, False, True],
    ).drop(columns=["_decision_priority"]).reset_index(drop=True)
    return localized


def _estimate_bet_amount(decision: str, approx_prob: float, ev_value: float) -> int:
    if decision != "BUY":
        return 0
    raw = max(1000.0, min(3000.0, approx_prob * max(ev_value, 1.0) * 10000.0))
    return int(round(raw / 100.0) * 100)


def _demo_reason(*, decision: str, has_real_odds: bool, risk_flag: bool, approx_prob: float, ev_value: float) -> str:
    parts = [f"1着見込み {approx_prob:.3f}", f"期待値 {ev_value:.3f}"]
    if not has_real_odds:
        parts.append("実オッズ未取得のため参考判定")
    if risk_flag:
        parts.append("リスクありのため慎重運用")
    if decision == "BUY":
        parts.append("購入候補ラインを通過")
    elif decision == "WATCH":
        parts.append("条件が弱いため様子見")
    else:
        parts.append("見送り条件に該当")
    return " / ".join(parts)


def select_demo_predictions(
    ev_df: pd.DataFrame,
    *,
    target_date: date,
    config: DemoDecisionConfig | None = None,
) -> pd.DataFrame:
    if ev_df.empty:
        return pd.DataFrame(
            columns=[
                "予測日",
                "レースID",
                "場コード",
                "レース番号",
                "normalized_win_probability",
                "model_name",
                "feature_set_name",
                "decision_source",
                "quality_decision",
                "execution_status",
                "final_decision",
                "判定",
                "総合評価",
                "期待値",
                "的中見込み",
                "推奨購入額",
                "オッズ",
                "オッズ取得",
                "リスク",
                "推奨組番",
                "1着候補",
                "2着候補",
                "3着候補",
                "理由",
                "データ警告",
            ]
        )

    if config is None:
        config = DemoDecisionConfig()
    work = _apply_demo_decisions(ev_df, config=config)
    return _localized_demo_frame(work, target_date=target_date)


def build_demo_summary(
    predictions_df: pd.DataFrame,
    *,
    target_date: date,
    generated_at: str,
    stale_warning: str,
    model_name: str,
    feature_set_name: str,
    decision_source: str,
) -> dict[str, Any]:
    def _count_column(column_name: str, value: str) -> int:
        if predictions_df.empty or column_name not in predictions_df.columns:
            return 0
        return int((predictions_df[column_name].astype(str) == value).sum())

    buy_count = _count_column("判定", "購入候補")
    watch_count = _count_column("判定", "様子見")
    skip_count = _count_column("判定", "見送り")
    odds_rate = 0.0
    if not predictions_df.empty:
        odds_rate = float((predictions_df["オッズ取得"] == "取得済み").mean())

    quality_decision_counts = {
        key: int((predictions_df["quality_decision"].astype(str) == key).sum()) if not predictions_df.empty and "quality_decision" in predictions_df.columns else 0
        for key in QUALITY_DECISION_ORDER
    }
    execution_status_counts = {
        key: int((predictions_df["execution_status"].astype(str) == key).sum()) if not predictions_df.empty and "execution_status" in predictions_df.columns else 0
        for key in EXECUTION_STATUS_ORDER
    }
    final_decision_counts: dict[str, int] = {}
    if not predictions_df.empty and "final_decision" in predictions_df.columns:
        for value in predictions_df["final_decision"].astype(str).tolist():
            final_decision_counts[value] = final_decision_counts.get(value, 0) + 1

    featured = []
    if not predictions_df.empty:
        featured_df = predictions_df.head(3).copy()
        featured = featured_df.to_dict(orient="records")

    summary = DemoSummary(
        target_date=target_date.isoformat(),
        generated_at=generated_at,
        model_name=model_name,
        feature_set_name=feature_set_name,
        decision_source=decision_source,
        判定レース数=int(len(predictions_df)),
        購入候補数=buy_count,
        様子見数=watch_count,
        見送り数=skip_count,
        オッズ取得率=round(odds_rate, 4),
        データ警告=stale_warning,
    )
    payload = {
        "予測日": summary.target_date,
        "generated_at": summary.generated_at,
        "model_name": summary.model_name,
        "feature_set_name": summary.feature_set_name,
        "decision_source": summary.decision_source,
        "判定レース数": summary.判定レース数,
        "購入候補数": summary.購入候補数,
        "様子見数": summary.様子見数,
        "見送り数": summary.見送り数,
        "オッズ取得率": summary.オッズ取得率,
        "データ警告": summary.データ警告,
        "注目レース": featured,
        "quality_decision_counts": quality_decision_counts,
        "execution_status_counts": execution_status_counts,
        "final_decision_counts": final_decision_counts,
        "quality_candidate_count": int(quality_decision_counts.get("buy_candidate", 0) + quality_decision_counts.get("watch_candidate", 0)),
        "execution_tradable_count": int(execution_status_counts.get("tradable", 0)),
        "execution_missing_odds_count": int(execution_status_counts.get("missing_odds", 0)),
        "execution_suspicious_odds_count": int(execution_status_counts.get("suspicious_odds", 0)),
    }
    return payload


def build_demo_diff_report(
    *,
    previous_summary: dict[str, Any] | None,
    previous_predictions: pd.DataFrame | None,
    current_summary: dict[str, Any],
    current_predictions: pd.DataFrame,
) -> dict[str, Any]:
    prev_buy = int(previous_summary.get("購入候補数", 0)) if previous_summary else 0
    prev_watch = int(previous_summary.get("様子見数", 0)) if previous_summary else 0
    prev_skip = int(previous_summary.get("見送り数", 0)) if previous_summary else 0
    curr_buy = int(current_summary.get("購入候補数", 0))
    curr_watch = int(current_summary.get("様子見数", 0))
    curr_skip = int(current_summary.get("見送り数", 0))

    def _top_races(df: pd.DataFrame | None) -> list[str]:
        if df is None or df.empty:
            return []
        col = "レースID" if "レースID" in df.columns else "race_id"
        return df.head(3)[col].astype(str).tolist()

    previous_top = _top_races(previous_predictions)
    current_top = _top_races(current_predictions)
    previous_model_name = ""
    if previous_summary:
        previous_model_name = str(previous_summary.get("model_name", "") or "")
        if not previous_model_name:
            source_paths = previous_summary.get("source_paths", {})
            if isinstance(source_paths, dict):
                model_path = source_paths.get("model", "")
                if model_path:
                    previous_model_name = Path(str(model_path)).stem
    current_model_name = str(current_summary.get("model_name", "") or "")
    return {
        "previous_model_name": previous_model_name,
        "current_model_name": current_model_name,
        "previous_feature_set_name": previous_summary.get("feature_set_name") if previous_summary else "",
        "current_feature_set_name": current_summary.get("feature_set_name", ""),
        "buy_count_delta": curr_buy - prev_buy,
        "watch_count_delta": curr_watch - prev_watch,
        "skip_count_delta": curr_skip - prev_skip,
        "top_races_changed": {
            "previous_top_races": previous_top,
            "current_top_races": current_top,
            "added": [race for race in current_top if race not in previous_top],
            "removed": [race for race in previous_top if race not in current_top],
        },
        "notes": [
            f"予測ソースは {current_summary.get('decision_source', '')}。",
            "比較対象は直前に保存されていたデモ出力。",
        ],
    }


def build_demo_selector_diagnostics(
    ev_df: pd.DataFrame,
    *,
    target_date: date,
    config: DemoDecisionConfig | None = None,
) -> dict[str, Any]:
    if config is None:
        config = DemoDecisionConfig()

    work = _apply_demo_decisions(ev_df, config=config)
    total_races = int(len(work))
    buy_count = int((work["decision"] == "BUY").sum())
    watch_count = int((work["decision"] == "WATCH").sum())
    skip_count = int((work["decision"] == "SKIP").sum())

    rejected_by_reason: dict[str, int] = {
        "below_min_win_proba": 0,
        "below_min_ev": 0,
        "above_max_ev": 0,
        "risk_flag": 0,
        "missing_odds": 0,
    }
    for reasons in work["reject_reasons"]:
        unique_reasons = list(dict.fromkeys(reasons))
        for reason in unique_reasons:
            if reason in rejected_by_reason:
                rejected_by_reason[reason] += 1

    final_rejected_by_reason: dict[str, int] = {
        reason: int((work["final_reject_reason"] == reason).sum())
        for reason in FINAL_REJECT_REASON_ORDER
    }

    def _example_row(row: dict[str, Any], *, include_risk: bool = False) -> dict[str, Any]:
        payload = {
            "race_id": str(row.get("race_id", "")),
            "race_label": f"{row.get('場コード', '')}R{int(row.get('レース番号', 0) or 0)}",
            "win_proba": float(row.get("normalized_win_probability", row.get("approx_prob", 0.0)) or 0.0),
            "ev": float(row.get("ev", 0.0) or 0.0),
            "reject_reasons": list(row.get("reject_reasons", [])) if isinstance(row.get("reject_reasons", []), list) else [],
            "final_reject_reason": str(row.get("final_reject_reason", "") or ""),
        }
        if include_risk:
            payload["risk_flag_reason"] = str(row.get("risk_flag_reason", ""))
            payload["risk_labels"] = list(row.get("risk_labels_list", []))
        return payload

    work["risk_labels_list"] = work.get("risk_labels", pd.Series("", index=work.index)).map(_parse_risk_labels)
    work["risk_flag_reason"] = work.apply(_categorize_risk_reason, axis=1)
    risk_flag_reason_counts = {
        reason: int(
            (
                work.loc[work["final_reject_reason"].eq("risk_flag"), "risk_flag_reason"]
                == reason
            ).sum()
        )
        for reason in ["risk_high_ev", "risk_missing_feature", "risk_suspicious_odds", "risk_other"]
    }
    risk_flag_examples = [
        _example_row(row._asdict(), include_risk=True)
        for row in work.loc[work["final_reject_reason"].eq("risk_flag")].sort_values(
            ["normalized_win_probability", "ev", "sort_score"],
            ascending=[False, False, False],
        ).head(10).itertuples(index=False)
    ]

    final_reject_examples = [
        _example_row(row._asdict())
        for row in work.loc[work["final_reject_reason"].ne("")].sort_values(
            ["normalized_win_probability", "ev", "sort_score"],
            ascending=[False, False, False],
        ).head(10).itertuples(index=False)
    ]

    non_buy = work.loc[work["decision"] != "BUY"].copy()
    if not non_buy.empty:
        non_buy["_diag_score"] = (
            pd.to_numeric(
                non_buy["normalized_win_probability"]
                if "normalized_win_probability" in non_buy.columns
                else non_buy["approx_prob"],
                errors="coerce",
            ).fillna(0.0)
            * 1000.0
            + pd.to_numeric(non_buy["ev"] if "ev" in non_buy.columns else 0.0, errors="coerce").fillna(0.0) * 100.0
            - non_buy["reject_reasons"].map(lambda items: len(items) if isinstance(items, list) else 0) * 5.0
            + non_buy["has_real_odds"].astype(int) * 10.0
        )
        nearest_examples = [
            _example_row(row._asdict())
            for row in non_buy.sort_values(
                ["_diag_score", "approx_prob", "ev", "sort_score"],
                ascending=[False, False, False, False],
            ).head(5).itertuples(index=False)
        ]
    else:
        nearest_examples = []

    watch_rows = work.loc[work["decision"] == "WATCH"].copy()
    if not watch_rows.empty:
        top_watch_candidates = [
            _example_row(row._asdict())
            for row in watch_rows.sort_values(
                ["sort_score", "approx_prob", "ev"],
                ascending=[False, False, False],
            ).head(5).itertuples(index=False)
        ]
    else:
        top_watch_candidates = []

    with_odds_final_outcomes = {
        decision: int((work.loc[work["has_real_odds"], "decision"] == decision).sum())
        for decision in ["BUY", "WATCH", "SKIP"]
    }
    without_odds_final_outcomes = {
        decision: int((work.loc[~work["has_real_odds"], "decision"] == decision).sum())
        for decision in ["BUY", "WATCH", "SKIP"]
    }

    thresholds = [0.27, 0.25, 0.23, 0.21]

    def _simulate_thresholds(frame: pd.DataFrame) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        buy_by_threshold: dict[str, int] = {}
        watch_by_threshold: dict[str, int] = {}
        skip_by_threshold: dict[str, int] = {}
        nearest_examples_by_threshold: dict[str, list[dict[str, Any]]] = {}

        for threshold in thresholds:
            local_config = DemoDecisionConfig(
                buy_min_prob=threshold,
                buy_min_ev=config.buy_min_ev,
                buy_max_ev=config.buy_max_ev,
                watch_min_prob=config.watch_min_prob,
                watch_min_ev=config.watch_min_ev,
                watch_no_odds_min_prob=config.watch_no_odds_min_prob,
                watch_no_odds_min_ev=config.watch_no_odds_min_ev,
            )
            local_work = _apply_demo_decisions(frame, config=local_config)
            key = f"{threshold:.2f}"
            buy_by_threshold[key] = int((local_work["decision"] == "BUY").sum())
            watch_by_threshold[key] = int((local_work["decision"] == "WATCH").sum())
            skip_by_threshold[key] = int((local_work["decision"] == "SKIP").sum())
            results.append(
                {
                    "threshold": threshold,
                    "buy_count": buy_by_threshold[key],
                    "watch_count": watch_by_threshold[key],
                    "skip_count": skip_by_threshold[key],
                    "final_reject_reason_counts": {
                        reason: int((local_work["final_reject_reason"] == reason).sum())
                        for reason in FINAL_REJECT_REASON_ORDER
                    },
                }
            )

            non_buy_local = local_work.loc[local_work["decision"] != "BUY"].copy()
            if non_buy_local.empty:
                nearest_examples_by_threshold[key] = []
            else:
                non_buy_local["_diag_score"] = (
                    pd.to_numeric(
                        non_buy_local["normalized_win_probability"]
                        if "normalized_win_probability" in non_buy_local.columns
                        else non_buy_local["approx_prob"],
                        errors="coerce",
                    ).fillna(0.0)
                    * 1000.0
                    + pd.to_numeric(non_buy_local["ev"] if "ev" in non_buy_local.columns else 0.0, errors="coerce").fillna(0.0)
                    * 100.0
                    - non_buy_local["final_reject_reason"].ne("").astype(int) * 5.0
                    + non_buy_local["has_real_odds"].astype(int) * 10.0
                )
                nearest_examples_by_threshold[key] = [
                    _example_row(row._asdict())
                    for row in non_buy_local.sort_values(
                        ["_diag_score", "approx_prob", "ev", "sort_score"],
                        ascending=[False, False, False, False],
                    ).head(3).itertuples(index=False)
                ]

        return {
            "results": results,
            "buy_count_by_threshold": buy_by_threshold,
            "watch_count_by_threshold": watch_by_threshold,
            "skip_count_by_threshold": skip_by_threshold,
            "nearest_to_buy_examples_by_threshold": nearest_examples_by_threshold,
        }

    all_sensitivity = _simulate_thresholds(work)
    odds_only_sensitivity = _simulate_thresholds(work.loc[work["has_real_odds"]].copy())

    missing_odds_rows = work.loc[work["final_reject_reason"].eq("missing_odds")].copy()
    counterfactual_work = missing_odds_rows.copy()
    if not counterfactual_work.empty:
        counterfactual_work["has_real_odds"] = True
        counterfactual_work["odds_status"] = "real"
        counterfactual_work["odds_source"] = "real"
        counterfactual_work["odds_raw_status"] = "real"
        counterfactual_work["decision_without_odds_gate"] = counterfactual_work.apply(
            lambda row: _decision_without_odds_gate(row, config),
            axis=1,
        )
    else:
        counterfactual_work["decision_without_odds_gate"] = pd.Series(dtype="object")

    missing_odds_counterfactual_examples = []
    if not counterfactual_work.empty:
        for row in counterfactual_work.sort_values(
            ["normalized_win_probability", "ev", "sort_score"],
            ascending=[False, False, False],
        ).head(10).itertuples(index=False):
            row_dict = row._asdict()
            missing_odds_counterfactual_examples.append(
                {
                    "race_id": str(row_dict.get("race_id", "")),
                    "race_label": f"{row_dict.get('場コード', '')}R{int(row_dict.get('レース番号', 0) or 0)}",
                    "win_proba": float(row_dict.get("normalized_win_probability", row_dict.get("approx_prob", 0.0)) or 0.0),
                    "ev": float(row_dict.get("ev", 0.0) or 0.0),
                    "original_decision": str(row_dict.get("decision", "")),
                    "counterfactual_decision": str(row_dict.get("decision_without_odds_gate", "")),
                    "reject_reasons": list(row_dict.get("reject_reasons", [])) if isinstance(row_dict.get("reject_reasons", []), list) else [],
                }
            )

    counterfactual_payload = {
        "model_name": str(work["model_name"].iloc[0]) if "model_name" in work.columns and not work.empty else "",
        "feature_set_name": str(work["feature_set_name"].iloc[0]) if "feature_set_name" in work.columns and not work.empty else "",
        "decision_source": str(work["decision_source"].iloc[0]) if "decision_source" in work.columns and not work.empty else "",
        "missing_odds_total": int(len(missing_odds_rows)),
        "would_be_buy_if_odds_present": int((counterfactual_work["decision_without_odds_gate"] == "BUY").sum()) if not counterfactual_work.empty else 0,
        "would_be_watch_if_odds_present": int((counterfactual_work["decision_without_odds_gate"] == "WATCH").sum()) if not counterfactual_work.empty else 0,
        "would_be_skip_if_odds_present": int((counterfactual_work["decision_without_odds_gate"] == "SKIP").sum()) if not counterfactual_work.empty else 0,
        "missing_odds_counterfactual_examples": missing_odds_counterfactual_examples,
        "notes": [
            "missing_odds は実オッズ取得ゲートの反実仮想であり、実オッズ値は捏造していません。",
            "counterfactual_decision は has_real_odds=True として他条件のみで再判定した結果です。",
        ],
    }

    model_name = str(work["model_name"].iloc[0]) if "model_name" in work.columns and not work.empty else ""
    feature_set_name = str(work["feature_set_name"].iloc[0]) if "feature_set_name" in work.columns and not work.empty else ""
    decision_source = str(work["decision_source"].iloc[0]) if "decision_source" in work.columns and not work.empty else ""

    return {
        "target_date": target_date.isoformat(),
        "model_name": model_name,
        "feature_set_name": feature_set_name,
        "decision_source": decision_source,
        "total_races": total_races,
        "buy_count": buy_count,
        "watch_count": watch_count,
        "skip_count": skip_count,
        "rejected_by_reason": rejected_by_reason,
        "final_rejected_by_reason": final_rejected_by_reason,
        "final_reject_reason_counts": final_rejected_by_reason,
        "final_reject_examples": final_reject_examples,
        "risk_flag_reason_counts": risk_flag_reason_counts,
        "risk_flag_examples": risk_flag_examples,
        "with_odds_final_outcomes": with_odds_final_outcomes,
        "without_odds_final_outcomes": without_odds_final_outcomes,
        "nearest_to_buy_examples": nearest_examples,
        "top_watch_candidates": top_watch_candidates,
        "sensitivity": {
            "thresholds_tested": thresholds,
            "results_all": all_sensitivity["results"],
            "results_with_odds_only": odds_only_sensitivity["results"],
            "buy_count_by_threshold": {
                "all": all_sensitivity["buy_count_by_threshold"],
                "odds_only": odds_only_sensitivity["buy_count_by_threshold"],
            },
            "watch_count_by_threshold": {
                "all": all_sensitivity["watch_count_by_threshold"],
                "odds_only": odds_only_sensitivity["watch_count_by_threshold"],
            },
            "skip_count_by_threshold": {
                "all": all_sensitivity["skip_count_by_threshold"],
                "odds_only": odds_only_sensitivity["skip_count_by_threshold"],
            },
            "nearest_to_buy_examples_by_threshold": {
                "all": all_sensitivity["nearest_to_buy_examples_by_threshold"],
                "odds_only": odds_only_sensitivity["nearest_to_buy_examples_by_threshold"],
            },
        },
        "notes": [
            "rejected_by_reason は同一候補に複数理由が重複計上されうる参考値です。",
            "final_reject_reason は BUY 判定の順序に従う排他的な最終落選理由です。",
            "risk_flag_reason_counts は risk_flag 候補の内訳です。",
            "sensitivity は min_win_proba のみを動かし、min_ev / max_ev / risk_flag は固定しています。",
            "results_with_odds_only は実オッズ取得済み候補だけを対象にしています。",
        ],
        "counterfactual": counterfactual_payload,
    }


def build_demo_risk_suspicious_odds_cases(
    ev_df: pd.DataFrame,
    *,
    target_date: date,
    config: DemoDecisionConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if config is None:
        config = DemoDecisionConfig()

    work = _apply_demo_decisions(ev_df, config=config)
    work["risk_labels_list"] = work.get("risk_labels", pd.Series("", index=work.index)).map(_parse_risk_labels)
    work["risk_flag_reason"] = work.apply(_categorize_risk_reason, axis=1)
    cases = work.loc[
        work["final_reject_reason"].eq("risk_flag") & work["risk_flag_reason"].eq("risk_suspicious_odds")
    ].copy()

    if cases.empty:
        columns = [
            "race_id",
            "race_label",
            "buy_combo",
            "normalized_win_probability",
            "ev",
            "real_odds",
            "odds_status",
            "risk_labels",
            "final_reject_reason",
            "current_decision",
            "counterfactual_decision_without_risk_suspicious_odds",
        ]
        empty_df = pd.DataFrame(columns=columns)
        summary = {
            "target_date": target_date.isoformat(),
            "total_cases": 0,
            "current_buy_count": 0,
            "counterfactual_buy_count_without_risk_suspicious_odds": 0,
            "current_watch_count": 0,
            "counterfactual_watch_count_without_risk_suspicious_odds": 0,
            "notes": [
                "risk_suspicious_odds に該当するケースはありませんでした。",
                "selector ロジックは変更していません。",
            ],
        }
        return empty_df, summary

    cases["counterfactual_decision_without_risk_suspicious_odds"] = cases.apply(
        lambda row: _decision_without_risk_suspicious_odds(row, config),
        axis=1,
    )

    cases_df = pd.DataFrame(
        {
            "race_id": cases["race_id"].astype(str),
            "race_label": cases["場コード"].astype(str) + "R" + pd.to_numeric(cases["レース番号"], errors="coerce").fillna(0).astype(int).astype(str),
            "buy_combo": (
                cases["推奨組番"]
                if "推奨組番" in cases.columns
                else cases["recommended_trifecta"]
                if "recommended_trifecta" in cases.columns
                else cases["trifecta"]
                if "trifecta" in cases.columns
                else pd.Series("", index=cases.index)
            ).fillna("").astype(str),
            "normalized_win_probability": pd.to_numeric(
                cases.get("normalized_win_probability", cases.get("approx_prob", 0.0)),
                errors="coerce",
            ).fillna(0.0),
            "ev": pd.to_numeric(cases.get("ev", 0.0), errors="coerce").fillna(0.0),
            "real_odds": pd.to_numeric(cases.get("odds", 0.0), errors="coerce").fillna(0.0),
            "odds_status": (
                cases["odds_status"]
                if "odds_status" in cases.columns
                else cases["odds_raw_status"]
                if "odds_raw_status" in cases.columns
                else pd.Series("", index=cases.index)
            ).fillna("").astype(str),
            "risk_labels": pd.Series(
                [" / ".join(labels) if labels else str(reason) for labels, reason in zip(cases["risk_labels_list"], cases["risk_flag_reason"], strict=False)],
                index=cases.index,
                dtype="object",
            ).fillna("").astype(str),
            "final_reject_reason": cases["final_reject_reason"].astype(str),
            "current_decision": cases["decision"].astype(str),
            "counterfactual_decision_without_risk_suspicious_odds": cases["counterfactual_decision_without_risk_suspicious_odds"].astype(str),
        }
    ).sort_values(["normalized_win_probability", "ev", "race_id"], ascending=[False, False, True]).reset_index(drop=True)

    summary = {
        "target_date": target_date.isoformat(),
        "total_cases": int(len(cases_df)),
        "current_buy_count": int((cases_df["current_decision"] == "BUY").sum()),
        "counterfactual_buy_count_without_risk_suspicious_odds": int(
            (cases_df["counterfactual_decision_without_risk_suspicious_odds"] == "BUY").sum()
        ),
        "current_watch_count": int((cases_df["current_decision"] == "WATCH").sum()),
        "counterfactual_watch_count_without_risk_suspicious_odds": int(
            (cases_df["counterfactual_decision_without_risk_suspicious_odds"] == "WATCH").sum()
        ),
        "notes": [
            "対象は final_reject_reason=risk_flag かつ risk_suspicious_odds を含む候補のみです。",
            "counterfactual は risk_suspicious_odds だけを取り除き、他条件は変更していません。",
        ],
    }
    return cases_df, summary


def build_demo_buy_promotion_diagnostics(
    ev_df: pd.DataFrame,
    *,
    target_date: date,
    config: DemoDecisionConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if config is None:
        config = DemoDecisionConfig()

    work = _apply_demo_decisions(ev_df, config=config)
    watch = work.loc[work["decision"].eq("WATCH")].copy()

    if watch.empty:
        columns = [
            "race_id",
            "race_label",
            "buy_combo",
            "normalized_win_probability",
            "ev",
            "real_odds",
            "current_decision",
            "buy_eligibility_score",
            "distance_to_buy",
            "why_not_buy",
            "counterfactual_to_buy",
        ]
        empty_df = pd.DataFrame(columns=columns)
        summary = {
            "target_date": target_date.isoformat(),
            "total_watch_cases": 0,
            "why_not_buy_counts": {},
            "nearest_to_buy_watch_cases": [],
            "notes": [
                "WATCH 候補がありませんでした。",
                "selector ロジックは変更していません。",
            ],
        }
        return empty_df, summary

    watch["why_not_buy"] = watch.apply(lambda row: _buy_promotion_diagnostics_reason(row, config), axis=1)
    watch["buy_eligibility_score"] = watch.apply(lambda row: _buy_promotion_eligibility_score(row, config), axis=1)
    watch["distance_to_buy"] = (1.0 - watch["buy_eligibility_score"]).clip(lower=0.0)
    watch["counterfactual_to_buy"] = watch.apply(lambda row: _buy_promotion_counterfactual(row, config), axis=1)
    watch["buy_blockers"] = watch["reject_reasons"].apply(lambda items: list(items) if isinstance(items, list) else [])
    watch["buy_promotion_gap"] = watch["distance_to_buy"] + watch["buy_blockers"].map(len).fillna(0).astype(float) * 0.1

    def _example_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "race_id": str(row.get("race_id", "")),
            "race_label": str(row.get("race_label", "")) or f"{row.get('場コード', '')}R{int(row.get('レース番号', 0) or 0)}",
            "buy_combo": str(
                row.get("buy_combo")
                or row.get("推奨組番")
                or row.get("recommended_trifecta")
                or row.get("trifecta")
                or ""
            ),
            "normalized_win_probability": float(row.get("normalized_win_probability", row.get("approx_prob", 0.0)) or 0.0),
            "ev": float(row.get("ev", 0.0) or 0.0),
            "real_odds": float(row.get("odds", 0.0) or 0.0),
            "current_decision": str(row.get("current_decision", row.get("decision", ""))),
            "buy_eligibility_score": float(row.get("buy_eligibility_score", 0.0) or 0.0),
            "distance_to_buy": float(row.get("distance_to_buy", 0.0) or 0.0),
            "why_not_buy": str(row.get("why_not_buy", "")),
            "counterfactual_to_buy": str(row.get("counterfactual_to_buy", "")),
        }

    watch_df = pd.DataFrame(
        {
            "race_id": watch["race_id"].astype(str),
            "race_label": watch["場コード"].astype(str) + "R" + pd.to_numeric(watch["レース番号"], errors="coerce").fillna(0).astype(int).astype(str),
            "buy_combo": (
                watch["推奨組番"]
                if "推奨組番" in watch.columns
                else watch["recommended_trifecta"]
                if "recommended_trifecta" in watch.columns
                else watch["trifecta"]
                if "trifecta" in watch.columns
                else pd.Series("", index=watch.index)
            ).fillna("").astype(str),
            "normalized_win_probability": pd.to_numeric(
                watch.get("normalized_win_probability", watch.get("approx_prob", 0.0)),
                errors="coerce",
            ).fillna(0.0),
            "ev": pd.to_numeric(watch.get("ev", 0.0), errors="coerce").fillna(0.0),
            "real_odds": pd.to_numeric(watch.get("odds", 0.0), errors="coerce").fillna(0.0),
            "current_decision": watch["decision"].astype(str),
            "buy_eligibility_score": pd.to_numeric(watch["buy_eligibility_score"], errors="coerce").fillna(0.0),
            "distance_to_buy": pd.to_numeric(watch["distance_to_buy"], errors="coerce").fillna(0.0),
            "why_not_buy": watch["why_not_buy"].astype(str),
            "counterfactual_to_buy": watch["counterfactual_to_buy"].astype(str),
            "buy_blockers": pd.Series(
                [" / ".join(items) if items else "" for items in watch["buy_blockers"]],
                index=watch.index,
                dtype="object",
            ).fillna("").astype(str),
        }
    ).sort_values(
        ["buy_eligibility_score", "distance_to_buy", "normalized_win_probability", "ev", "race_id"],
        ascending=[False, True, False, False, True],
    ).reset_index(drop=True)

    why_not_buy_counts = {
        reason: int((watch_df["why_not_buy"] == reason).sum())
        for reason in [
            "missing_odds",
            "risk_flag",
            "score_below_buy_band",
            "expected_value_not_strong_enough",
            "above_max_ev",
            "other",
        ]
    }
    why_not_buy_counts = {key: value for key, value in why_not_buy_counts.items() if value > 0}

    nearest_examples = [
        _example_row(row._asdict())
        for row in watch_df.sort_values(
            ["buy_eligibility_score", "distance_to_buy", "normalized_win_probability", "ev", "race_id"],
            ascending=[False, True, False, False, True],
        ).head(5).itertuples(index=False)
    ]

    summary = {
        "target_date": target_date.isoformat(),
        "total_watch_cases": int(len(watch_df)),
        "why_not_buy_counts": why_not_buy_counts,
        "nearest_to_buy_watch_cases": nearest_examples,
        "notes": [
            "対象は current_decision=WATCH の候補のみです。",
            "why_not_buy は現在の selector ロジックに沿って BUY へ上がれない主因を表します。",
            "counterfactual_to_buy は selector を変更せず、どの条件を少し緩めれば BUY に近づくかを示す診断です。",
        ],
    }
    return watch_df, summary
