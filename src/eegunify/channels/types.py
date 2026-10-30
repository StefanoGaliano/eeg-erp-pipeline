"""Channel type inference (blueprint section 7.2)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from eegunify.channels.naming import is_standard

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)^(status|trig(ger)?|sti\d*|stim.*|marker)$"), "stim"),
    (re.compile(r"(?i)(eog|heog|veog|^lo[12]$|^io[12]$|^so[12]$)"), "eog"),
    (re.compile(r"(?i)emg"), "emg"),
    (re.compile(r"(?i)(ecg|ekg)"), "ecg"),
    (re.compile(r"(?i)^(resp|breath)"), "resp"),
    (re.compile(r"(?i)^(temp)"), "temperature"),
)
_POSITIONAL_EEG = re.compile(r"^([A-H]([1-9]|[12]\d|3[0-2])|E\d{1,3})$")


def infer_type(name: str, reader_type: str | None = None) -> str:
    """Guess the type of one channel from its (normalized) name.

    Order: name-based rules (stim/EOG/EMG/ECG/...), then 10-05 names and positional cap labels
    (BioSemi ``A1``, EGI ``E12``) -> ``eeg``, then the type the reader already assigned if it
    is specific, else ``misc``.
    """
    for pattern, ch_type in _RULES:
        if pattern.search(name):
            return ch_type
    if is_standard(name) or _POSITIONAL_EEG.match(name):
        return "eeg"
    if reader_type and reader_type not in ("eeg", "misc"):
        return reader_type
    return "misc"


def infer_types(
    names: Iterable[str],
    *,
    configured: Mapping[str, str] | None = None,
    reader_types: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Types for all channels; ``configured`` (from ``channels.types``) always wins."""
    configured = configured or {}
    reader_types = reader_types or {}
    return {n: configured.get(n) or infer_type(n, reader_types.get(n)) for n in names}
