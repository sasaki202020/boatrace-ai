"""real_data_fetcher.py のテスト(通信はすべてモック)。"""
from __future__ import annotations

import time
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_processing.real_data_fetcher import (
    PLACE_CODES, RealDataFetcher, resolve_place,
)

# ----------------------------------------------------------------------
# フィクスチャHTML(公式サイトの構造を簡略化して再現)
# ----------------------------------------------------------------------

def _racelist_html():
    rows = ""
    for lane, (rid, grade, name, age, weight) in enumerate([
        (4001, "A1", "山田太郎", 34, 52.0), (4002, "A2", "鈴木次郎", 28, 51.5),
        (4003, "B1", "佐藤三郎", 41, 53.0), (4004, "B1", "田中四郎", 25, 52.5),
        (4005, "B2", "高橋五郎", 30, 54.0), (4006, "A1", "伊藤六郎", 38, 51.0),
    ], start=1):
        rows += f"""
        <tbody>
          <tr>
            <td class="is-boatColor{lane}">{lane}</td>
            <td><div><a href="/owpc/pc/data/racersearch/profile?toban={rid}"></a></div>
                <div>{rid} / {grade}</div>
                <div><a href="/owpc/pc/data/racersearch/profile?toban={rid}">{name}</a></div>
                <div>東京/東京 {age}歳/{weight}kg</div></td>
          </tr>
        </tbody>"""
    return f"<html><body><table>{rows}</table></body></html>"


def _beforeinfo_html():
    rows = ""
    times = [6.71, 6.80, 6.75, 6.90, 6.85, 6.78]
    for lane, t in enumerate(times, start=1):
        rows += f"""
        <tbody>
          <tr><td class="is-boatColor{lane}">{lane}</td><td>{t:.2f}</td></tr>
        </tbody>"""
    sts = [".07", ".04", ".08", ".05", ".04", "F.01"]
    st_rows = "".join(
        f'<span class="table1_boatImage1Number">{lane}</span>'
        f'<span class="table1_boatImage1Time">{st}</span>'
        for lane, st in enumerate(sts, start=1)
    )
    return f"""<html><body>
      <div class="weather1">天候 曇 風速 3.0m 水温 18.5℃ 波高 2cm</div>
      <table>{rows}</table>
      <div>{st_rows}</div>
    </body></html>"""


def _raceresult_html():
    # 着順は実サイトと同様に全角数字で表記する
    finish = {1: 1, 2: 4, 3: 2, 4: 6, 5: 5, 6: 3}
    rows = "".join(
        f"<tr><td>{''.join(chr(0xFF10 + int(c)) for c in str(pos))}</td>"
        f"<td>{lane}</td><td>選手{lane}</td></tr>"
        for lane, pos in sorted(finish.items(), key=lambda kv: kv[1])
    )
    return f"<html><body><table><tbody>{rows}</tbody></table></body></html>"


def _oddstf_html():
    odds = {1: 1.5, 2: 8.2, 3: 4.6, 4: 25.0, 5: 31.5, 6: 6.9}
    rows = "".join(
        f"<tr><td>{lane}</td><td>選手{lane}</td><td>{o:.1f}</td></tr>"
        for lane, o in odds.items()
    )
    return f"<html><body><table><tbody>{rows}</tbody></table></body></html>"


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"


class FakeSession:
    """URLごとにフィクスチャHTMLを返す疑似セッション。"""

    def __init__(self, robots_txt="User-agent: *\nAllow: /"):
        self.headers = {}
        self.robots_txt = robots_txt
        self.requested_urls = []

    def get(self, url, timeout=None):
        self.requested_urls.append(url)
        if url.endswith("robots.txt"):
            return FakeResponse(self.robots_txt)
        if "/racelist" in url:
            return FakeResponse(_racelist_html())
        if "/beforeinfo" in url:
            return FakeResponse(_beforeinfo_html())
        if "/raceresult" in url:
            return FakeResponse(_raceresult_html())
        if "/oddstf" in url:
            return FakeResponse(_oddstf_html())
        return FakeResponse("not found", 404)


@pytest.fixture
def fetcher(tmp_path):
    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=0.0,  # テストでは待機しない
                        session=FakeSession())
    return f


# ----------------------------------------------------------------------
def test_resolve_place():
    assert resolve_place("桐生") == 1
    assert resolve_place("大村") == 24
    assert resolve_place(12) == 12
    assert resolve_place("05") == 5
    with pytest.raises(ValueError):
        resolve_place("存在しない場")
    assert len(PLACE_CODES) == 24


