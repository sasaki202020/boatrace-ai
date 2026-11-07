from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

BASE_DIR = Path(".")
SIM_DIR = BASE_DIR / "reports" / "simulator"
WEEKLY_DIR = BASE_DIR / "reports" / "weekly"
MONITOR_DIR = BASE_DIR / "reports" / "monitoring"
ANALYSIS_DIR = BASE_DIR / "reports" / "analysis"
RECOMMEND_DIR = BASE_DIR / "reports" / "recommendations"

st.set_page_config(page_title="BoatRace Simulator Dashboard", layout="wide")


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_date_digits(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def list_files(pattern_dir: Path, glob_pattern: str) -> list[Path]:
    if not pattern_dir.exists():
        return []
    return sorted(pattern_dir.glob(glob_pattern), reverse=True)


@st.cache_data
def load_simulation_summaries() -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    if not SIM_DIR.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "buy_count",
                "hit_count",
                "hit_rate",
                "total_stake",
                "total_payout",
                "total_profit",
                "roi",
                "source_file",
            ]
        )

    for path in SIM_DIR.glob("simulation_summary_*.csv"):
        df = read_csv_if_exists(path)
        if df.empty:
            continue
        df = df.copy()
        if "date" in df.columns:
            df["date"] = df["date"].astype(str).map(normalize_date_digits)
        else:
            df["date"] = normalize_date_digits(path.stem.replace("simulation_summary_", ""))
        df["source_file"] = str(path)
        parts.append(df)

    if not parts:
        return pd.DataFrame(
            columns=[
                "date",
                "buy_count",
                "hit_count",
                "hit_rate",
                "total_stake",
                "total_payout",
                "total_profit",
                "roi",
                "source_file",
            ]
        )

    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return out


@st.cache_data
def load_monitoring_summary() -> pd.DataFrame:
    path = MONITOR_DIR / "daily_monitoring_summary.csv"
    df = read_csv_if_exists(path)
    if df.empty:
        return pd.DataFrame()

    if "date" in df.columns:
        df["date"] = df["date"].astype(str).map(normalize_date_digits)
        df = df[df["date"] != ""].copy()
        df = df[df["date"] != "TOTAL"].copy()

    return df.sort_values("date").reset_index(drop=True)


@st.cache_data
def load_change_log() -> pd.DataFrame:
    path = MONITOR_DIR / "change_log.csv"
    df = read_csv_if_exists(path)
    if df.empty:
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
    if "change_date" in df.columns:
        df["change_date"] = df["change_date"].astype(str)
    return df.sort_values(["change_date", "change_key"]).reset_index(drop=True)


@st.cache_data
def load_latest_weekly_json() -> tuple[Path | None, dict[str, Any]]:
    files = list_files(WEEKLY_DIR, "weekly_report_*.json")
    if not files:
        return None, {}
    path = files[0]
    return path, read_json_if_exists(path)


@st.cache_data
def load_latest_recommendation_json() -> tuple[Path | None, dict[str, Any]]:
    files = list_files(RECOMMEND_DIR, "next_change_*.json")
    if not files:
        return None, {}
    path = files[0]
    return path, read_json_if_exists(path)


@st.cache_data
def load_analysis_json_files() -> list[tuple[Path, dict[str, Any]]]:
    files = list_files(ANALYSIS_DIR, "compare_*.json")
    return [(path, read_json_if_exists(path)) for path in files]


def build_period_summary(sim_df: pd.DataFrame) -> dict[str, Any]:
    if sim_df.empty:
        return {"days": 0, "buy_count": 0, "hit_count": 0, "hit_rate": 0.0, "total_profit": 0, "roi": 0.0}

    buy_count = int(pd.to_numeric(sim_df.get("buy_count", 0), errors="coerce").fillna(0).sum())
    hit_count = int(pd.to_numeric(sim_df.get("hit_count", 0), errors="coerce").fillna(0).sum())
    total_stake = float(pd.to_numeric(sim_df.get("total_stake", 0), errors="coerce").fillna(0).sum())
    total_payout = float(pd.to_numeric(sim_df.get("total_payout", 0), errors="coerce").fillna(0).sum())
    total_profit = int(pd.to_numeric(sim_df.get("total_profit", 0), errors="coerce").fillna(0).sum())
    hit_rate = (hit_count / buy_count) if buy_count > 0 else 0.0
    roi = (total_payout / total_stake) if total_stake > 0 else 0.0

    return {
        "days": int(len(sim_df)),
        "buy_count": buy_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 4),
        "total_profit": total_profit,
        "roi": round(roi, 4),
    }


def filter_date_range(df: pd.DataFrame, date_col: str, start_ymd: str, end_ymd: str) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df.copy()
    out = df.copy()
    out[date_col] = out[date_col].astype(str).map(normalize_date_digits)
    return out[(out[date_col] >= start_ymd) & (out[date_col] <= end_ymd)].copy()


def render_metric_row(summary: dict[str, Any]) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("対象日数", summary.get("days", 0))
    c2.metric("BUY件数", summary.get("buy_count", 0))
    c3.metric("HIT件数", summary.get("hit_count", 0))
    c4.metric("HIT率", summary.get("hit_rate", 0.0))
    c5.metric("利益", summary.get("total_profit", 0))
    c6.metric("ROI", summary.get("roi", 0.0))


