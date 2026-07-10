from __future__ import annotations

import json

from src.external import baseline_backtest
from src.web import app as web_app


def _race_payload(*, source: str, combo: str, hit_combo: str, status: str = "ok") -> dict:
    predictions = [
        {"rank": 1, "combo": combo},
        {"rank": 2, "combo": "2-1-3"},
        {"rank": 3, "combo": "3-1-2"},
    ]
    result = {"status": status, "actualTrifecta": hit_combo, "payout": 1230}
    return {
        "date": "2026-05-08",
        "dateCompact": "20260508",
        "jcd": "12",
        "venue": "住之江",
        "raceNo": 4,
        "result": result,
        "sources": web_app._settle_external_sources([
            {
                "source": source,
                "name": source,
                "status": "ok",
                "sourceUrl": "https://example.test",
                "predictions": predictions,
            }
        ], result),
    }


def test_build_external_yosou_summary_reports_topn_rates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "ROOT", tmp_path)
    race_dir = tmp_path / "data" / "external_predictions" / "20260508" / "12"
    race_dir.mkdir(parents=True)
    (race_dir / "race_04.json").write_text(
        json.dumps(_race_payload(source="official", combo="1-2-3", hit_combo="1-2-3"), ensure_ascii=False),
        encoding="utf-8",
    )

    summary = web_app.build_external_yosou_summary("20260508")
    source = summary["sources"][0]

    assert source["source"] == "official"
    assert source["settledPredictionRaceCount"] == 1
    assert source["hitRate"] == 1.0
    assert source["top1HitRate"] == 1.0
    assert source["top3HitRate"] == 1.0
    assert source["top5HitRate"] == 1.0
    assert source["roi"] == 12.3


def test_build_external_yosou_summary_filters_by_jcd(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web_app, "ROOT", tmp_path)
    for jcd in ["12", "13"]:
        race_dir = tmp_path / "data" / "external_predictions" / "20260508" / jcd
        race_dir.mkdir(parents=True)
        (race_dir / "race_04.json").write_text(
            json.dumps(_race_payload(source="official", combo="1-2-3", hit_combo="1-2-3"), ensure_ascii=False),
            encoding="utf-8",
        )

    summary = web_app.build_external_yosou_summary("20260508", jcd="12")

    assert summary["requestedJcd"] == "12"
    assert summary["raceFileCount"] == 1


def test_backtest_external_baselines_writes_json_csv_md(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(baseline_backtest, "REPORT_ROOT", tmp_path / "reports" / "external" / "baseline_compare")

    def fake_summary(day: str, jcd: str | None = None) -> dict:
        return {
            "status": "ok",
            "date": "2026-05-08",
            "dateCompact": "20260508",
            "raceFileCount": 1,
            "settledRaceCount": 1,
            "sources": [
                {
                    "source": "nikkan",
                    "name": "日刊スポーツAI",
                    "raceCount": 1,
                    "predictionRaceCount": 1,
                    "predictionCount": 3,
                    "settledPredictionRaceCount": 1,
                    "hitCount": 1,
                    "top1HitCount": 0,
                    "top3HitCount": 1,
                    "top5HitCount": 1,
                    "returnYen": 1230,
                }
            ],
        }

    monkeypatch.setattr(baseline_backtest.web_app, "build_external_yosou_summary", fake_summary)
    monkeypatch.setattr(baseline_backtest.web_app, "reconcile_saved_external_yosou", lambda *args, **kwargs: {})

    result = baseline_backtest.backtest_external_baselines(
        start_date="20260508",
        end_date="20260508",
        fetch=False,
        reconcile=True,
    )

    assert result["status"] == "ok"
    assert result["sources"][0]["top3HitRate"] == 1.0
    assert result["sources"][0]["roi"] == 12.3
    assert (tmp_path / "reports" / "external" / "baseline_compare" / "20260508_20260508_external_baselines.json").exists()
    assert (tmp_path / "reports" / "external" / "baseline_compare" / "20260508_20260508_external_baselines.csv").exists()
    assert (tmp_path / "reports" / "external" / "baseline_compare" / "20260508_20260508_external_baselines.md").exists()
