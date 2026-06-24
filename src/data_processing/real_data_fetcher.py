"""ボートレース公式サイト(boatrace.jp)からの実データ取得モジュール。

スクレイピングマナー:
- robots.txt を確認し、許可されたパスのみアクセスする
- リクエスト間隔は最低 min_interval_seconds(既定2.0秒)空ける
- User-Agent に連絡先を明記し、失敗時は指数バックオフでリトライする
- 取得済みHTMLは data/raw/ にキャッシュし、再取得しない

注意: パーサーのセレクタは公式サイトの構造に合わせて実装しているが、
サイト改修で構造が変わった場合は各 parse_* メソッドの修正が必要。
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
import urllib.robotparser
import warnings
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# boatrace.jp は XHTML 宣言付きのため lxml が XML と誤認して警告するが、
# HTMLパーサーで問題なく解析できる
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

# 場コード(jcd)と場名の対応
PLACE_CODES = {
    "桐生": 1, "戸田": 2, "江戸川": 3, "平和島": 4, "多摩川": 5, "浜名湖": 6,
    "蒲郡": 7, "常滑": 8, "津": 9, "三国": 10, "びわこ": 11, "住之江": 12,
    "尼崎": 13, "鳴門": 14, "丸亀": 15, "児島": 16, "宮島": 17, "徳山": 18,
    "下関": 19, "若松": 20, "芦屋": 21, "福岡": 22, "唐津": 23, "大村": 24,
}
PLACE_NAMES = {v: k for k, v in PLACE_CODES.items()}


def resolve_place(place: str | int) -> int:
    """場名または場コードを jcd(1〜24)に解決する。"""
    if isinstance(place, int) or str(place).isdigit():
        jcd = int(place)
        if jcd not in PLACE_NAMES:
            raise ValueError(f"不正な場コードです: {place}")
        return jcd
    if place in PLACE_CODES:
        return PLACE_CODES[place]
    raise ValueError(f"不明なレース場です: {place}")


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


class RealDataFetcher:
    """boatrace.jp の出走表・直前情報・結果・オッズを取得する。"""

    def __init__(self,
                 base_url: str = "https://www.boatrace.jp",
                 cache_dir: str = "data/raw",
                 min_interval_seconds: float = 2.0,
                 max_retries: int = 4,
                 backoff_base_seconds: float = 2.0,
                 user_agent: str = "kensho-boat-race-ai/1.0 (research)",
                 session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval_seconds
        self.max_retries = max_retries
        self.backoff_base = backoff_base_seconds
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request_ts = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    # ------------------------------------------------------------------
    # HTTP層(robots / スロットリング / リトライ / キャッシュ)
    # ------------------------------------------------------------------
    def _load_robots(self) -> urllib.robotparser.RobotFileParser:
        if self._robots is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._throttle()
                resp = self.session.get(f"{self.base_url}/robots.txt", timeout=15)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # robots.txt が無い/エラーの場合は全許可として扱う(RFC 9309)
                    rp.parse([])
            except requests.RequestException:
                logger.warning("robots.txt を取得できませんでした。許可として続行します。")
                rp.parse([])
            self._robots = rp
        return self._robots

    def _allowed(self, url: str) -> bool:
        return self._load_robots().can_fetch(self.session.headers["User-Agent"], url)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _cache_path(self, page: str, hd: str, jcd: int, rno: int) -> Path:
        d = self.cache_dir / hd
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{page}_{jcd:02d}_{rno:02d}.html"

    def _get(self, page: str, hd: str, jcd: int, rno: int,
             force_refresh: bool = False) -> str:
        """ページHTMLを取得する。キャッシュ済みなら再取得しない。"""
        cache = self._cache_path(page, hd, jcd, rno)
        if cache.exists() and not force_refresh:
            return cache.read_text(encoding="utf-8")

        url = f"{self.base_url}/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={hd}"
        if not self._allowed(url):
            raise PermissionError(f"robots.txt によりアクセスが禁止されています: {url}")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                wait = self.backoff_base * (2 ** (attempt - 1))
                logger.info("リトライ %d/%d(%.0f秒待機): %s",
                            attempt, self.max_retries, wait, url)
                time.sleep(wait)
            try:
                self._throttle()
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    cache.write_text(resp.text, encoding="utf-8")
                    return resp.text
                if 400 <= resp.status_code < 500:
                    raise requests.HTTPError(
                        f"クライアントエラー {resp.status_code}: {url}")
                last_error = requests.HTTPError(
                    f"サーバーエラー {resp.status_code}: {url}")
            except requests.HTTPError:
                raise
            except requests.RequestException as e:
                last_error = e
        raise ConnectionError(f"取得に失敗しました({self.max_retries}回リトライ済み): "
                              f"{url} — {last_error}")

    # ------------------------------------------------------------------
    # パーサー
    # ------------------------------------------------------------------
    @staticmethod
    def parse_racelist(html: str) -> list[dict]:
        """出走表ページから選手情報を抽出する(lane昇順、最大6艇)。"""
        soup = BeautifulSoup(html, "lxml")
        entries: list[dict] = []
        # 各艇は出走表テーブルの tbody 単位。艇番セル(is-boatColor1〜6)で判定する
        for tbody in soup.select("table tbody"):
            lane_cell = tbody.find(class_=re.compile(r"is-boatColor([1-6])"))
            if lane_cell is None:
                continue
            m = re.search(r"is-boatColor([1-6])", " ".join(lane_cell.get("class", [])))
            if not m:
                continue
            lane = int(m.group(1))
            text = tbody.get_text(" ", strip=True)
            rid = re.search(r"(\d{4})\s*/\s*([AB][12])", text)
            aw = re.search(r"(\d{2})歳\s*/\s*([\d.]+)kg", text)
            # 選手名はプロフィールリンクのうちテキストを持つもの
            # (最初のリンクは写真のため空)
            name = None
            for a in tbody.select('a[href*="racersearch/profile"]'):
                t = a.get_text(strip=True)
                if t:
                    name = re.sub(r"[\s　]+", " ", t)
                    break
            entries.append({
                "lane": lane,
                "racer_id": int(rid.group(1)) if rid else None,
                "grade": rid.group(2) if rid else None,
                "name": name,
                "age": int(aw.group(1)) if aw else None,
                "weight": float(aw.group(2)) if aw else None,
            })
        entries.sort(key=lambda e: e["lane"])
        # 同一laneの重複(ページ内の別テーブル)を除去
        seen, uniq = set(), []
        for e in entries:
            if e["lane"] not in seen:
                seen.add(e["lane"])
                uniq.append(e)
        return uniq

    @staticmethod
    def parse_beforeinfo(html: str) -> dict:
        """直前情報ページから気象と展示タイム・STを抽出する。"""
        soup = BeautifulSoup(html, "lxml")
        info: dict = {"weather": None, "wind_speed": None,
                      "water_temp": None, "wave_height": None,
                      "exhibition_times": {}, "start_times": {}}

        weather_block = soup.select_one(".weather1") or soup
        wtext = weather_block.get_text(" ", strip=True)
        for w in ("晴", "曇", "雨", "雪", "霧"):
            if w in wtext:
                info["weather"] = w
                break
        m = re.search(r"風速[^\d]*([\d.]+)\s*m", wtext)
        if m:
            info["wind_speed"] = float(m.group(1))
        m = re.search(r"水温[^\d]*([\d.]+)\s*℃", wtext)
        if m:
            info["water_temp"] = float(m.group(1))
        m = re.search(r"波高?[^\d]*([\d.]+)\s*cm", wtext)
        if m:
            info["wave_height"] = float(m.group(1))

        # 展示タイム: 各艇の tbody から「艇番 … 6.50」形式の数値を拾う
        for tbody in soup.select("table tbody"):
            lane_cell = tbody.find(class_=re.compile(r"is-boatColor([1-6])"))
            if lane_cell is None:
                continue
            m = re.search(r"is-boatColor([1-6])", " ".join(lane_cell.get("class", [])))
            if not m:
                continue
            lane = int(m.group(1))
            text = tbody.get_text(" ", strip=True)
            t = re.search(r"\b(6\.\d{2}|7\.\d{2})\b", text)
            if t and lane not in info["exhibition_times"]:
                info["exhibition_times"][lane] = float(t.group(1))

        # スタート展示のST: 枠番(boatImage1Number)とタイム(boatImage1Time)の対
        numbers = soup.select(".table1_boatImage1Number")
        times = soup.select(".table1_boatImage1Time")
        for num_el, time_el in zip(numbers, times):
            num_txt = num_el.get_text(strip=True)
            st_txt = time_el.get_text(strip=True)
            m_lane = re.fullmatch(r"[1-6]", num_txt)
            m_st = re.fullmatch(r"(F?)(\.\d{2})", st_txt)
            if m_lane and m_st:
                st = float(m_st.group(2)) * (-1 if m_st.group(1) else 1)
                info["start_times"].setdefault(int(num_txt), st)
        return info

    @staticmethod
    def parse_raceresult(html: str) -> dict:
        """結果ページから着順とST(実測)を抽出する。"""
        soup = BeautifulSoup(html, "lxml")
        result: dict = {"finish": {}, "start_times": {},
                        "result_status": "missing",
                        "unavailable_reason": "missing"}
        for tbody in soup.select("table tbody"):
            for tr in tbody.select("tr"):
                # 着順は全角数字(「１」等)で表記されるため NFKC で正規化する
                cells = [_normalize_text(td.get_text(" ", strip=True))
                         for td in tr.select("td")]
                if len(cells) < 2:
                    continue
                # 着順テーブル: [着, 枠, 選手名, ...]
                m_pos = re.fullmatch(r"([1-6])", cells[0])
                m_lane = re.fullmatch(r"([1-6])", cells[1])
                if m_pos and m_lane:
                    lane = int(m_lane.group(1))
                    result["finish"].setdefault(lane, int(m_pos.group(1)))
        # スタート情報: 「1 .12」のような枠番とSTの組
        text = soup.get_text(" ", strip=True)
        for m in re.finditer(r"\b([1-6])\s+(F?\.\d{2})\b", text):
            lane = int(m.group(1))
            st_txt = m.group(2)
            st = float(st_txt.replace("F", "")) * (-1 if "F" in st_txt else 1)
            result["start_times"].setdefault(lane, st)
        status, reason = RealDataFetcher.classify_result_html(html, result["finish"])
        result["result_status"] = status
        result["unavailable_reason"] = reason
        return result

    @staticmethod
    def classify_result_html(html: str, finish: dict[int, int] | None = None) -> tuple[str, str]:
        """結果HTMLの状態を分類する。final 以外は日次レポートへ理由を渡す。"""
        finish = finish or {}
        if finish:
            return "final", ""
        text = _normalize_text(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
        if any(word in text for word in ("中止", "順延", "打切", "打ち切", "不成立")):
            return "cancelled_or_postponed", "cancelled_or_postponed"
        if any(word in text for word in ("発売中", "締切前", "投票受付", "オッズ")):
            return "unavailable", "unavailable"
        if any(word in text for word in ("データがありません", "本日の開催はありません", "開催はありません")):
            return "no_race_day", "no_race_day"
        return "parse_error", "parse_error"

    @staticmethod
    def parse_oddstf(html: str) -> dict[int, float]:
        """単勝オッズページから {lane: odds} を抽出する。"""
        soup = BeautifulSoup(html, "lxml")
        odds: dict[int, float] = {}
        for tbody in soup.select("table tbody"):
            for tr in tbody.select("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
                if len(cells) < 2:
                    continue
                m_lane = re.fullmatch(r"([1-6])", cells[0])
                m_odds = re.search(r"^(\d{1,3}\.\d)$", cells[-1])
                if m_lane and m_odds:
                    odds.setdefault(int(m_lane.group(1)), float(m_odds.group(1)))
        return odds

    # ------------------------------------------------------------------
    # 高レベルAPI
    # ------------------------------------------------------------------
    def discover_course_ids(self, date: str) -> list[int]:
        """指定日の開催場を出走表ページから重複なしで検出する。"""
        hd = date.replace("-", "")
        observed: list[int] = []
        for jcd in sorted(PLACE_NAMES):
            found = False
            for rno in (1, 2):
                try:
                    html = self._get("racelist", hd, jcd, rno)
                    if self.parse_racelist(html):
                        found = True
                        break
                except (ValueError, ConnectionError, PermissionError, requests.HTTPError) as e:
                    logger.debug("%s %s %sR の開催検出をスキップ: %s",
                                 date, PLACE_NAMES[jcd], rno, e)
            if found:
                observed.append(jcd)
        return observed

    def fetch_race(self, date: str, place: str | int, race_number: int,
                   include_results: bool = True) -> pd.DataFrame:
        """1レース分のデータを取得し、共通スキーマの DataFrame を返す。

        date は 'YYYY-MM-DD'。include_results=False なら未開催レース
        (当日予想用)として結果列を NaN のままにする。
        """
        jcd = resolve_place(place)
        hd = date.replace("-", "")
        race_id = f"{hd}{jcd:02d}{race_number:02d}"

        entries = self.parse_racelist(self._get("racelist", hd, jcd, race_number))
        if not entries:
            raise ValueError(f"出走表を解析できませんでした: {race_id}")
        before = self.parse_beforeinfo(self._get("beforeinfo", hd, jcd, race_number))
        try:
            odds = self.parse_oddstf(self._get("oddstf", hd, jcd, race_number))
        except (ConnectionError, requests.HTTPError):
            odds = {}

        finish: dict[int, int] = {}
        result_st: dict[int, float] = {}
        if include_results:
            res = self.parse_raceresult(self._get("raceresult", hd, jcd, race_number))
            finish = res["finish"]
            result_st = res["start_times"]

        rows = []
        for e in entries:
            lane = e["lane"]
            pos = finish.get(lane)
            rows.append({
                "race_id": race_id,
                "race_date": date,
                "course_id": jcd,
                "race_number": race_number,
                "weather": before["weather"],
                "wind_speed": before["wind_speed"],
                "water_temp": before["water_temp"],
                "wave_height": before["wave_height"],
                "racer_id": e["racer_id"],
                "name": e["name"],
                "age": e["age"],
                "weight": e["weight"],
                "grade": e["grade"],
                "lane": lane,
                "exhibition_time": before["exhibition_times"].get(lane),
                "start_time": result_st.get(lane, before["start_times"].get(lane)),
                "finish_position": pos,
                "win": None if pos is None else int(pos == 1),
                "place": None if pos is None else int(pos <= 2),
                "show": None if pos is None else int(pos <= 3),
                "win_odds": odds.get(lane),
            })
        return pd.DataFrame(rows)

    def fetch_results_for_predictions(self, date: str,
                                      predictions: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
        """保存済み予想に含まれるレースについて結果だけを取得する。"""
        columns = [
            "race_id", "race_date", "course_id", "race_number", "lane",
            "finish_position", "win", "start_time",
            "result_status", "unavailable_reason",
        ]
        if predictions.empty:
            return pd.DataFrame(columns=columns), []

        rows: list[dict] = []
        errors: list[dict] = []
        races = (
            predictions[["race_id", "course_id", "race_number"]]
            .drop_duplicates()
            .sort_values(["course_id", "race_number"])
        )
        hd = date.replace("-", "")
        for race in races.itertuples(index=False):
            race_id = str(race.race_id)
            jcd = int(race.course_id)
            rno = int(race.race_number)
            race_preds = predictions[predictions["race_id"].astype(str) == race_id]
            finish: dict[int, int] = {}
            result_st: dict[int, float] = {}
            status = "missing"
            reason = "missing"
            try:
                parsed = self.parse_raceresult(self._get("raceresult", hd, jcd, rno))
                finish = parsed["finish"]
                result_st = parsed["start_times"]
                status = parsed.get("result_status", "parse_error")
                reason = parsed.get("unavailable_reason", "parse_error")
            except (ValueError, ConnectionError, PermissionError, requests.HTTPError) as e:
                errors.append({
                    "race_id": race_id,
                    "course_id": jcd,
                    "race_number": rno,
                    "reason": reason,
                    "message": str(e),
                })

            if status != "final":
                errors.append({
                    "race_id": race_id,
                    "course_id": jcd,
                    "race_number": rno,
                    "reason": reason,
                    "message": status,
                })

            for pred in race_preds.itertuples(index=False):
                lane = int(pred.lane)
                pos = finish.get(lane)
                rows.append({
                    "race_id": race_id,
                    "race_date": date,
                    "course_id": jcd,
                    "race_number": rno,
                    "lane": lane,
                    "finish_position": pos,
                    "win": None if pos is None else int(pos == 1),
                    "start_time": result_st.get(lane),
                    "result_status": status,
                    "unavailable_reason": "" if status == "final" else reason,
                })
        return pd.DataFrame(rows, columns=columns), errors

    def fetch_day(self, date: str, place: str | int,
                  include_results: bool = True,
                  race_numbers: list[int] | None = None) -> pd.DataFrame:
        """1開催日・1場の全レース(既定1〜12R)を取得する。

        序盤のレースが連続して解析できない場合は非開催日と判断し、
        残りのレースへのアクセスを打ち切る。
        """
        frames = []
        consecutive_failures = 0
        for rno in race_numbers or range(1, 13):
            try:
                frames.append(self.fetch_race(date, place, rno, include_results))
                consecutive_failures = 0
            except (ValueError, ConnectionError, requests.HTTPError) as e:
                logger.warning("%s %sR の取得をスキップ: %s", date, rno, e)
                consecutive_failures += 1
                if not frames and consecutive_failures >= 2:
                    logger.info("%s %s: 非開催日と判断して残りをスキップします",
                                date, place)
                    break
        return pd.concat(frames, ignore_index=True) if frames \
            else pd.DataFrame()

    def fetch_period(self, start_date: str, end_date: str,
                     places: list[str | int]) -> pd.DataFrame:
        """期間×場のデータを一括取得する(学習データ収集用)。"""
        frames = []
        for d in pd.date_range(start_date, end_date):
            for p in places:
                df = self.fetch_day(d.strftime("%Y-%m-%d"), p)
                if len(df):
                    frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames \
            else pd.DataFrame()
