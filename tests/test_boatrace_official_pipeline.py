from __future__ import annotations

from pathlib import Path

from collections import Counter

from src.pipeline.boatrace_official_pipeline import (
    BoatStats,
    BeforeInfo,
    PredictionInfo,
    RaceBundle,
    RaceMeta,
    apply_focus_boost,
    build_output_paths,
    parse_meta_from_text,
    parse_racelist,
    rank_predictions,
)


def test_parse_meta_uses_canonical_race_id() -> None:
    meta = parse_meta_from_text("## イベント\n### 1R\n締切予定時刻 10:00 10:30", "2026-04-19", "02", 1)
    assert meta.race_id == "20260419-02-01"
    assert meta.jcd == "02"
    assert meta.race_no == 1


def test_build_output_paths_points_to_repo_contract() -> None:
    paths = build_output_paths(Path("data"), "2026-04-19")
    assert paths["odds_csv"] == Path("data") / "odds" / "20260419" / "all_trifecta_odds.csv"
    assert paths["summary_json"] == Path("data") / "predictions" / "20260419" / "summary.json"


def test_parse_racelist_extracts_current_line_based_boat_blocks() -> None:
    html = """
    <html><body>
      <div>締切予定時刻 15:18 15:44</div>
      <div>## イベント</div>
      <div>### 1R</div>
      <div>枠</div><div>ボートレーサー</div><div>全国</div><div>当地</div><div>モーター</div><div>ボート</div>
      <div>1</div><div>5303</div><div>/</div><div>B1</div><div>土井 歩夢</div><div>福岡/福岡</div><div>23歳/52.0kg</div>
      <div>F1</div><div>L0</div><div>0.17</div><div>5.73</div><div>33.73</div><div>59.04</div><div>5.43</div>
      <div>28.57</div><div>57.14</div><div>58</div><div>22.73</div><div>36.36</div><div>71</div><div>32.97</div><div>43.96</div>
      <div>2</div><div>4272</div><div>/</div><div>B1</div><div>大場 広孝</div><div>福岡/福岡</div><div>44歳/57.3kg</div>
      <div>F1</div><div>L0</div><div>0.16</div><div>4.85</div><div>24.32</div><div>41.89</div><div>3.18</div>
      <div>5.88</div><div>17.65</div><div>62</div><div>35.63</div><div>55.17</div><div>72</div><div>30.49</div><div>42.68</div>
      <div>今節成績</div>
    </body></html>
    """

    meta, boats = parse_racelist(html, "2026-04-24", "01", 1)

    assert meta.race_id == "20260424-01-01"
    assert len(boats) == 2
    assert boats[1].reg_no == "5303"
    assert boats[1].rank == "B1"
    assert boats[1].branch == "福岡"
    assert boats[1].hometown == "福岡"
    assert boats[2].reg_no == "4272"
    assert boats[2].name == "大場 広孝"


def test_rank_predictions_without_odds_uses_model_prob(monkeypatch) -> None:
    bundle = RaceBundle(
        meta=RaceMeta(date="2026-04-20", race_id="20260420-11-01", jcd="11", venue="びわこ", race_no=1),
        boats={
            1: BoatStats(no=1),
            2: BoatStats(no=2),
            3: BoatStats(no=3),
        },
        before=BeforeInfo(),
        pcexpect=PredictionInfo(focus_lines=[], official_boat_mentions=Counter()),
        odds={},
    )

    monkeypatch.setattr(
        "src.pipeline.boatrace_official_pipeline.score_boats",
        lambda boats, official_mentions: {1: 1.0, 2: 2.0, 3: 3.0},
    )
    monkeypatch.setattr(
        "src.pipeline.boatrace_official_pipeline.plackett_luce_combo_probs",
        lambda scores: {"1-2-3": 0.10, "2-1-3": 0.80, "3-1-2": 0.10},
    )
    monkeypatch.setattr("src.pipeline.boatrace_official_pipeline.apply_focus_boost", apply_focus_boost)

    ranked = rank_predictions(bundle, top_n=5)

    assert ranked[0]["combo"] == "2-1-3"
    assert ranked[0]["model_prob"] == 0.80
