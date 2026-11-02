"""JSON dump of a config for other implementations (Phase M, docs/BLUEPRINT_PHASES.md §11).

MATLAB reads configs through this dump (``jsondecode``) instead of parsing YAML, so the merge
logic lives in one place: with ``subject`` the ``overrides["sub-<subject>"]`` patch is applied,
and with ``resolved`` the component library is resolved too (presets, overrides). The dump
carries ``config_hash``, the sha256 of the canonical JSON of ``config`` and ``components``;
MATLAB writes it back into its provenance so parity tests know both sides used the same config.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from eegunify import __version__
from eegunify.config.loader import resolve_for_subject
from eegunify.config.schema import Config
from eegunify.erp.hashing import canonical_json, normalize

DUMP_VERSION = 1


def dump_config(
    cfg: Config,
    *,
    subject: str | None = None,
    resolved: bool = False,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    """The JSON-ready dump of ``cfg``.

    Parameters
    ----------
    cfg
        The validated config.
    subject
        Subject label without ``sub-``: apply that subject's overrides.
    resolved
        Also resolve the ``components`` section against the component library.
    project_root
        Root for ``components.library_paths``.

    Returns
    -------
    dict
        ``dump_version``, ``eegunify_version``, ``subject``, ``config`` (all fields, defaults
        included), ``components`` (``{}`` unless ``resolved``) and ``config_hash``.
    """
    if subject is not None:
        cfg = resolve_for_subject(cfg, subject)
    components: dict[str, Any] = {}
    if resolved and cfg.components.use:
        from eegunify.erp.components.library import resolve

        components = resolve(cfg.components, project_root=project_root).to_json()
    body = {"config": normalize(cfg), "components": normalize(components)}
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return {
        "dump_version": DUMP_VERSION,
        "eegunify_version": __version__,
        "subject": subject,
        **body,
        "config_hash": digest,
    }
