import datetime as dt

import geopandas as gpd
import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import LineString, Point, box

from shaduw_langs_de_snelweg.config import OutputConfig
from shaduw_langs_de_snelweg.outputs import (
    SCORE_COLOR_SCALE,
    _score_color,
    merge_results,
    write_map,
    write_outputs,
)
from shaduw_langs_de_snelweg.shadow import modeled_shadow_fraction


def sample_gdf():
    poly = box(5.0, 52.0, 5.001, 52.001)
    gdf = gpd.GeoDataFrame(
        {
            "stop_id": ["s1"],
            "name": ["Test stop"],
            "country": ["NL"],
            "shade_score": [0.55],
            "shade_class": ["good"],
            "shadow_frac_modeled_15h": [0.6],
            "tree_fraction": [0.4],
            "ndvi_summer": [0.5],
            "n_scenes_used": [10],
            "processed_at": [dt.datetime.now(dt.timezone.utc)],
            "geometry": [poly],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    gdf["centroid"] = gpd.GeoSeries([Point(5.0005, 52.0005)], crs="EPSG:4326")
    return gdf


def test_write_outputs_creates_all_files(tmp_path):
    cfg = OutputConfig(directory=tmp_path)
    paths = write_outputs(sample_gdf(), cfg)
    for path in paths.values():
        assert path.exists()

    round_trip = gpd.read_parquet(paths["geoparquet"])
    assert list(round_trip["stop_id"]) == ["s1"]
    assert "centroid" in round_trip.columns

    gpkg = gpd.read_file(paths["geopackage"])
    assert "centroid_lon" in gpkg.columns
    assert "centroid" not in gpkg.columns


def test_merge_results_concatenates_and_dedupes(tmp_path):
    nl = sample_gdf()
    de = sample_gdf()
    de["stop_id"] = ["s2"]
    de["country"] = ["DE"]
    dupe = sample_gdf()  # same stop_id as nl, newer score: should win
    dupe["shade_score"] = [0.99]

    paths = []
    for i, gdf in enumerate((nl, de, dupe)):
        path = tmp_path / f"part{i}.parquet"
        gdf.to_parquet(path)
        paths.append(path)

    merged = merge_results(paths)
    assert sorted(merged["stop_id"]) == ["s1", "s2"]
    assert merged.loc[merged["stop_id"] == "s1", "shade_score"].iloc[0] == 0.99
    assert merged.crs is not None


def test_merge_results_skips_missing_paths(tmp_path):
    path = tmp_path / "part0.parquet"
    sample_gdf().to_parquet(path)
    not_yet_run = tmp_path / "part1.parquet"

    merged = merge_results([path, not_yet_run])
    assert sorted(merged["stop_id"]) == ["s1"]


def test_merge_results_raises_if_all_paths_missing(tmp_path):
    with pytest.raises(ValueError):
        merge_results([tmp_path / "missing.parquet"])


def test_write_outputs_embeds_static_roads_overlay(tmp_path):
    roads_path = tmp_path / "eu-major-roads.geojson"
    roads = gpd.GeoDataFrame(
        {"ref": ["A1"], "geometry": [LineString([(5.0, 52.0), (5.1, 52.1)])]},
        crs="EPSG:4326",
    )
    roads.to_file(roads_path, driver="GeoJSON")

    cfg = OutputConfig(directory=tmp_path / "out")
    paths = write_outputs(sample_gdf(), cfg, roads_path=roads_path)

    html = paths["map"].read_text()
    assert "Road:" in html
    assert "A1" in html


def test_write_outputs_without_roads_path_has_no_overlay(tmp_path):
    cfg = OutputConfig(directory=tmp_path)
    paths = write_outputs(sample_gdf(), cfg)  # roads_path defaults to None
    assert "Road:" not in paths["map"].read_text()


def test_score_color_has_five_distinct_buckets():
    assert len(SCORE_COLOR_SCALE) == 5
    assert len({color for _, color in SCORE_COLOR_SCALE}) == 5


def test_score_color_is_monotonic_low_to_high():
    samples = [0.0, 0.15, 0.25, 0.40, 0.60]
    colors = [_score_color(s) for s in samples]
    # each successive bucket should differ (score strictly increases across bins)
    assert len(set(colors)) == len(samples)


def test_score_color_falls_back_for_missing_score():
    assert _score_color(None) == "#9e9e9e"
    assert _score_color(float("nan")) == "#9e9e9e"


def multi_stop_gdf():
    rows = []
    for i, (stop_id, score) in enumerate([("bad", 0.02), ("mid", 0.30), ("best", 0.90)]):
        poly = box(5.0 + i * 0.01, 52.0, 5.001 + i * 0.01, 52.001)
        rows.append(
            {
                "stop_id": stop_id,
                "name": stop_id,
                "country": "NL",
                "shade_score": score,
                "shade_class": "unknown",
                "shadow_frac_modeled_15h": 0.0,
                "tree_fraction": 0.0,
                "ndvi_summer": 0.0,
                "n_scenes_used": 1,
                "processed_at": dt.datetime.now(dt.timezone.utc),
                "geometry": poly,
            }
        )
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf["centroid"] = gdf.geometry.centroid
    return gdf


def test_write_map_draws_best_stop_last_so_it_stays_on_top(tmp_path):
    # deliberately unsorted input: "best" (highest score) comes first
    gdf = multi_stop_gdf().iloc[[2, 0, 1]].reset_index(drop=True)
    path = tmp_path / "map.html"
    write_map(gdf, path)

    html = path.read_text()
    positions = {
        stop_id: html.index(f"stop_id</b>: {stop_id}") for stop_id in ("bad", "mid", "best")
    }
    assert positions["bad"] < positions["mid"] < positions["best"]


def test_write_map_roads_are_on_a_pane_behind_stops(tmp_path):
    roads = gpd.GeoDataFrame(
        {"ref": ["A1"], "geometry": [LineString([(5.0, 52.0), (5.1, 52.1)])]},
        crs="EPSG:4326",
    )
    path = tmp_path / "map.html"
    write_map(sample_gdf(), path, roads=roads)

    html = path.read_text()
    assert "createPane" in html
    assert '"pane": "roads"' in html
    assert "preferCanvas" in html


def make_chm(values):
    shape = values.shape
    res = 1.0
    coords = {
        "y": 5_790_000.0 - (np.arange(shape[0]) + 0.5) * res,
        "x": 650_000.0 + (np.arange(shape[1]) + 0.5) * res,
    }
    da = xr.DataArray(values.astype("float32"), coords=coords, dims=("y", "x"))
    return da.rio.write_crs("EPSG:32631")


def test_modeled_shadow_fraction_bounds():
    when = dt.datetime(2025, 6, 21, 15, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    mask = np.zeros((20, 20), dtype=bool)
    mask[5:15, 5:15] = True

    bare = make_chm(np.zeros((20, 20)))
    assert modeled_shadow_fraction(bare, mask, 52.0, 5.0, when) == 0.0

    forest = make_chm(np.full((20, 20), 12.0))
    assert modeled_shadow_fraction(forest, mask, 52.0, 5.0, when) == 1.0

    empty_mask = np.zeros((20, 20), dtype=bool)
    assert np.isnan(modeled_shadow_fraction(bare, empty_mask, 52.0, 5.0, when))
