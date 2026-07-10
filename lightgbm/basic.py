from __future__ import annotations

"""Minimal local compatibility layer for lightgbm.basic."""

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class Booster:
    """Minimal booster wrapper used by local compatibility code."""

    _backend: Any | None = None

    def predict(self, data, **kwargs):  # noqa: ANN001
        if self._backend is None:
            raise RuntimeError("Booster backend is not available in local compatibility layer.")
        return np.asarray(self._backend.predict_proba(data, **kwargs))[:, 1]

