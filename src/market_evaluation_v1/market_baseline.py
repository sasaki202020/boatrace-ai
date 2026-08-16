from __future__ import annotations

import math
import re
from itertools import permutations
from typing import Any, Iterable


ALL_TRIFECTA_COMBOS = tuple(
    "-".join(map(str, combo)) for combo in permutations(range(1, 7), 3)
)
_COMBO_RE = re.compile(r"^[1-6]-[1-6]-[1-6]$")


class MarketBaselineError(ValueError):
    """Raised when an odds table cannot be used as a complete market baseline."""


def normalize_trifecta(value: Any) -> str:
    if isinstance(value, (tuple, list)):
        value = "-".join(str(part) for part in value)
    text = str(value).replace(" ", "").strip()
    if not _COMBO_RE.fullmatch(text):
        raise MarketBaselineError("invalid_trifecta")
    parts = text.split("-")
    if len(set(parts)) != 3:
        raise MarketBaselineError("trifecta_boat_repeated")
    return text


def calculate_market_probabilities(
    odds_rows: Iterable[dict[str, Any]], *, require_complete: bool = True
) -> list[dict[str, Any]]:
    """Normalize inverse odds within one race without using any result data."""
    normalized: dict[str, float] = {}
    for row in odds_rows:
        combo = normalize_trifecta(row.get("trifecta", row.get("combo")))
        if combo in normalized:
            raise MarketBaselineError("duplicate_trifecta")
        try:
            odds = float(row.get("odds"))
        except (TypeError, ValueError) as exc:
            raise MarketBaselineError("odds_not_numeric") from exc
        if not math.isfinite(odds) or odds <= 0:
            raise MarketBaselineError("odds_not_positive_finite")
        normalized[combo] = odds
    if require_complete and set(normalized) != set(ALL_TRIFECTA_COMBOS):
        missing = len(set(ALL_TRIFECTA_COMBOS) - set(normalized))
        extra = len(set(normalized) - set(ALL_TRIFECTA_COMBOS))
        raise MarketBaselineError(f"incomplete_trifecta_table:missing={missing}:extra={extra}")
    denominator = sum(1.0 / odds for odds in normalized.values())
    if not math.isfinite(denominator) or denominator <= 0:
        raise MarketBaselineError("market_probability_denominator_invalid")
    output = [
        {
            "trifecta": combo,
            "odds": odds,
            "rawImpliedProbability": 1.0 / odds,
            "marketProbability": (1.0 / odds) / denominator,
        }
        for combo, odds in sorted(normalized.items())
    ]
    total = sum(float(row["marketProbability"]) for row in output)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise MarketBaselineError("market_probability_sum_invalid")
    return output
