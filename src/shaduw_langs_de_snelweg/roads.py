"""Major road network overlay: a static motorway-centreline dataset for the map.

The map reads a single pre-built static file (``load_major_roads``) with no
network access at render time. That file is built (or refreshed) explicitly
and occasionally via ``shaduw fetch-roads`` / :func:`build_major_roads_dataset`,
one Overpass query per country, cached per-country so a rebuild only re-fetches
countries that changed or were never fetched.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from shaduw_langs_de_snelweg.stops import overpass_query

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"


def load_major_roads(path: Path | None) -> gpd.GeoDataFrame:
    """Load the static major-roads overlay; no network access.

    Returns an empty GeoDataFrame (logged) if ``path`` is ``None`` or the
    file doesn't exist yet, so a missing overlay never breaks map writing.
    """
    if path is None or not path.exists():
        logger.info("no major-roads dataset at %s; map will have no roads overlay", path)
        return gpd.GeoDataFrame(columns=["ref", "geometry"], geometry="geometry", crs=WGS84)
    return gpd.read_file(path)


def _fetch_country_roads(
    country_iso: str,
    cache_dir: Path,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
    timeout: int = 300,
) -> gpd.GeoDataFrame:
    """Motorway centrelines for one country, dissolved by route ref, cached to disk."""
    cache = cache_dir / "roads" / f"{country_iso.lower()}.geojson"
    if cache.exists():
        return gpd.read_file(cache)

    query = f"""
    [out:json][timeout:{timeout}];
    area["ISO3166-1"="{country_iso.upper()}"][admin_level=2]->.a;
    way(area.a)["highway"="motorway"]["ref"];
    out geom;
    """
    data = overpass_query(query, overpass_url, timeout)

    records = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        ref = el.get("tags", {}).get("ref")
        if not ref:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        records.append({"ref": ref, "geometry": LineString(coords)})

    if records:
        gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)
        # OSM splits each motorway into many short ways; merge same-ref
        # segments into one row so the map has one hover target per route.
        gdf = gdf.dissolve(by="ref").reset_index()
        # ~1 km: a country's motorway network is still tens of thousands of
        # vertices at 100 m, which bloats the map HTML enough to make the
        # browser choke; imperceptible at the country/continent zoom this
        # overlay is meant for.
        gdf["geometry"] = gdf.geometry.simplify(0.01)
    else:
        gdf = gpd.GeoDataFrame(columns=["ref", "geometry"], geometry="geometry", crs=WGS84)

    cache.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache, driver="GeoJSON")
    logger.info("major roads %s: %d routes cached to %s", country_iso, len(gdf), cache)
    return gdf


def build_major_roads_dataset(
    countries: list[str],
    cache_dir: Path,
    out_path: Path,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
) -> gpd.GeoDataFrame:
    """Fetch (or reuse per-country cache) and write the static roads dataset.

    One Overpass query per country not already cached under
    ``cache_dir/roads/``; a failed fetch for one country is logged and
    skipped rather than aborting the whole build. Writes the concatenated
    result to ``out_path`` and returns it.
    """
    frames = []
    needs_delay = False
    for country in sorted(set(countries)):
        cache = cache_dir / "roads" / f"{country.lower()}.geojson"
        if not cache.exists():
            if needs_delay:
                # longer than stops.py's per-point delay: a whole country's
                # motorway network is a much heavier query and trips
                # Overpass's rate limiting (429) faster.
                time.sleep(10.0)
            needs_delay = True
        try:
            gdf = _fetch_country_roads(country, cache_dir, overpass_url)
        except Exception:
            logger.exception("major roads fetch failed for %s; skipping", country)
            continue
        if not gdf.empty:
            frames.append(gdf)

    gdf = (
        gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=WGS84)
        if frames
        else gpd.GeoDataFrame(columns=["ref", "geometry"], geometry="geometry", crs=WGS84)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GeoJSON")
    logger.info("major roads dataset: %d routes written to %s", len(gdf), out_path)
    return gdf
