import geopandas as gpd
import numpy as np
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import box

from shaduw_langs_de_snelweg.config import DetectionConfig
from shaduw_langs_de_snelweg.metrics import (
    brightness,
    detected_shadow_fraction,
    mask_for_geometry,
    ndvi,
)

UTM = "EPSG:32631"


def make_grid(shape=(20, 20), res=10.0, x0=650_000.0, y0=5_790_000.0):
    """North-up UTM grid coords (pixel centres)."""
    y = y0 - (np.arange(shape[0]) + 0.5) * res
    x = x0 + (np.arange(shape[1]) + 0.5) * res
    return {"y": y, "x": x}


def make_composite(red=0.05, green=0.05, blue=0.05, nir=0.3, shape=(20, 20)):
    coords = make_grid(shape)
    data = {}
    for name, value in {
        "red": red,
        "green": green,
        "blue": blue,
        "nir": nir,
        "red_veg": red,
        "nir_veg": nir,
    }.items():
        data[name] = xr.DataArray(
            np.full(shape, value, dtype="float32"), coords=coords, dims=("y", "x")
        )
    ds = xr.Dataset(data)
    return ds.rio.write_crs(UTM)


def grid_polygon_4326(x0, y0, x1, y1):
    """A UTM box converted to WGS84 (metrics functions expect 4326 geometries)."""
    return gpd.GeoSeries([box(x0, y0, x1, y1)], crs=UTM).to_crs("EPSG:4326").iloc[0]


def test_mask_for_geometry_selects_expected_pixels():
    ds = make_composite()
    # half the grid: columns 0..9 (x 650000..650100), all rows
    geom = grid_polygon_4326(650_000, 5_789_800, 650_100, 5_790_000)
    mask = mask_for_geometry(ds["red"], geom)
    assert mask.shape == (20, 20)
    assert 150 <= mask.sum() <= 220  # ~half of 400 pixels, reprojection tolerance


def test_ndvi_value():
    ds = make_composite(red=0.1, nir=0.5)
    values = ndvi(ds).values
    assert np.allclose(values, (0.5 - 0.1) / (0.5 + 0.1), atol=1e-6)


def test_brightness_includes_nir():
    ds = make_composite(red=0.1, green=0.2, blue=0.3, nir=0.4)
    assert np.allclose(brightness(ds).values, 0.25, atol=1e-6)


def test_vegetation_is_not_shadow():
    # healthy vegetation: dark in RGB but bright in NIR -> not shadow
    ds = make_composite(red=0.04, green=0.06, blue=0.03, nir=0.35)
    mask = np.ones((20, 20), dtype=bool)
    frac = detected_shadow_fraction(ds, mask, DetectionConfig())
    assert frac == 0.0


def test_detected_shadow_fraction_half_dark():
    ds = make_composite(red=0.1, green=0.1, blue=0.1, nir=0.3)
    # make the northern half dark in all bands (true shadow)
    for band in ("red", "green", "blue", "nir"):
        ds[band].values[:10, :] = 0.03
    mask = np.ones((20, 20), dtype=bool)
    frac = detected_shadow_fraction(
        ds, mask, DetectionConfig(brightness_threshold=0.08)
    )
    assert np.isclose(frac, 0.5)


def test_detected_shadow_fraction_ignores_nan():
    ds = make_composite(red=0.03, green=0.03, blue=0.03, nir=0.05)
    for band in ("red", "green", "blue", "nir"):
        ds[band].values[:10, :] = np.nan  # e.g. clouds masked out
    mask = np.ones((20, 20), dtype=bool)
    frac = detected_shadow_fraction(
        ds, mask, DetectionConfig(brightness_threshold=0.08)
    )
    assert np.isclose(frac, 1.0)  # all *valid* pixels are dark
