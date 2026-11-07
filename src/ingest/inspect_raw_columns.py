import pandas as pd
import os
import glob
import json

class ColumnInspector:
    """
    raw フォルダ内の CSV ファイルの列名を監査し、レポートを出力する。
    """
    def __init__(self, raw_dir="data/raw/official", config_path="config/column_aliases.json"):
        self.raw_dir = raw_dir
        with open(config_path if os.path.exists(config_path) else "config/column_aliases.seed.json", "r", encoding="utf-8") as f:
            self.aliases = json.load(f)
        self.canonical_cols = self._get_all_canonical()

    def _get_all_canonical(self):
        # 簡易的にエイリアス定義のキーを正とする
        return list(self.aliases.keys())

    def inspect(self, output_report="docs/raw_column_audit.md"):
        files = glob.glob(os.path.join(self.raw_dir, "*.csv"))
        report_lines = ["# Raw Column Audit Report\n"]
        
        for f in files:
            df = pd.read_csv(f, nrows=0)
            cols = df.columns.tolist()
            
            mapped = []
            unknown = []
            for c in cols:
                # どの canonical にヒットするか
                found = False
                for canonical, al in self.aliases.items():
                    if c == canonical or c in al:
                        mapped.append((c, canonical))
                        found = True
                        break
                if not found:
                    unknown.append(c)
            
            missing = [c for c in self.canonical_cols if c not in [m[1] for m in mapped]]
            
            report_lines.append(f"## File: {os.path.basename(f)}")
            report_lines.append(f"- **Mapped Columns**: {len(mapped)}")
            report_lines.append(f"- **Unknown Columns**: {unknown}")
            report_lines.append(f"- **Missing Canonical Columns**: {missing}\n")
            
        os.makedirs(os.path.dirname(output_report), exist_ok=True)
        with open(output_report, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        print(f"Audit report generated: {output_report}")

if __name__ == "__main__":
    inspector = ColumnInspector()
    inspector.inspect()
