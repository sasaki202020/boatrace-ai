from __future__ import annotations

import json

import src.external.official_expect as official_expect
from src.external.official_expect import _extract_predictions_from_html


def test_extract_predictions_from_html_extracts_mark_rank_lane_and_player_name() -> None:
    html = """
    <html>
      <body>
        <tbody class="is-fs12 ">
          <tr>
            <td><img src="/static_extra/pc/images/icon_mark1_1.png" width="17" height="17" alt="" /></td>
            <td class="is-fs14 is-boatColor1" rowspan="4">1</td>
            <td rowspan="4"><a href="/owpc/pc/data/racersearch/profile?toban=5303"><img src="/racerphoto/5303.jpg" /></a></td>
            <td rowspan="4">
              <div class="is-fs18 is-fBold"><a href="/owpc/pc/data/racersearch/profile?toban=5303">土井　　歩夢</a></div>
            </td>
          </tr>
        </tbody>
        <tbody class="is-fs12 ">
          <tr>
            <td><img src="/static_extra/pc/images/icon_mark1_3.png" width="17" height="17" alt="" /></td>
            <td class="is-fs14 is-boatColor2" rowspan="4">2</td>
            <td rowspan="4"><a href="/owpc/pc/data/racersearch/profile?toban=4272"><img src="/racerphoto/4272.jpg" /></a></td>
            <td rowspan="4">
              <div class="is-fs18 is-fBold"><a href="/owpc/pc/data/racersearch/profile?toban=4272">大場　　広孝</a></div>
            </td>
          </tr>
        </tbody>
      </body>
    </html>
    """

    rows = _extract_predictions_from_html(html)

    assert rows == [
        {"mark_rank": 1, "lane": 1, "player_name": "土井 歩夢"},
        {"mark_rank": 3, "lane": 2, "player_name": "大場 広孝"},
    ]


def test_extract_predictions_from_html_handles_missing_predictions() -> None:
    html = """
    <html>
      <body>
        <div>予想がまだありません</div>
        <tbody class="is-fs12 ">
          <tr>
            <td>&nbsp;</td>
            <td class="is-fs14 is-boatColor1" rowspan="4">1</td>
          </tr>
        </tbody>
      </body>
    </html>
    """

    rows = _extract_predictions_from_html(html)

    assert rows == []


def test_extract_predictions_from_html_handles_invalid_html() -> None:
    rows = _extract_predictions_from_html("<<< invalid html >>>")
    assert rows == []


def test_fetch_official_expect_writes_iso_json_date_and_date8(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(official_expect, "DATA_ROOT", tmp_path / "data" / "external" / "official_expect")
    monkeypatch.setattr(official_expect, "REPORT_ROOT", tmp_path / "reports" / "external" / "official_expect")

    index_html = """
    <html>
      <body>
        <a href="/owpc/pc/race/pcexpect?rno=1&jcd=01&hd=20260424">桐生 1R</a>
      </body>
    </html>
    """
    race_html = """
    <html>
      <body>
        <tbody class="is-fs12 ">
          <tr>
            <td><img src="/static_extra/pc/images/icon_mark1_1.png" width="17" height="17" alt="" /></td>
            <td class="is-fs14 is-boatColor1" rowspan="4">1</td>
            <td rowspan="4">
              <div class="is-fs18 is-fBold"><a href="/owpc/pc/data/racersearch/profile?toban=5303">土井　　歩夢</a></div>
            </td>
          </tr>
        </tbody>
      </body>
    </html>
    """

    def fake_fetch_html(session, url, *, timeout, retries, retry_sleep):
        if "index?hd=20260424" in url:
            return index_html, "live"
        return race_html, "live"

    monkeypatch.setattr(official_expect, "_fetch_html", fake_fetch_html)

    result = official_expect.fetch_official_expect(target_date="2026-04-24")
    json_path = tmp_path / "data" / "external" / "official_expect" / "20260424" / "official_expect.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert result["date"] == "2026-04-24"
    assert payload["date"] == "2026-04-24"
    assert payload["date8"] == "20260424"
