import os
import re
from typing import List, Dict

import pandas as pd


class OfficialTxtParser:
    """
    公式固定長テキストから Gate 2 用の最小 canonical DataFrame を生成する。
    現状は K*.TXT の着順付き帳票を historical として扱い、
    KBN*.TXT 相当の番組表は today として最小抽出する。
    """

    RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})R")
    DATE_RE = re.compile(r"(20\d{2})/\s*(\d{1,2})/\s*(\d{1,2})")
    ENTRY_LINE_RE = re.compile(
        r"^\s*(\d)\s+(\d{4}).*?(A1|A2|B1|B2)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+(\d+)\s+([0-9.]+)\s+(\d+)\s+([0-9.]+)"
    )
    PAYOUT_TRIFECTA_RE = re.compile(r"^\s*(\d{1,2})R\s+\d-\d-\d\s+([0-9,]+)")
    FULLWIDTH_TRANS = str.maketrans({
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "　": " ",
        "．": ".",
        "／": "/",
        "－": "-",
        "ー": "-",
        "，": ",",
    })

    def parse(self, input_path: str, raw_kind: str = "kse_txt") -> Dict:
        basename = os.path.basename(input_path)
        with open(input_path, "r", encoding="cp932", errors="replace") as f:
            lines = [line.rstrip("\r\n") for line in f]

        if raw_kind == "fan_txt":
            # fan*.txt は日付ヘッダを持たないため date は不要
            race_rows, warnings = self._extract_fan_rows(input_path)
            file_type = "master"
        else:
            date_value = self._extract_date(lines, basename)
            jcd_value = self._extract_jcd(lines)
            if raw_kind == "kbn_txt":
                race_rows, warnings = self._extract_entry_rows(lines, date_value, basename, jcd_value)
                file_type = "today"
            else:
                race_rows, warnings = self._extract_race_rows(lines, date_value, basename, include_results=True, jcd_value=jcd_value)
                file_type = "historical"

        if not race_rows:
            raise ValueError(f"No race result rows could be parsed from {basename}")

        df = pd.DataFrame(race_rows)
        return {
            "dataframe": df,
            "file_type": file_type,
            "warnings": [
                "Parsed from official TXT with minimal field extraction; unavailable columns are left blank"
            ] + warnings,
        }

    def _extract_date(self, lines: List[str], basename: str) -> str:
        for line in lines:
            match = self.DATE_RE.search(line)
            if match:
                year, month, day = match.groups()
                return f"{year}-{int(month):02d}-{int(day):02d}"
        file_match = re.search(r"(\d{2})(\d{2})(\d{2})", basename)
        if file_match:
            year, month, day = file_match.groups()
            return f"20{year}-{month}-{day}"
        raise ValueError("Date header not found in official TXT")

    def _extract_jcd(self, lines: List[str]) -> str | None:
        """
        行頭に '24KBGN' / '24BBGN' のような施設コードが入っているため、先頭数値2桁を jcd として取得する。
        """
        for line in lines[:5]:
            line = line.strip()
            if len(line) >= 2 and line[:2].isdigit():
                return line[:2]
        return None

    def _extract_race_rows(self, lines: List[str], date_value: str, basename: str, include_results: bool = True, jcd_value: str | None = None):
        rows = []
        warnings = []
        current_race_no = None
        venue_base = os.path.splitext(basename)[0]
        in_result_block = False
        section_index = 0
        last_race_no = None
        if include_results:
            trifecta_payout_map, exacta_payout_map, quinella_payout_map = self._extract_payout_maps(lines)
        else:
            trifecta_payout_map, exacta_payout_map, quinella_payout_map = {}, {}, {}

        for line_no, line in enumerate(lines, start=1):
            normalized_line = self._normalize_text(line)
            header_match = self.RACE_HEADER_RE.match(normalized_line)
            if header_match:
                current_race_no = int(header_match.group(1))
                if current_race_no == 1 and last_race_no not in (None, 1):
                    section_index += 1
                elif section_index == 0:
                    section_index = 1
                last_race_no = current_race_no
                in_result_block = False
                continue

            if current_race_no is not None and normalized_line.replace("-", "").strip() == "":
                in_result_block = True
                continue

            if in_result_block and normalized_line.strip() == "":
                in_result_block = False
                continue

            tokens = [token for token in re.split(r"\s+", normalized_line.strip()) if token]
            if current_race_no is None or not in_result_block or len(tokens) < 8:
                if current_race_no is not None and in_result_block and normalized_line.strip() and len(tokens) < 8:
                    warnings.append(
                        f"{basename}: race {current_race_no} line {line_no} skipped malformed result row (too few tokens): {line.strip()}"
                    )
                continue

            try:
                venue = f"{venue_base}_s{section_index:02d}"

                if include_results:
                    if not (tokens[0].isdigit() and len(tokens[0]) == 2 and tokens[1].isdigit() and tokens[2].isdigit()):
                        warnings.append(
                            f"{basename}: race {current_race_no} line {line_no} skipped malformed result row: {line.strip()}"
                        )
                        continue
                    finish_position = tokens[0]
                    lane = tokens[1]
                    racer_id = tokens[2]

                    if len(tokens) >= 11 and tokens[-1] == "." and tokens[-2] == ".":
                        motor_no = tokens[-7]
                        boat_no = tokens[-6]
                        national_win_rate = tokens[-5]
                        st_value = tokens[-3]  # This is the actual race ST
                        avg_st = pd.NA        # Set to NA, to be filled by Fanbook master
                        exhibition_time = pd.NA
                    else:
                        motor_no = tokens[-6]
                        boat_no = tokens[-5]
                        national_win_rate = tokens[-4]
                        st_value = tokens[-2]  # This is the actual race ST
                        avg_st = pd.NA        # Set to NA, to be filled by Fanbook master
                        exhibition_time = tokens[-1]
                else:
                    if not (tokens[0].isdigit() and tokens[1].isdigit()):
                        continue
                    finish_position = pd.NA
                    lane = tokens[0]
                    racer_id = tokens[1]
                    motor_no = tokens[-5] if len(tokens) >= 5 else pd.NA
                    boat_no = tokens[-4] if len(tokens) >= 4 else pd.NA
                    national_win_rate = tokens[-3] if len(tokens) >= 3 else pd.NA
                    st_value = tokens[-2] if len(tokens) >= 2 else pd.NA
                    avg_st = pd.NA
                    exhibition_time = tokens[-1] if len(tokens) >= 1 else pd.NA

                race_id = f"{date_value.replace('-', '')}-{venue}-{current_race_no:02d}"

                row = {
                    "race_id": race_id,
                    "date": date_value,
                    "jcd": jcd_value,
                    "venue": venue,
                    "race_no": current_race_no,
                    "lane": int(lane),
                    "racer_id": int(racer_id),
                    "racer_class": "",
                    "avg_st": avg_st,
                    "national_win_rate": self._to_float(national_win_rate),
                    "national_2ren_rate": pd.NA,
                    "local_2ren_rate": pd.NA,
                    "motor_no": int(motor_no),
                    "motor_2ren_rate": pd.NA,
                    "boat_no": int(boat_no),
                    "boat_2ren_rate": pd.NA,
                    "season": pd.NA,
                    "day_number": pd.NA,
                    "exhibition_time": self._normalize_exhibition_time(exhibition_time),
                    "body_weight": pd.NA,
                    "tilt": pd.NA,
                    "parts_change_flag": pd.NA,
                    "propeller_new_flag": pd.NA,
                    "prev_race_course": pd.NA,
                    "prev_race_st": pd.NA,
                    "prev_race_finish": pd.NA,
                    "start_display_st": self._to_float(st_value),
                    "wind_speed": pd.NA,
                    "weather": pd.NA,
                    "water_temp": pd.NA,
                    "wave_height": pd.NA,
                    # 払戻金(100円基準)をオッズ相当へ変換して保持
                    "odds_trifecta": trifecta_payout_map.get((section_index, current_race_no), pd.NA),
                    "odds_exacta": exacta_payout_map.get((section_index, current_race_no), pd.NA),
                    # alias: downstream 互換
                    "odds_2rentan": exacta_payout_map.get((section_index, current_race_no), pd.NA),
                    "odds_quinella": quinella_payout_map.get((section_index, current_race_no), pd.NA),
                    # alias: downstream 互換
                    "odds_2renpuku": quinella_payout_map.get((section_index, current_race_no), pd.NA),
                }
                if include_results:
                    row["finish_position"] = int(finish_position)
                    row["win_label"] = 1 if int(finish_position) == 1 else 0

                rows.append(row)
            except Exception as exc:
                warnings.append(f"{basename}: skipped line {line_no} due to parse error: {exc}")

        return rows, warnings

    def _normalize_text(self, value: str) -> str:
        return value.translate(self.FULLWIDTH_TRANS)

    def _extract_payout_maps(self, lines: List[str]) -> tuple[Dict[tuple, float], Dict[tuple, float], Dict[tuple, float]]:
        """
        [払戻金] セクションの 3連単払戻(円)を抽出し、100円基準のオッズ相当へ変換する。
        例: 20710円 -> 207.1
        """
        trifecta_by_race: Dict[tuple, float] = {}
        exacta_by_race: Dict[tuple, float] = {}
        quinella_by_race: Dict[tuple, float] = {}
        section_index = 0
        in_payout_block = False
        seen_row_in_block = False

        for line in lines:
            stripped = line.strip()
            if "[払戻金]" in stripped:
                section_index += 1
                in_payout_block = True
                seen_row_in_block = False
                continue

            if not in_payout_block:
                continue

            if not stripped:
                # 払戻テーブルは空行で終了。次の [払戻金] を待つ
                if seen_row_in_block:
                    in_payout_block = False
                continue

            # 代表フォーマット:
            # 1R  1-3-6 1830 1-3-6 760 1-3 390 1-3 310
            tokens = [t for t in re.split(r"\s+", stripped) if t]
            if len(tokens) >= 9 and tokens[0].endswith("R") and tokens[0][:-1].isdigit():
                race_no = int(tokens[0][:-1])
                tri_yen = tokens[2].replace(",", "")
                exa_yen = tokens[6].replace(",", "")
                qui_yen = tokens[8].replace(",", "")
                if tri_yen.isdigit():
                    trifecta_by_race[(section_index, race_no)] = int(tri_yen) / 100.0
                if exa_yen.isdigit():
                    exacta_by_race[(section_index, race_no)] = int(exa_yen) / 100.0
                if qui_yen.isdigit():
                    quinella_by_race[(section_index, race_no)] = int(qui_yen) / 100.0
                seen_row_in_block = True
                continue

            # 後方互換: 三連単のみの古い行形式
            m = self.PAYOUT_TRIFECTA_RE.match(line)
            if m:
                race_no = int(m.group(1))
                payout_yen = m.group(2).replace(",", "")
                if payout_yen.isdigit():
                    trifecta_by_race[(section_index, race_no)] = int(payout_yen) / 100.0
                    seen_row_in_block = True

        return trifecta_by_race, exacta_by_race, quinella_by_race

    def _extract_entry_rows(self, lines: List[str], date_value: str, basename: str, jcd_value: str | None = None):
        rows = []
        warnings = []
        race_no = 0
        in_entry_block = False
        venue = os.path.splitext(basename)[0]
        if jcd_value is None:
            jcd_value = self._extract_jcd(lines)

        for line_no, line in enumerate(lines, start=1):
            if line.replace("-", "").strip() == "":
                in_entry_block = True
                continue

            if in_entry_block and not line.strip():
                in_entry_block = False
                continue

            match = self.ENTRY_LINE_RE.match(line)
            if not match:
                continue

            lane, racer_id, racer_class, national_win_rate, national_2ren_rate, local_win_rate, local_2ren_rate, motor_no, motor_2ren_rate, boat_no, boat_2ren_rate = match.groups()

            if lane == "1":
                race_no += 1

            race_id = f"{date_value.replace('-', '')}-{venue}-{race_no:02d}"
            rows.append({
                "race_id": race_id,
                "date": date_value,
                "jcd": jcd_value,
                "venue": venue,
                "race_no": race_no,
                "lane": int(lane),
                "racer_id": int(racer_id),
                "racer_class": racer_class,
                "avg_st": pd.NA,
                "national_win_rate": self._to_float(national_win_rate),
                "national_2ren_rate": self._to_float(national_2ren_rate),
                "local_2ren_rate": self._to_float(local_2ren_rate),
                "motor_no": int(motor_no),
                "motor_2ren_rate": self._to_float(motor_2ren_rate),
                "boat_no": int(boat_no),
                "boat_2ren_rate": self._to_float(boat_2ren_rate),
                "season": pd.NA,
                "day_number": pd.NA,
                "exhibition_time": pd.NA,
                "body_weight": pd.NA,
                "tilt": pd.NA,
                "parts_change_flag": pd.NA,
                "propeller_new_flag": pd.NA,
                "prev_race_course": pd.NA,
                "prev_race_st": pd.NA,
                "prev_race_finish": pd.NA,
                "start_display_st": pd.NA,
                "wind_speed": pd.NA,
                "weather": pd.NA,
                "water_temp": pd.NA,
                "wave_height": pd.NA,
                "odds_trifecta": pd.NA,
                "odds_exacta": pd.NA,
                "odds_2rentan": pd.NA,
                "odds_quinella": pd.NA,
                "odds_2renpuku": pd.NA,
            })

        return rows, warnings

    def _normalize_exhibition_time(self, raw_value) -> float:
        if pd.isna(raw_value):
            return pd.NA
        raw_value = str(raw_value).strip()
        if raw_value in {"", ".", "....."}:
            return pd.NA
        parts = raw_value.split(".")
        if len(parts) == 3:
            try:
                return float(f"{parts[0]}.{parts[1]}{parts[2]}")
            except ValueError:
                return pd.NA
        try:
            return float(raw_value)
        except ValueError:
            return pd.NA

    def _to_float(self, raw_value):
        if pd.isna(raw_value):
            return pd.NA
        raw_value = str(raw_value).strip()
        if raw_value in {"", ".", ".....", "<NA>", "nan", "NaN", "None"}:
            return pd.NA
        try:
            return float(raw_value)
        except ValueError:
            return pd.NA

    def _extract_fan_rows(self, input_path: str):
        """
        fan*.txt (選手マスタ) から統計情報を抽出する。
        バイト位置ベースでパースする。
        """
        rows = []
        warnings = []
        try:
            with open(input_path, "rb") as f:
                lines = f.readlines()
            
            for line_no, line in enumerate(lines, start=1):
                if len(line) < 150:
                    continue
                
                try:
                    # bytes 0-4: racer_id
                    racer_id_str = line[0:4].decode("cp932", errors="replace").strip()
                    if not racer_id_str.isdigit():
                        continue
                    
                    # 統計値の抽出 (バイト位置はプロファイリングに基づき調整)
                    # 58:62 -> 全国勝率 (0756 -> 7.56)
                    # 62:66 -> 全国2連率 (0554 -> 55.4)
                    # 66:70 -> 当地勝率 (0350 -> 3.50)
                    # 70:74 -> 当地2連率 (0311 -> 31.1)
                    # 103:106 -> 平均ST (132 -> 0.132)
                    
                    n_win_s = line[58:62].decode("cp932", errors="replace").strip()
                    n_2ren_s = line[62:66].decode("cp932", errors="replace").strip()
                    l_win_s = line[66:70].decode("cp932", errors="replace").strip()
                    l_2ren_s = line[70:74].decode("cp932", errors="replace").strip()
                    st_s = line[103:106].decode("cp932", errors="replace").strip()
                    
                    def safe_div(s, div):
                        try:
                            return float(s) / div
                        except:
                            return pd.NA

                    rows.append({
                        "racer_id": int(racer_id_str),
                        "avg_st": safe_div(st_s, 1000.0),
                        "national_win_rate": safe_div(n_win_s, 100.0),
                        "national_2ren_rate": safe_div(n_2ren_s, 10.0),
                        "local_win_rate": safe_div(l_win_s, 100.0),
                        "local_2ren_rate": safe_div(l_2ren_s, 10.0),
                    })
                except Exception as exc:
                    warnings.append(f"Line {line_no} parse error: {exc}")
        except Exception as exc:
            warnings.append(f"File {input_path} read error: {exc}")
            
        return rows, warnings
