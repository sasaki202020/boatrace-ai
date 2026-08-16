from __future__ import annotations

from pathlib import Path

from src.ingest.parsers import beforeinfo_parser
from src.pipeline.boatrace_official_pipeline import BoatStats


HTML = """
<html>
  <body>
    <div class="weather1_bodyUnit is-direction"><div class="weather1_bodyUnitLabelTitle">気温</div><div class="weather1_bodyUnitLabelData">24.5</div><div class="weather1_bodyUnitImage is-direction14"></div></div>
    <div class="weather1_bodyUnit is-weather"><div class="weather1_bodyUnitLabelTitle">天候</div><div class="weather1_bodyUnitLabelData">晴れ</div><div class="weather1_bodyUnitImage is-weather7"></div></div>
    <div class="weather1_bodyUnit is-wind"><div class="weather1_bodyUnitLabelTitle">風速</div><div class="weather1_bodyUnitLabelData">3.2</div></div>
    <div class="weather1_bodyUnit is-waterTemperature"><div class="weather1_bodyUnitLabelTitle">水温</div><div class="weather1_bodyUnitLabelData">20.1</div></div>
    <div class="weather1_bodyUnit is-wave"><div class="weather1_bodyUnitLabelTitle">波高</div><div class="weather1_bodyUnitLabelData">1.0</div></div>
    <table>
      <tr><th>スタート展示</th></tr>
      <tr><th>コース</th><th>並び</th><th>ST</th></tr>
    </table>
  </body>
</html>
"""


def _fake_detail_parser(html: str, boats: dict[int, BoatStats]) -> dict[str, object]:
    boat = boats.setdefault(1, BoatStats(no=1))
    boat.name = "テスト選手"
    boat.exhibition_time = 6.78
    boat.start_display_st = "0.12"
    boat.tilt = -0.5
    boat.parts = ["P1"]
    return {"weather": {}, "startExhibition": []}


def test_parse_beforeinfo_html_returns_weather_and_start_exhibition(monkeypatch):
    monkeypatch.setattr(beforeinfo_parser, "parse_beforeinfo_detail", _fake_detail_parser)
    parsed = beforeinfo_parser.parse_beforeinfo_html(HTML, "20260425", "01", 1)

    assert parsed["dataStatus"] in {"ok", "pending"}
    assert parsed["weather"]["temperature"] == 24.5
    assert parsed["weather"]["water"]["temperature"] == 20.1
    assert parsed["startExhibition"]
    assert parsed["startExhibition"][0]["time"] == 6.78
    assert parsed["boats"][0]["exhibitionTime"] == 6.78


def test_parse_beforeinfo_html_treats_zero_exhibition_time_as_missing(monkeypatch):
    def _detail_zero(html: str, boats: dict[int, BoatStats]) -> dict[str, object]:
        boat = boats.setdefault(1, BoatStats(no=1))
        boat.exhibition_time = 0.0
        boat.start_display_st = "F.01"
        return {"weather": {}, "startExhibition": []}

    monkeypatch.setattr(beforeinfo_parser, "parse_beforeinfo_detail", _detail_zero)
    parsed = beforeinfo_parser.parse_beforeinfo_html(HTML, "20260425", "01", 1)

    assert parsed["startExhibition"][0]["time"] is None


def test_real_fixture_parses_exhibition_table_values():
    fixture = Path(__file__).parent / "fixtures" / "real_pages" / "20260610_01_1" / "beforeinfo.html"
    parsed = beforeinfo_parser.parse_beforeinfo_html(fixture.read_text(encoding="utf-8"), "20260610", "01", 1)
    assert [boat["exhibitionTime"] for boat in parsed["boats"]] == [6.78, 6.77, 6.80, 6.67, 6.82, 6.74]
    assert all(boat["tilt"] is not None for boat in parsed["boats"])
    assert [row["course"] for row in parsed["startExhibition"]] == [1, 2, 3, 4, 5, 6]
    assert parsed["weather"]["sky"] in {"晴", "曇り", "雨"}
