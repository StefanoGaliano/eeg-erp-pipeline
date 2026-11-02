"""Component-level reference (docs/BLUEPRINT_PHASES.md section 12).

A component definition may name the reference its window and sites were defined for
(``reference: Fz`` for the ISCEV VEP, ``P9/P10`` for ERP CORE's P3, ``average``). When it
differs from the dataset's offline reference (``reference.scheme``/``channels``), the erp stage
measures that component on re-referenced *copies* of the averages and epochs. Re-referencing
is linear, so doing it after averaging gives the same waves as doing it before; the dataset's
own outputs (``_ave.fif``, other components) keep the dataset reference.

Only EEG channels are re-referenced (as in preproc). A reference channel that is bad, or not
in the data, is an error, except when the data are not re-referenced offline
(``scheme: none``) and the component reference is the online one (``reference.online``): the
data are then already referenced to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

import mne

from eegunify.config.schema import Config
from eegunify.erp.averaging import ErpError

Inst = TypeVar("Inst", mne.Evoked, mne.BaseEpochs)


class ComponentReferenceError(ErpError):
    """A component reference that cannot be applied to the data."""


@dataclass(frozen=True)
class Reference:
    """``average`` or a list of channels (their mean is the reference)."""

    kind: str  # "average" | "channels"
    channels: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "average" if self.kind == "average" else "/".join(self.channels)

    def key(self) -> str:
        """Comparable form: case-insensitive, channel order irrelevant."""
        if self.kind == "average":
            return "average"
        return "/".join(sorted(c.lower() for c in self.channels))


def parse(text: str) -> Reference:
    """``average``, ``Fz`` or ``P9/P10`` (``+`` and ``,`` also separate channels)."""
    t = text.strip()
    if t.lower() in ("average", "avg", "car"):
        return Reference("average")
    chans = tuple(c.strip() for c in t.replace("+", "/").replace(",", "/").split("/") if c.strip())
    if not chans:
        raise ComponentReferenceError(f"empty component reference {text!r}")
    return Reference("channels", chans)


def dataset_reference(cfg: Config) -> Reference | None:
    """The data's reference after preproc, or None when it is only known as free text."""
    ref = cfg.reference
    if ref.scheme == "average":
        return Reference("average")
    if ref.scheme == "channels":
        return Reference("channels", tuple(ref.channels))
    if ref.scheme == "none" and ref.online and ref.online != "n/a":
        try:
            return parse(ref.online)
        except ComponentReferenceError:
            return None
    return None


def needed(component_reference: str | None, cfg: Config) -> Reference | None:
    """The reference to apply for a component, or None when the data already have it."""
    if component_reference is None:
        return None
    target = parse(component_reference)
    current = dataset_reference(cfg)
    if current is not None and current.key() == target.key():
        return None
    return target


def apply(inst: Inst, target: Reference, cfg: Config) -> Inst:
    """A re-referenced copy of ``inst`` (EEG channels only)."""
    if target.kind == "average":
        out: Inst = inst.copy().set_eeg_reference(
            "average", projection=False, ch_type="eeg", verbose=False
        )
        return out
    lower = {c.lower(): c for c in inst.ch_names}
    missing = [c for c in target.channels if c.lower() not in lower]
    if missing:
        online = dataset_reference(cfg) if cfg.reference.scheme == "none" else None
        if online is not None and online.key() == target.key():
            return inst.copy()  # recorded against it and never re-referenced
        raise ComponentReferenceError(
            f"component reference {target.label}: channels {missing} not in the data"
        )
    names = [lower[c.lower()] for c in target.channels]
    bad = [c for c in names if c in inst.info["bads"]]
    if bad:
        raise ComponentReferenceError(
            f"component reference {target.label}: channels {bad} are bad (interpolate them, "
            "or override the component's reference)"
        )
    out = inst.copy().set_eeg_reference(names, projection=False, ch_type="eeg", verbose=False)
    return out
