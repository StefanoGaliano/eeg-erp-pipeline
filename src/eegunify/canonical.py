"""The canonical in-memory recording and its invariants (blueprint section 5).

Every reader output goes through :mod:`eegunify.pipeline` into a :class:`Recording`; nothing is
written unless :func:`validate` reports no errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import mne
import numpy as np

from eegunify.channels.naming import is_standard
from eegunify.events.behavior import AlignmentReport
from eegunify.events.extract import EventResult
from eegunify.units import UnitVerdict

ALLOWED_TYPES = frozenset({"eeg", "eog", "emg", "ecg", "misc", "resp", "temperature"})
_FACTOR_TO_DECLARED = {1e-6: "uV", 1e-3: "mV"}
_POSITIONAL_EEG = re.compile(r"^([A-H]\d{1,3}|E\d{1,3})$")


@dataclass
class Recording:
    """A recording in canonical form plus everything needed to write and audit it."""

    raw: mne.io.BaseRaw
    subject: str
    format: str
    events: EventResult
    reference: str
    ground: str | None
    units: UnitVerdict
    rename_log: dict[str, str]
    missing_positions: list[str]
    provenance: dict[str, Any] = field(default_factory=dict)
    sidecar: dict[str, Any] = field(default_factory=dict)
    behavior: AlignmentReport | None = None
    """Values for ``_eeg.json`` that MNE-BIDS cannot know (hardware, task description, ...)."""
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Violation:
    rule: int
    level: Literal["error", "warning"]
    message: str


class CanonicalError(ValueError):
    """A recording breaks one or more canonical invariants."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        lines = [f"  [rule {v.rule}] {v.message}" for v in violations]
        super().__init__("recording is not canonical:\n" + "\n".join(lines))


UNIT_ERROR_RATIO = 10.0


def _far_outside(value: float, rng: tuple[float, float]) -> bool:
    lo, hi = rng
    return bool(value > hi * UNIT_ERROR_RATIO or value < lo / UNIT_ERROR_RATIO)


def check(
    rec: Recording,
    *,
    montage_expected: bool = True,
    expected_range_uV: tuple[float, float] = (0.5, 500.0),
) -> list[Violation]:
    """Evaluate all invariants and return every violation (errors and warnings)."""
    raw = rec.raw
    rng = expected_range_uV
    out: list[Violation] = []

    # 1. Units: Volts in memory, plausible amplitude.
    picks = mne.pick_types(
        raw.info,
        eeg=True,
        eog=True,
        emg=True,
        ecg=True,
        misc=True,
        resp=True,
        temperature=True,
        exclude=[],
    )
    if picks.size and not np.isfinite(raw.get_data(picks=picks)).all():
        out.append(Violation(1, "error", "data contain NaN or Inf"))
    if not rec.units.plausible:
        # Refuse only unmistakable scale errors: a 10^3/10^6 factor fixes it AND the amplitude
        # is more than 10x outside the range. Infant or unreferenced data sit legitimately just
        # above the range (ds005106, ds007096) and get a warning instead.
        level: Literal["error", "warning"] = (
            "error"
            if rec.units.suggested_factor != 1.0 and _far_outside(rec.units.robust_sd_uV, rng)
            else "warning"
        )
        hint = ""
        if rec.units.suggested_factor in _FACTOR_TO_DECLARED:
            hint = (
                f" (if the header is wrong, set units.declared: "
                f"{_FACTOR_TO_DECLARED[rec.units.suggested_factor]})"
            )
        out.append(Violation(1, level, f"implausible EEG amplitude: {rec.units.explanation}{hint}"))

    # 2. Channel names normalized.
    for i in mne.pick_types(raw.info, eeg=True, exclude=[]):
        name = raw.ch_names[i]
        if not (is_standard(name) or _POSITIONAL_EEG.match(name)):
            out.append(Violation(2, "warning", f"EEG channel {name!r} is not a 10-05 name"))

    # 3. Every channel typed, no stim channels left.
    for name, ch_type in zip(raw.ch_names, raw.get_channel_types(), strict=True):
        if ch_type not in ALLOWED_TYPES:
            out.append(Violation(3, "error", f"channel {name!r} has type {ch_type!r}"))
    if not mne.pick_types(raw.info, eeg=True, exclude=[]).size:
        out.append(Violation(3, "error", "no EEG channels"))

    # 4. Events: annotations carry the original code.
    extras = raw.annotations.extras
    if len(raw.annotations) != len(rec.events.table):
        out.append(Violation(4, "error", "annotations and event table disagree in length"))
    if any("code" not in (e or {}) for e in extras):
        out.append(Violation(4, "error", "annotation without original event code"))
    if len(rec.events.table) == 0:
        out.append(Violation(4, "warning", "recording has no events"))

    # 5. Online reference recorded.
    if not rec.reference or rec.reference == "n/a":
        out.append(Violation(5, "warning", "online reference unknown (set reference.online)"))

    # 6. Montage.
    if montage_expected and rec.missing_positions:
        out.append(
            Violation(6, "warning", f"EEG channels without positions: {rec.missing_positions}")
        )

    # 7. Bad channels exist.
    for bad in raw.info["bads"]:
        if bad not in raw.ch_names:
            out.append(Violation(7, "error", f"bad channel {bad!r} is not in the recording"))

    # 8. Measurement date is handled at write time (anonymization) - nothing to check here.

    # 9. Provenance attached.
    if not rec.provenance.get("source_files"):
        out.append(Violation(9, "error", "provenance has no source files"))

    # 10. No resampling/filtering: sfreq must equal the file's (recorded in provenance).
    sf = rec.provenance.get("source_sfreq")
    if sf is not None and not np.isclose(sf, raw.info["sfreq"]):
        out.append(Violation(10, "error", "sampling rate changed during ingestion"))
    return out


def validate(
    rec: Recording,
    *,
    montage_expected: bool = True,
    expected_range_uV: tuple[float, float] = (0.5, 500.0),
) -> list[Violation]:
    """Raise :class:`CanonicalError` on any error-level violation; return the warnings."""
    violations = check(rec, montage_expected=montage_expected, expected_range_uV=expected_range_uV)
    errors = [v for v in violations if v.level == "error"]
    if errors:
        raise CanonicalError(errors)
    return violations