def df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows_"
    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = []
    for _, row in df.iterrows():
        values = ["" if pd.isna(row[c]) else str(row[c]) for c in cols]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def main() -> None:
    st.title("競艇売買シミュレータ ダッシュボード")

    sim_df = load_simulation_summaries()
    mon_df = load_monitoring_summary()
    change_df = load_change_log()
    weekly_path, weekly_obj = load_latest_weekly_json()
    recommend_path, recommend_obj = load_latest_recommendation_json()
    analysis_items = load_analysis_json_files()

    all_dates = sorted(set(sim_df["date"].tolist()) | set(mon_df["date"].tolist())) if not sim_df.empty or not mon_df.empty else []
    if all_dates:
        default_start = all_dates[max(0, len(all_dates) - 7)]
        default_end = all_dates[-1]
    else:
        default_start = ""
        default_end = ""

    st.sidebar.header("表示条件")
    start_date = st.sidebar.text_input("開始日 (YYYYMMDD)", value=default_start)
    end_date = st.sidebar.text_input("終了日 (YYYYMMDD)", value=default_end)

    sim_filtered = filter_date_range(sim_df, "date", start_date, end_date) if start_date and end_date else sim_df.copy()
    mon_filtered = filter_date_range(mon_df, "date", start_date, end_date) if start_date and end_date else mon_df.copy()

    st.subheader("期間サマリ")
    render_metric_row(build_period_summary(sim_filtered))

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["日別シミュレーション", "週次レポート", "次の変更提案", "Before / After", "変更履歴"]
    )

    with tab1:
        st.markdown("### 日別シミュレーション")
        if sim_filtered.empty:
            st.info("simulation_summary がありません。")
        else:
            plot_df = sim_filtered.copy()
            plot_df["date"] = pd.to_datetime(plot_df["date"], format="%Y%m%d", errors="coerce")
            st.line_chart(plot_df.set_index("date")[["buy_count", "hit_count"]])
            st.line_chart(plot_df.set_index("date")[["roi"]])
            st.dataframe(sim_filtered, use_container_width=True)

        st.markdown("### 日別モニタリング")
        if mon_filtered.empty:
            st.info("daily_monitoring_summary がありません。")
        else:
            mon_plot = mon_filtered.copy()
            mon_plot["date"] = pd.to_datetime(mon_plot["date"], format="%Y%m%d", errors="coerce")
            chart_cols = [c for c in ["real_odds_available", "pending_unpublished", "buy_count", "buy_hit_rate", "avg_odds"] if c in mon_plot.columns]
            if chart_cols:
                st.line_chart(mon_plot.set_index("date")[chart_cols])
            st.dataframe(mon_filtered, use_container_width=True)

    with tab2:
        st.markdown("### 最新の週次レポート")
        if not weekly_obj:
            st.info("weekly_report がありません。")
        else:
            st.caption(f"source: {weekly_path}")
            kpi = weekly_obj.get("kpi", {})
            total = weekly_obj.get("total", {})
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("週BUY件数", kpi.get("weekly_buy_count", 0))
            c2.metric("週HIT率", kpi.get("weekly_hit_rate", 0.0))
            c3.metric("週ROI", kpi.get("weekly_roi", 0.0))
            c4.metric("週利益", kpi.get("weekly_profit", 0))
            st.markdown("#### KPI")
            st.json(kpi)
            st.markdown("#### Total")
            st.json(total)
            rows = weekly_obj.get("rows", [])
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with tab3:
        st.markdown("### 次の変更提案")
        if not recommend_obj:
            st.info("next_change recommendation がありません。")
        else:
            st.caption(f"source: {recommend_path}")
            rec = recommend_obj.get("recommendation", {})
            ranked = recommend_obj.get("ranked_candidates", [])
            st.success(rec.get("message", "提案なし"))
            action_plan = rec.get("action_plan", {})
            if action_plan:
                st.markdown("#### Action Plan")
                st.json(action_plan)
            if ranked:
                st.markdown("#### Ranked Candidates")
                st.dataframe(pd.DataFrame(ranked), use_container_width=True)

    with tab4:
        st.markdown("### Before / After 比較")
        if not analysis_items:
            st.info("compare_*.json がありません。")
        else:
            labels = [p.name for p, _ in analysis_items]
            selected_label = st.selectbox("比較ファイルを選択", labels, index=0)
            selected_idx = labels.index(selected_label)
            selected_path, selected_obj = analysis_items[selected_idx]
            st.caption(f"source: {selected_path}")
            change_obj = selected_obj.get("change", {})
            before_summary = selected_obj.get("before_summary", {})
            after_summary = selected_obj.get("after_summary", {})
            effect_judgement = selected_obj.get("effect_judgement", "")
            comparison_rows = selected_obj.get("comparison_rows", [])
            c1, c2, c3 = st.columns(3)
            c1.metric("変更キー", change_obj.get("change_key", ""))
            c2.metric("変更日", change_obj.get("change_date", ""))
            c3.metric("判定", effect_judgement)
            st.markdown("#### Change")
            st.json(change_obj)
            st.markdown("#### Before Summary")
            st.json(before_summary)
            st.markdown("#### After Summary")
            st.json(after_summary)
            if comparison_rows:
                st.markdown("#### Comparison")
                st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True)

    with tab5:
        st.markdown("### change_log")
        if change_df.empty:
            st.info("change_log.csv がありません。")
        else:
            st.dataframe(change_df, use_container_width=True)
            if "change_key" in change_df.columns:
                counts = change_df["change_key"].astype(str).value_counts().reset_index()
                counts.columns = ["change_key", "count"]
                st.markdown("#### 変更回数")
                st.dataframe(counts, use_container_width=True)


if __name__ == "__main__":
    main()
