"""Output writers: GeoParquet, GeoPackage and a Folium quick-look map."""

from __future__ import annotations

import logging
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

from shaduw_langs_de_snelweg.config import OutputConfig

logger = logging.getLogger(__name__)

CLASS_COLORS = {
    "good": "#2e7d32",
    "partial": "#f9a825",
    "none": "#c62828",
    "unknown": "#9e9e9e",
}


def merge_results(paths: list[Path]) -> gpd.GeoDataFrame:
    """Concatenate per-run result GeoParquets (e.g. one per country).

    Later files win on duplicate ``stop_id`` values, so a re-processed
    country can be merged over an older combined set.
    """
    frames = [gpd.read_parquet(p) for p in paths]
    gdf = pd.concat(frames, ignore_index=True)
    gdf = gdf.drop_duplicates(subset="stop_id", keep="last").reset_index(drop=True)
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=frames[0].crs)


def write_outputs(gdf: gpd.GeoDataFrame, cfg: OutputConfig) -> dict[str, Path]:
    """Write GeoParquet + GeoPackage + HTML map; returns the paths."""
    cfg.directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "geoparquet": cfg.directory / cfg.geoparquet,
        "geopackage": cfg.directory / cfg.geopackage,
        "map": cfg.directory / cfg.map_html,
    }

    # GeoParquet keeps both geometry columns (polygon + centroid).
    gdf.to_parquet(paths["geoparquet"])

    # GeoPackage supports one geometry column; centroid becomes lon/lat.
    gpkg = gdf.copy()
    if "centroid" in gpkg.columns:
        gpkg["centroid_lon"] = gpkg["centroid"].x
        gpkg["centroid_lat"] = gpkg["centroid"].y
        gpkg = gpkg.drop(columns=["centroid"])
    if "processed_at" in gpkg.columns:
        gpkg["processed_at"] = gpkg["processed_at"].astype(str)
    gpkg.to_file(paths["geopackage"], driver="GPKG")

    write_map(gdf, paths["map"])
    for name, path in paths.items():
        logger.info("wrote %s: %s", name, path)
    return paths


def write_map(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """Folium quick-look: parking polygons + markers coloured by shade class."""
    if "centroid" in gdf.columns:
        centroids = gpd.GeoSeries(gdf["centroid"], crs=gdf.crs)
    else:
        centroids = gdf.geometry.to_crs(gdf.estimate_utm_crs()).centroid.to_crs(gdf.crs)
    m = folium.Map(
        location=[centroids.y.mean(), centroids.x.mean()],
        zoom_start=8,
        tiles="OpenStreetMap",
    )
    popup_fields = [
        "stop_id",
        "name",
        "shade_score",
        "shade_class",
        "shadow_frac_modeled_15h",
        "tree_fraction",
        "ndvi_summer",
        "n_scenes_used",
    ]
    for i, (_, row) in enumerate(gdf.iterrows()):
        color = CLASS_COLORS.get(row.get("shade_class", "unknown"), "#9e9e9e")
        lines = []
        for field in popup_fields:
            value = row.get(field)
            if isinstance(value, float):
                value = round(value, 3)
            lines.append(f"<b>{field}</b>: {value}")
        popup = folium.Popup("<br>".join(lines), max_width=350)
        folium.GeoJson(
            row.geometry.__geo_interface__,
            style_function=lambda _f, c=color: {
                "color": c,
                "fillColor": c,
                "fillOpacity": 0.35,
            },
        ).add_to(m)
        centroid = centroids.iloc[i]
        folium.CircleMarker(
            location=[centroid.y, centroid.x],
            radius=7,
            color=color,
            fill=True,
            fill_opacity=0.9,
            popup=popup,
        ).add_to(m)
    m.save(str(path))
