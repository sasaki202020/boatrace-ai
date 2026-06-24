"""check_db_quality.py の基本動作テスト。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_CHECK_DB = _SCRIPTS / "check_db_quality.py"

sys.path.insert(0, str(_ROOT))


def test_check_db_quality_missing_db(tmp_path):
    """存在しないDBパスを渡した場合、非ゼロで終了しエラーメッセージを出す。"""
    nonexistent = tmp_path / "no_such.db"
    result = subprocess.run(
        [sys.executable, str(_CHECK_DB), "--db", str(nonexistent)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "DBなしでゼロ終了するのは異常"
    combined = result.stdout + result.stderr
    assert "見つかりません" in combined, f"エラーメッセージが出ていない:\n{combined}"


def test_check_db_quality_with_empty_db(tmp_path):
    """スキーマだけ存在する空DBでは行数0で正常終了する。"""
    from src.data_processing.database import BoatRaceDatabase

    db_path = tmp_path / "empty.db"
    BoatRaceDatabase(str(db_path))  # スキーマ初期化のみ

    result = subprocess.run(
        [sys.executable, str(_CHECK_DB), "--db", str(db_path)],
        capture_output=True,
        text=True,
    )
    # 異常レコードはないので exit 0
    assert result.returncode == 0, f"空DBでも正常終了すべき:\n{result.stdout}\n{result.stderr}"
    assert "0 行" in result.stdout or "0" in result.stdout
