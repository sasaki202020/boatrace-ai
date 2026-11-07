from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_DIAG_DIR = Path("reports/diagnostics")
DEFAULT_OUTPUT_DIR = Path("reports/diagnostics")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integrate approx_prob and rank_model diagnostics into one focus decision"
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--diag-dir", type=Path, default=DEFAULT_DIAG_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def normalize_date_str(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"Invalid date: {value}")
    return digits


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_diag_pair(diag_dir: Path, date_str: str) -> tuple[dict[str, Any], dict[str, Any]]:
    approx_path = diag_dir / f"approx_prob_diagnostic_{date_str}.json"
    rank_path = diag_dir / f"rank_model_diagnostic_{date_str}.json"
    return read_json(approx_path), read_json(rank_path)


def score_approx_focus(approx_obj: dict[str, Any]) -> tuple[float, list[str]]:
    if not approx_obj:
        return 0.0, ["approx_prob診断が存在しない"]

    gs = approx_obj.get("global_summary", {})
    diagnosis = str(approx_obj.get("diagnosis", ""))
    corr = float(gs.get("correlation_prob_hit", 0.0) or 0.0)
    rows = int(gs.get("rows", 0) or 0)

    score = 0.0
    reasons: list[str] = []

    if rows == 0:
        score += 2
        reasons.append("approx_prob診断の有効行が0")
        return score, reasons

    if corr <= 0:
        score += 9
        reasons.append("approx_prob と的中が正相関していない")

    if "正相関していない" in diagnosis:
        score += 8
        reasons.append("診断が確率推定崩れを示唆")

    if "校正" in diagnosis:
        score += 5
        reasons.append("校正崩れの示唆")

    if "微修正向き" in diagnosis:
        score -= 2
        reasons.append("確率推定は完全崩壊ではない")

    return score, reasons


def score_rank_focus(rank_obj: dict[str, Any]) -> tuple[float, list[str]]:
    if not rank_obj:
        return 0.0, ["rank_model診断が存在しない"]

    top1 = rank_obj.get("top1_summary", {})
    err = rank_obj.get("error_profile", {})
    diagnosis = str(rank_obj.get("diagnosis", ""))

    top1_exact = float(top1.get("top1_exact_hit_rate", 0.0) or 0.0)
    top1_first = float(top1.get("top1_first_hit_rate", 0.0) or 0.0)
    first_ok_tail_ng = float(err.get("first_ok_but_tail_ng_rate", 0.0) or 0.0)
    all_included_order_ng = float(err.get("all_boats_included_but_order_ng_rate", 0.0) or 0.0)

    score = 0.0
    reasons: list[str] = []

    if top1_first >= 0.30 and top1_exact < 0.10:
        score += 8
        reasons.append("1着は拾えているが exact が弱い")

    if first_ok_tail_ng >= 0.20:
        score += 7
        reasons.append("1着は合うが2着3着で落としている")

    if all_included_order_ng >= 0.10:
        score += 7
        reasons.append("3艇集合は合うが順番で落としている")

    if "並びが弱い" in diagnosis or "順位モデル改善を優先" in diagnosis:
        score += 8
        reasons.append("診断が順位改善優先を示唆")

    if "微修正向き" in diagnosis:
        score -= 2
        reasons.append("順位モデルは完全崩壊ではない")

    return score, reasons


def score_order_only_focus(rank_obj: dict[str, Any]) -> tuple[float, list[str]]:
    if not rank_obj:
        return 0.0, ["rank_model診断が存在しない"]

    err = rank_obj.get("error_profile", {})
    diagnosis = str(rank_obj.get("diagnosis", ""))
    all_included_order_ng = float(err.get("all_boats_included_but_order_ng_rate", 0.0) or 0.0)

    score = 0.0
    reasons: list[str] = []

    if all_included_order_ng >= 0.15:
        score += 9
        reasons.append("3艇集合は合っているのに順序で落としている比率が高い")

    if "setは合うが順番が弱い" in diagnosis or "order校正" in diagnosis:
        score += 8
        reasons.append("診断がorder校正改善を示唆")

    return score, reasons


