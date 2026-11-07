from __future__ import annotations

from src.evaluation.backtest_day import build_backtest_summary
from src.evaluation.settle_results import _extract_pay_results


def test_extract_pay_results_parses_combo_and_payout() -> None:
    html = """
    <html><body>
    <table><tbody>
      <tr>
        <th>12R</th>
        <td data-href="/owpc/pc/race/raceresult?rno=12&jcd=24&hd=20260424">1 | 2 | 3</td>
        <td data-href="/owpc/pc/race/raceresult?rno=12&jcd=24&hd=20260424">¥590</td>
        <td data-href="/owpc/pc/race/raceresult?rno=12&jcd=24&hd=20260424">1</td>
      </tr>
    </tbody></table>
    </body></html>
    """
    results = _extract_pay_results(html, source_url="https://example.invalid", source_status="live", date="2026-04-24")
    assert ("24", 12) in results
    item = results[("24", 12)]
    assert item.combo == "1-2-3"
    assert item.payout == 590
    assert item.popularity == 1


def test_build_backtest_summary_aggregates_metrics() -> None:
    summary = build_backtest_summary(
        {
            "date": "2026-04-24",
            "jcd": "24",
            "raceCount": 1,
            "buyCount": 2,
            "hitCount": 1,
            "stakeAmount": 200.0,
            "payoutAmount": 590.0,
            "resultsStatus": "available",
            "settlements": [
                {
                    "buyCount": 2,
                    "hitCount": 1,
                    "stakeAmount": 200.0,
                    "payoutAmount": 590.0,
                }
            ],
            "generatedAt": "2026-04-24T00:00:00",
        }
    )
    assert summary["hitRate"] == 0.5
    assert summary["recoveryRate"] == 2.95
    assert summary["roi"] == 1.95
