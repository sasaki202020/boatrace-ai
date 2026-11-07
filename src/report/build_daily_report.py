import pandas as pd
import datetime
import os

class ReportBuilder:
    """
    戦略判定結果を Markdown レポートにまとめる。
    """
    def _pick_honmei(self, ev_df, skip_df):
        buy_races = set(skip_df.loc[skip_df["decision"] != "SKIP", "race_id"].tolist())
        buy_candidates = ev_df[ev_df["race_id"].isin(buy_races)].copy()
        if buy_candidates.empty:
            return None
        return buy_candidates.sort_values(["ev", "approx_prob"], ascending=False).iloc[0]

    def _pick_ana(self, ev_df, skip_df, honmei_row=None):
        buy_races = set(skip_df.loc[skip_df["decision"] != "SKIP", "race_id"].tolist())
        buy_candidates = ev_df[ev_df["race_id"].isin(buy_races)].copy()
        if buy_candidates.empty:
            return None
        if honmei_row is not None:
            buy_candidates = buy_candidates[buy_candidates["trifecta"] != honmei_row["trifecta"]]
        if buy_candidates.empty:
            return None
        ana_candidates = buy_candidates[
            buy_candidates["first_win_proba"] < buy_candidates["first_win_proba"].median()
        ]
        target = ana_candidates if not ana_candidates.empty else buy_candidates
        return target.sort_values(["ev", "odds"], ascending=False).iloc[0]

    def _format_candidate(self, title, row):
        if row is None:
            return [
                f"## {title}",
                "- 該当なし",
                "",
            ]
        return [
            f"## {title}",
            f"- レース: {row['race_id']}",
            f"- 買い目: {row['trifecta']}",
            f"- 近似確率: {row['approx_prob']:.2%}",
            f"- オッズ: {row['odds']:.2f}",
            f"- EV: {row['ev']:.2f}",
            "",
        ]

    def _build_buy_rows(self, ev_df, skip_df):
        buy_rows = skip_df[skip_df["decision"] != "SKIP"].copy()
        if buy_rows.empty:
            return buy_rows

        buy_rows = buy_rows.rename(columns={"recommended_trifecta": "trifecta"})
        merge_cols = [c for c in ["race_id", "trifecta", "risk_flag", "odds_source"] if c in ev_df.columns]
        if {"race_id", "trifecta"}.issubset(merge_cols):
            meta = ev_df[merge_cols].drop_duplicates(subset=["race_id", "trifecta"])
            buy_rows = buy_rows.merge(meta, on=["race_id", "trifecta"], how="left")
        if "risk_flag" not in buy_rows.columns:
            buy_rows["risk_flag"] = False
        if "odds_source" not in buy_rows.columns:
            buy_rows["odds_source"] = "unknown"
        return buy_rows.sort_values(["ev", "approx_prob"], ascending=False).reset_index(drop=True)

    def _format_buy_candidates(self, buy_rows):
        lines = ["## BUY候補一覧"]
        if buy_rows.empty:
            lines.append("- BUY候補なし")
            lines.append("")
            return lines

        lines.append("| race_id | trifecta | first_win_proba | approx_prob | odds | ev | risk_flag | odds_source |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | --- |")
        for _, row in buy_rows.iterrows():
            lines.append(
                "| {race_id} | {trifecta} | {first_win_proba:.3f} | {approx_prob:.3f} | {odds:.2f} | {ev:.3f} | {risk_flag} | {odds_source} |".format(
                    race_id=row["race_id"],
                    trifecta=row["trifecta"],
                    first_win_proba=float(row["first_win_proba"]),
                    approx_prob=float(row["approx_prob"]),
                    odds=float(row["odds"]),
                    ev=float(row["ev"]),
                    risk_flag=bool(row["risk_flag"]),
                    odds_source=row["odds_source"],
                )
            )
        lines.append("")
        return lines

    def _write_buy_final_check(self, buy_rows, output_dir):
        lines = [
            "# BUY Final Check (Pre-Race)",
            "",
            f"作成日: {datetime.datetime.now().strftime('%Y-%m-%d')}",
            f"対象: BUY 判定 {len(buy_rows)}件（人手最終確認用）",
            "",
        ]
        if buy_rows.empty:
            lines.extend(["- BUY候補なし", ""])
        else:
            for idx, row in buy_rows.iterrows():
                lines.extend([
                    f"## {idx + 1}) {row['race_id']}",
                    "",
                    f"- recommended_trifecta: `{row['trifecta']}`",
                    f"- odds: `{float(row['odds'])}`",
                    f"- ev: `{float(row['ev'])}`",
                    f"- risk_flag: `{bool(row['risk_flag'])}`",
                    f"- odds_source: `{row['odds_source']}`",
                    f"- reason: `{row['reason']}`",
                    "",
                    "発走前チェック:",
                    "- [ ] 実オッズ最終値を確認（急変がないか）",
                    "- [ ] 欠場・F/L・展示異常の有無を確認",
                    "- [ ] 天候/風/波の急変が許容範囲か確認",
                    "- [ ] 資金配分ルールに抵触しないか確認",
                    "",
                    "最終判定:",
                    "- 判定（BUY / SKIP）: ``",
                    "- 最終オッズ: ``",
                    "- メモ: ``",
                    "",
                ])

        path = os.path.join(output_dir, "buy_final_check.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Report generated: {path}")

    def _ensure_run_result_log_template(self, output_dir):
        path = os.path.join(output_dir, "run_result_log.md")
        if os.path.exists(path):
            return
        lines = [
            "# Run Result Log",
            "",
            "運用日: `YYYY-MM-DD`",
            "",
            "## 記録テーブル",
            "",
            "| race_id | 最終判定 | 実際に買ったか | 最終オッズ | 着順結果 | 的中 / 不的中 | 振り返りメモ |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
            "|  | BUY / SKIP | YES / NO |  |  | 的中 / 不的中 |  |",
            "|  | BUY / SKIP | YES / NO |  |  | 的中 / 不的中 |  |",
            "|  | BUY / SKIP | YES / NO |  |  | 的中 / 不的中 |  |",
            "",
            "## メモ",
            "",
            "- 当日の判断基準:",
            "- 例外対応:",
            "- 次回改善点:",
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Template ensured: {path}")

    def build(self, ev_analysis_path, skip_decisions_path, output_dir="reports"):
        ev_df = pd.read_csv(ev_analysis_path)
        skip_df = pd.read_csv(skip_decisions_path)

        if "date" in skip_df.columns and not skip_df.empty and pd.notna(skip_df["date"].iloc[0]):
            report_date = str(skip_df["date"].iloc[0])
        else:
            report_date = datetime.datetime.now().strftime("%Y-%m-%d")

        honmei = self._pick_honmei(ev_df, skip_df)
        ana = self._pick_ana(ev_df, skip_df, honmei)
        skip_rows = skip_df[skip_df["decision"] == "SKIP"].copy()
        buy_rows = self._build_buy_rows(ev_df, skip_df)

        report = [
            f"# Daily Investment Report: {report_date}\n",
            "## Summary",
            f"- Total Races Analyzed: {skip_df['race_id'].nunique()}",
            f"- Buy Races: {len(skip_df[skip_df['decision'] != 'SKIP'])}",
            f"- Skip Races: {len(skip_rows)}",
            "",
        ]

        report.extend(self._format_candidate("本命", honmei))
        report.extend(self._format_candidate("穴", ana))
        report.extend(self._format_buy_candidates(buy_rows))

        report.append("## 見送り")
        if skip_rows.empty:
            report.append("- 見送りレースなし")
        else:
            for _, row in skip_rows.iterrows():
                report.append(f"- {row['race_id']}: {row['reason']}")
        report.append("")

        report.append("## 根拠")
        if honmei is not None:
            report.append(
                f"- 本命は EV 上位かつ近似確率が高い {honmei['trifecta']} を採用。"
            )
        if ana is not None:
            report.append(
                f"- 穴は 本命より勝率の低い側から EV を確保できる {ana['trifecta']} を採用。"
            )
        if not skip_rows.empty:
            report.append("- 見送りは EV 閾値未達、1着候補の信頼度不足、または暫定オッズ判定を理由に記録。")

        os.makedirs(output_dir, exist_ok=True)
        filename = os.path.join(output_dir, "daily_report.md")
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
            
        print(f"Report generated: {filename}")
        self._write_buy_final_check(buy_rows, output_dir)
        self._ensure_run_result_log_template(output_dir)

if __name__ == "__main__":
    builder = ReportBuilder()
    builder.build(
        "data/strategy_outputs/ev_analysis.csv",
        "data/strategy_outputs/skip_decisions.csv",
    )
