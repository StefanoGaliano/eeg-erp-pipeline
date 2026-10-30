"""Map raw event codes to hierarchical condition names (``stimulus/face``)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_WS_RE = re.compile(r"\s+")


class UnmappedEventError(ValueError):
    """An event code has no mapping and ``events.unmapped`` is ``error``."""


def _collapse(s: str) -> str:
    return _WS_RE.sub(" ", s.strip())


@dataclass(frozen=True)
class EventMapper:
    """Compiled form of the ``events.mapping`` config block.

    Keys may be an integer (``"5"``), an inclusive range (``"1-40"``) or a string label
    (``"Stimulus/S  1"``; whitespace is collapsed before matching).
    """

    ranges: tuple[tuple[int, int, str], ...]
    labels: Mapping[str, str]

    @classmethod
    def from_config(cls, mapping: Mapping[str, str]) -> EventMapper:
        ranges: list[tuple[int, int, str]] = []
        labels: dict[str, str] = {}
        for key, trial_type in mapping.items():
            m = _RANGE_RE.match(key)
            if m:
                ranges.append((int(m.group(1)), int(m.group(2)), trial_type))
            elif key.strip().isdigit():
                n = int(key)
                ranges.append((n, n, trial_type))
            else:
                labels[_collapse(key)] = trial_type
        return cls(tuple(sorted(ranges)), labels)

    def lookup(self, code: int | str) -> str | None:
        """Condition name for ``code``, or ``None`` if unmapped."""
        if isinstance(code, str):
            hit = self.labels.get(_collapse(code))
            if hit is not None:
                return hit
            parsed = parse_code(code)
            if isinstance(parsed, str):
                return None
            code = parsed
        for lo, hi, trial_type in self.ranges:
            if lo <= code <= hi:
                return trial_type
        return None


_BV_RE = re.compile(r"^(?:Stimulus|Response)/[SR]\s*(\d+)$")


def parse_code(description: str) -> int | str:
    """Turn an annotation description into an integer code when it encodes one.

    Handles plain integers (``"12"``), BrainVision markers (``"Stimulus/S  1"``,
    ``"Response/R 12"``) and EEGLAB-style ``"condition 5"``; otherwise returns the stripped
    string.
    """
    d = description.strip()
    if d.lstrip("-").isdigit():
        return int(d)
    m = _BV_RE.match(d)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(?i)condition\s+(\d+)", d)
    if m:
        return int(m.group(1))
    return d