def score_data_insufficient(approx_obj: dict[str, Any], rank_obj: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    approx_rows = int(approx_obj.get("global_summary", {}).get("rows", 0) or 0)
    rank_races = int(rank_obj.get("top1_summary", {}).get("race_count", 0) or 0)

    if approx_rows == 0:
        score += 8
        reasons.append("approx_prob の有効データがない")
    if rank_races == 0:
        score += 8
        reasons.append("rank_model の有効レースがない")
    if approx_rows > 0 and approx_rows < 30:
        score += 3
        reasons.append("approx_prob の母数が少ない")
    if rank_races > 0 and rank_races < 10:
        score += 3
        reasons.append("rank_model の母数が少ない")

    return score, reasons


def choose_focus(approx_obj: dict[str, Any], rank_obj: dict[str, Any]) -> dict[str, Any]:
    candidates = []

    approx_score, approx_reasons = score_approx_focus(approx_obj)
    candidates.append({"focus": "approx_prob", "priority_score": round(approx_score, 4), "reasons": approx_reasons})

    rank_score, rank_reasons = score_rank_focus(rank_obj)
    candidates.append({"focus": "rank_model", "priority_score": round(rank_score, 4), "reasons": rank_reasons})

    order_score, order_reasons = score_order_only_focus(rank_obj)
    candidates.append({"focus": "order_calibration", "priority_score": round(order_score, 4), "reasons": order_reasons})

    data_score, data_reasons = score_data_insufficient(approx_obj, rank_obj)
    candidates.append({"focus": "data_insufficient", "priority_score": round(data_score, 4), "reasons": data_reasons})

    ranked = sorted(candidates, key=lambda x: (-x["priority_score"], x["focus"]))
    top = ranked[0]
    action_plan = build_action_plan(top["focus"])

    return {
        "top_focus": top["focus"],
        "top_score": top["priority_score"],
        "top_reasons": top["reasons"],
        "ranked_candidates": ranked,
        "action_plan": action_plan,
        "message": build_message(top["focus"], top["reasons"]),
    }


def build_action_plan(focus: str) -> dict[str, Any]:
    plans = {
        "approx_prob": {
            "what_to_fix": "確率推定または校正",
            "next_step": "approx_prob の特徴量か校正方法を1点だけ修正",
            "do_not_touch": "buy_min_approx_prob の大幅変更",
            "hold_days": 3,
            "success_metric": "correlation_prob_hit 改善 / bin単調性改善 / top-k 命中率改善",
        },
        "rank_model": {
            "what_to_fix": "2着3着順位モデル",
            "next_step": "順位特徴量または順位ロジックを1点だけ修正",
            "do_not_touch": "EV閾値やBUY件数上限の同時変更",
            "hold_days": 3,
            "success_metric": "top1_first_hit_rate 維持以上 / exact_hit_rate 改善 / first_ok_but_tail_ng_rate 低下",
        },
        "order_calibration": {
            "what_to_fix": "3艇の並び替え・order校正",
            "next_step": "候補集合は維持し、順序スコアリングだけ1点改善",
            "do_not_touch": "候補集合生成ロジック全体の大改修",
            "hold_days": 3,
            "success_metric": "set_match_3_rate を維持しつつ exact_hit_rate 改善",
        },
        "data_insufficient": {
            "what_to_fix": "データ量",
            "next_step": "固定運用で3〜7日追加観測",
            "do_not_touch": "ロジックの多重変更",
            "hold_days": 3,
            "success_metric": "比較可能な母数確保",
        },
    }
    return plans.get(
        focus,
        {
            "what_to_fix": "未定",
            "next_step": "1変更だけ実施",
            "do_not_touch": "複数ゲートの同時変更",
            "hold_days": 3,
            "success_metric": "hit_rate / ROI / BUY件数",
        },
    )


def build_message(focus: str, reasons: list[str]) -> str:
    reason_text = " / ".join(reasons) if reasons else "理由なし"
    messages = {
        "approx_prob": f"次に直すべき本丸は approx_prob。理由: {reason_text}",
        "rank_model": f"次に直すべき本丸は 2着3着順位モデル。理由: {reason_text}",
        "order_calibration": f"次に直すべき本丸は order校正。理由: {reason_text}",
        "data_insufficient": f"まだロジック改善より観測継続が先。理由: {reason_text}",
    }
    return messages.get(focus, f"次の改善候補: {focus} / {reason_text}")


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_no candidates_"
    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = []
        for c in cols:
            value = row[c]
            if isinstance(value, list):
                values.append(" / ".join(str(v) for v in value))
            elif isinstance(value, dict):
                values.append(json.dumps(value, ensure_ascii=False))
            elif pd.isna(value):
                values.append("")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def write_markdown_report(
    approx_obj: dict[str, Any],
    rank_obj: dict[str, Any],
    result_obj: dict[str, Any],
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Model Focus Diagnostic Report")
    lines.append("")
    lines.append("## approx_prob Diagnosis")
    lines.append("")
    lines.append(f"- diagnosis: {approx_obj.get('diagnosis')}")
    for k, v in approx_obj.get("global_summary", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## rank_model Diagnosis")
    lines.append("")
    lines.append(f"- diagnosis: {rank_obj.get('diagnosis')}")
    for k, v in rank_obj.get("top1_summary", {}).items():
        lines.append(f"- {k}: {v}")
    for k, v in rank_obj.get("error_profile", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Ranked Focus Candidates")
    lines.append("")
    lines.append(df_to_markdown(pd.DataFrame(result_obj.get("ranked_candidates", []))))
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(f"- top_focus: {result_obj.get('top_focus')}")
    lines.append(f"- top_score: {result_obj.get('top_score')}")
    lines.append(f"- message: {result_obj.get('message')}")
    lines.append("")
    lines.append("## Action Plan")
    lines.append("")
    action_plan = result_obj.get("action_plan", {})
    for k, v in action_plan.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    date_str = normalize_date_str(args.date)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    approx_obj, rank_obj = load_diag_pair(args.diag_dir, date_str)
    result_obj = choose_focus(approx_obj, rank_obj)

    base_name = f"model_focus_{date_str}"
    json_path = args.output_dir / f"{base_name}.json"
    md_path = args.output_dir / f"{base_name}.md"
    csv_path = args.output_dir / f"{base_name}.csv"

    json_path.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(approx_obj, rank_obj, result_obj, md_path)

    ranked_df = pd.DataFrame(result_obj.get("ranked_candidates", []))
    ranked_df.to_csv(csv_path, index=False, encoding="utf-8")

    print("\n=== Model Focus Diagnostic ===")
    print(f"top_focus: {result_obj.get('top_focus')}")
    print(f"top_score: {result_obj.get('top_score')}")
    print(f"message: {result_obj.get('message')}")
    print("\nRanked candidates:")
    print(ranked_df.to_string(index=False) if not ranked_df.empty else "no candidates")
    print("\nSaved:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")


if __name__ == "__main__":
    main()
