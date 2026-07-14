from pathlib import Path

import pytest
from pydantic import ValidationError

from shaduw_langs_de_snelweg.config import CanopyConfig, load_config

REPO = Path(__file__).parent.parent


def test_load_repo_prototype_config():
    cfg = load_config(REPO / "configs" / "nl-prototype.toml")
    assert cfg.country == "NL"
    assert cfg.stops.seed_path.is_absolute()
    assert cfg.stops.seed_path.exists()
    assert cfg.cache_dir.is_absolute()
    assert cfg.output.directory.is_absolute()
    assert cfg.shadow.hours == [12, 15]
    assert cfg.stac.max_cloud_cover == 30.0


def test_minimal_config_defaults(tmp_path):
    seed = tmp_path / "seeds.geojson"
    seed.write_text('{"type": "FeatureCollection", "features": []}')
    toml = tmp_path / "cfg.toml"
    toml.write_text('[stops]\nseed_path = "seeds.geojson"\n')
    cfg = load_config(toml)
    assert cfg.stops.seed_path == seed
    assert cfg.canopy.source == "meta"
    assert cfg.canopy.url_template is None
    assert cfg.score.w_modeled_15h == 0.5
    assert cfg.detection.brightness_threshold == 0.08


def test_local_canopy_requires_path():
    with pytest.raises(ValidationError):
        CanopyConfig(source="local")
    with pytest.raises(ValidationError):
        CanopyConfig(source="banana")


def test_default_url_templates_contain_tile_placeholder():
    from shaduw_langs_de_snelweg.canopy import DEFAULT_URL_TEMPLATES

    assert all("{tile}" in t for t in DEFAULT_URL_TEMPLATES.values())
