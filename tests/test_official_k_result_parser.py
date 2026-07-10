from __future__ import annotations

from pathlib import Path

from src.ingest.parsers.official_k_result_parser import parse_official_k_result_file, parse_official_k_result_text


K_FILE = Path(__file__).resolve().parents[1] / "data" / "raw" / "official" / "results" / "K260404.TXT"


def test_official_k_result_parser_reads_real_file() -> None:
    parsed = parse_official_k_result_file(K_FILE)
    assert parsed["sourceType"] == "official_txt_k"
    assert parsed["resultSource"] == "official_txt_k"
    assert parsed["raceCount"] >= 90

    first = parsed["races"][0]
    assert first["date"] == "20260404"
    assert first["jcd"] == "22"
    assert first["rno"] == 1
    assert first["raceStatus"] == "ok"
    assert first["trifectaCombo"] == "1-2-3"
    assert first["trifectaPayout"] == 470
    assert first["finishOrder"][:3] == [1, 2, 3]


def test_official_k_result_parser_normalizes_fullwidth_and_statuses() -> None:
    text = "\n".join(
        [
            "22KBGN",
            "1R テスト H1800m 晴 風 北 3m 波 2cm",
            "３連単 １－２－３ ￥1,230 5人気",
            "22KEND",
            "21KBGN",
            "1R テスト H1800m 晴 風 北 3m 波 2cm",
            "返還",
            "21KEND",
            "20KBGN",
            "1R テスト H1800m 晴 風 北 3m 波 2cm",
            "不成立",
            "20KEND",
        ]
    )
    parsed = parse_official_k_result_text(text=text, source_path="synthetic/K260404.TXT", date8="20260404")
    assert parsed["raceCount"] == 3
    assert parsed["races"][0]["trifectaCombo"] == "1-2-3"
    assert parsed["races"][0]["trifectaPayout"] == 1230
    assert parsed["races"][0]["raceStatus"] == "ok"
    assert "finish_order_missing" in parsed["races"][0]["parseWarnings"]
    assert parsed["races"][1]["raceStatus"] == "refund"
    assert parsed["races"][2]["raceStatus"] == "no_contest"
