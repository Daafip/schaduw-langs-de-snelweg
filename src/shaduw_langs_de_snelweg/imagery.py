"""Sentinel-2 L2A acquisition: STAC search, cloud masking, seasonal composites.

Scenes are searched once for the whole period, bucketed into meteorological
seasons (DJF/MAM/JJA/SON), and reduced to a cloud-masked median composite
per season. Only the needed bands are loaded, clipped to the stop AOI, so a
composite costs a few hundred KB of transfer, never a full tile.
"""

from __future__ import annotations

import datetime as dt
import logging

import geopandas as gpd
import numpy as np
import pystac
import xarray as xr
from odc.geo.geom import Geometry
from odc.stac import load as odc_load
from pystac_client import Client

from shaduw_langs_de_snelweg.config import SEASONS, StacConfig

logger = logging.getLogger(__name__)

BANDS = ["red", "green", "blue", "nir", "scl"]

#: SCL classes always masked: nodata, saturated, cloud medium/high, cirrus.
CLOUD_SCL = (0, 1, 8, 9, 10)
#: Additionally masked for vegetation (NDVI): cloud shadow, snow. Kept for
#: shadow detection — persistent "cloud shadow" at a fixed spot is tree shadow.
VEG_EXTRA_SCL = (3, 11)

MONTH_TO_SEASON = {m: s for s, months in SEASONS.items() for m in months}


def search_items(aoi_4326, cfg: StacConfig) -> list[pystac.Item]:
    """STAC search for Sentinel-2 L2A scenes intersecting the AOI."""
    end = dt.date.fromisoformat(cfg.end_date) if cfg.end_date else dt.date.today()
    start = end.replace(year=end.year - cfg.years_back)
    client = Client.open(cfg.url)
    search = client.search(
        collections=[cfg.collection],
        intersects=aoi_4326.__geo_interface__,
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": cfg.max_cloud_cover}},
    )
    items = list(search.items())
    logger.info("STAC search: %d scenes %s..%s", len(items), start, end)
    return items


def group_items_by_season(
    items: list[pystac.Item], max_per_season: int
) -> dict[str, list[pystac.Item]]:
    """Bucket scenes into seasons, keeping the least cloudy ones first."""
    buckets: dict[str, list[pystac.Item]] = {s: [] for s in SEASONS}
    for item in items:
        buckets[MONTH_TO_SEASON[item.datetime.month]].append(item)
    for season, season_items in buckets.items():
        season_items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100.0))
        buckets[season] = season_items[:max_per_season]
    return buckets


def needs_boa_offset(item: pystac.Item) -> bool:
    """Whether the -1000 DN BOA offset (processing baseline >= 04.00) still
    needs to be subtracted to harmonise with the pre-2022 convention."""
    props = item.properties
    if props.get("earthsearch:boa_offset_applied") is True:
        return False
    baseline = props.get("s2:processing_baseline") or props.get(
        "sentinel2:processing_baseline"
    )
    try:
        return baseline is not None and float(baseline) >= 4.0
    except (TypeError, ValueError):
        return False


def load_composite(
    items: list[pystac.Item], aoi_4326, resolution: float
) -> xr.Dataset | None:
    """Load scenes clipped to the AOI and reduce to a median composite.

    Returns a Dataset (UTM grid) with reflectance medians in 0-1:
    ``red``, ``green``, ``blue``, ``nir`` (cloud-masked) and
    ``red_veg``, ``nir_veg`` (additionally cloud-shadow/snow-masked, for
    NDVI). ``attrs["n_scenes"]`` records how many scenes contributed.
    Returns ``None`` when no scenes are available.
    """
    if not items:
        return None
    utm = gpd.GeoSeries([aoi_4326], crs="EPSG:4326").estimate_utm_crs()
    geopolygon = Geometry(aoi_4326.__geo_interface__, "EPSG:4326")
    ds = odc_load(
        items,
        bands=BANDS,
        geopolygon=geopolygon,
        crs=str(utm),
        resolution=resolution,
        groupby="solar_day",
        dtype="uint16",
        chunks=None,
    )

    reflectance = ds[["red", "green", "blue", "nir"]].astype("float32")
    # Harmonise baseline >= 04.00 scenes to the old DN convention (-1000).
    offsets = np.array(
        [1000.0 if needs_boa_offset(i) else 0.0 for i in _items_per_time(ds, items)],
        dtype="float32",
    )
    offset_da = xr.DataArray(offsets, coords={"time": ds.time}, dims=["time"])
    reflectance = ((reflectance - offset_da) / 10000.0).clip(0.0, 1.0)

    scl = ds["scl"]
    cloudy = scl.isin(CLOUD_SCL)
    veg_invalid = cloudy | scl.isin(VEG_EXTRA_SCL)

    masked = reflectance.where(~cloudy)
    composite = masked.median(dim="time", skipna=True)
    veg = (
        reflectance[["red", "nir"]].where(~veg_invalid).median(dim="time", skipna=True)
    )
    composite["red_veg"] = veg["red"]
    composite["nir_veg"] = veg["nir"]
    composite.attrs["n_scenes"] = int(ds.sizes["time"])
    return composite


def _items_per_time(ds: xr.Dataset, items: list[pystac.Item]) -> list[pystac.Item]:
    """Match one representative STAC item to each time slice of the loaded cube
    (``groupby="solar_day"`` merges same-day scenes; baseline is identical
    within a day for our purposes)."""
    by_day: dict[dt.date, pystac.Item] = {}
    for item in items:
        by_day.setdefault(item.datetime.date(), item)
    out = []
    for t in ds.time.values:
        day = np.datetime64(t, "D").astype(dt.date)
        # fall back to the first item if the day is not found (should not happen)
        out.append(by_day.get(day, items[0]))
    return out


def seasonal_composites(aoi_4326, cfg: StacConfig) -> dict[str, xr.Dataset | None]:
    """Search once and build one cloud-masked median composite per season."""
    items = search_items(aoi_4326, cfg)
    buckets = group_items_by_season(items, cfg.max_items_per_season)
    composites: dict[str, xr.Dataset | None] = {}
    for season, season_items in buckets.items():
        composites[season] = load_composite(season_items, aoi_4326, cfg.resolution)
        n = (
            composites[season].attrs["n_scenes"]
            if composites[season] is not None
            else 0
        )
        logger.info("season %s: composite from %d scenes", season, n)
    return composites
