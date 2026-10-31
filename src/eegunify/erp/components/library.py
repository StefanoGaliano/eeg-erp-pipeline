"""Component library: ERP component definitions as data (docs/BLUEPRINT_PHASES.md section 5.2).

Windows and electrodes are never hard-coded in Python. They live in the YAML files next to
this module (``visual.yaml``, ``cognitive.yaml``, ``motor.yaml``) and in any extra files a
config lists in ``components.library_paths``. Every entry is keyed ``<id>@<preset>``
(``n170@erpcore``) and validated by :class:`ComponentDef`.

Resolution (:func:`resolve`): ``components.use: [n170]`` takes ``n170@<components.preset>`` if
it exists, else the first other preset that defines ``n170`` (with a warning naming it);
``n170@classic`` picks a preset explicitly. ``components.overrides[<id>]`` is deep-merged over
the result and validated again.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from eegunify.config.erp_sections import ComponentsSection

_KEY_RE = re.compile(r"^([a-z][a-z0-9_]*)@([a-z][a-z0-9_]*)$")
BUILTIN_FILES = ("visual.yaml", "cognitive.yaml", "motor.yaml")

Measure = Literal["mean_amplitude", "peak_amplitude", "peak_latency", "fractional_area_latency"]
_DEFAULT_MEASURES: tuple[Measure, ...] = ("mean_amplitude",)


class LibraryError(ValueError):
    """A component file is malformed, or a requested component cannot be resolved."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class Rois(_Strict):
    """Electrodes a component is measured at; each list is pooled (mean waveform) first."""

    primary: list[str] = Field(min_length=1)
    """Main measurement site(s); these values are compared with published ones."""
    secondary: list[str] = Field(default_factory=list)
    """Extra sites reported alongside, each measured separately."""


class ComponentDef(_Strict):
    """One ERP component under one preset (a library entry ``<id>@<preset>``)."""

    name: str
    """Display name (``N170``)."""
    citation: str
    """Where the window and sites come from (paper and table)."""
    epoch_set: str = "stim"
    """Epoch set (``epochs.sets`` name) the component is measured on."""
    contrast: str | None = None
    """Difference wave measured (``contrasts`` name); the conditions are measured too."""
    window_ms: tuple[float, float]
    """Measurement window, relative to the time-locking event."""
    rois: Rois
    """Measurement sites."""
    lateralized_pairs: list[tuple[str, str]] = Field(default_factory=list)
    """Left/right channel pairs for contralateral-minus-ipsilateral waves (N2pc, LRP)."""
    measures: list[Measure] = Field(default_factory=lambda: list(_DEFAULT_MEASURES))
    """Measurements taken in the window."""
    polarity: Literal["positive", "negative"]
    """Expected sign of the component (peak search direction, polarity QA)."""
    fractional_area: float = Field(default=0.5, gt=0, lt=1)
    """Fraction of the signed area for ``fractional_area_latency``."""
    reference: str | None = None
    """Reference the window was defined for (``average``, ``P9/P10``, ``Fz``)."""
    baseline_ms: tuple[float, float] | None = None
    """Baseline the source used, when it differs from the epoch set's (ERN, LRP)."""
    notes: str = ""
    """Free text: caveats, onset delays, what the values were checked against."""

    @model_validator(mode="after")
    def _window(self) -> ComponentDef:
        if self.window_ms[1] <= self.window_ms[0]:
            raise ValueError("window_ms must be [start, end] with start < end")
        return self


@dataclass(frozen=True)
class ResolvedComponent:
    """A component chosen for a run: its library key and final (overridden) definition."""

    id: str
    key: str
    definition: ComponentDef
    overridden: bool = False


