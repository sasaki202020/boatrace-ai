from __future__ import annotations

from pathlib import Path

from src.data.parse_fixed_width import BoatRaceParser


def _entry_row(lane: int, racer_id: int) -> bytes:
    row = bytearray(b" " * 80)
    prefix = f"{lane} {racer_id:04d}".encode("ascii")
    row[: len(prefix)] = prefix
    row[22:24] = b"A1"
    row[25:30] = b" 6.00"
    row[30:36] = b" 40.00"
    row[36:41] = b" 5.50"
    row[41:47] = b" 35.00"
    row[50:56] = b" 42.00"
    row[59:65] = b" 38.00"
    return bytes(row)


def test_entry_parser_uses_section_code_and_fullwidth_two_digit_race_header(tmp_path: Path):
    path = tmp_path / "B260714.TXT"
    lines = [b"STARTB", b"24BBGN", "　１０Ｒ  電話投票締切予定１７：４１".encode("cp932")]
    lines.extend(_entry_row(lane, 3200 + lane) for lane in range(1, 7))
    # Venue names in arbitrary content must not alter the section scope.
    lines.extend(["福岡".encode("cp932"), b"23BBGN", "　１２Ｒ  電話投票締切予定１８：０８".encode("cp932")])
    lines.extend(_entry_row(lane, 4200 + lane) for lane in range(1, 7))
    path.write_bytes(b"\r\n".join(lines) + b"\r\n")

    frame = BoatRaceParser.parse_entries_file(path)

    assert len(frame) == 12
    assert set(frame["union_key"]) == {"20260714_24_10", "20260714_23_12"}
    assert frame.groupby("union_key")["lane"].nunique().to_dict() == {
        "20260714_23_12": 6,
        "20260714_24_10": 6,
    }
    assert frame.duplicated(["union_key", "lane"]).sum() == 0
    assert set(frame.loc[frame["union_key"] == "20260714_24_10", "deadline"]) == {"17:41"}
    assert set(frame.loc[frame["union_key"] == "20260714_23_12", "deadline"]) == {"18:08"}
    assert set(frame["source_file"]) == {"B260714.TXT"}