def test_parse_racelist():
    entries = RealDataFetcher.parse_racelist(_racelist_html())
    assert len(entries) == 6
    assert entries[0] == {"lane": 1, "racer_id": 4001, "grade": "A1",
                          "name": "山田太郎", "age": 34, "weight": 52.0}
    assert [e["lane"] for e in entries] == [1, 2, 3, 4, 5, 6]


def test_parse_beforeinfo():
    info = RealDataFetcher.parse_beforeinfo(_beforeinfo_html())
    assert info["weather"] == "曇"
    assert info["wind_speed"] == 3.0
    assert info["water_temp"] == 18.5
    assert info["wave_height"] == 2.0
    assert info["exhibition_times"][1] == 6.71
    assert len(info["exhibition_times"]) == 6
    assert info["start_times"][1] == 0.07
    assert info["start_times"][6] == -0.01  # フライングは負値


def test_parse_raceresult():
    res = RealDataFetcher.parse_raceresult(_raceresult_html())
    assert res["finish"] == {1: 1, 2: 4, 3: 2, 4: 6, 5: 5, 6: 3}
    assert res["result_status"] == "final"
    assert res["unavailable_reason"] == ""


def test_parse_raceresult_unavailable_status():
    res = RealDataFetcher.parse_raceresult("<html><body>発売中 オッズ</body></html>")
    assert res["finish"] == {}
    assert res["result_status"] == "unavailable"
    assert res["unavailable_reason"] == "unavailable"


def test_parse_raceresult_cancelled_status():
    res = RealDataFetcher.parse_raceresult("<html><body>荒天のため中止</body></html>")
    assert res["finish"] == {}
    assert res["result_status"] == "cancelled_or_postponed"
    assert res["unavailable_reason"] == "cancelled_or_postponed"


def test_parse_oddstf():
    odds = RealDataFetcher.parse_oddstf(_oddstf_html())
    assert odds[1] == 1.5
    assert odds[4] == 25.0
    assert len(odds) == 6


def test_fetch_race_schema(fetcher):
    df = fetcher.fetch_race("2026-06-12", "桐生", 1)
    assert len(df) == 6
    assert df["race_id"].iloc[0] == "202606120101"
    assert df["course_id"].iloc[0] == 1
    assert df["weather"].iloc[0] == "曇"
    # 1号艇が1着 → win=1
    row1 = df[df["lane"] == 1].iloc[0]
    assert row1["finish_position"] == 1 and row1["win"] == 1
    assert row1["win_odds"] == 1.5
    assert (df.groupby("race_id")["win"].sum() == 1).all()


def test_fetch_race_without_results(fetcher):
    df = fetcher.fetch_race("2026-06-12", 1, 2, include_results=False)
    assert df["finish_position"].isna().all()
    assert df["win"].isna().all()
    # 出走表・直前情報・オッズは取得済み
    assert df["exhibition_time"].notna().all()
    assert df["win_odds"].notna().all()


def test_fetch_results_for_predictions(fetcher):
    predictions = pd.DataFrame({
        "race_id": ["202606120101"] * 6,
        "course_id": [1] * 6,
        "race_number": [1] * 6,
        "lane": [1, 2, 3, 4, 5, 6],
    })
    results, errors = fetcher.fetch_results_for_predictions("2026-06-12", predictions)
    assert errors == []
    assert len(results) == 6
    assert set(results["result_status"]) == {"final"}
    assert results[results["lane"] == 1]["win"].iloc[0] == 1


def test_cache_prevents_refetch(fetcher):
    fetcher.fetch_race("2026-06-12", 1, 1)
    n_first = len(fetcher.session.requested_urls)
    fetcher.fetch_race("2026-06-12", 1, 1)  # 2回目はキャッシュから
    assert len(fetcher.session.requested_urls) == n_first
    # robots.txt + 4ページ分のリクエストのみ
    assert n_first == 5


def test_robots_disallow_blocks_fetch(tmp_path):
    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=0.0,
                        session=FakeSession("User-agent: *\nDisallow: /owpc/"))
    with pytest.raises(PermissionError):
        f.fetch_race("2026-06-12", 1, 1)


