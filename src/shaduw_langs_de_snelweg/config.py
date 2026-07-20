"""Configuration models for the shade detection pipeline.

The pipeline is driven by a single TOML file, see ``configs/nl-prototype.toml``
for a documented example. Load it with :func:`load_config`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

#: Season key -> months (meteorological seasons).
SEASONS: dict[str, tuple[int, int, int]] = {
    "djf": (12, 1, 2),
    "mam": (3, 4, 5),
    "jja": (6, 7, 8),
    "son": (9, 10, 11),
}


class StopsConfig(BaseModel):
    """Where the stops come from and how their analysis area is built.

    Attributes
    ----------
    seed_path : Path
        GeoJSON with seed stops (points or polygons). Point seeds are
        resolved to a parking polygon via Overpass, or buffered as fallback.
    parking_buffer_m : float
        Buffer radius around a point seed when no OSM polygon is found.
    aoi_buffer_m : float
        Extra buffer around the parking polygon: trees *next to* a parking
        cast shade *onto* it.
    use_overpass : bool
        Query Overpass for parking polygons around point seeds.
    overpass_url : str
        Overpass API endpoint.
    osm_search_radius_m : float
        Search radius around a point seed for OSM parking polygons.
    """

    seed_path: Path
    parking_buffer_m: float = 75.0
    aoi_buffer_m: float = 30.0
    use_overpass: bool = True
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    osm_search_radius_m: float = 150.0


class StacConfig(BaseModel):
    """Sentinel-2 L2A acquisition via STAC.

    Attributes
    ----------
    url : str
        STAC API endpoint. Default is AWS earth-search (no account needed).
    collection : str
        Collection id.
    max_cloud_cover : float
        Maximum scene cloud cover percentage in the search.
    years_back : int
        How many years back from ``end_date`` to search.
    end_date : str | None
        ISO date (``YYYY-MM-DD``) marking the end of the search period;
        ``None`` means today.
    resolution : float
        Output grid resolution in metres.
    max_items_per_season : int
        Cap on scenes per seasonal composite (most recent first).
    """

    url: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-l2a"
    max_cloud_cover: float = 30.0
    years_back: int = 2
    end_date: str | None = None
    resolution: float = 10.0
    max_items_per_season: int = 12


class CanopyConfig(BaseModel):
    """Canopy height model source.

    Attributes
    ----------
    source : str
        ``"meta"`` (Meta/WRI global canopy height, 1 m COG quadkey tiles on
        AWS Open Data — the default), ``"eth"`` (ETH Global Canopy Height
        2020, 10 m 3-degree COGs) or ``"local"`` (a local GeoTIFF).
    local_path : Path | None
        Path to a local CHM GeoTIFF when ``source = "local"``.
    url_template : str | None
        Override the tile URL template for the chosen source; ``{tile}`` is
        replaced by the quadkey (meta) or tile label like ``N51E003`` (eth).
        ``None`` uses the built-in template.
    tree_height_threshold_m : float
        Canopy height above which a pixel counts as tree.
    resolution : float
        Working resolution (metres) for the shadow model grid.
    """

    source: str = "meta"
    local_path: Path | None = None
    url_template: str | None = None
    tree_height_threshold_m: float = 3.0
    resolution: float = 1.0

    @model_validator(mode="after")
    def _check_local(self) -> "CanopyConfig":
        if self.source == "local" and self.local_path is None:
            raise ValueError("canopy.source = 'local' requires canopy.local_path")
        if self.source not in ("meta", "eth", "local"):
            raise ValueError(f"unknown canopy source: {self.source!r}")
        return self


class ShadowModelConfig(BaseModel):
    """Geometric shadow casting settings.

    Attributes
    ----------
    date : str
        Month-day (``MM-DD``) of the modelled day; default summer solstice.
    hours : list[int]
        Local clock hours to model, e.g. ``[12, 15]``.
    timezone : str
        IANA timezone for the local hours.
    self_shade_height_m : float
        Pixels with canopy above this height are counted as shaded
        (a car parked under the canopy itself).
    """

    date: str = "06-21"
    hours: list[int] = Field(default_factory=lambda: [12, 15])
    timezone: str = "Europe/Amsterdam"
    self_shade_height_m: float = 2.0


class DetectionConfig(BaseModel):
    """Spectral shadow detection on seasonal composites.

    Attributes
    ----------
    brightness_threshold : float
        Broadband (R+G+B+NIR)/4 reflectance below which a cloud-free pixel
        is counted as shadow at ~10:30 acquisition time. Calibrated on NL
        composites: vegetated/paved surfaces sit around 0.10-0.14, shadow
        below ~0.08.
    """

    brightness_threshold: float = 0.08


class ScoreConfig(BaseModel):
    """Weights and class bounds for the composite shade score.

    ``score = w_modeled_15h * shadow_frac_modeled_15h
            + w_tree_fraction * tree_fraction
            + w_detected_summer * shadow_frac_detected_jja``

    Weights are renormalised over the metrics that are available, so a
    missing composite degrades gracefully instead of dragging the score down.
    """

    w_modeled_15h: float = 0.5
    w_tree_fraction: float = 0.3
    w_detected_summer: float = 0.2
    bound_partial: float = 0.15
    bound_good: float = 0.40


class OutputConfig(BaseModel):
    """Output artefacts."""

    directory: Path = Path("output")
    geoparquet: str = "stops_shade.parquet"
    geopackage: str = "stops_shade.gpkg"
    map_html: str = "stops_shade_map.html"
    write_rasters: bool = False


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration (one TOML file)."""

    name: str = "shaduw-langs-de-snelweg"
    country: str = "NL"
    cache_dir: Path = Path("cache")
    # static, pre-built via `shaduw fetch-roads`; loading it touches no network
    roads_path: Path = Path("roads/eu-major-roads.geojson")
    stops: StopsConfig
    stac: StacConfig = Field(default_factory=StacConfig)
    canopy: CanopyConfig = Field(default_factory=CanopyConfig)
    shadow: ShadowModelConfig = Field(default_factory=ShadowModelConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: str | Path) -> PipelineConfig:
    """Load a :class:`PipelineConfig` from a TOML file.

    Relative paths in the config (seeds, cache, output) are resolved
    relative to the config file's directory.
    """
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    cfg = PipelineConfig(**raw)

    base = path.parent.resolve()

    def _abs(p: Path) -> Path:
        return p if p.is_absolute() else base / p

    cfg.stops.seed_path = _abs(cfg.stops.seed_path)
    cfg.cache_dir = _abs(cfg.cache_dir)
    cfg.roads_path = _abs(cfg.roads_path)
    cfg.output.directory = _abs(cfg.output.directory)
    if cfg.canopy.local_path is not None:
        cfg.canopy.local_path = _abs(cfg.canopy.local_path)
    return cfg
