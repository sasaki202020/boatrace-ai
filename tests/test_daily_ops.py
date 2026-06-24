from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.daily_ops import (
    PREDICTION_COLUMNS,
    RESULT_COLUMNS,
    daily_output_dir,
    run_daily_prediction,
    run_daily_settlement,
)


def _write_config(base_dir: Path) -> None:
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        """
data:
  database_path: data/boat_race.db
  raw_cache_dir: data/raw
fetcher:
  base_url: https://www.boatrace.jp
  min_interval_seconds: 0.0
  max_retries: 1
  backoff_base_seconds: 0.0
  user_agent: test
features:
  rolling_windows: [3, 5, 10]
output:
  model_path: data/models/model.pkl
  predictions_dir: data/predictions
backtest:
  unit_stake: 100
  ev_threshold: 1.2
  confidence_threshold: 0.4
""".strip(),
        encoding="utf-8",
    )
    model = base_dir / "data" / "models" / "model.pkl"
    model.parent.mkdir(parents=True)
    model.write_text("dummy", encoding="utf-8")


def _entries(date: str, course_id: int = 1, race_numbers=(1, 2)) -> pd.DataFrame:
    rows = []
    for rno in race_numbers:
        race_id = f"{date.replace('-', '')}{course_id:02d}{rno:02d}"
        for lane in range(1, 7):
            rows.append({
                "race_id": race_id,
                "race_date": date,
                "course_id": course_id,
                "race_number": rno,
                "weather": "晴",
                "wind_speed": 1.0,
                "water_temp": 20.0,
                "wave_height": 1.0,
                "racer_id": 4000 + rno * 10 + lane,
                "name": f"選手{rno}{lane}",
                "age": 30 + lane,
                "weight": 52.0,
                "grade": "A1" if lane == 1 else "B1",
                "lane": lane,
                "exhibition_time": 6.70 + lane * 0.01,
                "start_time": 0.10 + lane * 0.01,
                "finish_position": float("nan"),
                "win": float("nan"),
                "place": float("nan"),
                "show": float("nan"),
                "win_odds": 1.5 + lane,
            })
    return pd.DataFrame(rows)


class FakeFetcher:
    def __init__(self, entries: pd.DataFrame | None = None):
        self.entries = entries

    def discover_course_ids(self, date: str) -> list[int]:
        return [1]

    def fetch_day(self, date: str, place: int, include_results: bool = False):
        return self.entries.copy() if self.entries is not None else _entries(date, place)


class FakeModel:
    def predict_races(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["pred_proba_raw"] = 7 - out["lane"]
        out["pred_proba"] = out.groupby("race_id")["pred_proba_raw"].transform(
            lambda s: s / s.sum()
        )
        out["pred_rank"] = (
            out.groupby("race_id")["pred_proba"].rank(method="first", ascending=False).astype(int)
        )
        return out


def test_daily_prediction_writes_outputs_without_result_columns(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("src.daily_ops.PredictionSystem.load", lambda path: FakeModel())

    result = run_daily_prediction("2026-06-13", tmp_path, fetcher=FakeFetcher())

    out_dir = daily_output_dir(tmp_path, "2026-06-13")
    pred = pd.read_csv(out_dir / "predictions.csv", dtype={"race_id": str})
    assert result["status"] == "partial"
    assert len(pred) == 12
    assert "finish_position" not in pred.columns
    assert "win" not in pred.columns
    assert list(pred.columns) == PREDICTION_COLUMNS
    assert (out_dir / "predictions.json").exists()
    assert (out_dir / "morning_run.json").exists()
    assert (out_dir / "coverage.json").exists()


def test_daily_prediction_does_not_overwrite_existing_predictions(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("src.daily_ops.PredictionSystem.load", lambda path: FakeModel())
    run_daily_prediction("2026-06-13", tmp_path, fetcher=FakeFetcher())
    pred_path = daily_output_dir(tmp_path, "2026-06-13") / "predictions.csv"
    original = pred_path.read_text(encoding="utf-8-sig")

    empty_fetcher = FakeFetcher(pd.DataFrame())
    result = run_daily_prediction("2026-06-13", tmp_path, fetcher=empty_fetcher)

    assert result["status"] == "exists"
    assert pred_path.read_text(encoding="utf-8-sig") == original


class FinalResultFetcher:
    def fetch_results_for_predictions(self, date: str, predictions: pd.DataFrame):
        rows = []
        for pred in predictions.itertuples(index=False):
            lane = int(pred.lane)
            rows.append({
                "race_id": pred.race_id,
                "race_date": date,
                "course_id": int(pred.course_id),
                "race_number": int(pred.race_number),
                "lane": lane,
                "finish_position": lane,
                "win": int(lane == 1),
                "start_time": 0.10 + lane * 0.01,
                "result_status": "final",
                "unavailable_reason": "",
            })
        return pd.DataFrame(rows, columns=RESULT_COLUMNS), []


def test_daily_settlement_writes_report_and_idempotent_rolling(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("src.daily_ops.PredictionSystem.load", lambda path: FakeModel())
    run_daily_prediction("2026-06-13", tmp_path, fetcher=FakeFetcher())

    report = run_daily_settlement("2026-06-13", tmp_path, fetcher=FinalResultFetcher())
    report2 = run_daily_settlement("2026-06-13", tmp_path, fetcher=FinalResultFetcher())

    out_dir = daily_output_dir(tmp_path, "2026-06-13")
    assert report["status"] == "settled"
    assert report2["settled_races"] == 2
    assert (out_dir / "results.csv").exists()
    assert (out_dir / "settlement.csv").exists()
    assert (out_dir / "daily_report.json").exists()
    assert (out_dir / "daily_report.md").exists()
    rolling = pd.read_csv(tmp_path / "output" / "daily" / "rolling_summary.csv")
    assert len(rolling) == 1
    assert rolling["date"].astype(str).tolist() == ["2026-06-13"]


class UnavailableResultFetcher:
    def fetch_results_for_predictions(self, date: str, predictions: pd.DataFrame):
        rows = []
        for pred in predictions.itertuples(index=False):
            rows.append({
                "race_id": pred.race_id,
                "race_date": date,
                "course_id": int(pred.course_id),
                "race_number": int(pred.race_number),
                "lane": int(pred.lane),
                "finish_position": None,
                "win": None,
                "start_time": None,
                "result_status": "unavailable",
                "unavailable_reason": "unavailable",
            })
        return pd.DataFrame(rows, columns=RESULT_COLUMNS), [{
            "race_id": str(predictions["race_id"].iloc[0]),
            "reason": "unavailable",
            "message": "unavailable",
        }]


def test_daily_settlement_records_unavailable_reason(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("src.daily_ops.PredictionSystem.load", lambda path: FakeModel())
    run_daily_prediction("2026-06-13", tmp_path, fetcher=FakeFetcher())

    report = run_daily_settlement("2026-06-13", tmp_path, fetcher=UnavailableResultFetcher())
    settlement = pd.read_csv(daily_output_dir(tmp_path, "2026-06-13") / "settlement.csv")

    assert report["status"] == "no_final_results"
    assert report["unavailable_races_count"] == 2
    assert report["errors_by_reason"]["unavailable"] >= 1
    assert set(settlement["result_status"]) == {"unavailable"}
    assert set(settlement["result_unavailable_reason"]) == {"unavailable"}
