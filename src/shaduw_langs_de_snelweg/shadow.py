"""Geometric shadow casting: canopy height + sun position -> shadow footprint.

Sentinel-2 only shows ~10:30 shadows; this module *computes* the shadow
footprint for any date/time (e.g. 15:00 on the summer solstice) by
ray-marching from each pixel toward the sun over the canopy height model.
A pixel at ground level is shaded when some canopy along that ray rises
above the sun ray; pixels directly under canopy are shaded by definition.
"""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib
import xarray as xr


def solar_position(lat: float, lon: float, when: dt.datetime) -> tuple[float, float]:
    """Solar (azimuth, apparent elevation) in degrees for a timezone-aware time.

    Azimuth follows the pvlib convention: degrees clockwise from north.
    """
    if when.tzinfo is None:
        raise ValueError("`when` must be timezone-aware")
    times = pd.DatetimeIndex([when])
    pos = pvlib.solarposition.get_solarposition(times, lat, lon)
    return float(pos["azimuth"].iloc[0]), float(pos["apparent_elevation"].iloc[0])


def _shift2d(a: np.ndarray, dr: int, dc: int, fill: float = 0.0) -> np.ndarray:
    """Array whose value at (i, j) is ``a[i + dr, j + dc]`` (out of bounds -> fill)."""
    h, w = a.shape
    out = np.full_like(a, fill)
    r0, r1 = max(0, -dr), min(h, h - dr)
    c0, c1 = max(0, -dc), min(w, w - dc)
    if r0 < r1 and c0 < c1:
        out[r0:r1, c0:c1] = a[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
    return out


def cast_shadow_mask(
    chm: np.ndarray,
    resolution: float,
    azimuth_deg: float,
    elevation_deg: float,
    self_shade_height_m: float = 2.0,
) -> np.ndarray | None:
    """Boolean ground-shadow mask for a north-up canopy height raster.

    Parameters
    ----------
    chm : np.ndarray
        Canopy height in metres, row 0 = north, NaN treated as 0.
    resolution : float
        Pixel size in metres (projected CRS).
    azimuth_deg : float
        Sun azimuth in degrees, clockwise from north.
    elevation_deg : float
        Sun elevation in degrees above the horizon.
    self_shade_height_m : float
        Canopy above this height shades its own pixel.

    Returns
    -------
    np.ndarray of bool, or None when the sun is at/below the horizon.
    """
    if elevation_deg <= 0:
        return None
    chm = np.nan_to_num(np.asarray(chm, dtype="float32"), nan=0.0)
    tan_e = math.tan(math.radians(elevation_deg))
    max_h = float(chm.max())
    shaded = chm > self_shade_height_m
    if max_h <= 0:
        return shaded

    # Unit vector toward the sun in map coordinates (east, north); in raster
    # indices row increases southward.
    az = math.radians(azimuth_deg)
    dx_east, dy_north = math.sin(az), math.cos(az)

    max_dist = max_h / tan_e
    step = resolution / 2.0
    offsets: set[tuple[int, int]] = set()
    for k in range(1, int(math.ceil(max_dist / step)) + 1):
        d = k * step
        dr = round(-dy_north * d / resolution)
        dc = round(dx_east * d / resolution)
        if (dr, dc) != (0, 0):
            offsets.add((dr, dc))

    for dr, dc in offsets:
        d_cell = math.hypot(dr, dc) * resolution
        shaded |= _shift2d(chm, dr, dc) > d_cell * tan_e
    return shaded


def modeled_shadow_fraction(
    chm: xr.DataArray,
    parking_mask: np.ndarray,
    lat: float,
    lon: float,
    when: dt.datetime,
    self_shade_height_m: float = 2.0,
) -> float:
    """Fraction of parking pixels shaded at a given moment.

    ``chm`` must be on a projected (metre) grid; ``parking_mask`` is a boolean
    array on the same grid selecting the parking polygon. Returns NaN when the
    sun is down or the mask is empty.
    """
    n_parking = int(parking_mask.sum())
    if n_parking == 0:
        return float("nan")
    mask = modeled_shadow_mask(chm, lat, lon, when, self_shade_height_m)
    if mask is None:
        return float("nan")
    return float(mask.values.astype(bool)[parking_mask].sum() / n_parking)


def modeled_shadow_mask(
    chm: xr.DataArray,
    lat: float,
    lon: float,
    when: dt.datetime,
    self_shade_height_m: float = 2.0,
) -> xr.DataArray | None:
    """Shadow footprint on the CHM grid (uint8: 1 = shaded), or None if the
    sun is down. Keeps the CHM's coordinates/CRS, so it writes as a GeoTIFF."""
    azimuth, elevation = solar_position(lat, lon, when)
    resolution = abs(float(chm.rio.resolution()[0]))
    mask = cast_shadow_mask(
        chm.values, resolution, azimuth, elevation, self_shade_height_m
    )
    if mask is None:
        return None
    return chm.copy(data=mask.astype("uint8"))


def local_time(date_monthday: str, hour: int, timezone: str, year: int) -> dt.datetime:
    """Build a timezone-aware datetime from ``MM-DD``, an hour and a tz name."""
    month, day = (int(p) for p in date_monthday.split("-"))
    return dt.datetime(year, month, day, hour, 0, tzinfo=ZoneInfo(timezone))
