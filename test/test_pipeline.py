import json

import geopandas as gpd
import pytest
from shapely.geometry import box

from shaduw_langs_de_snelweg import pipeline
from shaduw_langs_de_snelweg.config import (
    OutputConfig,
    PipelineConfig,
    StopsConfig,
)


@pytest.fixture
def cfg(tmp_path):
    seed = tmp_path / "seeds.geojson"
    seed.write_text('{"type": "FeatureCollection", "features": []}')
    return PipelineConfig(
        stops=StopsConfig(seed_path=seed, use_overpass=False),
        cache_dir=tmp_path / "cache",
        output=OutputConfig(directory=tmp_path / "output"),
    )


def prepared_stops():
    poly = box(5.0, 52.0, 5.002, 52.001)
    return gpd.GeoDataFrame(
        {
            "stop_id": ["s1", "s2"],
            "name": ["Stop 1", "Stop 2"],
            "country": ["NL", "NL"],
            "polygon_source": ["seed", "seed"],
            "geometry": [poly, poly],
            "aoi": [poly.buffer(0.001), poly.buffer(0.001)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


GOOD_METRICS = {
    "ndvi_summer": 0.6,
    "ndvi_winter": 0.4,
    "ndvi_delta": 0.2,
    "tree_fraction": 0.3,
    "mean_canopy_height": 5.0,
    "shadow_frac_detected_djf": 0.1,
    "shadow_frac_detected_mam": 0.1,
    "shadow_frac_detected_jja": 0.1,
    "shadow_frac_detected_son": 0.1,
    "n_scenes_used": 10,
    "shadow_frac_modeled_12h": 0.2,
    "shadow_frac_modeled_15h": 0.25,
    "shade_score": 0.24,
    "shade_class": "partial",
    "processed_at": "2026-07-14T12:00:00+00:00",
}


def test_run_uses_cache_and_retries_failures(cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "_stops_with_cache", lambda c, f: prepared_stops())

    calls = []

    def fake_process(row, _cfg):
        calls.append(row["stop_id"])
        if row["stop_id"] == "s2" and len(calls) <= 2:
            raise RuntimeError("boom")
        return dict(GOOD_METRICS)

    monkeypatch.setattr(pipeline, "process_stop", fake_process)

    # first run: s1 succeeds and is cached, s2 fails and is NOT cached
    gdf = pipeline.run_pipeline(cfg)
    assert list(gdf["stop_id"]) == ["s1"]
    assert calls == ["s1", "s2"]
    cached = json.loads((cfg.cache_dir / "stops" / "s1.json").read_text())
    assert "error" not in cached
    assert not (cfg.cache_dir / "stops" / "s2.json").exists()

    # second run: s1 comes from cache, s2 is retried and now succeeds
    gdf = pipeline.run_pipeline(cfg)
    assert calls == ["s1", "s2", "s2"]
    assert sorted(gdf["stop_id"]) == ["s1", "s2"]
    assert (cfg.output.directory / cfg.output.geoparquet).exists()


def test_force_recomputes_cached_stops(cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "_stops_with_cache", lambda c, f: prepared_stops())
    calls = []

    def fake_process(row, _cfg):
        calls.append(row["stop_id"])
        return dict(GOOD_METRICS)

    monkeypatch.setattr(pipeline, "process_stop", fake_process)

    pipeline.run_pipeline(cfg)
    pipeline.run_pipeline(cfg, force=True)
    assert calls == ["s1", "s2", "s1", "s2"]


def test_limit_processes_first_n(cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "_stops_with_cache", lambda c, f: prepared_stops())
    monkeypatch.setattr(pipeline, "process_stop", lambda row, _cfg: dict(GOOD_METRICS))
    gdf = pipeline.run_pipeline(cfg, limit=1)
    assert list(gdf["stop_id"]) == ["s1"]
