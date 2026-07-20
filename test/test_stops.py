import json
import math

import geopandas as gpd
import pytest
import requests
from shapely.geometry import Point

from shaduw_langs_de_snelweg import stops as stops_module
from shaduw_langs_de_snelweg.stops import (
    buffer_in_meters,
    load_seeds,
    osm_elements_to_polygons,
    overpass_query,
)


def overpass_json():
    square = [
        {"lon": 5.0000, "lat": 52.0000},
        {"lon": 5.0010, "lat": 52.0000},
        {"lon": 5.0010, "lat": 52.0006},
        {"lon": 5.0000, "lat": 52.0006},
        {"lon": 5.0000, "lat": 52.0000},
    ]
    open_way = square[:-1]
    return {
        "elements": [
            {"type": "way", "id": 1, "geometry": square, "tags": {}},
            {"type": "way", "id": 2, "geometry": open_way, "tags": {}},  # not closed
            {"type": "node", "id": 3, "lat": 52.0, "lon": 5.0},
        ]
    }


def test_osm_elements_to_polygons_keeps_closed_ways_only():
    polys = osm_elements_to_polygons(overpass_json())
    assert len(polys) == 1
    assert polys[0].is_valid


def test_load_seeds_generates_ids(tmp_path):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "A"},
                "geometry": {"type": "Point", "coordinates": [5.0, 52.0]},
            },
            {
                "type": "Feature",
                "properties": {"stop_id": "x-1", "name": "B"},
                "geometry": {"type": "Point", "coordinates": [5.1, 52.1]},
            },
        ],
    }
    path = tmp_path / "seeds.geojson"
    path.write_text(json.dumps(geojson))
    gdf = load_seeds(path)
    assert set(gdf["stop_id"]) == {"stop-0", "x-1"}
    assert gdf.crs.to_epsg() == 4326


def test_load_seeds_rejects_duplicate_ids(tmp_path):
    feature = {
        "type": "Feature",
        "properties": {"stop_id": "dup", "name": "A"},
        "geometry": {"type": "Point", "coordinates": [5.0, 52.0]},
    }
    geojson = {"type": "FeatureCollection", "features": [feature, feature]}
    path = tmp_path / "seeds.geojson"
    path.write_text(json.dumps(geojson))
    with pytest.raises(ValueError, match="duplicate"):
        load_seeds(path)


def test_buffer_in_meters_radius():
    buffered = buffer_in_meters(Point(5.0, 52.0), 100.0)
    area_m2 = (
        gpd.GeoSeries([buffered], crs="EPSG:4326")
        .to_crs(gpd.GeoSeries([buffered], crs="EPSG:4326").estimate_utm_crs())
        .area.iloc[0]
    )
    assert math.isclose(area_m2, math.pi * 100.0**2, rel_tol=0.05)


def test_repo_seed_file_loads():
    from pathlib import Path

    seed = Path(__file__).parent.parent / "configs" / "seeds" / "nl-prototype.geojson"
    gdf = load_seeds(seed)
    assert len(gdf) == 5
    assert gdf["stop_id"].is_unique


class _FakeResponse:
    def __init__(self, status_code, headers=None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._json_data


def test_overpass_query_backs_off_on_429_retry_after(monkeypatch):
    responses = [
        _FakeResponse(429, headers={"Retry-After": "17"}),
        _FakeResponse(200, json_data={"elements": []}),
    ]

    def fake_post(url, data, headers, timeout):
        return responses.pop(0)

    sleeps = []
    monkeypatch.setattr(stops_module.requests, "post", fake_post)
    monkeypatch.setattr(stops_module.time, "sleep", lambda s: sleeps.append(s))

    result = overpass_query("[out:json];", "https://overpass.kumi.systems/api/interpreter", 60)

    assert result == {"elements": []}
    assert 17 in sleeps  # honoured the server's Retry-After, not just a fixed backoff


def test_overpass_query_defaults_wait_when_no_retry_after(monkeypatch):
    responses = [
        _FakeResponse(429),  # no Retry-After header
        _FakeResponse(200, json_data={"elements": []}),
    ]

    def fake_post(url, data, headers, timeout):
        return responses.pop(0)

    sleeps = []
    monkeypatch.setattr(stops_module.requests, "post", fake_post)
    monkeypatch.setattr(stops_module.time, "sleep", lambda s: sleeps.append(s))

    overpass_query("[out:json];", "https://overpass.kumi.systems/api/interpreter", 60)

    assert stops_module.DEFAULT_RATE_LIMIT_WAIT_S in sleeps
