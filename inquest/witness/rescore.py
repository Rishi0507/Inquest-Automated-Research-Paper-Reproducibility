"""Eval-phase re-scoring from predictions captured by the Witness.

A metric-definition difference (macro against weighted F1, AUC against AP, accuracy
against balanced accuracy) changes only how predictions are scored. Captured arrays let
the platform evaluate the alternative definition without training again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
from sklearn import metrics as skm

SCORE_FNS = {"roc_auc_score", "average_precision_score", "log_loss", "top_k_accuracy_score"}
ALLOWED = {
    "accuracy_score", "balanced_accuracy_score", "f1_score", "fbeta_score", "precision_score", "recall_score",
    "roc_auc_score", "average_precision_score", "matthews_corrcoef", "jaccard_score", "top_k_accuracy_score",
    "log_loss", "mean_squared_error", "mean_absolute_error", "r2_score",
}


def load(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as z:
        return z["y_true"], z["y_pred"]


def _coerce_kwargs(kwargs: dict) -> dict:
    out = {}
    for k, v in (kwargs or {}).items():
        if k in ("repository_function", "error"):
            continue
        if isinstance(v, str):
            try:
                v = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                pass
        out[k] = v
    return out


def _labels(y_pred: np.ndarray, fn: str) -> np.ndarray:
    """Hard labels for label-based metrics; scores are kept for ranking metrics."""
    if fn in SCORE_FNS:
        return y_pred
    if y_pred.ndim == 2 and y_pred.shape[1] > 1:
        return y_pred.argmax(axis=1)
    return y_pred


def score(y_true: np.ndarray, y_pred: np.ndarray, fn: str, kwargs: dict | None = None) -> float:
    if fn not in ALLOWED:
        raise ValueError(f"re-scoring supports scikit-learn metrics only, not {fn!r}")
    f = getattr(skm, fn)
    return float(f(y_true, _labels(y_pred, fn), **_coerce_kwargs(kwargs or {})))


def rescore_payload(path: str | Path, fn: str, kwargs: dict | None = None) -> float:
    y_true, y_pred = load(path)
    return score(y_true, y_pred, fn, kwargs)
