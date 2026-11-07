import json
import difflib
import pandas as pd
import glob
import os

class AliasSuggester:
    """
    Unknown Columns に対して、既存の Canonical Columns から類似したものを提案する。
    """
    def __init__(self, config_path="config/column_aliases.json"):
        with open(config_path if os.path.exists(config_path) else "config/column_aliases.seed.json", "r", encoding="utf-8") as f:
            self.aliases = json.load(f)
        self.canonical_cols = list(self.aliases.keys())

    def suggest_for_file(self, file_path):
        df = pd.read_csv(file_path, nrows=0)
        unknowns = []
        for c in df.columns:
            found = False
            for canonical, al in self.aliases.items():
                if c == canonical or c in al:
                    found = True
                    break
            if not found:
                unknowns.append(c)
        
        suggestions = {}
        for u in unknowns:
            # 類似度が高い上位3件
            matches = difflib.get_close_matches(u, self.canonical_cols, n=3, cutoff=0.3)
            suggestions[u] = matches
            
        return suggestions

if __name__ == "__main__":
    suggester = AliasSuggester()
    raw_files = glob.glob("data/raw/official/*.csv")
    for f in raw_files:
        s = suggester.suggest_for_file(f)
        if s:
            print(f"Suggestions for {os.path.basename(f)}:")
            for u, matches in s.items():
                print(f"  '{u}' might be: {matches}")
