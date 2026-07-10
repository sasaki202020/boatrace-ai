from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.parse_official_entries_html import parse_official_entries_html
from src.utils.race_id import canonical_race_id


def test_parse_official_entries_html_smoke() -> None:
    html = """
    <html><body>
      <h1>出走表</h1>
      <div>1R</div>
      <div>1 Image</div>
      <div>4409 / A2</div>
      <div>坂元 浩仁</div>
      <div>愛知/愛知</div>
      <div>40歳/53.5kg</div>
      <div>F0</div>
      <div>L0</div>
      <div>0.14</div>
      <div>6.86 53.57 73.21 5.68 46.43 60.71 24 0.00 0.00 127 0.00 0.00</div>
      <div>2 Image</div>
      <div>4835 / B1</div>
      <div>橋本 明</div>
      <div>広島/広島</div>
      <div>37歳/53.5kg</div>
      <div>F0</div>
      <div>L0</div>
      <div>0.15</div>
      <div>4.95 21.18 50.59 4.53 25.00 45.00 35 0.00 0.00 143 0.00 0.00</div>
    </body></html>
    """
    df = parse_official_entries_html(html, target_date="2026-04-19", jcd="21", race_no=2)
    assert not df.empty
    assert canonical_race_id("2026-04-19", 21, 2) in df["race_id"].astype(str).unique().tolist()
    assert set(df["lane"].astype(int).tolist()) == {1, 2}
    assert "racer_id" in df.columns
