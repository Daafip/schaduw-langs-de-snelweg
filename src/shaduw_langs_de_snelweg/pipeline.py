"""Pipeline orchestration: one stop = one unit of work, cached on disk.

Per-stop metric results are cached as JSON in ``cache_dir/stops/``, and the
prepared stop table (Overpass lookups) as GeoJSON, so interrupted runs
resume where they left off. Use ``force=True`` to recompute everything.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from tqdm import tqdm

from shaduw_langs_de_snelweg.canopy import load_canopy
from shaduw_langs_de_snelweg.config import PipelineConfig
from shaduw_langs_de_snelweg.imagery import seasonal_composites
from shaduw_langs_de_snelweg.metrics import compute_stop_metrics, ndvi
from shaduw_langs_de_snelweg.outputs import write_outputs
from shaduw_langs_de_snelweg.score import shade_class, shade_score
from shaduw_langs_de_snelweg.shadow import local_time, modeled_shadow_mask
from shaduw_langs_de_snelweg.stops import prepare_stops

logger = logging.getLogger(__name__)


def _stops_with_cache(cfg: PipelineConfig, force: bool) -> gpd.GeoDataFrame:
    """Prepare the stop table, cached as GeoJSON (Overpass is rate-limited)."""
    cache = cfg.cache_dir / "stops_prepared.geojson"
    if cache.exists() and not force:
        gdf = gpd.read_file(cache)
        # pyogrio may auto-parse JSON-typed string fields into dicts already
        gdf["aoi"] = gdf["aoi"].map(
            lambda g: shape(g if isinstance(g, dict) else json.loads(g))
        )
        logger.info("loaded %d prepared stops from cache", len(gdf))
        return gdf
    gdf = prepare_stops(cfg.stops, cfg.country)
    cache.parent.mkdir(parents=True, exist_ok=True)
    serializable = gdf.copy()
    serializable["aoi"] = serializable["aoi"].map(
        lambda g: json.dumps(g.__geo_interface__)
    )
    serializable.to_file(cache, driver="GeoJSON")
    return gdf


def process_stop(row, cfg: PipelineConfig) -> dict:
    """Compute all metrics for one stop (row from the prepared stop table)."""
    composites = seasonal_composites(row["aoi"], cfg.stac)
    chm = load_canopy(row["aoi"], cfg.canopy, cfg.cache_dir, row["stop_id"])
    metrics = compute_stop_metrics(
        parking_geom_4326=row.geometry,
        aoi_geom_4326=row["aoi"],
        composites=composites,
        chm=chm,
        detection_cfg=cfg.detection,
        shadow_cfg=cfg.shadow,
        tree_threshold=cfg.canopy.tree_height_threshold_m,
    )
    metrics["shade_score"] = shade_score(metrics, cfg.score)
    metrics["shade_class"] = shade_class(metrics["shade_score"], cfg.score)
    metrics["processed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if cfg.output.write_rasters:
        _write_debug_rasters(row, cfg, composites, chm)
    return metrics


def _write_debug_rasters(row, cfg: PipelineConfig, composites: dict, chm) -> None:
    """Per-stop debug rasters: summer NDVI, canopy window, modelled shadow masks."""
    raster_dir = cfg.output.directory / "rasters" / str(row["stop_id"])
    raster_dir.mkdir(parents=True, exist_ok=True)

    jja = composites.get("jja")
    if jja is not None:
        ndvi(jja).rio.to_raster(raster_dir / "ndvi_jja.tif")
    chm.rio.to_raster(raster_dir / "canopy.tif")

    centroid = row.geometry.centroid
    year = dt.date.today().year
    for hour in cfg.shadow.hours:
        when = local_time(cfg.shadow.date, hour, cfg.shadow.timezone, year)
        mask = modeled_shadow_mask(
            chm, centroid.y, centroid.x, when, cfg.shadow.self_shade_height_m
        )
        if mask is not None:
            mask.rio.to_raster(raster_dir / f"shadow_modeled_{hour:d}h.tif")
    logger.info("debug rasters for %s: %s", row["stop_id"], raster_dir)


def run_pipeline(
    cfg: PipelineConfig,
    force: bool = False,
    limit: int | None = None,
) -> gpd.GeoDataFrame:
    """Run the pipeline for all stops in the config; returns the result table.

    Failed stops are logged and skipped (recorded with an ``error`` field in
    their cache file), so one bad stop never kills a batch run.
    """
    stops = _stops_with_cache(cfg, force)
    if limit is not None:
        stops = stops.head(limit)

    stop_cache = cfg.cache_dir / "stops"
    stop_cache.mkdir(parents=True, exist_ok=True)

    records = []
    for _, row in tqdm(stops.iterrows(), total=len(stops), desc="stops"):
        cache_file = stop_cache / f"{row['stop_id']}.json"
        metrics = None
        if cache_file.exists() and not force:
            metrics = json.loads(cache_file.read_text())
            if "error" in metrics:  # stale failure from an older run: retry
                metrics = None
        if metrics is None:
            try:
                metrics = process_stop(row, cfg)
            except Exception as exc:  # keep the batch alive
                logger.exception("stop %s failed", row["stop_id"])
                metrics = {"error": str(exc)}
            else:
                # only successes are cached, so failed stops retry next run
                cache_file.write_text(json.dumps(metrics, default=str))
        if "error" in metrics:
            continue
        records.append(
            {
                "stop_id": row["stop_id"],
                "name": row["name"],
                "country": row["country"],
                "polygon_source": row["polygon_source"],
                "geometry": row.geometry,
                **metrics,
            }
        )

    if not records:
        raise RuntimeError("no stops were processed successfully")

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    utm = gdf.estimate_utm_crs()
    gdf["centroid"] = gdf.geometry.to_crs(utm).centroid.to_crs(gdf.crs)
    gdf["processed_at"] = pd.to_datetime(gdf["processed_at"])
    write_outputs(gdf, cfg.output)
    return gdf
