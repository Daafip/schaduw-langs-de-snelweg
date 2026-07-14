"""Stop ingestion: seed loading, OSM parking polygons, AOI construction.

One stop = one unit of work. A stop enters the pipeline as either a point
(buffered or resolved to an OSM parking polygon) or a ready-made polygon.
The analysis AOI is the parking polygon plus a buffer, because trees *next
to* a parking cast shade *onto* it.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import geopandas as gpd
import requests
import shapely
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from shaduw_langs_de_snelweg.config import StopsConfig

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"

#: Fallback endpoints tried after the configured one; the public Overpass
#: servers are frequently busy and then return HTML error pages with HTTP 200.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

#: overpass-api.de rejects the default python-requests agent with HTTP 406.
OVERPASS_HEADERS = {
    "User-Agent": "shaduw-langs-de-snelweg/0.1 (github.com/HKV-products-services)"
}


def overpass_query(
    query: str, primary_url: str, timeout: int, retries: int = 2
) -> dict:
    """POST an Overpass QL query, retrying across mirrors on failure."""
    urls = [primary_url] + [u for u in OVERPASS_MIRRORS if u != primary_url]
    last_error: Exception | None = None
    for attempt in range(retries):
        for url in urls:
            try:
                resp = requests.post(
                    url,
                    data={"data": query},
                    headers=OVERPASS_HEADERS,
                    timeout=timeout + 30,
                )
                resp.raise_for_status()
                return resp.json()  # busy servers return HTML with HTTP 200
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                logger.warning("Overpass request to %s failed: %s", url, exc)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"all Overpass endpoints failed: {last_error}")


def load_seeds(path: str | Path) -> gpd.GeoDataFrame:
    """Load seed stops from a GeoJSON file.

    Expected feature properties (all optional except geometry):
    ``stop_id``, ``name``, ``osm_id``. Missing ``stop_id`` values are
    generated as ``stop-<n>``.
    """
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = gdf.to_crs(WGS84)
    if "name" not in gdf.columns:
        gdf["name"] = None
    if "stop_id" not in gdf.columns:
        gdf["stop_id"] = None
    missing = gdf["stop_id"].isna()
    gdf.loc[missing, "stop_id"] = [f"stop-{i}" for i in gdf.index[missing]]
    gdf["stop_id"] = gdf["stop_id"].astype(str)
    if gdf["stop_id"].duplicated().any():
        dupes = gdf["stop_id"][gdf["stop_id"].duplicated()].tolist()
        raise ValueError(f"duplicate stop_id values in seed file: {dupes}")
    return gdf


def overpass_parking_polygons(
    lat: float,
    lon: float,
    radius_m: float,
    overpass_url: str,
    timeout: int = 90,
) -> list[Polygon]:
    """Fetch candidate parking/rest-area polygons around a point via Overpass."""
    query = f"""
    [out:json][timeout:{timeout}];
    (
      way(around:{radius_m:.0f},{lat:.7f},{lon:.7f})["amenity"="parking"];
      way(around:{radius_m:.0f},{lat:.7f},{lon:.7f})["highway"~"^(rest_area|services)$"];
    );
    out geom;
    """
    return osm_elements_to_polygons(overpass_query(query, overpass_url, timeout))


def osm_elements_to_polygons(overpass_json: dict) -> list[Polygon]:
    """Convert Overpass ``out geom`` way elements to shapely polygons."""
    polygons: list[Polygon] = []
    for el in overpass_json.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue  # not a closed ring
        poly = Polygon(coords)
        if poly.is_valid and not poly.is_empty:
            polygons.append(poly)
    return polygons


def resolve_parking_polygon(
    seed_geom: shapely.Geometry,
    cfg: StopsConfig,
) -> tuple[Polygon, str]:
    """Return (parking polygon in WGS84, source label) for one seed geometry.

    Polygon seeds are used as-is. Point seeds are resolved via Overpass
    (union of parking polygons found nearby); if that fails or finds
    nothing, the point is buffered by ``parking_buffer_m``.
    """
    if seed_geom.geom_type in ("Polygon", "MultiPolygon"):
        return unary_union(seed_geom), "seed"

    point: Point = seed_geom.centroid
    if cfg.use_overpass:
        try:
            polys = overpass_parking_polygons(
                point.y, point.x, cfg.osm_search_radius_m, cfg.overpass_url
            )
        except RuntimeError as exc:
            logger.warning("Overpass lookup failed (%s); buffering point instead", exc)
            polys = []
        if polys:
            merged = unary_union(polys)
            # keep only parts that are actually near the seed point
            near = [
                g
                for g in getattr(merged, "geoms", [merged])
                if g.distance(point) < cfg.osm_search_radius_m / 111_000
            ]
            if near:
                return unary_union(near), "osm"

    return buffer_in_meters(point, cfg.parking_buffer_m), "buffer"


def buffer_in_meters(geom: shapely.Geometry, distance_m: float) -> shapely.Geometry:
    """Buffer a WGS84 geometry by a distance in metres via the local UTM zone."""
    series = gpd.GeoSeries([geom], crs=WGS84)
    utm = series.estimate_utm_crs()
    return series.to_crs(utm).buffer(distance_m).to_crs(WGS84).iloc[0]


def prepare_stops(cfg: StopsConfig, country: str) -> gpd.GeoDataFrame:
    """Build the stop table: parking polygon + analysis AOI per seed.

    Returns a GeoDataFrame (WGS84) with columns ``stop_id``, ``name``,
    ``country``, ``geometry`` (parking polygon), ``aoi`` (shapely polygon)
    and ``polygon_source``.
    """
    seeds = load_seeds(cfg.seed_path)
    records = []
    for _, row in seeds.iterrows():
        if records and cfg.use_overpass:
            time.sleep(2.0)  # politeness delay, public Overpass rate-limits fast
        parking, source = resolve_parking_polygon(row.geometry, cfg)
        aoi = buffer_in_meters(parking, cfg.aoi_buffer_m)
        records.append(
            {
                "stop_id": row["stop_id"],
                "name": row["name"],
                "country": country,
                "geometry": parking,
                "aoi": aoi,
                "polygon_source": source,
            }
        )
        logger.info("stop %s: parking polygon from %s", row["stop_id"], source)
    return gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)


def discover_stops(
    country_iso: str,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
    timeout: int = 300,
) -> gpd.GeoDataFrame:
    """Discover rest areas / service areas in a country via Overpass (Phase 2).

    Returns a GeoDataFrame of polygons with ``stop_id`` (``osm-way-<id>``)
    and ``name`` columns, suitable to write as a seed GeoJSON.
    """
    query = f"""
    [out:json][timeout:{timeout}];
    area["ISO3166-1"="{country_iso.upper()}"][admin_level=2]->.a;
    (
      way(area.a)["highway"="rest_area"];
      way(area.a)["highway"="services"];
    );
    out geom;
    """
    data = overpass_query(query, overpass_url, timeout)

    records = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue
        poly = Polygon(coords)
        if not poly.is_valid or poly.is_empty:
            continue
        records.append(
            {
                "stop_id": f"osm-way-{el['id']}",
                "name": el.get("tags", {}).get("name"),
                "geometry": poly,
            }
        )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)
