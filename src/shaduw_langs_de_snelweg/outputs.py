"""Output writers: GeoParquet, GeoPackage and a Folium quick-look map."""

from __future__ import annotations

import logging
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

from shaduw_langs_de_snelweg.config import OutputConfig
from shaduw_langs_de_snelweg.roads import load_major_roads

logger = logging.getLogger(__name__)

#: kept for callers that key off the stored 3-class ``shade_class`` field
#: (e.g. the introduction notebook plots a single stop at a time).
CLASS_COLORS = {
    "good": "#2e7d32",
    "partial": "#f9a825",
    "none": "#c62828",
    "unknown": "#9e9e9e",
}

#: Finer 5-bucket ramp for the multi-stop map, keyed by ``shade_score``
#: (upper bound, colour) in increasing order. Purely a rendering choice —
#: the underlying data still only has the 3-class ``shade_class``.
SCORE_COLOR_SCALE = [
    (0.10, "#b71c1c"),  # very poor
    (0.20, "#e65100"),  # poor
    (0.32, "#f9a825"),  # partial
    (0.46, "#7cb342"),  # good
    (float("inf"), "#1b5e20"),  # excellent
]


def _score_color(score: float | None) -> str:
    if score is None or pd.isna(score):
        return CLASS_COLORS["unknown"]
    for upper, color in SCORE_COLOR_SCALE:
        if score < upper:
            return color
    return SCORE_COLOR_SCALE[-1][1]


def merge_results(paths: list[Path]) -> gpd.GeoDataFrame:
    """Concatenate per-run result GeoParquets (e.g. one per country).

    Missing paths are skipped (logged), so a merge list can name every
    country's output even when only some of them have been run so far.
    Later files win on duplicate ``stop_id`` values, so a re-processed
    country can be merged over an older combined set.
    """
    existing = []
    for p in paths:
        if p.exists():
            existing.append(p)
        else:
            logger.warning("merge: skipping missing input %s", p)
    if not existing:
        raise ValueError(f"no input files exist among: {[str(p) for p in paths]}")
    frames = [gpd.read_parquet(p) for p in existing]
    gdf = pd.concat(frames, ignore_index=True)
    gdf = gdf.drop_duplicates(subset="stop_id", keep="last").reset_index(drop=True)
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=frames[0].crs)


def write_outputs(
    gdf: gpd.GeoDataFrame, cfg: OutputConfig, roads_path: Path | None = None
) -> dict[str, Path]:
    """Write GeoParquet + GeoPackage + HTML map; returns the paths.

    ``roads_path``, if given, is a pre-built static major-roads GeoJSON (see
    :func:`~shaduw_langs_de_snelweg.roads.build_major_roads_dataset`) drawn
    on the map; loading it never touches the network.
    """
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

    write_map(gdf, paths["map"], roads=load_major_roads(roads_path))
    for name, path in paths.items():
        logger.info("wrote %s: %s", name, path)
    return paths


def write_map(
    gdf: gpd.GeoDataFrame, path: Path, roads: gpd.GeoDataFrame | None = None
) -> None:
    """Folium quick-look: parking polygons + markers coloured by shade score.

    ``roads``, if given (see :func:`~shaduw_langs_de_snelweg.roads.load_major_roads`),
    is drawn as a black, togglable motorway overlay with the route number on
    hover, on its own map pane behind the stops so it never covers them.

    Stops are drawn in ascending ``shade_score`` order so the best (greenest)
    ones paint last and stay on top where markers overlap. ``prefer_canvas``
    renders to one canvas instead of one SVG/DOM node per stop — with
    thousands of stops plus a continent's motorways, the DOM-per-feature
    default is slow enough that layers can appear to silently not render.
    """
    if "shade_score" in gdf.columns:
        gdf = gdf.sort_values("shade_score", na_position="first").reset_index(drop=True)
    if "centroid" in gdf.columns:
        centroids = gpd.GeoSeries(gdf["centroid"], crs=gdf.crs)
    else:
        centroids = gdf.geometry.to_crs(gdf.estimate_utm_crs()).centroid.to_crs(gdf.crs)
    m = folium.Map(
        location=[centroids.y.mean(), centroids.x.mean()],
        zoom_start=8,
        # tile.openstreetmap.org now requires a Referer header, which
        # file:// pages don't send, so its tiles fail to load with a
        # "referer is required" error tile when the map HTML is opened
        # directly from disk. CartoDB's tiles don't have that requirement.
        tiles="CartoDB positron",
        prefer_canvas=True,
    )
    if roads is not None and not roads.empty:
        # dedicated pane below the default overlay pane (z-index 400) so
        # roads always render behind stop polygons/markers. Note: this must
        # NOT be wrapped in a FeatureGroup + LayerControl — that combination
        # throws inside Leaflet itself (a "Cannot read properties of
        # undefined (reading 'setZIndex')" in L.Control.Layers._addLayer),
        # which silently aborts the rest of the script, including every stop
        # marker added afterward.
        folium.map.CustomPane("roads", z_index=350, pointer_events=True).add_to(m)
        folium.GeoJson(
            roads,
            name="Major roads",
            style_function=lambda _f: {"color": "#000000", "weight": 2, "opacity": 0.8},
            tooltip=folium.GeoJsonTooltip(fields=["ref"], aliases=["Road:"]),
            pane="roads",
        ).add_to(m)
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
        color = _score_color(row.get("shade_score"))
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
