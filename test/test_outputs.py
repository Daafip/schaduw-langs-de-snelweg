import datetime as dt

import geopandas as gpd
import numpy as np
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import Point, box

from shaduw_langs_de_snelweg.config import OutputConfig
from shaduw_langs_de_snelweg.outputs import merge_results, write_outputs
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
