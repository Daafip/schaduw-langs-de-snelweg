import geopandas as gpd

from shaduw_langs_de_snelweg import roads as roads_module
from shaduw_langs_de_snelweg.roads import build_major_roads_dataset, load_major_roads


def overpass_json():
    return {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "motorway", "ref": "A1"},
                "geometry": [
                    {"lon": 5.0, "lat": 52.0},
                    {"lon": 5.1, "lat": 52.1},
                ],
            },
            {
                "type": "way",
                "id": 2,
                "tags": {"highway": "motorway", "ref": "A1"},
                "geometry": [
                    {"lon": 5.1, "lat": 52.1},
                    {"lon": 5.2, "lat": 52.2},
                ],
            },
            {
                "type": "way",
                "id": 3,
                "tags": {"highway": "motorway"},  # no ref: excluded
                "geometry": [
                    {"lon": 6.0, "lat": 53.0},
                    {"lon": 6.1, "lat": 53.1},
                ],
            },
        ]
    }


def test_load_major_roads_missing_path_returns_empty(tmp_path):
    gdf = load_major_roads(tmp_path / "does-not-exist.geojson")
    assert gdf.empty

    gdf = load_major_roads(None)
    assert gdf.empty


def test_build_major_roads_dataset_dissolves_by_ref_writes_static_file_and_caches_per_country(
    tmp_path, monkeypatch
):
    calls = []

    def fake_query(query, url, timeout, retries=2):
        calls.append(query)
        return overpass_json()

    monkeypatch.setattr(roads_module, "overpass_query", fake_query)

    cache_dir = tmp_path / "cache"
    out_path = tmp_path / "static" / "eu-major-roads.geojson"
    gdf = build_major_roads_dataset(["NL"], cache_dir, out_path)

    assert list(gdf["ref"]) == ["A1"]  # both A1 segments merged into one row
    assert (cache_dir / "roads" / "nl.geojson").exists()
    assert len(calls) == 1

    # loading the static file afterwards is offline: no further query
    loaded = load_major_roads(out_path)
    assert list(loaded["ref"]) == ["A1"]
    assert len(calls) == 1

    # rebuilding reuses the per-country cache rather than re-querying
    build_major_roads_dataset(["NL"], cache_dir, out_path)
    assert len(calls) == 1


def test_build_major_roads_dataset_skips_failed_country(tmp_path, monkeypatch):
    def fake_fetch(country, cache_dir, overpass_url="", timeout=300):
        if country == "BAD":
            raise RuntimeError("all Overpass endpoints failed")
        return gpd.GeoDataFrame(
            {"ref": ["A1"], "geometry": gpd.GeoSeries.from_wkt(["LINESTRING (5 52, 6 53)"])},
            crs="EPSG:4326",
        )

    monkeypatch.setattr(roads_module, "_fetch_country_roads", fake_fetch)

    gdf = build_major_roads_dataset(["NL", "BAD"], tmp_path / "cache", tmp_path / "out.geojson")
    assert list(gdf["ref"]) == ["A1"]


def test_build_major_roads_dataset_empty_when_all_fail(tmp_path, monkeypatch):
    def fake_fetch(country, cache_dir, overpass_url="", timeout=300):
        raise RuntimeError("boom")

    monkeypatch.setattr(roads_module, "_fetch_country_roads", fake_fetch)

    gdf = build_major_roads_dataset(["NL"], tmp_path / "cache", tmp_path / "out.geojson")
    assert gdf.empty
