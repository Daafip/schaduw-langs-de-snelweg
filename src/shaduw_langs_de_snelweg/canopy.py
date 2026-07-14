"""Canopy height model (CHM) acquisition.

Default source is the Meta/WRI global canopy height layer (1 m, AWS Open
Data, no account needed): COG tiles named by Bing quadkey (zoom 9), so only
the AOI window is fetched via HTTP range reads. Alternatives: the ETH Global
Canopy Height 2020 3-degree COG tiles (10 m; the original ETH host has been
unreliable since the dataset moved to the research collection), or a local
GeoTIFF via ``canopy.source = "local"``.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import geopandas as gpd
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from rasterio.errors import RasterioIOError
from rioxarray.merge import merge_arrays

from shaduw_langs_de_snelweg.config import CanopyConfig

logger = logging.getLogger(__name__)

DEFAULT_URL_TEMPLATES = {
    "meta": (
        "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/"
        "alsgedi_global_v6_float/chm/{tile}.tif"
    ),
    "eth": (
        "https://share.phys.ethz.ch/~pf/nlangdata/"
        "ETH_GlobalCanopyHeight_10m_2020_version1/3deg_cogs/"
        "ETH_GlobalCanopyHeight_10m_2020_{tile}_Map.tif"
    ),
}

#: Zoom level of the Meta/WRI quadkey tiles.
META_ZOOM = 9

#: Margin (degrees) added around the AOI when clipping the CHM, so shadows
#: cast by tall trees just outside the AOI are still modelled (~150-220 m).
CLIP_MARGIN_DEG = 0.002


# --- ETH 3-degree tiles ----------------------------------------------------


def eth_tile_label(lat: float, lon: float) -> str:
    """3-degree ETH tile label containing a WGS84 coordinate, e.g. ``N51E003``."""
    lat0 = int(math.floor(lat / 3.0) * 3)
    lon0 = int(math.floor(lon / 3.0) * 3)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


def eth_tile_labels_for_bounds(bounds: tuple[float, float, float, float]) -> set[str]:
    """All ETH tile labels intersecting a (minx, miny, maxx, maxy) WGS84 box."""
    minx, miny, maxx, maxy = bounds
    labels = set()
    lat = math.floor(miny / 3.0) * 3
    while lat <= maxy:
        lon = math.floor(minx / 3.0) * 3
        while lon <= maxx:
            labels.add(eth_tile_label(lat + 0.001, lon + 0.001))
            lon += 3
        lat += 3
    return labels


# --- Meta/WRI quadkey tiles (Bing tile system) -------------------------------


def latlon_to_tilexy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """WGS84 coordinate -> (tileX, tileY) in the Bing/WebMercator tile scheme."""
    n = 2**zoom
    lat = min(max(lat, -85.05112878), 85.05112878)
    sin_lat = math.sin(math.radians(lat))
    x = int((lon + 180.0) / 360.0 * n)
    y = int((0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tilexy_to_quadkey(x: int, y: int, zoom: int) -> str:
    """(tileX, tileY, zoom) -> Bing quadkey string."""
    digits = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        digits.append(str(digit))
    return "".join(digits)


def meta_quadkeys_for_bounds(
    bounds: tuple[float, float, float, float], zoom: int = META_ZOOM
) -> set[str]:
    """All Meta CHM quadkeys intersecting a (minx, miny, maxx, maxy) WGS84 box."""
    minx, miny, maxx, maxy = bounds
    x0, y0 = latlon_to_tilexy(maxy, minx, zoom)  # north-west corner
    x1, y1 = latlon_to_tilexy(miny, maxx, zoom)  # south-east corner
    return {
        tilexy_to_quadkey(x, y, zoom)
        for x in range(x0, x1 + 1)
        for y in range(y0, y1 + 1)
    }


# --- loading -----------------------------------------------------------------


def tile_urls(
    cfg: CanopyConfig, bounds: tuple[float, float, float, float]
) -> list[str]:
    """Resolve the CHM source URLs/paths covering a WGS84 bounding box."""
    if cfg.source == "local":
        return [str(cfg.local_path)]
    template = cfg.url_template or DEFAULT_URL_TEMPLATES[cfg.source]
    if cfg.source == "eth":
        labels = eth_tile_labels_for_bounds(bounds)
    else:
        labels = meta_quadkeys_for_bounds(bounds)
    return [template.format(tile=label) for label in sorted(labels)]


def load_canopy(
    aoi_4326,
    cfg: CanopyConfig,
    cache_dir: Path,
    stop_id: str,
) -> xr.DataArray:
    """Load the CHM clipped to the AOI, reprojected to the local UTM grid.

    The clipped window is cached as a GeoTIFF per stop, so reruns do not
    re-fetch remote tiles. NaN/nodata is filled with 0 (no canopy). Raises
    ``RuntimeError`` when no tile could be read — better a loudly failed
    stop than one that silently claims "no trees".
    """
    cache_path = cache_dir / "canopy" / f"{stop_id}.tif"
    if cache_path.exists():
        chm = rioxarray.open_rasterio(cache_path, masked=True).squeeze(
            "band", drop=True
        )
        return chm.fillna(0.0)

    bounds = aoi_4326.buffer(CLIP_MARGIN_DEG).bounds
    clipped = []
    for src in tile_urls(cfg, bounds):
        try:
            da = rioxarray.open_rasterio(src, masked=True).squeeze("band", drop=True)
            clipped.append(da.rio.clip_box(*bounds, crs="EPSG:4326"))
        except RasterioIOError as exc:
            logger.warning("canopy tile %s unavailable: %s", src, exc)
    if not clipped:
        raise RuntimeError(f"no canopy tiles available for stop {stop_id}")
    merged = clipped[0] if len(clipped) == 1 else merge_arrays(clipped)

    utm = gpd.GeoSeries([aoi_4326], crs="EPSG:4326").estimate_utm_crs()
    chm = merged.rio.reproject(utm, resolution=cfg.resolution)
    chm = chm.where(chm < 200).clip(min=0.0)  # drop nodata sentinels (e.g. 255)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    chm.rio.to_raster(cache_path)
    logger.info("canopy %s: cached %s", stop_id, cache_path)
    return chm.fillna(0.0)
