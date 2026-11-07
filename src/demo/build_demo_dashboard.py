from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def _safe_load_json(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _build_featured_cards(featured_rows: list[dict]) -> str:
    if not featured_rows:
        return "<p class='empty'>注目レースはありません。</p>"
    cards: list[str] = []
    for row in featured_rows:
        cards.append(
            f"""
            <div class="featured-card">
              <div class="featured-title">{row.get('レースID', '-')} / {row.get('final_decision', row.get('判定', '-'))}</div>
              <div class="featured-main">推奨組番: {row.get('推奨組番', '-')}</div>
              <div class="featured-meta">quality: {row.get('quality_decision', '-')} / execution: {row.get('execution_status', '-')}</div>
              <div class="featured-meta">総合評価 {row.get('総合評価', '-')} / 期待値 {row.get('期待値', '-')} / 的中見込み {row.get('的中見込み', '-')}</div>
              <div class="featured-reason">{row.get('理由', '')}</div>
            </div>
            """
        )
    return "".join(cards)


def _count_or_zero(mapping: dict, key: str) -> int:
    value = mapping.get(key, 0)
    try:
        return int(value)
    except Exception:
        return 0


def _render_toolbar(title: str, items: list[tuple[str, str, bool]]) -> str:
    buttons = []
    for label, tone, active in items:
        active_class = " active" if active else ""
        tone_class = f" {tone}" if tone else ""
        buttons.append(f"<span class='toolbar-button{tone_class}{active_class}'>{label}</span>")
    return f"""
    <div class="toolbar">
      <div class="toolbar-title">{title}</div>
      <div class="toolbar-buttons">{''.join(buttons)}</div>
    </div>
    """


def _render_empty_state(title: str, message: str) -> str:
    return f"""
    <div class="empty-state">
      <div class="empty-state-title">{title}</div>
      <div class="empty-state-body">{message}</div>
    </div>
    """


def _render_summary_row(title: str, text: str) -> str:
    return f"""
    <div class="mini-card">
      <div class="mini-title">{title}</div>
      <div class="mini-value mini-value-wrap">{text}</div>
    </div>
    """


def _build_predictions_table(predictions_df: pd.DataFrame) -> str:
    if predictions_df.empty:
        return _render_empty_state(
            "予測一覧はまだありません",
            "予測を実行すると、ここにレースごとの判定一覧が表示されます。",
        )
    table_html = predictions_df.to_html(index=False, classes="demo-table", border=0, escape=False)
    return f"<div class='table-wrap'>{table_html}</div>"


def build_dashboard_html(summary: dict, predictions_df: pd.DataFrame) -> str:
    warning = str(summary.get("データ警告", "") or "")
    warning_block = ""
    if warning:
        warning_block = f"<div class='warning'>注意: {warning}</div>"

    featured_rows = list(summary.get("注目レース", []))
    featured_html = _build_featured_cards(featured_rows)
    quality_counts = dict(summary.get("quality_decision_counts", {}))
    execution_counts = dict(summary.get("execution_status_counts", {}))
    final_counts = dict(summary.get("final_decision_counts", {}))
    quality_candidate_count = int(summary.get("quality_candidate_count", 0) or 0)
    execution_tradable_count = int(summary.get("execution_tradable_count", 0) or 0)
    execution_missing_count = int(summary.get("execution_missing_odds_count", 0) or 0)
    execution_suspicious_count = int(summary.get("execution_suspicious_odds_count", 0) or 0)
    artifact_paths = dict(summary.get("artifact_paths", {}))
    odds_diag_path = ROOT / "reports" / "demo" / str(summary.get("予測日", "")).replace("-", "") / "demo_odds_pipeline_diagnostics.json"
    odds_diag = _safe_load_json(odds_diag_path)
    selector_diag = _safe_load_json(Path(artifact_paths.get("selector_diagnostics_json", ""))) if artifact_paths.get("selector_diagnostics_json") else {}
    odds_missing_reasons = dict(odds_diag.get("missing_reason_counts", {}))
    if not featured_rows:
        featured_html = _render_empty_state(
            "注目レースはまだありません",
            "まだ予測結果がありません。予測を実行すると、ここに注目レースが表示されます。",
        )
    table_html = _build_predictions_table(predictions_df)

    if execution_tradable_count == 0:
        if execution_missing_count > 0:
            reason_text = "候補はありますが、オッズ未取得のため購入判定できません。"
            if odds_missing_reasons.get("odds_race_id_mismatch", 0):
                reason_text += " 主な原因: race_id 不一致。"
        elif execution_suspicious_count > 0:
            reason_text = "候補はありますが、オッズ要確認のため実行可能に入っていません。"
            if odds_missing_reasons.get("odds_race_id_mismatch", 0):
                reason_text += " 主な原因: race_id 不一致。"
            elif selector_diag.get("risk_flag_reason_counts"):
                reason_text += " 主な原因: risk_suspicious_odds。"
        else:
            reason_text = "候補はありますが、実行可能レースがありません。"
        status_block = f"<div class='status-note'>{reason_text}</div>"
    else:
        status_block = "<div class='status-note ok'>実行可能な候補があります。</div>"

    venue_summary_html = _render_empty_state(
        "場別サマリーは準備中",
        "今回は場別グラフを出していません。データが揃うと、この領域に場ごとの集計が表示されます。",
    )
    win_view_html = _render_empty_state(
        "勝率ビューは準備中",
        "今回は装飾チャートを追加していません。必要な時だけ、予測と実行可能性の状態を見せる構成にしています。",
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>競艇AI デモダッシュボード</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d9e2ec;
      --text: #16202a;
      --muted: #5f6b78;
      --buy: #137333;
      --watch: #9a6700;
      --skip: #b42318;
      --accent: #175cd3;
      --accent-2: #0f766e;
      --soft-bg: #f8fafc;
    }}
    body {{
      margin: 0;
      background: linear-gradient(180deg, #edf2f8 0%, #f8fafc 100%);
      color: var(--text);
      font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
    }}
    .wrap {{
      max-width: 1360px;
      margin: 0 auto;
      padding: 24px;
    }}
    .hero {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
      margin-bottom: 16px;
    }}
    .hero h1 {{
      margin: 0 0 8px 0;
      font-size: 28px;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }}
    .hero-pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      color: var(--muted);
      background: #f8fafc;
    }}
    .warning {{
      margin-top: 14px;
      background: #fff4e5;
      border: 1px solid #f7c97c;
      color: #8a4b00;
      padding: 12px;
      border-radius: 8px;
      font-size: 14px;
    }}
    .status-note {{
      margin-top: 12px;
      background: #f8fafc;
      border: 1px solid var(--line);
      color: var(--muted);
      padding: 12px 14px;
      border-radius: 8px;
      font-size: 14px;
      line-height: 1.6;
    }}
    .status-note.ok {{
      background: #ecfdf3;
      border-color: #a6e3b2;
      color: #135d2a;
    }}
    .toolbar {{
      display: flex;
      gap: 12px;
      align-items: flex-start;
      justify-content: space-between;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft-bg);
      margin-bottom: 12px;
      flex-wrap: wrap;
    }}
    .toolbar-title {{
      font-size: 13px;
      color: var(--muted);
      min-width: 130px;
      padding-top: 4px;
    }}
    .toolbar-buttons {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .toolbar-button {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      padding: 8px 12px;
      font-size: 12px;
      line-height: 1;
      white-space: nowrap;
    }}
    .toolbar-button.active {{
      border-color: var(--accent);
      color: var(--accent);
      background: #eff6ff;
    }}
    .toolbar-button.secondary.active {{
      border-color: var(--accent-2);
      color: var(--accent-2);
      background: #ecfeff;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .kpi-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
    }}
    .kpi-title {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .kpi-value {{
      font-size: 28px;
      font-weight: 700;
    }}
    .subgrid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .mini-card {{
      background: #fbfdff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .mini-title {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .mini-value {{
      font-size: 20px;
      font-weight: 700;
    }}
    .mini-value-wrap {{
      font-size: 14px;
      line-height: 1.6;
      word-break: break-word;
    }}
    .section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
      margin-bottom: 16px;
    }}
    .section h2 {{
      margin: 0 0 12px 0;
      font-size: 18px;
    }}
    .featured-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}
    .featured-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfdff;
    }}
    .featured-title {{
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .featured-main {{
      font-size: 16px;
      margin-bottom: 6px;
    }}
    .featured-meta, .featured-reason {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }}
    .demo-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      min-width: 980px;
    }}
    .demo-table th, .demo-table td {{
      border-bottom: 1px solid #e7edf3;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }}
    .demo-table th {{
      background: #f8fafc;
      position: sticky;
      top: 0;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .footer {{
      color: var(--muted);
      font-size: 12px;
    }}
    .empty {{
      color: var(--muted);
      margin: 0;
    }}
    .empty-state {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      background: #fbfdff;
      color: var(--muted);
    }}
    .empty-state-title {{
      font-weight: 700;
      color: var(--text);
      margin-bottom: 6px;
    }}
    .empty-state-body {{
      line-height: 1.7;
      font-size: 14px;
    }}
    .decision-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    @media (max-width: 768px) {{
      .wrap {{
        padding: 14px;
      }}
      .hero h1 {{
        font-size: 22px;
      }}
      .kpi-grid, .subgrid, .featured-grid, .decision-summary {{
        grid-template-columns: 1fr;
      }}
      .toolbar {{
        padding: 10px 12px;
      }}
      .toolbar-title {{
        min-width: 100%;
      }}
      .section {{
        padding: 14px;
      }}
      .demo-table {{
        min-width: 860px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>競艇AI リプレイデモ</h1>
      <p>予測日: {summary.get('予測日', '-')} / 生成時刻: {summary.get('generated_at', '-')}</p>
      <div class="hero-meta">
        <span class="hero-pill">使用モデル: {summary.get('model_name', '-')}</span>
        <span class="hero-pill">feature set: {summary.get('feature_set_name', '-')}</span>
        <span class="hero-pill">予測ソース: {summary.get('decision_source', '-')}</span>
      </div>
      {warning_block}
      {status_block}
    </div>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-title">判定レース数</div><div class="kpi-value">{summary.get('判定レース数', 0)}</div></div>
      <div class="kpi-card"><div class="kpi-title">質としての候補数</div><div class="kpi-value">{quality_candidate_count}</div></div>
      <div class="kpi-card"><div class="kpi-title">実行可能</div><div class="kpi-value">{execution_tradable_count}</div></div>
      <div class="kpi-card"><div class="kpi-title">未取得</div><div class="kpi-value">{execution_missing_count}</div></div>
      <div class="kpi-card"><div class="kpi-title">要確認</div><div class="kpi-value">{execution_suspicious_count}</div></div>
      <div class="kpi-card"><div class="kpi-title">オッズ取得率</div><div class="kpi-value">{_format_pct(float(summary.get('オッズ取得率', 0.0) or 0.0))}</div></div>
    </div>
    {_render_toolbar("表示モード", [("標準", "", True), ("勝率重視", "", False), ("期待値重視", "", False)])}
    {_render_toolbar("実行アクション", [("予測を実行", "secondary", True), ("オッズ更新", "secondary", False), ("デモ再生", "secondary", False)])}
    <div class="subgrid">
      {_render_summary_row("quality_decision", f"buy { _count_or_zero(quality_counts, 'buy_candidate') } / watch { _count_or_zero(quality_counts, 'watch_candidate') } / weak { _count_or_zero(quality_counts, 'weak_candidate') }")}
      {_render_summary_row("execution_status", f"tradable { _count_or_zero(execution_counts, 'tradable') } / missing { _count_or_zero(execution_counts, 'missing_odds') } / suspicious { _count_or_zero(execution_counts, 'suspicious_odds') }")}
      {_render_summary_row("final_decision", f"BUY { _count_or_zero(final_counts, 'BUY') } / BUY候補（未取得） { _count_or_zero(final_counts, 'BUY候補（未取得）') } / WATCH { _count_or_zero(final_counts, 'WATCH') }")}
    </div>
    <div class="section">
      <h2>判定の見え方</h2>
      <div class="decision-summary">
        <div class="mini-card"><div class="mini-title">質としての判定</div><div class="mini-value mini-value-wrap">候補の強さだけを見た分類です。</div></div>
        <div class="mini-card"><div class="mini-title">実行可能性の判定</div><div class="mini-value mini-value-wrap">オッズ取得 / 要確認 / 実行可能を切り分けます。</div></div>
        <div class="mini-card"><div class="mini-title">最終判定</div><div class="mini-value mini-value-wrap">質と実行可能性を合わせて表示します。</div></div>
      </div>
    </div>
    <div class="section">
      <h2>注目レース3件</h2>
      <div class="featured-grid">{featured_html}</div>
    </div>
    <div class="section">
      <h2>場別サマリー</h2>
      {venue_summary_html}
    </div>
    <div class="section">
      <h2>勝率ビュー</h2>
      {win_view_html}
    </div>
    <div class="section">
      <h2>判定一覧</h2>
      {table_html}
    </div>
    <div class="footer">出力: demo_summary.json / demo_predictions.csv / dashboard.html</div>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a replay demo dashboard HTML.")
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--predictions-path", required=True)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args()

    summary_path = Path(args.summary_path)
    predictions_path = Path(args.predictions_path)
    output_path = Path(args.output_path)

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    predictions_df = pd.read_csv(predictions_path, low_memory=False)
    html = build_dashboard_html(summary, predictions_df)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[saved] {output_path}")


if __name__ == "__main__":
    main()
