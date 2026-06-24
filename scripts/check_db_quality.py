"""DB品質確認スクリプト。3年分取得後のDBの状態を検証する。

使い方:
    python scripts/check_db_quality.py
    python scripts/check_db_quality.py --db E:/boat_data/official/boat_race.db
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path


def run_checks(db_path: Path) -> int:
    """DB品質チェックを実行する。

    問題が見つかれば 1 を返す。正常なら 0 を返す。
    DBファイルが存在しない場合は FileNotFoundError を送出する。
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"DBファイルが見つかりません: {db_path}\n"
            "       まず main.py --source real --fetch でデータを取得してください。"
        )

    con = sqlite3.connect(db_path)
    issues = 0

    # ------------------------------------------------------------------
    # 1. テーブル行数
    # ------------------------------------------------------------------
    print("=" * 60)
    print("【テーブル行数】")
    for tbl in ["races", "entries", "results", "odds"]:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"  {tbl:<10}: {n:>10,} 行")
        except sqlite3.OperationalError:
            print(f"  {tbl:<10}: テーブルなし ← 異常")
            issues += 1

    # ------------------------------------------------------------------
    # 2. 期間
    # ------------------------------------------------------------------
    print()
    print("【期間】")
    try:
        row = con.execute("SELECT MIN(race_date), MAX(race_date) FROM races").fetchone()
        if row[0] is None:
            print("  races テーブルが空です")
        else:
            print(f"  最小: {row[0]}  最大: {row[1]}")
    except sqlite3.OperationalError:
        print("  races テーブルなし")
        issues += 1

    # ------------------------------------------------------------------
    # 3. 主キー重複チェック
    # ------------------------------------------------------------------
    print()
    print("【主キー重複チェック】")
    dup_checks = [
        (
            "races",
            "race_id",
            "SELECT COUNT(*) FROM ("
            "  SELECT race_id FROM races GROUP BY race_id HAVING COUNT(*) > 1"
            ")",
        ),
        (
            "entries",
            "(race_id, lane)",
            "SELECT COUNT(*) FROM ("
            "  SELECT race_id, lane FROM entries GROUP BY race_id, lane HAVING COUNT(*) > 1"
            ")",
        ),
        (
            "results",
            "(race_id, lane)",
            "SELECT COUNT(*) FROM ("
            "  SELECT race_id, lane FROM results GROUP BY race_id, lane HAVING COUNT(*) > 1"
            ")",
        ),
        (
            "odds",
            "(race_id, lane)",
            "SELECT COUNT(*) FROM ("
            "  SELECT race_id, lane FROM odds GROUP BY race_id, lane HAVING COUNT(*) > 1"
            ")",
        ),
    ]
    for tbl, key, sql in dup_checks:
        try:
            n_dup = con.execute(sql).fetchone()[0]
            if n_dup:
                print(f"  [NG] {tbl:<10} キー {key}: {n_dup} 件の重複 ← 異常")
                issues += 1
            else:
                print(f"  [OK] {tbl:<10} キー {key}: 重複なし")
        except sqlite3.OperationalError:
            pass  # テーブルなし → 行数チェックで既報

    # ------------------------------------------------------------------
    # 4. results カバレッジ
    # ------------------------------------------------------------------
    print()
    print("【results カバレッジ(entries に対して)】")
    try:
        total_e = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        with_r = con.execute(
            "SELECT COUNT(*) FROM results WHERE finish_position IS NOT NULL"
        ).fetchone()[0]
        pct = with_r / total_e * 100 if total_e else 0.0
        print(f"  entries: {total_e:,}  finish_position あり: {with_r:,}  ({pct:.1f}%)")
        if total_e > 100 and pct < 50:
            print("  [WARN] カバレッジが低い。結果が未公開 or パース失敗の可能性があります。")
    except sqlite3.OperationalError:
        pass

    # ------------------------------------------------------------------
    # 5. 日別レース数(先頭5日と末尾5日)
    # ------------------------------------------------------------------
    print()
    print("【日別レース数(先頭5日)】")
    try:
        rows = con.execute(
            "SELECT race_date, COUNT(DISTINCT race_id) AS n FROM races "
            "GROUP BY race_date ORDER BY race_date LIMIT 5"
        ).fetchall()
        for r in rows:
            print(f"  {r[0]}: {r[1]:2d} R")
    except sqlite3.OperationalError:
        pass

    # ------------------------------------------------------------------
    # 6. 記録ゼロ日のサンプル
    # ------------------------------------------------------------------
    print()
    print("【記録ゼロ日のサンプル(最大10日)】")
    print("  ※ 非開催日と取得失敗日は区別できません。該当日は要手動確認。")
    try:
        dates_in_db = {
            r[0] for r in con.execute("SELECT DISTINCT race_date FROM races")
        }
        if not dates_in_db:
            print("  races テーブルが空です")
        else:
            d_start = datetime.date.fromisoformat(min(dates_in_db))
            d_end   = datetime.date.fromisoformat(max(dates_in_db))
            all_dates = {
                (d_start + datetime.timedelta(d)).isoformat()
                for d in range((d_end - d_start).days + 1)
            }
            missing = sorted(all_dates - dates_in_db)
            print(
                f"  全日数: {(d_end - d_start).days + 1}  "
                f"記録あり: {len(dates_in_db)}  "
                f"記録なし: {len(missing)}"
            )
            for d in missing[:10]:
                print(f"    {d}  ← 非開催または取得失敗(要確認)")
            if len(missing) > 10:
                print(f"    ... 他 {len(missing) - 10} 日")
    except sqlite3.OperationalError:
        pass

    con.close()

    # ------------------------------------------------------------------
    # 結果サマリ
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    if issues:
        print(f"[FAIL] {issues} 件の異常を検出しました。")
    else:
        print("[OK] チェック完了。異常なし。")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DB品質確認スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n  python scripts/check_db_quality.py\n"
               "  python scripts/check_db_quality.py --db E:/boat_data/official/boat_race.db",
    )
    parser.add_argument(
        "--db",
        default="E:/boat_data/official/boat_race.db",
        help="確認するDBのパス (デフォルト: E:/boat_data/official/boat_race.db)",
    )
    args = parser.parse_args()

    try:
        sys.exit(run_checks(Path(args.db)))
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
