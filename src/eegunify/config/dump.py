"""JSON dump of a config for other implementations (Phase M, docs/BLUEPRINT_PHASES.md §11).

MATLAB reads configs through this dump (``jsondecode``) instead of parsing YAML, so the merge
logic lives in one place: with ``subject`` the ``overrides["sub-<subject>"]`` patch is applied,
and with ``resolved`` the component library is resolved too (presets, overrides). The dump
carries ``config_hash``, the sha256 of the canonical JSON of ``config`` and ``components``;
MATLAB writes it back into its provenance so parity tests know both sides used the same config.

``matlab`` repeats what MATLAB needs in a form ``jsondecode`` keeps intact (it rewrites object
keys such as ``"1-40"`` into field names): mappings as lists of pairs, plus the tables the
Python pipeline takes from MNE and package data (10-05 names, aliases, BioSemi 64 order,
the ``_eeg.json`` keys inherited from a BIDS source), so
both sides normalize channel names from one source. It is derived from ``config`` and is not
part of the hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from eegunify import __version__
from eegunify.config.loader import resolve_for_subject
from eegunify.config.schema import Config
from eegunify.erp.hashing import canonical_json, normalize


def _pairs(mapping: dict[str, Any], key: str, value: str) -> list[dict[str, Any]]:
    return [{key: k, value: v} for k, v in mapping.items()]


def _matlab_tables(cfg: Config, subject: str | None) -> dict[str, Any]:
    from eegunify.channels.naming import _biosemi64_order, default_aliases, standard_1005_names
    from eegunify.pipeline import INHERITED_SIDECAR_KEYS

    ev = cfg.events
    return {
        "event_mapping": _pairs(ev.mapping, "key", "trial_type"),
        "onset_shift_ms": _pairs(ev.onset_shift_ms, "pattern", "ms"),
        "rename": _pairs(cfg.channels.rename, "from", "to"),
        "types": _pairs(cfg.channels.types, "name", "type"),
        "aliases": _pairs({**default_aliases(), **cfg.channels.aliases_extra}, "from", "to"),
        "bads": cfg.bads_for(subject) if subject is not None else [],
        "standard_1005": list(standard_1005_names()),
        "biosemi64": list(_biosemi64_order()),
        "inherited_sidecar_keys": list(INHERITED_SIDECAR_KEYS),
    }


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
        "matlab": _matlab_tables(cfg, subject),
    }
