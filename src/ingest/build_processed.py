import pandas as pd
import os
import json
import glob
from src.ingest.validators import SchemaValidator
from src.ingest.normalize import Normalizer
from src.ingest.official_txt_parser import OfficialTxtParser

class ProcessedBuilder:
    """
    Raw から Processed への構築をオーケストレートする。
    """
    def __init__(self, alias_path="config/column_aliases.json", manifest_path="config/source_manifest.json"):
        self.aliases = self._load_aliases(alias_path)
        self.manifest_path = manifest_path
        self.normalizer = Normalizer()
        self.txt_parser = OfficialTxtParser()

    def _load_aliases(self, path):
        if not os.path.exists(path):
            path = "config/column_aliases.example.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _read_raw_file(self, input_path):
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "cp932"):
            try:
                return pd.read_csv(input_path, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
        return pd.read_csv(input_path)

    def _detect_raw_kind(self, input_path):
        ext = os.path.splitext(input_path)[1].lower()
        basename = os.path.basename(input_path).lower()
        parent = os.path.basename(os.path.dirname(input_path)).lower()
        if ext == ".csv":
            return "csv"
        if ext == ".txt":
            if basename.startswith("kse"):
                return "kse_txt"
            if basename.startswith("kbn"):
                return "kbn_txt"
            if basename.startswith("b"):
                return "kbn_txt"
            if basename.startswith("k"):
                return "kse_txt"
            if basename.startswith("fan"):
                return "fan_txt"
            if parent == "results":
                return "kse_txt"
            if parent == "entries":
                return "kbn_txt"
            if parent in {"fan", "fanbook"}:
                return "fan_txt"
            return "txt"
        return "unknown"

    def apply_aliases(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        列名揺れを正規の列名にマッピング
        """
        rename_map = {}
        for canonical, list_of_aliases in self.aliases.items():
            for alias in list_of_aliases:
                if alias in df.columns:
                    rename_map[alias] = canonical
        return df.rename(columns=rename_map)

    def process_file(self, input_path):
        raw_kind = self._detect_raw_kind(input_path)
        if raw_kind in {"kse_txt", "kbn_txt"}:
            parsed = self.txt_parser.parse(input_path, raw_kind=raw_kind)
            validator = SchemaValidator()
            df = parsed["dataframe"]
            is_valid = validator.validate_dataframe(df)
            summary = validator.get_summary()
            warnings = parsed["warnings"] + summary["warnings"]
            if not is_valid:
                return {
                    "input_path": input_path,
                    "ok": False,
                    "file_type": None,
                    "rows": 0,
                    "warnings": warnings,
                    "fatal_errors": summary["fatal_errors"],
                    "race_issues": summary["race_issues"],
                }
            return {
                "input_path": input_path,
                "ok": True,
                "file_type": parsed["file_type"],
                "rows": len(df),
                "warnings": warnings,
                "fatal_errors": summary["fatal_errors"],
                "race_issues": summary["race_issues"],
                "dataframe": df,
            }

        if raw_kind == "fan_txt":
            parsed = self.txt_parser.parse(input_path, raw_kind=raw_kind)
            return {
                "input_path": input_path,
                "ok": True,
                "file_type": parsed["file_type"],
                "rows": len(parsed["dataframe"]),
                "warnings": parsed["warnings"],
                "fatal_errors": [],
                "race_issues": [],
                "dataframe": parsed["dataframe"],
            }

        if raw_kind != "csv":
            message_map = {
                "fan_txt": "fan*.txt is a racer seasonal stats source and cannot produce race-level historical/today datasets by itself",
                "txt": "Unsupported TXT source. Convert to canonical CSV or implement a fixed-width parser first",
                "unknown": "Unsupported file extension in raw input",
            }
            return {
                "input_path": input_path,
                "ok": False,
                "file_type": None,
                "rows": 0,
                "warnings": [message_map.get(raw_kind, "Unsupported raw source")],
                "fatal_errors": [],
                "race_issues": [],
            }

        validator = SchemaValidator()
        df_raw = self._read_raw_file(input_path)

        # 1. Alias Mapping
        df = self.apply_aliases(df_raw)

        # 2. Validation
        is_valid = validator.validate_dataframe(df)
        summary = validator.get_summary()

        if not is_valid:
            print(f"Validation FAILED for {input_path}")
            return {
                "input_path": input_path,
                "ok": False,
                "file_type": None,
                "rows": 0,
                "warnings": summary["warnings"],
                "fatal_errors": summary["fatal_errors"],
                "race_issues": summary["race_issues"],
            }

        # 3. Normalization
        df = self.normalizer.normalize_results(df)
        df = self.normalizer.convert_types(df)

        # 4. File type detection
        file_type = self._get_file_type_from_manifest(input_path)
        if not file_type:
            file_type = "historical" if "finish_position" in df.columns else "today"

        return {
            "input_path": input_path,
            "ok": True,
            "file_type": file_type,
            "rows": len(df),
            "warnings": summary["warnings"],
            "fatal_errors": summary["fatal_errors"],
            "race_issues": summary["race_issues"],
            "dataframe": df,
        }

    def _get_file_type_from_manifest(self, input_path):
        if not os.path.exists(self.manifest_path):
            return None
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            for src in manifest.get("sources", []):
                # 相対パスまたは絶対パスでの一致を確認
                if os.path.abspath(src["file"]) == os.path.abspath(input_path):
                    file_type = src.get("type")
                    if file_type in {"historical", "today"}:
                        return file_type
        return None

    def _backfill_from_master(self, df: pd.DataFrame, master_df: pd.DataFrame) -> pd.DataFrame:
        """
        選手マスタを使用して欠損値を補完する。
        """
        if df.empty or master_df.empty or "racer_id" not in df.columns:
            return df

        # バックフィル対象の列
        target_cols = [
            "avg_st", "national_win_rate", "national_2ren_rate", 
            "local_win_rate", "local_2ren_rate"
        ]
        
        # master_df から必要な列だけ抽出し、列名が重複しないよう suffix をつける
        master_subset = master_df[["racer_id"] + [c for c in target_cols if c in master_df.columns]].copy()
        for col in target_cols:
            if col in master_subset.columns:
                master_subset = master_subset.rename(columns={col: f"{col}_master"})

        enriched = df.copy()
        # 元データの各列を数値化
        for col in target_cols:
            if col in enriched.columns:
                enriched[col] = pd.to_numeric(enriched[col], errors="coerce")

        # マージ
        enriched = enriched.merge(master_subset, on="racer_id", how="left")
        
        # マスタの値で穴埋め
        for col in target_cols:
            master_col = f"{col}_master"
            if master_col in enriched.columns and col in enriched.columns:
                enriched[col] = enriched[col].fillna(enriched[master_col])
            if master_col in enriched.columns:
                enriched = enriched.drop(columns=[master_col])
        
        return enriched

    def _backfill_today_avg_st(self, historical_df: pd.DataFrame, today_df: pd.DataFrame) -> pd.DataFrame:
        # (互換性のために残すが、マスタがある場合は _backfill_from_master が優先される)
        if historical_df.empty or today_df.empty or "racer_id" not in today_df.columns:
            return today_df

        if "avg_st" not in historical_df.columns:
            return today_df

        hist_avg_st = pd.to_numeric(historical_df["avg_st"], errors="coerce")
        hist_keys = historical_df[["racer_id"]].copy()
        hist_keys["avg_st"] = hist_avg_st
        hist_keys = hist_keys.dropna(subset=["racer_id", "avg_st"])
        if hist_keys.empty:
            return today_df

        racer_avg_st = (
            hist_keys.groupby("racer_id", as_index=False)["avg_st"]
            .mean()
            .rename(columns={"avg_st": "avg_st_hist"})
        )

        enriched = today_df.copy()
        if "avg_st" in enriched.columns:
            enriched["avg_st"] = pd.to_numeric(enriched["avg_st"], errors="coerce")
        else:
            enriched["avg_st"] = pd.NA

        enriched = enriched.merge(racer_avg_st, on="racer_id", how="left")
        enriched["avg_st"] = enriched["avg_st"].fillna(enriched["avg_st_hist"])
        return enriched.drop(columns=["avg_st_hist"])

    def build_all(self, raw_dir="data/raw", output_dir="data/processed"):
        os.makedirs(output_dir, exist_ok=True)

        raw_files = sorted(
            f for pattern in ("*.csv", "*.txt")
            for f in glob.glob(os.path.join(raw_dir, "**", pattern), recursive=True)
            if os.path.isfile(f)
        )

        official_raw_dir = os.path.join(raw_dir, "official")
        if os.path.isdir(official_raw_dir):
            raw_files.extend(
                f for pattern in ("*.csv", "*.txt")
                for f in glob.glob(os.path.join(official_raw_dir, "**", pattern), recursive=True)
                if os.path.isfile(f)
            )
            raw_files = sorted(set(raw_files))

        aggregated = {"historical": [], "today": [], "master": []}
        summary = {
            "status": "PASS",
            "processed_files": [],
            "generated_outputs": [],
            "fatal_errors": [],
            "warnings": [],
            "race_issues": [],
            "counts": {
                "raw_files_found": len(raw_files),
                "files_processed": 0,
                "historical_rows": 0,
                "today_rows": 0,
            },
        }

        for input_path in raw_files:
            try:
                result = self.process_file(input_path)
            except Exception as exc:
                result = {
                    "input_path": input_path,
                    "ok": False,
                    "file_type": None,
                    "rows": 0,
                    "warnings": [],
                    "fatal_errors": [f"Unhandled ingestion error: {exc}"],
                }
            summary["processed_files"].append({
                "input_path": input_path,
                "ok": result["ok"],
                "file_type": result["file_type"],
                "rows": result["rows"],
                "fatal_errors": result["fatal_errors"],
                "warnings": result["warnings"],
                "race_issues": result.get("race_issues", []),
            })
            summary["fatal_errors"].extend(
                f"{input_path}: {message}" for message in result["fatal_errors"]
            )
            summary["warnings"].extend(
                f"{input_path}: {message}" for message in result["warnings"]
            )
            summary["race_issues"].extend(
                {
                    "input_path": input_path,
                    **issue,
                }
                for issue in result.get("race_issues", [])
            )

            if result["ok"]:
                aggregated[result["file_type"]].append(result["dataframe"])
                summary["counts"]["files_processed"] += 1

        combined_outputs = {}
        for file_type, frames in aggregated.items():
            if not frames:
                continue
            combined_outputs[file_type] = pd.concat(frames, ignore_index=True)

        # 選手マスタを用いたバックフィル
        if "master" in combined_outputs:
            master_df = combined_outputs["master"].drop_duplicates(subset=["racer_id"], keep="last")
            for target_type in ["historical", "today"]:
                if target_type in combined_outputs:
                    combined_outputs[target_type] = self._backfill_from_master(
                        combined_outputs[target_type],
                        master_df
                    )
        elif "historical" in combined_outputs and "today" in combined_outputs:
            # マスタがない場合のフォールバック (既存ロジック)
            combined_outputs["today"] = self._backfill_today_avg_st(
                combined_outputs["historical"],
                combined_outputs["today"],
            )

        for file_type, combined in combined_outputs.items():
            if file_type == "master":
                continue # master_races.csv は出力不要（内部利用のみ）
            output_path = os.path.join(output_dir, f"{file_type}_races.csv")
            combined.to_csv(output_path, index=False)
            summary["generated_outputs"].append(output_path)
            summary["counts"][f"{file_type}_rows"] = len(combined)
            print(f"Processed: {output_path} (Rows: {len(combined)})")

        if summary["fatal_errors"]:
            summary["status"] = "FAIL"
        elif summary["counts"]["files_processed"] == 0:
            summary["status"] = "NO_DATA"
            summary["warnings"].append(f"No supported race-level CSV files found in {raw_dir}")

        summary_path = os.path.join(output_dir, "validation_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=4, ensure_ascii=False)

        self._write_validation_failure_report(summary, output_dir)
        print(f"Validation summary written to {summary_path}")
        return summary

    def _write_validation_failure_report(self, summary, output_dir):
        report_dir = os.path.join("reports", "ingest")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "validation_failure_report.md")

        lines = [
            "# Ingestion Failure Report",
            "",
            f"- Status: `{summary['status']}`",
            f"- Processed files: `{summary['counts']['files_processed']}` / `{summary['counts']['raw_files_found']}`",
            f"- Fatal errors: `{len(summary['fatal_errors'])}`",
            f"- Warnings: `{len(summary['warnings'])}`",
            f"- Race issues: `{len(summary['race_issues'])}`",
            "",
        ]

        if summary["fatal_errors"]:
            lines.extend(["## Fatal Errors", ""])
            for message in summary["fatal_errors"]:
                lines.append(f"- {message}")
            lines.append("")

        if summary["race_issues"]:
            lines.extend(["## Race Issues", "", "| file | race_id | rows | missing | duplicate | malformed | order_ok |", "| --- | --- | ---: | --- | --- | --- | --- |"])
            for issue in summary["race_issues"]:
                lines.append(
                    f"| `{issue['input_path']}` | `{issue['race_id']}` | {issue['rows']} | "
                    f"`{issue['missing_lanes']}` | `{issue['duplicate_lanes']}` | `{issue['malformed_rows']}` | `{issue['order_ok']}` |"
                )
            lines.append("")

        if summary["warnings"]:
            lines.extend(["## Warnings", ""])
            for message in summary["warnings"][:200]:
                lines.append(f"- {message}")
            lines.append("")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        summary["failure_report_path"] = report_path

if __name__ == "__main__":
    builder = ProcessedBuilder()
    builder.build_all()
