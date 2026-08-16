from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.ingest import official_fetcher
from src.ingest.browser_fetcher import BrowserFetchResult


def _beforeinfo_html() -> str:
    return """
    <html>
      <body>
        <div class="weather1_bodyUnit is-direction"><div class="weather1_bodyUnitLabelTitle">気温</div><div class="weather1_bodyUnitLabelData">24.5</div><div class="weather1_bodyUnitImage is-direction14"></div></div>
        <div class="weather1_bodyUnit is-weather"><div class="weather1_bodyUnitLabelTitle">天候</div><div class="weather1_bodyUnitLabelData">晴れ</div></div>
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


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 4, 25, tzinfo=tz)


def test_fetch_beforeinfo_html_uses_browser_when_today_cache_is_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(official_fetcher, "RAW_ROOT", tmp_path)
    monkeypatch.setattr(official_fetcher, "datetime", _FixedDatetime)
    monkeypatch.setattr(
        official_fetcher,
        "parse_beforeinfo_html",
        lambda html, target_date, jcd, race_no: {
            "dataStatus": "ok" if "晴れ" in html else "pending",
            "dataStatusReason": [],
            "missingReason": [],
            "parseWarnings": [],
            "beforeInfo": {"weather": {"temperature": 24.5}, "startExhibition": [{"no": 1, "course": 1}]},
        },
    )

    cache_path = tmp_path / "20260425" / "01" / "beforeinfo_01.html"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("<html><body>データがありません</body></html>", encoding="utf-8")

    def fake_browser(url: str, *, timeout: float, output_path: Path | None = None):
        html = _beforeinfo_html()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
        return BrowserFetchResult(url=url, html=html, fetch_status="live", source="playwright")

    monkeypatch.setattr(official_fetcher, "fetch_html_with_browser", fake_browser)
    monkeypatch.setattr(official_fetcher, "_fetch_html", lambda *args, **kwargs: ("", "unavailable"))

    result = official_fetcher.fetch_beforeinfo_html(target_date="20260425", jcd="01", race_no=1)

    assert result["fetchStatus"] == "live"
    assert result["fallbackUsed"] is True
    assert result["dataStatus"] == "ok"
    assert result["beforeInfo"]["weather"]["temperature"] == 24.5
    assert result["beforeinfoFallbackUsed"] is True


def test_fetch_odds3t_html_uses_browser_when_today_cache_is_partial(tmp_path, monkeypatch, odds3t_html):
    monkeypatch.setattr(official_fetcher, "RAW_ROOT", tmp_path)
    monkeypatch.setattr(official_fetcher, "datetime", _FixedDatetime)
    cache_path = tmp_path / "20260425" / "01" / "odds3t_01.html"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("<html><body>発売前</body></html>", encoding="utf-8")

    browser_html = odds3t_html

    def fake_browser(url: str, *, timeout: float, output_path: Path | None = None):
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(browser_html, encoding="utf-8")
        return BrowserFetchResult(url=url, html=browser_html, fetch_status="live", source="playwright")

    monkeypatch.setattr(official_fetcher, "fetch_html_with_browser", fake_browser)
    monkeypatch.setattr(official_fetcher, "_fetch_html", lambda *args, **kwargs: ("", "unavailable"))

    result = official_fetcher.fetch_odds3t_html(
        target_date="20260425",
        jcd="01",
        race_no=1,
        race_id="20260425-01-01",
    )

    assert result["fetchStatus"] == "live"
    assert result["fallbackUsed"] is True
    assert result["dataStatus"] == "available"
    assert result["parsedOddsCount"] > 0
    assert result["parsed"]
