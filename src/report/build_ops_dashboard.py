import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


def load_dual_mode_rows(experiments_dir: Path) -> pd.DataFrame:
    files = sorted(experiments_dir.glob("*_dual_mode_summary.json"))
    rows: list[dict] = []
    for fp in files:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_prefix = payload.get("run_prefix", fp.stem.replace("_dual_mode_summary", ""))
        m = re.search(r"(\d{8})", run_prefix)
        run_date = m.group(1) if m else ""
        for r in payload.get("results", []):
            row = dict(r)
            row["run_prefix"] = run_prefix
            row["run_date"] = run_date
            row["source_file"] = fp.name
            rows.append(row)
    if not rows:
        return pd.DataFrame(
            columns=[
                "run_prefix",
                "run_date",
                "window",
                "mode",
                "buy",
                "trifecta_roi",
                "trifecta_hit_rate",
                "exacta_roi",
                "exacta_hit_rate",
            ]
        )
    df = pd.DataFrame(rows)
    for col in ["buy", "trifecta_roi", "trifecta_hit_rate", "exacta_roi", "exacta_hit_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values(["run_date", "window", "mode"], ascending=[False, True, True]).reset_index(drop=True)


def kpi_card(title: str, value: str, note: str = "", good: bool | None = None) -> str:
    tone = "neutral"
    if good is True:
        tone = "good"
    elif good is False:
        tone = "bad"
    return f"""
    <div class="card {tone}">
      <div class="card-title">{title}</div>
      <div class="card-value">{value}</div>
      <div class="card-note">{note}</div>
    </div>
    """


def build_svg_trend(values: list[float], labels: list[str], width: int = 560, height: int = 180) -> str:
    if not values:
        return "<div class='chart-empty'>データなし</div>"

    clean_vals = [float(v) for v in values]
    v_min = min(clean_vals)
    v_max = max(clean_vals)
    if abs(v_max - v_min) < 1e-9:
        v_min -= 0.5
        v_max += 0.5

    pad_l, pad_r, pad_t, pad_b = 36, 12, 14, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(clean_vals)

    def x(i: int) -> float:
        if n == 1:
            return pad_l + plot_w / 2
        return pad_l + (plot_w * i / (n - 1))

    def y(v: float) -> float:
        ratio = (v - v_min) / (v_max - v_min)
        return pad_t + (1.0 - ratio) * plot_h

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(clean_vals))
    x0 = x(0)
    x1 = x(n - 1)
    y_roi1 = y(1.0)

    dots = "\n".join(
        f"<circle cx='{x(i):.1f}' cy='{y(v):.1f}' r='3.2' fill='#1662c4'><title>{labels[i]}: {v:.4f}</title></circle>"
        for i, v in enumerate(clean_vals)
    )
    x_ticks = "\n".join(
        f"<text x='{x(i):.1f}' y='{height - 8}' text-anchor='middle' class='tick'>{labels[i]}</text>"
        for i in range(n)
    )

    return f"""
    <svg viewBox="0 0 {width} {height}" class="trend-svg" role="img" aria-label="ROI trend">
      <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfdff" />
      <line x1="{x0:.1f}" y1="{y_roi1:.1f}" x2="{x1:.1f}" y2="{y_roi1:.1f}" stroke="#c98b8b" stroke-dasharray="4 4"/>
      <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#cfd9e6" />
      <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="#cfd9e6" />
      <polyline points="{pts}" fill="none" stroke="#1662c4" stroke-width="2.4" />
      {dots}
      {x_ticks}
      <text x="6" y="{y_roi1 - 4:.1f}" class="tick">ROI=1.0</text>
    </svg>
    """


def build_html(df: pd.DataFrame) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if df.empty:
        return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>Ops Dashboard</title></head>
