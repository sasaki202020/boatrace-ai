import json
from pathlib import Path

import pandas as pd


RAW_DIR = Path("data/raw")
OUT_CSV = Path("data/processed/historical_races.csv")
OUT_JSON = Path("data/processed/build_report.json")
PROGRAM_CSV = Path("data/csv/program/program.csv")

REQUIRED_COLS = ["race_id", "date", "lane", "finish_position"]
OPTIONAL_COLS = [
    "venue",
    "race_no",
    "racer_id",
    "racer_class",
    "avg_st",
    "start_display_st",
    "exhibition_time",
    "prev_race_st",
    "prev_race_finish",
    "prev_race_course",
    "motor_no",
    "motor_2ren_rate",
    "boat_no",
    "boat_2ren_rate",
    "win_rate_venue",
    "national_win_rate",
    "national_2ren_rate",
    "local_2ren_rate",
    "odds_trifecta",
    "odds_exacta",
    "odds_2rentan",
    "odds_quinella",
    "odds_2renpuku",
    "weather",
    "wind_speed",
    "wave_height",
    "jcd",
    "grade",
]


def read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.read_csv(path)


def _merge_program_win_rate(combined: pd.DataFrame) -> pd.DataFrame:
    """
    program.csv にしかない win_rate_venue を historical_races に持ち込む。

    raw の official parser ではこの列が落ちるため、race_id/lane/date/jcd/race_no
    で安定結合して補完する。
    """
    if not PROGRAM_CSV.exists():
        return combined

    try:
        program = read_csv_any_encoding(PROGRAM_CSV)
    except Exception as exc:
        print(f"[warn] program.csv 読み込み失敗: {exc}")
        return combined

    required = {"date", "jcd", "race_no", "lane", "win_rate_venue"}
    if not required.issubset(program.columns):
        print("[warn] program.csv に win_rate_venue がありません")
        return combined

    if not {"date", "jcd", "race_no", "lane"}.issubset(combined.columns):
        print("[warn] historical_races 側に結合キーがありません")
        return combined

    prog = program[["date", "jcd", "race_no", "lane", "win_rate_venue"]].copy()
    prog["date"] = pd.to_datetime(prog["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    combined = combined.copy()
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    if "win_rate_venue" in combined.columns:
        combined = combined.rename(columns={"win_rate_venue": "win_rate_venue_hist"})

    for col in ["jcd", "race_no", "lane"]:
        prog[col] = pd.to_numeric(prog[col], errors="coerce")
        combined[col] = pd.to_numeric(combined[col], errors="coerce")

    prog = prog.dropna(subset=["date", "jcd", "race_no", "lane"]).copy()
    combined = combined.dropna(subset=["date", "jcd", "race_no", "lane"]).copy()
    prog["jcd"] = prog["jcd"].astype(int)
    prog["race_no"] = prog["race_no"].astype(int)
    prog["lane"] = prog["lane"].astype(int)
    combined["jcd"] = combined["jcd"].astype(int)
    combined["race_no"] = combined["race_no"].astype(int)
    combined["lane"] = combined["lane"].astype(int)

    merged = combined.merge(
        prog,
        on=["date", "jcd", "race_no", "lane"],
        how="left",
        suffixes=("", "_program"),
    )
    base_win = None
    if "win_rate_venue_hist" in merged.columns:
        base_win = pd.to_numeric(merged["win_rate_venue_hist"], errors="coerce")
    if "win_rate_venue_program" in merged.columns:
        prog_win = pd.to_numeric(merged["win_rate_venue_program"], errors="coerce")
        base_win = prog_win if base_win is None else base_win.combine_first(prog_win)
        merged = merged.drop(columns=["win_rate_venue_program"])
    if base_win is not None:
        merged["win_rate_venue"] = base_win
        merged = merged.drop(columns=["win_rate_venue_hist"], errors="ignore")

    return merged


def main() -> None:
    raw_files = sorted(RAW_DIR.glob("**/*.csv"))
    print(f"[raw] {len(raw_files)}件のCSVを検出")

    frames = []
    skipped = []

    for f in raw_files:
        try:
            if "_head" in f.stem.lower():
                skipped.append({"file": str(f.name), "reason": "head fragment skipped"})
                continue

            df = read_csv_any_encoding(f)
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                skipped.append({"file": str(f.name), "reason": f"必須列なし: {missing}"})
                continue

            use_cols = REQUIRED_COLS + [c for c in OPTIONAL_COLS if c in df.columns]
            df = df[use_cols].copy()
            df["_source"] = f.name
            frames.append(df)
            print(f"  [ok] {f.name}: {len(df)}行")
        except Exception as e:
            skipped.append({"file": str(f.name), "reason": str(e)})

    if not frames:
        print("[error] 読み込めたファイルが0件です")
        raise SystemExit(1)

    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["race_id", "lane"], keep="last")
    after = len(combined)

    if "date" in combined.columns:
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        combined = combined.sort_values(["date", "race_id", "lane"]).reset_index(drop=True)

    # 二連単/二連複の列名ゆれを吸収
    if "odds_exacta" in combined.columns:
        if "odds_2rentan" not in combined.columns:
            combined["odds_2rentan"] = combined["odds_exacta"]
        else:
            combined["odds_2rentan"] = combined["odds_2rentan"].where(
                pd.to_numeric(combined["odds_2rentan"], errors="coerce").notna(),
                combined["odds_exacta"],
            )
    if "odds_quinella" in combined.columns:
        if "odds_2renpuku" not in combined.columns:
            combined["odds_2renpuku"] = combined["odds_quinella"]
        else:
            combined["odds_2renpuku"] = combined["odds_2renpuku"].where(
                pd.to_numeric(combined["odds_2renpuku"], errors="coerce").notna(),
                combined["odds_quinella"],
            )

    combined = _merge_program_win_rate(combined)

    print(f"\n[combined] {before}行 → 重複除去後 {after}行")

    date_col = next((c for c in combined.columns if "date" in c.lower()), None)
    date_min = str(combined[date_col].min()) if date_col else None
    date_max = str(combined[date_col].max()) if date_col else None

    tri_races = combined[combined["finish_position"].isin([1, 2, 3])]
    tri_race_count = (
        tri_races.groupby("race_id")
        .filter(lambda x: set([1, 2, 3]).issubset(set(x["finish_position"])))["race_id"]
        .nunique()
    )

    print(f"[stats] race_count={combined['race_id'].nunique()} / date={date_min}〜{date_max}")
    print(f"[stats] 三連単復元可能レース数={tri_race_count}")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.drop(columns=["_source"], errors="ignore").to_csv(OUT_CSV, index=False)
    print(f"\n[saved] {OUT_CSV}")

    report = {
        "raw_files_found": len(raw_files),
        "raw_files_loaded": len(frames),
        "raw_files_skipped": skipped,
        "rows_before_dedup": before,
        "rows_after_dedup": after,
        "race_count": int(combined["race_id"].nunique()),
        "trifecta_race_count": int(tri_race_count),
        "exacta_odds_nonnull": int(pd.to_numeric(combined.get("odds_exacta"), errors="coerce").notna().sum()) if "odds_exacta" in combined.columns else 0,
        "exacta_alias_nonnull": int(pd.to_numeric(combined.get("odds_2rentan"), errors="coerce").notna().sum()) if "odds_2rentan" in combined.columns else 0,
        "date_range": {"min": date_min, "max": date_max},
        "output": str(OUT_CSV),
        "next_steps": [
            "py -m src.features.build_features",
            "py -m src.models.train_win_model",
            "py -m src.models.predict_win_proba",
            "py src/eval/diagnose_trifecta_rank_structure.py",
        ],
    }

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
