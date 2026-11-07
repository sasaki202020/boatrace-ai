from __future__ import annotations

from pathlib import Path

from src.ingest.parsers.result_parser import parse_result_html


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "result"


def _read(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def test_result_parser_pc_normal_extracts_combo_and_payout() -> None:
    parsed = parse_result_html(_read("result_pc_normal.html"), "20260425-24-01")
    assert parsed["dataStatus"] == "ok"
    assert parsed["raceStatus"] == "ok"
    assert parsed["trifectaCombo"] == "1-6-3"
    assert parsed["trifectaPayout"] == 310
    assert parsed["trifectaPopularity"] == 1
    assert parsed["finishOrder"] == [1, 2, 3, 4, 5, 6]
    assert parsed["detectedVariant"] == "structured_table"
    assert parsed["parserPathUsed"] == "structured_table"
    assert parsed["detectedTables"] >= 2


def test_result_parser_sp_normal_extracts_combo_and_payout() -> None:
    parsed = parse_result_html(_read("result_sp_normal.html"), "20260425-24-01")
    assert parsed["trifectaCombo"] == "1-6-3"
    assert parsed["trifectaPayout"] == 310
    assert parsed["raceStatus"] in {"ok", "available", "available_without_trifecta"}


def test_result_parser_text_fallback_and_fullwidth_normalization() -> None:
    parsed = parse_result_html(_read("result_text_only_payout.html"), "20260425-24-01")
    assert parsed["trifectaCombo"] == "1-6-3"
    assert parsed["trifectaPayout"] == 310
    assert parsed["parserPathUsed"] in {"text_fallback", "structured_table"}

    parsed_fullwidth = parse_result_html(_read("result_fullwidth_combo.html"), "20260425-24-01")
    assert parsed_fullwidth["trifectaCombo"] == "1-2-3"
    assert parsed_fullwidth["trifectaPayout"] == 1230


def test_result_parser_ok_without_finish_order_is_allowed() -> None:
    html = """
    <html><body>
    <table>
      <thead><tr><th>返還</th></tr></thead>
      <tbody><tr><td>返還</td></tr></tbody>
    </table>
    <div>3連単 1-2-3 ¥590 1人気</div>
    <div>払戻金 ¥590</div>
    </body></html>
    """
    parsed = parse_result_html(html, "20260425-24-01")
    assert parsed["trifectaCombo"] == "1-2-3"
    assert parsed["trifectaPayout"] == 590
    assert parsed["raceStatus"] == "ok"
    assert "finish_order_missing" in parsed["parseWarnings"]
    assert "result_refund_marker_ignored" in parsed["parseWarnings"]


def test_result_parser_pending_refund_cancel_and_no_trifecta() -> None:
    pending = parse_result_html(_read("result_before_publish.html"), "20260425-24-01")
    assert pending["raceStatus"] == "pending"
    assert pending["dataStatus"] == "pending"

    refund = parse_result_html(_read("result_refund.html"), "20260425-24-01")
    assert refund["raceStatus"] == "ok"
    assert "result_refund_marker_ignored" in refund["parseWarnings"]

    refund_only = parse_result_html(
        """
        <html><body>
          <div>返還</div>
          <div>払戻金 返還</div>
        </body></html>
        """,
        "20260425-24-01",
    )
    assert refund_only["raceStatus"] == "refund"

    canceled = parse_result_html(_read("result_cancel.html"), "20260425-24-01")
    assert canceled["raceStatus"] == "canceled"

    no_trifecta = parse_result_html(_read("result_no_trifecta.html"), "20260425-24-01")
    assert no_trifecta["raceStatus"] in {"available_without_trifecta", "parse_error"}
    assert "result_parse_no_trifecta_combo" in no_trifecta["parseWarnings"] or no_trifecta["trifectaCombo"] is None


def test_result_parser_missing_has_warnings() -> None:
    missing = parse_result_html("", "20260425-24-01")
    assert missing["dataStatus"] == "unavailable"
    assert "empty_html" in missing["parseWarnings"]
