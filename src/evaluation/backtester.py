"""回収率バックテスト(3戦略)。

戦略:
1. honmei      — 全レースで予想1位の艇に単勝を賭ける
2. expected    — 期待値 (予測勝率 × オッズ) が閾値超のときのみ賭ける
3. confidence  — 予測勝率が閾値超のときのみ予想1位に賭ける
"""
from __future__ import annotations

import pandas as pd


class Backtester:
    def __init__(self, unit_stake: int = 100, ev_threshold: float = 1.2,
                 confidence_threshold: float = 0.40):
        self.unit_stake = unit_stake
        self.ev_threshold = ev_threshold
        self.confidence_threshold = confidence_threshold

    def run(self, pred_df: pd.DataFrame) -> dict[str, dict]:
        """predict_races() の出力(pred_proba / pred_rank 付き)を受け取る。"""
        top = pred_df[pred_df["pred_rank"] == 1].copy()
        top["ev"] = top["pred_proba"] * top["win_odds"]

        results = {
            "honmei": self._settle(top),
            "expected_value": self._settle(top[top["ev"] > self.ev_threshold]),
            "confidence": self._settle(top[top["pred_proba"] > self.confidence_threshold]),
        }
        return results

    def _settle(self, bets: pd.DataFrame) -> dict:
        n = len(bets)
        if n == 0:
            return {"n_bets": 0, "n_hits": 0, "hit_rate": 0.0,
                    "total_stake": 0, "total_return": 0.0, "roi": 0.0}
        stake = n * self.unit_stake
        hits = bets[bets["win"] == 1]
        ret = float((hits["win_odds"] * self.unit_stake).sum())
        return {
            "n_bets": n,
            "n_hits": int(len(hits)),
            "hit_rate": float(len(hits) / n),
            "total_stake": int(stake),
            "total_return": ret,
            "roi": float(ret / stake),
        }

    @staticmethod
    def format_report(results: dict[str, dict]) -> str:
        names = {"honmei": "本命戦略", "expected_value": "期待値戦略", "confidence": "確信度戦略"}
        lines = ["=== 回収率バックテスト ===",
                 f"{'戦略':<8}{'賭け数':>8}{'的中数':>8}{'的中率':>9}{'回収率':>9}"]
        for key, r in results.items():
            lines.append(
                f"{names.get(key, key):<8}{r['n_bets']:>9}{r['n_hits']:>9}"
                f"{r['hit_rate']:>9.1%}{r['roi']:>9.1%}"
            )
        return "\n".join(lines)