<body><h1>運用ダッシュボード</h1><p>データがありません。</p><p>generated_at: {now}</p></body></html>"""

    latest_prefix = df.iloc[0]["run_prefix"]
    latest = df[df["run_prefix"] == latest_prefix].copy()

    def pick(window: str, mode: str) -> pd.Series | None:
        sub = latest[(latest["window"] == window) & (latest["mode"] == mode)]
        return sub.iloc[0] if len(sub) else None

    t_recent = pick("recent30", "trifecta")
    e_recent = pick("recent30", "exacta_filtered")
    t_all = pick("all", "trifecta")
    e_all = pick("all", "exacta_filtered")

    cards = []
    if t_recent is not None:
        cards.append(
            kpi_card(
                "三連単 recent30 ROI",
                f"{t_recent['trifecta_roi']:.3f}",
                f"BUY={int(t_recent['buy'])} / hit={t_recent['trifecta_hit_rate']:.3f}",
                good=bool(t_recent["trifecta_roi"] >= 1.0),
            )
        )
    if e_recent is not None:
        cards.append(
            kpi_card(
                "二連単 recent30 ROI",
                f"{e_recent['exacta_roi']:.3f}",
                f"BUY={int(e_recent['buy'])} / hit={e_recent['exacta_hit_rate']:.3f}",
                good=bool(e_recent["exacta_roi"] >= 1.0),
            )
        )
    if t_all is not None:
        cards.append(
            kpi_card(
                "三連単 all ROI",
                f"{t_all['trifecta_roi']:.3f}",
                f"BUY={int(t_all['buy'])} / hit={t_all['trifecta_hit_rate']:.3f}",
                good=bool(t_all["trifecta_roi"] >= 1.0),
            )
        )
    if e_all is not None:
        cards.append(
            kpi_card(
                "二連単 all ROI",
                f"{e_all['exacta_roi']:.3f}",
                f"BUY={int(e_all['buy'])} / hit={e_all['exacta_hit_rate']:.3f}",
                good=bool(e_all["exacta_roi"] >= 1.0),
            )
        )

    # 7-run moving averages (recent30) for quick stability check.
    hist_for_ma = df.copy()
    hist_for_ma["run_dt"] = pd.to_datetime(hist_for_ma["run_date"], format="%Y%m%d", errors="coerce")
    hist_for_ma = hist_for_ma.dropna(subset=["run_dt"]).sort_values("run_dt")
    tri_recent = hist_for_ma[
        (hist_for_ma["window"] == "recent30") & (hist_for_ma["mode"] == "trifecta")
    ]["trifecta_roi"].tail(7)
    exa_recent = hist_for_ma[
        (hist_for_ma["window"] == "recent30") & (hist_for_ma["mode"] == "exacta_filtered")
    ]["exacta_roi"].tail(7)
    if len(tri_recent) > 0:
        tri_ma = float(pd.to_numeric(tri_recent, errors="coerce").mean())
        cards.append(
            kpi_card(
                "三連単 7-run MA(ROI)",
                f"{tri_ma:.3f}",
                f"recent30 / n={len(tri_recent)}",
                good=bool(tri_ma >= 1.0),
            )
        )
    if len(exa_recent) > 0:
        exa_ma = float(pd.to_numeric(exa_recent, errors="coerce").mean())
        cards.append(
            kpi_card(
                "二連単 7-run MA(ROI)",
                f"{exa_ma:.3f}",
                f"recent30 / n={len(exa_recent)}",
                good=bool(exa_ma >= 1.0),
            )
        )

    # trend series for recent30 by mode
    trend_src = hist_for_ma[hist_for_ma["window"] == "recent30"].copy()
    tri_trend = trend_src[trend_src["mode"] == "trifecta"].sort_values("run_dt")
    exa_trend = trend_src[trend_src["mode"] == "exacta_filtered"].sort_values("run_dt")
    tri_labels = [d.strftime("%m/%d") if pd.notna(d) else "?" for d in tri_trend["run_dt"].tolist()]
    exa_labels = [d.strftime("%m/%d") if pd.notna(d) else "?" for d in exa_trend["run_dt"].tolist()]
    tri_svg = build_svg_trend(pd.to_numeric(tri_trend["trifecta_roi"], errors="coerce").dropna().tolist(), tri_labels[-len(pd.to_numeric(tri_trend["trifecta_roi"], errors="coerce").dropna().tolist()):])
    exa_svg = build_svg_trend(pd.to_numeric(exa_trend["exacta_roi"], errors="coerce").dropna().tolist(), exa_labels[-len(pd.to_numeric(exa_trend["exacta_roi"], errors="coerce").dropna().tolist()):])

    latest_table = latest[
        ["window", "mode", "buy", "trifecta_roi", "trifecta_hit_rate", "exacta_roi", "exacta_hit_rate"]
    ].copy()
    latest_table["buy"] = latest_table["buy"].fillna(0).astype(int)
    for c in ["trifecta_roi", "trifecta_hit_rate", "exacta_roi", "exacta_hit_rate"]:
        latest_table[c] = latest_table[c].map(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
    latest_html = latest_table.to_html(index=False, classes="tbl", border=0)

    hist = df.copy()
    hist["run_date"] = hist["run_date"].replace("", "(unknown)")
    hist["buy"] = hist["buy"].fillna(0).astype(int)
    for c in ["trifecta_roi", "trifecta_hit_rate", "exacta_roi", "exacta_hit_rate"]:
        hist[c] = hist[c].map(lambda x: f"{x:.4f}" if pd.notna(x) else "-")
    hist_html = hist[
        ["run_date", "run_prefix", "window", "mode", "buy", "trifecta_roi", "trifecta_hit_rate", "exacta_roi", "exacta_hit_rate"]
    ].to_html(index=False, classes="tbl", border=0)

    # consecutive alert counts (recent30)
    def consecutive_bad_count(src: pd.DataFrame, window: str, mode: str, metric_col: str) -> int:
        sub = src[(src["window"] == window) & (src["mode"] == mode)].sort_values("run_dt", ascending=False)
        cnt = 0
        for _, r in sub.iterrows():
            v = pd.to_numeric(r.get(metric_col), errors="coerce")
            if pd.isna(v):
                break
            if float(v) < 1.0:
                cnt += 1
            else:
                break
        return cnt

    tri_bad_streak = 0
    exa_bad_streak = 0
    if not hist_for_ma.empty:
        tri_bad_streak = consecutive_bad_count(hist_for_ma, "recent30", "trifecta", "trifecta_roi")
        exa_bad_streak = consecutive_bad_count(hist_for_ma, "recent30", "exacta_filtered", "exacta_roi")

    alert_rows = []
    if tri_bad_streak >= 2:
        alert_rows.append(f"<div class='alert bad'>三連単 recent30 ROI が <b>{tri_bad_streak}連続</b> で 1.0 未満です。</div>")
    else:
        alert_rows.append(f"<div class='alert good'>三連単 recent30 ROI 連続悪化は {tri_bad_streak} 回です。</div>")
    if exa_bad_streak >= 2:
        alert_rows.append(f"<div class='alert bad'>二連単 recent30 ROI が <b>{exa_bad_streak}連続</b> で 1.0 未満です。</div>")
    else:
        alert_rows.append(f"<div class='alert good'>二連単 recent30 ROI 連続悪化は {exa_bad_streak} 回です。</div>")

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BoatRace Ops Dashboard</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1f2a37;
      --muted: #5b6777;
      --line: #d8e0ea;
      --good: #1f8f4e;
      --bad: #b53a3a;
      --accent: #1662c4;
    }}
    body {{
      margin: 0; padding: 24px; background: linear-gradient(180deg, #eef3fb, #f8fafc);
      color: var(--text); font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
    }}
    .wrap {{ max-width: 1280px; margin: 0 auto; }}
    .title {{ font-size: 28px; font-weight: 700; margin: 0 0 6px 0; }}
    .sub {{ color: var(--muted); margin: 0 0 20px 0; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .card {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
      padding: 12px 14px; box-shadow: 0 6px 18px rgba(20, 40, 80, 0.06);
    }}
    .card.good {{ border-left: 6px solid var(--good); }}
    .card.bad {{ border-left: 6px solid var(--bad); }}
    .card.neutral {{ border-left: 6px solid var(--accent); }}
    .card-title {{ font-size: 13px; color: var(--muted); margin-bottom: 4px; }}
    .card-value {{ font-size: 30px; font-weight: 700; line-height: 1.1; }}
    .card-note {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}
    .panel {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin-bottom: 14px;
      box-shadow: 0 6px 18px rgba(20, 40, 80, 0.05);
    }}
    .panel h2 {{ font-size: 18px; margin: 0 0 10px 0; }}
    table.tbl {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    table.tbl th, table.tbl td {{ border-bottom: 1px solid #e7edf5; padding: 7px 8px; text-align: right; }}
    table.tbl th:first-child, table.tbl td:first-child {{ text-align: left; }}
    table.tbl th:nth-child(2), table.tbl td:nth-child(2) {{ text-align: left; }}
    .tips {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px;
    }}
    .alerts {{
      display: grid; grid-template-columns: 1fr; gap: 8px; margin-bottom: 14px;
    }}
    .alert {{
      border-radius: 10px; padding: 10px 12px; font-size: 14px; border: 1px solid var(--line);
      background: #f8fbff;
    }}
    .alert.good {{ border-left: 6px solid var(--good); }}
    .alert.bad {{ border-left: 6px solid var(--bad); background: #fff5f5; border-color: #f0caca; }}
    .tip {{
      background: #f8fbff; border: 1px solid #d5e3f8; border-radius: 10px; padding: 10px;
      font-size: 13px;
    }}
    .trend-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 10px;
    }}
    .trend-box {{
      border: 1px solid #dbe4f0; border-radius: 10px; padding: 8px; background: #ffffff;
    }}
    .trend-title {{ font-size: 13px; color: var(--muted); margin: 0 0 6px 0; }}
    .trend-svg {{ width: 100%; height: auto; display: block; }}
    .tick {{ font-size: 10px; fill: #5c6c80; }}
    .chart-empty {{ color: var(--muted); font-size: 12px; padding: 8px; }}
    .footer {{ color: var(--muted); font-size: 12px; margin-top: 8px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1 class="title">BoatRace 運用ダッシュボード</h1>
    <p class="sub">latest run: <b>{latest_prefix}</b> / generated at: {now}</p>
    <div class="cards">
      {''.join(cards)}
    </div>
    <div class="alerts">
      {''.join(alert_rows)}
    </div>
    <div class="panel">
      <h2>最新ラン（モード比較）</h2>
      {latest_html}
    </div>
    <div class="panel">
      <h2>履歴（dual mode summary）</h2>
      {hist_html}
    </div>
    <div class="panel">
      <h2>ROI推移（recent30）</h2>
      <div class="trend-grid">
        <div class="trend-box">
          <p class="trend-title">三連単 ROI 推移</p>
          {tri_svg}
        </div>
        <div class="trend-box">
          <p class="trend-title">二連単 ROI 推移</p>
          {exa_svg}
        </div>
      </div>
    </div>
    <div class="panel">
      <h2>運用ガイド（判断しやすいUI向け）</h2>
      <div class="tips">
        <div class="tip"><b>三連単モード:</b> ROI>=1 を維持できている間は現行継続。in_candidates_rate 低下が続くときだけ見直し。</div>
        <div class="tip"><b>二連単モード:</b> BUY件数が少ないので、7〜14日移動平均でROI判定。単日で閾値は触らない。</div>
        <div class="tip"><b>切替ルール:</b> recent30 の exacta_roi が 1.0 を下回る状態が 2週間続いたら二連単停止。</div>
      </div>
    </div>
    <div class="footer">
      source: reports/experiments/*_dual_mode_summary.json
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static HTML ops dashboard from dual-mode summaries.")
    parser.add_argument("--experiments-dir", default="reports/experiments")
    parser.add_argument("--output", default="reports/dashboard/ops_dashboard.html")
    args = parser.parse_args()

    experiments_dir = Path(args.experiments_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = load_dual_mode_rows(experiments_dir)
    html = build_html(df)
    output_path.write_text(html, encoding="utf-8")

    print(f"[saved] {output_path}")
    print(f"[rows] {len(df)}")


if __name__ == "__main__":
    main()
