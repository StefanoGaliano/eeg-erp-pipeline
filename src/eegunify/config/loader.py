"""Load a YAML config, validate it, and resolve per-subject overrides."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from eegunify.config.schema import Config


class ConfigError(ValueError):
    """Raised when a config file cannot be read or does not validate."""


def _format_errors(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        lines.append(f"  {loc}: {e['msg']}")
    return "\n".join(lines)


def parse_config(data: dict[str, Any], source: str = "<dict>") -> Config:
    """Validate an already-parsed mapping into a :class:`Config`."""
    try:
        return Config.model_validate(data)
    except ValidationError as err:
        raise ConfigError(f"invalid config {source}:\n{_format_errors(err)}") from err


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML config file.

    Parameters
    ----------
    path
        Path to the YAML file.

    Returns
    -------
    Config
        The validated config.

    Raises
    ------
    ConfigError
        If the file is missing, is not valid YAML, or fails validation. The message lists every
        failing key by its dotted YAML path.
    """
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(f"config not found: {p}") from err
    except yaml.YAMLError as err:
        raise ConfigError(f"config {p} is not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise ConfigError(f"config {p} must be a mapping at the top level")
    return parse_config(data, str(p))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def resolve_for_subject(cfg: Config, subject: str) -> Config:
    """Return the config with the ``overrides["sub-<subject>"]`` patch applied.

    A per-subject ``channels.bads`` list in an override replaces that subject's bads.
    """
    patch = cfg.overrides.get(f"sub-{subject}")
    if not patch:
        return cfg
    base = cfg.model_dump(mode="python")
    if "channels" in patch and isinstance(patch["channels"].get("bads"), list):
        patch = copy.deepcopy(patch)
        patch["channels"]["bads"] = {f"sub-{subject}": patch["channels"]["bads"]}
    merged = _deep_merge(base, patch)
    merged["overrides"] = {}
    return parse_config(merged, f"<override sub-{subject}>")
