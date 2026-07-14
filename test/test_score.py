import math

from shaduw_langs_de_snelweg.config import ScoreConfig
from shaduw_langs_de_snelweg.score import shade_class, shade_score

CFG = ScoreConfig()


def test_full_weighted_score():
    metrics = {
        "shadow_frac_modeled_15h": 0.6,
        "tree_fraction": 0.4,
        "shadow_frac_detected_jja": 0.2,
    }
    expected = 0.5 * 0.6 + 0.3 * 0.4 + 0.2 * 0.2
    assert math.isclose(shade_score(metrics, CFG), expected)


def test_missing_metric_renormalises():
    metrics = {
        "shadow_frac_modeled_15h": float("nan"),
        "tree_fraction": 0.4,
        "shadow_frac_detected_jja": 0.2,
    }
    expected = (0.3 * 0.4 + 0.2 * 0.2) / 0.5
    assert math.isclose(shade_score(metrics, CFG), expected)


def test_all_missing_gives_nan():
    assert math.isnan(shade_score({}, CFG))


def test_values_clipped_to_unit_interval():
    metrics = {
        "shadow_frac_modeled_15h": 1.5,
        "tree_fraction": -0.2,
        "shadow_frac_detected_jja": 0.0,
    }
    expected = 0.5 * 1.0 + 0.3 * 0.0 + 0.2 * 0.0
    assert math.isclose(shade_score(metrics, CFG), expected)


def test_shade_class_bounds():
    assert shade_class(0.0, CFG) == "none"
    assert shade_class(0.14, CFG) == "none"
    assert shade_class(0.15, CFG) == "partial"
    assert shade_class(0.39, CFG) == "partial"
    assert shade_class(0.40, CFG) == "good"
    assert shade_class(1.0, CFG) == "good"
    assert shade_class(float("nan"), CFG) == "unknown"
