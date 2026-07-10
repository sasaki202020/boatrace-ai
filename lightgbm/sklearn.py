from __future__ import annotations

"""Minimal local compatibility layer for lightgbm.sklearn."""

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression


def early_stopping(*args: Any, **kwargs: Any):  # noqa: ANN401
    """Return a no-op callback so existing training code can keep its signature."""

    def _callback(*callback_args: Any, **callback_kwargs: Any) -> None:  # noqa: ANN401
        return None

    return _callback


@dataclass
class _BackendSpec:
    class_weight: Any | None = None
    random_state: int | None = None
    max_iter: int = 1000


class LGBMClassifier(BaseEstimator, ClassifierMixin):
    """Small scikit-learn backed substitute for the LightGBM estimator API."""

    def __init__(self, **kwargs: Any):
        self.params = dict(kwargs)
        self._backend_spec = _BackendSpec(
            class_weight=kwargs.get("class_weight"),
            random_state=kwargs.get("random_state"),
            max_iter=int(kwargs.get("max_iter", 1000)),
        )
        self._backend: LogisticRegression | None = None
        self.classes_: np.ndarray | None = None
        self.n_features_in_: int | None = None

    def get_params(self, deep: bool = True) -> dict[str, Any]:  # noqa: ARG002
        return dict(self.params)

    def set_params(self, **params: Any) -> "LGBMClassifier":
        self.params.update(params)
        self._backend_spec = _BackendSpec(
            class_weight=self.params.get("class_weight"),
            random_state=self.params.get("random_state"),
            max_iter=int(self.params.get("max_iter", 1000)),
        )
        return self

    def fit(self, X, y, eval_set=None, callbacks=None):  # noqa: ANN001, ARG002
        X_arr = np.asarray(X)
        y_arr = np.asarray(y)
        model = LogisticRegression(
            class_weight=self._backend_spec.class_weight,
            random_state=self._backend_spec.random_state,
            max_iter=self._backend_spec.max_iter,
            solver="lbfgs",
        )
        model.fit(X_arr, y_arr)
        self._backend = model
        self.classes_ = model.classes_
        self.n_features_in_ = getattr(model, "n_features_in_", X_arr.shape[1])
        return self

    def predict_proba(self, X):  # noqa: ANN001
        booster = getattr(self, "_Booster", None)
        if booster is not None:
            raw = booster.predict(X)
            raw = np.asarray(raw, dtype=float)
            raw = np.clip(raw, 0.0, 1.0)
            return np.column_stack([1.0 - raw, raw])
        if self._backend is None:
            raise RuntimeError("Estimator has not been fitted.")
        return self._backend.predict_proba(np.asarray(X))

    def predict(self, X):  # noqa: ANN001
        proba = self.predict_proba(X)
        return (proba[:, 1] >= 0.5).astype(int)
