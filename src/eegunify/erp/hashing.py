"""Stage cache keys (docs/BLUEPRINT_PHASES.md section 4.2).

``stage_key`` = sha256 of the canonical JSON of everything that determines a stage's output:
stage name and schema version, its resolved config section (including seeds and, for erp and
group, the component definitions), the upstream stage keys (or rawdata file hashes for the
first stage), and the eegunify and MNE *minor* versions. Patch releases keep caches valid;
minor releases invalidate them.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel

HASHED_PACKAGES = ("eegunify", "mne")


def normalize(obj: Any) -> Any:
    """JSON-ready, deterministic form of a config value (also used in provenance)."""
    if isinstance(obj, BaseModel):
        return normalize(obj.model_dump(mode="json"))
    if isinstance(obj, Mapping):
        return {str(k): normalize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [normalize(v) for v in obj]
    if isinstance(obj, set | frozenset):
        return sorted(normalize(v) for v in obj)
    if isinstance(obj, PurePath):
        return obj.as_posix()
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"cannot hash non-finite float {obj!r}")
        # 1.0 and 1 must hash the same: YAML authors write either.
        return int(obj) if obj.is_integer() else obj
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    raise TypeError(f"cannot hash value of type {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, tuples as lists, paths as POSIX."""
    return json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def minor_version(v: str) -> str:
    """``1.13.2`` -> ``1.13`` (``0.0.0`` for unknown)."""
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else v


def major_version(v: str) -> str:
    return v.split(".")[0]


def software_versions() -> dict[str, str]:
    """Minor versions of the packages whose upgrade must invalidate the cache."""
    out = {}
    for pkg in HASHED_PACKAGES:
        try:
            out[pkg] = minor_version(version(pkg))
        except PackageNotFoundError:  # pragma: no cover - always installed in practice
            out[pkg] = "0.0"
    return out


def stage_key(
    *,
    stage: str,
    schema_version: int,
    config: Any,
    upstream: Mapping[str, str],
    versions: Mapping[str, str] | None = None,
) -> str:
    """The cache key of one stage run (hex sha256).

    ``upstream`` maps an input name (an upstream stage, ``sub-015:erp`` for group inputs, or a
    rawdata file name) to its key or file hash.
    """
    payload = {
        "stage": stage,
        "schema_version": schema_version,
        "config": config,
        "upstream": dict(upstream),
        "versions": dict(versions if versions is not None else software_versions()),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()
