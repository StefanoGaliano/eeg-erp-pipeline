"""Per-dataset configuration: pydantic schema and YAML loader."""

from eegunify.config.loader import ConfigError, load_config, resolve_for_subject
from eegunify.config.schema import Config

__all__ = ["Config", "ConfigError", "load_config", "resolve_for_subject"]
