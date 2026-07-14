import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from shaduw_langs_de_snelweg.shadow import (
    _shift2d,
    cast_shadow_mask,
    local_time,
    solar_position,
)


def test_shift2d_reads_toward_offset():
    a = np.zeros((5, 5))
    a[3, 2] = 7.0
    # value at (i, j) = a[i+dr, j+dc]; from (1, 2) with dr=2 we see a[3, 2]
    out = _shift2d(a, dr=2, dc=0)
    assert out[1, 2] == 7.0
    assert out[3, 2] == 0.0
    # out-of-bounds is filled
    assert _shift2d(a, dr=10, dc=0).sum() == 0.0


def test_sun_below_horizon_returns_none():
    assert cast_shadow_mask(np.zeros((5, 5)), 1.0, 180.0, 0.0) is None
    assert cast_shadow_mask(np.zeros((5, 5)), 1.0, 180.0, -5.0) is None


def test_single_tree_sun_from_south():
    chm = np.zeros((21, 21), dtype="float32")
    chm[10, 10] = 10.0  # one 10 m tree in the middle
    mask = cast_shadow_mask(chm, resolution=1.0, azimuth_deg=180.0, elevation_deg=45.0)
    assert mask[10, 10]  # under the canopy itself
    # shadow extends north (up, decreasing row): length = 10 / tan(45) = 10 m
    assert mask[5, 10]
    assert mask[1, 10]
    assert not mask[0, 10]  # just beyond the shadow tip
    # no shadow south, east or west of the tree
    assert not mask[15, 10]
    assert not mask[10, 5]
    assert not mask[10, 15]


def test_single_tree_sun_from_west():
    chm = np.zeros((21, 21), dtype="float32")
    chm[10, 10] = 8.0
    mask = cast_shadow_mask(chm, resolution=1.0, azimuth_deg=270.0, elevation_deg=45.0)
    # sun in the west -> shadow east (increasing column)
    assert mask[10, 14]
    assert not mask[10, 6]
    assert not mask[6, 10]


def test_zenith_sun_only_self_shade():
    chm = np.zeros((11, 11), dtype="float32")
    chm[5, 5] = 10.0
    chm[2, 2] = 1.0  # below self-shade height
    mask = cast_shadow_mask(chm, 1.0, 180.0, 89.9, self_shade_height_m=2.0)
    assert mask[5, 5]
    assert not mask[2, 2]
    assert mask.sum() == 1


def test_nan_treated_as_no_canopy():
    chm = np.full((9, 9), np.nan, dtype="float32")
    chm[4, 4] = 6.0
    mask = cast_shadow_mask(chm, 1.0, 180.0, 45.0)
    assert mask[4, 4]
    assert mask[2, 4]


def test_solar_position_nl_summer_noon():
    when = dt.datetime(2025, 6, 21, 13, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    azimuth, elevation = solar_position(52.0, 5.0, when)
    # max elevation at 52N on the solstice is ~61.4 deg; 13:00 CEST is near noon
    assert 50.0 < elevation < 63.0
    assert 130.0 < azimuth < 185.0


def test_solar_position_requires_tz():
    with pytest.raises(ValueError):
        solar_position(52.0, 5.0, dt.datetime(2025, 6, 21, 12, 0))


def test_local_time():
    when = local_time("06-21", 15, "Europe/Amsterdam", 2025)
    assert when == dt.datetime(2025, 6, 21, 15, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
