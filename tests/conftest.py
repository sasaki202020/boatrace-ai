import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def official_k_file(tmp_path: Path) -> Path:
    """Create a minimal CP932 official K result file for clean-clone tests."""

    lines = [
        "22KBGN",
        "1R テスト H1800m 晴 風 北 3m 波 2cm",
        "01 1 4001 選手一 30 50.0 6.70 1 0.10 1:50.0",
        "02 2 4002 選手二 31 51.0 6.71 2 0.11 1:50.1",
        "03 3 4003 選手三 32 52.0 6.72 3 0.12 1:50.2",
        "04 4 4004 選手四 33 53.0 6.73 4 0.13 1:50.3",
        "05 5 4005 選手五 34 54.0 6.74 5 0.14 1:50.4",
        "06 6 4006 選手六 35 55.0 6.75 6 0.15 1:50.5",
        "3連単 1-2-3 470",
        "22KEND",
    ]
    path = tmp_path / "K260404.TXT"
    path.write_text("\n".join(lines), encoding="cp932")
    return path


@pytest.fixture
def odds3t_html() -> str:
    """Return a complete 120-combination 3T odds table without live data."""

    def cells(values: list[object]) -> str:
        return "".join(f"<td>{value}</td>" for value in values)

    header: list[str] = []
    body: list[str] = []
    for first in range(1, 7):
        header.extend([str(first), ""])
    body.append(f"<tr>{cells(header)}</tr>")

    for block_index in range(5):
        first_row: list[object] = []
        later_rows = [[] for _ in range(3)]
        for first in range(1, 7):
            second = [boat for boat in range(1, 7) if boat != first][block_index]
            thirds = [boat for boat in range(1, 7) if boat not in {first, second}]
            first_row.extend([second, thirds[0], f"{10 + block_index + first / 10:.1f}"])
            for row_index, third in enumerate(thirds[1:]):
                later_rows[row_index].extend([third, f"{20 + block_index + first + row_index / 10:.1f}"])
        body.append(f"<tr>{cells(first_row)}</tr>")
        for row in later_rows:
            body.append(f"<tr>{cells(row)}</tr>")

    return "<html><body><h1>3連単 オッズ</h1><table>" + "".join(body) + "</table></body></html>"
