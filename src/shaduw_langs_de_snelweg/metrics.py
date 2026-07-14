"""Per-stop metrics: NDVI seasonality, tree fraction, detected & modelled shadow.

Zonal conventions (see plan §4.3):

- NDVI and tree fraction over the full AOI (parking + buffer ring), because
  the shade-giving vegetation stands *next to* the parking.
- Mean canopy height over the buffer ring around the parking.
- Shadow fractions (detected and modelled) over the parking polygon itself —
  that is where the car stands.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import xarray as xr
from rasterio.features import geometry_mask

from shaduw_langs_de_snelweg.config import SEASONS, DetectionConfig, ShadowModelConfig
from shaduw_langs_de_snelweg.shadow import local_time, modeled_shadow_fraction

logger = logging.getLogger(__name__)


def mask_for_geometry(da: xr.DataArray | xr.Dataset, geom_4326) -> np.ndarray:
    """Boolean pixel mask (True inside geometry) on a rioxarray grid."""
    import geopandas as gpd

    geom_proj = gpd.GeoSeries([geom_4326], crs="EPSG:4326").to_crs(da.rio.crs).iloc[0]
    return ~geometry_mask(
        [geom_proj.__geo_interface__],
        out_shape=(da.rio.height, da.rio.width),
        transform=da.rio.transform(),
        invert=False,
    )


def ndvi(composite: xr.Dataset) -> xr.DataArray:
    """NDVI from the vegetation-masked median composite."""
    red, nir = composite["red_veg"], composite["nir_veg"]
    return (nir - red) / (nir + red).where((nir + red) > 0)


def brightness(composite: xr.Dataset) -> xr.DataArray:
    """Broadband brightness: mean of R, G, B and NIR reflectance.

    NIR must be included: healthy vegetation is *dark* in the visible bands
    (RGB mean ~0.05) but bright in NIR (~0.33), while true shadow is dark in
    all four. Visible-only brightness would flag entire green AOIs as shadow.
    """
    return (
        composite["red"] + composite["green"] + composite["blue"] + composite["nir"]
    ) / 4.0


def _masked_mean(da: xr.DataArray, mask: np.ndarray) -> float:
    values = da.values[mask]
    values = values[np.isfinite(values)]
    return float(values.mean()) if values.size else float("nan")


def _masked_fraction(
    condition: np.ndarray, valid: np.ndarray, mask: np.ndarray
) -> float:
    """Fraction of valid masked pixels where ``condition`` holds."""
    sel = mask & valid
    n = int(sel.sum())
    return float(condition[sel].sum() / n) if n else float("nan")


def detected_shadow_fraction(
    composite: xr.Dataset, parking_mask: np.ndarray, cfg: DetectionConfig
) -> float:
    """Fraction of (cloud-free) parking pixels dark enough to be shadow at ~10:30."""
    bright = brightness(composite).values
    valid = np.isfinite(bright)
    return _masked_fraction(bright < cfg.brightness_threshold, valid, parking_mask)


def vegetation_metrics(
    composites: dict[str, xr.Dataset | None], aoi_mask_of, tree_threshold: float, chm
) -> dict[str, float]:
    """NDVI summer/winter/delta over the AOI, tree fraction & canopy height."""
    out: dict[str, float] = {}
    for key, season in (("ndvi_summer", "jja"), ("ndvi_winter", "djf")):
        comp = composites.get(season)
        if comp is None:
            out[key] = float("nan")
            continue
        out[key] = _masked_mean(ndvi(comp), aoi_mask_of(comp))
    out["ndvi_delta"] = out["ndvi_summer"] - out["ndvi_winter"]

    chm_vals = chm.values
    aoi_mask_chm = aoi_mask_of(chm)
    valid = np.isfinite(chm_vals)
    out["tree_fraction"] = _masked_fraction(
        chm_vals > tree_threshold, valid, aoi_mask_chm
    )
    return out


def compute_stop_metrics(
    parking_geom_4326,
    aoi_geom_4326,
    composites: dict[str, xr.Dataset | None],
    chm: xr.DataArray,
    detection_cfg: DetectionConfig,
    shadow_cfg: ShadowModelConfig,
    tree_threshold: float,
    year: int | None = None,
) -> dict[str, float | int]:
    """All tabular metrics for one stop (plan §4.3)."""
    year = year or dt.date.today().year
    centroid = parking_geom_4326.centroid
    ring_geom = aoi_geom_4326.difference(parking_geom_4326)

    def aoi_mask_of(da):
        return mask_for_geometry(da, aoi_geom_4326)

    metrics: dict[str, float | int] = {}

    # Vegetation & canopy
    metrics.update(vegetation_metrics(composites, aoi_mask_of, tree_threshold, chm))
    ring_mask = mask_for_geometry(
        chm, ring_geom if not ring_geom.is_empty else aoi_geom_4326
    )
    metrics["mean_canopy_height"] = _masked_mean(chm, ring_mask)

    # Detected shadow per season (~10:30 acquisitions)
    n_scenes = 0
    for season in SEASONS:
        comp = composites.get(season)
        if comp is None:
            metrics[f"shadow_frac_detected_{season}"] = float("nan")
            continue
        parking_mask = mask_for_geometry(comp, parking_geom_4326)
        metrics[f"shadow_frac_detected_{season}"] = detected_shadow_fraction(
            comp, parking_mask, detection_cfg
        )
        n_scenes += comp.attrs.get("n_scenes", 0)
    metrics["n_scenes_used"] = n_scenes

    # Modelled shadow (the headline metric): summer solstice afternoons
    parking_mask_chm = mask_for_geometry(chm, parking_geom_4326)
    for hour in shadow_cfg.hours:
        when = local_time(shadow_cfg.date, hour, shadow_cfg.timezone, year)
        metrics[f"shadow_frac_modeled_{hour:d}h"] = modeled_shadow_fraction(
            chm,
            parking_mask_chm,
            centroid.y,
            centroid.x,
            when,
            shadow_cfg.self_shade_height_m,
        )
    return metrics