def test_throttle_enforces_interval(tmp_path):
    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=2.0,
                        session=FakeSession())
    sleeps = []
    with patch.object(time, "sleep", side_effect=lambda s: sleeps.append(s)):
        f.fetch_race("2026-06-12", 1, 1)
    # 連続リクエスト間で約2秒の待機が入る(初回を除く4回)
    assert len(sleeps) >= 4
    assert all(1.9 <= s <= 2.0 for s in sleeps)


def test_retry_with_backoff(tmp_path):
    class FlakySession(FakeSession):
        def __init__(self):
            super().__init__()
            self.failures = 2

        def get(self, url, timeout=None):
            if "/racelist" in url and self.failures > 0:
                self.failures -= 1
                return FakeResponse("error", 503)
            return super().get(url, timeout)

    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=0.0,
                        backoff_base_seconds=2.0,
                        session=FlakySession())
    sleeps = []
    with patch.object(time, "sleep", side_effect=lambda s: sleeps.append(s)):
        df = f.fetch_race("2026-06-12", 1, 1)
    assert len(df) == 6
    # 指数バックオフ: 2秒 → 4秒
    assert sleeps[:2] == [2.0, 4.0]


def test_fetch_day_skips_rest_on_non_race_day(tmp_path):
    """序盤2レースの出走表が解析できない日は残りを取得しない。"""
    empty_session = FakeSession()
    empty_session.get = lambda url, timeout=None: FakeResponse(
        "User-agent: *\nAllow: /" if url.endswith("robots.txt")
        else "<html><body>本日の開催はありません</body></html>")
    requested = []
    orig_get = empty_session.get
    empty_session.get = lambda url, timeout=None: (
        requested.append(url), orig_get(url))[1]
    empty_session.headers = {}
    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=0.0, session=empty_session)
    df = f.fetch_day("2026-06-12", "桐生")
    assert df.empty
    racelist_urls = [u for u in requested if "/racelist" in u]
    assert len(racelist_urls) == 2  # 1R・2Rで打ち切り


def test_discover_course_ids_returns_unique_race_days(tmp_path):
    class SelectiveSession(FakeSession):
        def get(self, url, timeout=None):
            self.requested_urls.append(url)
            if url.endswith("robots.txt"):
                return FakeResponse(self.robots_txt)
            if "/racelist" in url and ("jcd=01" in url or "jcd=12" in url):
                return FakeResponse(_racelist_html())
            return FakeResponse("<html><body>本日の開催はありません</body></html>")

    session = SelectiveSession()
    f = RealDataFetcher(cache_dir=str(tmp_path / "raw"),
                        min_interval_seconds=0.0, session=session)
    assert f.discover_course_ids("2026-06-12") == [1, 12]


# ----------------------------------------------------------------------
# 実サイトから採取したページに対する回帰テスト
# (.github/workflows/boatrace-sample-pages.yml で採取)
# ----------------------------------------------------------------------
from pathlib import Path

REAL_PAGES = Path(__file__).parent / "fixtures" / "real_pages" / "20260610_01_1"
needs_real_pages = pytest.mark.skipif(
    not REAL_PAGES.exists(), reason="実ページフィクスチャがありません")


def _real(page: str) -> str:
    return (REAL_PAGES / f"{page}.html").read_text(encoding="utf-8")


@needs_real_pages
def test_parse_real_racelist():
    entries = RealDataFetcher.parse_racelist(_real("racelist"))
    assert [e["lane"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert all(e["name"] for e in entries)
    assert all(e["racer_id"] and 1000 <= e["racer_id"] <= 9999 for e in entries)
    assert entries[0]["racer_id"] == 3072
    assert entries[5]["grade"] == "A1"


@needs_real_pages
def test_parse_real_beforeinfo():
    info = RealDataFetcher.parse_beforeinfo(_real("beforeinfo"))
    assert info["weather"] == "曇"
    assert len(info["exhibition_times"]) == 6
    assert len(info["start_times"]) == 6
    assert info["start_times"][6] == -0.01  # F.01 → 負値


@needs_real_pages
def test_parse_real_raceresult():
    res = RealDataFetcher.parse_raceresult(_real("raceresult"))
    assert res["finish"] == {6: 1, 1: 2, 4: 3, 2: 4, 3: 5, 5: 6}


@needs_real_pages
def test_parse_real_oddstf():
    odds = RealDataFetcher.parse_oddstf(_real("oddstf"))
    assert len(odds) == 6
    assert odds[1] == 1.3