@dataclass
class Resolution:
    components: dict[str, ResolvedComponent]
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Definitions as written to provenance and hashed into the stage key."""
        return {
            cid: {
                "key": rc.key,
                "overridden": rc.overridden,
                **rc.definition.model_dump(mode="json"),
            }
            for cid, rc in sorted(self.components.items())
        }


def split_key(key: str) -> tuple[str, str]:
    m = _KEY_RE.match(key)
    if not m:
        raise LibraryError(f"component key {key!r} must look like '<id>@<preset>' (lowercase)")
    return m.group(1), m.group(2)


def _parse(text: str, source: str) -> dict[str, ComponentDef]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as err:
        raise LibraryError(f"{source} is not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise LibraryError(f"{source} must be a mapping of '<id>@<preset>' entries")
    out: dict[str, ComponentDef] = {}
    for key, entry in data.items():
        split_key(str(key))
        try:
            out[str(key)] = ComponentDef.model_validate(entry)
        except ValidationError as err:
            raise LibraryError(f"{source}: entry {key!r} is invalid:\n{err}") from err
    return out


def load_library(extra_paths: Iterable[str | Path] = ()) -> dict[str, ComponentDef]:
    """All built-in entries plus those of ``extra_paths``; a key defined twice is an error."""
    pkg = resources.files("eegunify.erp.components")
    sources = [
        (f"eegunify/erp/components/{n}", pkg.joinpath(n).read_text("utf-8")) for n in BUILTIN_FILES
    ]
    for p in extra_paths:
        path = Path(p)
        try:
            sources.append((path.as_posix(), path.read_text(encoding="utf-8")))
        except FileNotFoundError as err:
            raise LibraryError(f"component library file not found: {path.as_posix()}") from err
    library: dict[str, ComponentDef] = {}
    origin: dict[str, str] = {}
    for src, text in sources:
        for key, comp in _parse(text, src).items():
            if key in library:
                raise LibraryError(f"component {key!r} defined in {origin[key]} and {src}")
            library[key] = comp
            origin[key] = src
    return library


def presets(library: Mapping[str, ComponentDef]) -> list[str]:
    return sorted({split_key(k)[1] for k in library})


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve(
    section: ComponentsSection,
    library: Mapping[str, ComponentDef] | None = None,
    project_root: str | Path = ".",
) -> Resolution:
    """Pick and override the components a config asks for (``components`` section).

    Raises
    ------
    LibraryError
        For an unknown id or preset, an override of a component not in ``use``, or an
        override that makes the definition invalid.
    """
    if library is None:
        library = load_library(Path(project_root) / p for p in section.library_paths)
    by_id: dict[str, list[str]] = {}
    for key in sorted(library):
        by_id.setdefault(split_key(key)[0], []).append(key)
    res = Resolution(components={})
    for item in section.use:
        if "@" in item:
            cid, _ = split_key(item)
            if item not in library:
                raise LibraryError(f"unknown component {item!r}; known: {sorted(library)}")
            key = item
        else:
            cid = item
            keys = by_id.get(cid)
            if not keys:
                raise LibraryError(f"unknown component {cid!r}; known ids: {sorted(by_id)}")
            key = f"{cid}@{section.preset}"
            if key not in library:
                key = keys[0]
                res.warnings.append(
                    f"{cid}: no '{section.preset}' preset; using {key} (available: {keys})"
                )
        if cid in res.components:
            raise LibraryError(f"component {cid!r} listed twice in components.use")
        res.components[cid] = ResolvedComponent(cid, key, library[key])
    unknown = set(section.overrides) - set(res.components)
    if unknown:
        raise LibraryError(f"components.overrides for {sorted(unknown)} not in components.use")
    for cid, patch in section.overrides.items():
        rc = res.components[cid]
        merged = _deep_merge(rc.definition.model_dump(mode="python"), patch)
        try:
            definition = ComponentDef.model_validate(merged)
        except ValidationError as err:
            raise LibraryError(f"components.overrides.{cid} is invalid:\n{err}") from err
        res.components[cid] = ResolvedComponent(cid, rc.key, definition, overridden=True)
    return res
