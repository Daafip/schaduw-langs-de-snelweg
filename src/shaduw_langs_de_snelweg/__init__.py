"""Shade detection at roadside stops (EU) from open satellite data."""

__version__ = "0.1.0"

from shaduw_langs_de_snelweg.config import PipelineConfig, load_config
from shaduw_langs_de_snelweg.pipeline import run_pipeline

__all__ = ["PipelineConfig", "load_config", "run_pipeline", "__version__"]
