# -*- coding: utf-8 -*-
import os
import re
import pandas as pd
import logging
import numpy as np
from pathlib import Path

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BoatRaceParser:
    """
    BOAT RACE 公式テキストファイル (固定長) を解析して CSV に変換する。
    バイトベースの解析により、全角文字による位置ずれを防止する。
    """
    
    VENUE_MAP = {
        "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04", "多摩川": "05",
        "浜名湖": "06", "蒲郡": "07", "常滑": "08", "津": "09", "三国": "10",
        "びわこ": "11", "琵琶湖": "11", "住之江": "12", "尼崎": "13", "鳴門": "14",
        "丸亀": "15", "児島": "16", "宮島": "17", "徳山": "18", "下関": "19",
        "若松": "20", "芦屋": "21", "福岡": "22", "唐津": "23", "大村": "24"
    }

    @staticmethod
    def _get_date_from_filename(file_path):
        fname = Path(file_path).name
        m = re.search(r"[KB](\d{2})(\d{2})(\d{2})", fname, re.IGNORECASE)
        if m:
            yy, mm, dd = m.groups()
            return f"{2000+int(yy)}-{mm}-{dd}"
        return None

    @staticmethod
    def parse_results_file(file_path):
        results = []
        p = Path(file_path)
        if not p.exists(): return pd.DataFrame()
        current_date = BoatRaceParser._get_date_from_filename(file_path)
        with p.open("rb") as f:
            lines = f.read().splitlines()
        
        current_venue = None
        current_jcd = None
        current_race_no = None
        venue_bytes = [(v_name.encode("cp932"), v_name, v_code) for v_name, v_code in BoatRaceParser.VENUE_MAP.items()]
        re_race = re.compile(br"(\d{1,2})R")
        re_row = re.compile(br"^\s*(\d{2})\s+(\d)\s+(\d{4})")

        for line in lines:
            if not line.strip(): continue
            for b_name, v_name, v_code in venue_bytes:
                if b_name in line:
                    current_venue = v_name
                    current_jcd = v_code
                    break
            if b"R" in line[:15]:
                m_r = re_race.search(line[:15])
                if m_r:
                    try: current_race_no = int(m_r.group(1).decode("cp932"))
                    except: pass
            
            m_row = re_row.search(line)
            if m_row and len(line) >= 60:
                try:
                    if not (current_date and current_jcd and current_race_no): continue
                    
                    finish_pos = m_row.group(1).decode("cp932")
                    lane = m_row.group(2).decode("cp932")
                    racer_id = m_row.group(3).decode("cp932")
                    
                    st_val = line[56:61].decode("cp932", errors="replace").strip()
                    exhibit = line[48:52].decode("cp932", errors="replace").strip()
                    
                    union_key = f"{current_date.replace('-', '')}_{current_jcd}_{current_race_no:02d}"
                    results.append({
                        "date": current_date, "jcd": current_jcd, "venue": current_venue, "race_no": current_race_no,
                        "finish_position": finish_pos, "lane": lane, "racer_id": racer_id,
                        "st": st_val, "exhibition_time": exhibit, "union_key": union_key,
                        "race_id": f"{current_date.replace('-', '')}_{current_venue}_{current_race_no}"
                    })
                except Exception: pass
        return pd.DataFrame(results)

    @staticmethod
    def parse_entries_file(file_path):
        entries = []
        p = Path(file_path)
        if not p.exists(): return pd.DataFrame()
        current_date = BoatRaceParser._get_date_from_filename(file_path)
        with p.open("rb") as f:
            lines = f.read().splitlines()
        current_jcd = None
        current_venue = None
        current_race_no = None
        venue_bytes = [(v_name.encode("cp932"), v_name, v_code) for v_name, v_code in BoatRaceParser.VENUE_MAP.items()]
        re_row = re.compile(br"^(\d)\s+(\d{4})")

        for line in lines:
            if not line.strip(): continue
            for b_name, v_name, v_code in venue_bytes:
                if b_name in line:
                    current_venue = v_name
                    current_jcd = v_code
                    break
            if b"\x82\x71" in line: # 'Ｒ'
                m_r = re.search(br"([\x30-\x39\x82\x4f-\x82\x58]{1,2})\x82\x71", line)
                if m_r:
                    raw_num = m_r.group(1).decode("cp932")
                    num_str = raw_num.translate(str.maketrans('１２３４５６７８９０', '1234567890'))
                    try: current_race_no = int(num_str)
                    except: pass
            
            m_row = re_row.search(line)
            if m_row and len(line) >= 70:
                try:
                    if not (current_jcd and current_race_no and current_date): continue
                    def safe_float(b_slice):
                        val = b_slice.decode("cp932", errors="replace").strip()
                        try: return float(val)
                        except: return np.nan
                    lane = m_row.group(1).decode("cp932")
                    racer_id = m_row.group(2).decode("cp932")
                    union_key = f"{current_date.replace('-', '')}_{current_jcd}_{current_race_no:02d}"
                    entries.append({
                        "union_key": union_key, "lane": lane, "racer_id": racer_id,
                        "racer_class": line[22:24].decode("cp932").strip(),
                        "national_win_rate": safe_float(line[25:30]), "national_2ren_rate": safe_float(line[30:36]),
                        "local_win_rate": safe_float(line[36:41]), "local_2ren_rate": safe_float(line[41:47]),
                        "motor_2ren_rate": safe_float(line[50:56]), "boat_2ren_rate": safe_float(line[59:65]),
                    })
                except Exception: pass
        return pd.DataFrame(entries)

    def process_all(self, results_dir, entries_dir, historical_out, today_out, target_date=None):
        res_path, ent_path = Path(results_dir), Path(entries_dir)
        res_files = sorted([str(p) for p in res_path.glob("K*.TXT")]) if res_path.exists() else []
        all_results = []
        for f in res_files:
            df = self.parse_results_file(f)
            if not df.empty: all_results.append(df)
        if not all_results:
            logger.warning("No valid results parsed.")
            return
        df_res = pd.concat(all_results, ignore_index=True)
        ent_files = sorted([str(p) for p in ent_path.glob("B*.TXT")]) if ent_path.exists() else []
        all_entries = []
        for f in ent_files:
            df = self.parse_entries_file(f)
            if not df.empty: all_entries.append(df)
        if all_entries:
            df_ent = pd.concat(all_entries, ignore_index=True)
            df_res["lane"] = df_res["lane"].astype(str)
            df_ent["lane"] = df_ent["lane"].astype(str)
            merge_cols = ["union_key", "lane", "racer_class", "national_win_rate", "national_2ren_rate", 
                          "local_win_rate", "local_2ren_rate", "motor_2ren_rate", "boat_2ren_rate"]
            df_hist = df_res.merge(df_ent[merge_cols], on=["union_key", "lane"], how="left")
            df_hist.to_csv(historical_out, index=False)
            logger.info(f"Historical dataset saved. Rows: {len(df_hist)}")
            today_entry_file = None
            if target_date:
                target_key = pd.to_datetime(target_date, errors="coerce")
                if pd.notna(target_key):
                    target_name = f"B{target_key.strftime('%y%m%d')}.TXT"
                    candidate = ent_path / target_name
                    if candidate.exists():
                        today_entry_file = str(candidate)
            if today_entry_file is None and ent_files:
                today_entry_file = ent_files[-1]
            df_today = self.parse_entries_file(today_entry_file) if today_entry_file else pd.DataFrame()
            df_today.to_csv(today_out, index=False)
        else:
            df_res.to_csv(historical_out, index=False)
            logger.info(f"Only historical results saved. Rows: {len(df_res)}")

if __name__ == "__main__":
    import sys
    import argparse
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass
    current_dir = Path(__file__).parent.absolute()
    BASE_DIR = current_dir.parent.parent
    cli = argparse.ArgumentParser()
    cli.add_argument("--target-date", default=None, help="YYYY-MM-DD. Use the matching entry TXT for today_races.csv if available.")
    args = cli.parse_args()
    parser = BoatRaceParser()
    parser.process_all(
        str(BASE_DIR / "data" / "raw" / "official" / "results"),
        str(BASE_DIR / "data" / "raw" / "official" / "entries"),
        str(BASE_DIR / "data" / "processed" / "historical_races.csv"),
        str(BASE_DIR / "data" / "processed" / "today_races.csv"),
        target_date=args.target_date,
    )
