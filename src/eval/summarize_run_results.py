import argparse
import json
from pathlib import Path

import pandas as pd


def _normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def _is_yes(value):
    text = _normalize_text(value).upper()
    return text in {"YES", "Y", "TRUE", "1", "買った", "購入"}


def _is_buy(value):
    text = _normalize_text(value).upper()
    return text == "BUY"


def _is_hit(value):
    text = _normalize_text(value)
    return text == "的中"


def _read_markdown_table(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8").splitlines()
    table_lines = [line for line in lines if line.strip().startswith("|")]
    if len(table_lines) < 3:
        return pd.DataFrame()

    # 1行目ヘッダ、2行目区切り線、3行目以降データ
    header = [c.strip() for c in table_lines[0].strip("|").split("|")]
    rows = []
    for line in table_lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        rows.append(cells)
    if not rows:
        return pd.DataFrame(columns=header)
    return pd.DataFrame(rows, columns=header)


def summarize(log_path: Path) -> dict:
    df = _read_markdown_table(log_path)
    if df.empty:
        return {
            "source": str(log_path),
            "total_rows": 0,
            "buy_rows": 0,
            "executed_buy_rows": 0,
            "hit_rows": 0,
            "hit_rate": None,
            "avg_odds_executed_buy": None,
            "roi": None,
            "note": "No result rows found in run_result_log.md",
        }

    work = df.copy()
    work["最終判定"] = work["最終判定"].map(_normalize_text)
    work["実際に買ったか"] = work["実際に買ったか"].map(_normalize_text)
    work["的中 / 不的中"] = work["的中 / 不的中"].map(_normalize_text)
    work["最終オッズ"] = pd.to_numeric(work["最終オッズ"], errors="coerce")
    work = work[work["race_id"].map(_normalize_text) != ""].copy()

    buy_mask = work["最終判定"].apply(_is_buy).astype(bool)
    executed_mask = work["実際に買ったか"].apply(_is_yes).astype(bool)
    executed_buy_mask = buy_mask & executed_mask
    hit_mask = work["的中 / 不的中"].apply(_is_hit).astype(bool)
    hit_buy_mask = executed_buy_mask & hit_mask

    executed_count = int(executed_buy_mask.sum())
    hit_count = int(hit_buy_mask.sum())
    avg_odds = work.loc[executed_buy_mask, "最終オッズ"].mean()
    avg_odds = float(avg_odds) if pd.notna(avg_odds) else None

    # 1点100円を想定した相対ROI（掛け金1単位換算）
    payout_sum = work.loc[hit_buy_mask, "最終オッズ"].sum(min_count=1)
    payout_sum = float(payout_sum) if pd.notna(payout_sum) else 0.0
    roi = (payout_sum / executed_count) if executed_count > 0 else None

    return {
        "source": str(log_path),
        "total_rows": int(len(work)),
        "buy_rows": int(buy_mask.sum()),
        "executed_buy_rows": executed_count,
        "hit_rows": hit_count,
        "hit_rate": (hit_count / executed_count) if executed_count > 0 else None,
        "avg_odds_executed_buy": avg_odds,
        "roi": roi,
        "assumption": "ROI is calculated as sum(odds of hit executed BUY) / executed BUY count",
    }


def main():
    parser = argparse.ArgumentParser(description="Summarize BUY/SKIP performance from run_result_log.md")
    parser.add_argument(
        "--input",
        default="reports/run_result_log.md",
        help="Path to run_result_log.md",
    )
    parser.add_argument(
        "--output",
        default="reports/run_result_summary.json",
        help="Output summary JSON path",
    )
    args = parser.parse_args()

    log_path = Path(args.input)
    if not log_path.exists():
        raise FileNotFoundError(f"Input file not found: {log_path}")

    summary = summarize(log_path)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary saved: {output_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
