"""Composite shade score and categorical class."""

from __future__ import annotations

import math

from shaduw_langs_de_snelweg.config import ScoreConfig


def shade_score(metrics: dict, cfg: ScoreConfig) -> float:
    """Weighted 0-1 shade score (plan §4.4).

    Weights are renormalised over the metrics that are actually available,
    so a thin winter composite or missing canopy tile degrades the score
    gracefully instead of dragging it to zero.
    """
    parts = [
        (cfg.w_modeled_15h, metrics.get("shadow_frac_modeled_15h")),
        (cfg.w_tree_fraction, metrics.get("tree_fraction")),
        (cfg.w_detected_summer, metrics.get("shadow_frac_detected_jja")),
    ]
    total_w = 0.0
    acc = 0.0
    for weight, value in parts:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        acc += weight * min(max(float(value), 0.0), 1.0)
        total_w += weight
    if total_w == 0.0:
        return float("nan")
    return acc / total_w


def shade_class(score: float, cfg: ScoreConfig) -> str:
    """Categorical label: ``none`` / ``partial`` / ``good`` (``unknown`` for NaN)."""
    if math.isnan(score):
        return "unknown"
    if score < cfg.bound_partial:
        return "none"
    if score < cfg.bound_good:
        return "partial"
    return "good"
