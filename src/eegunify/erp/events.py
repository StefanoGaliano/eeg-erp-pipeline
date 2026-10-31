"""Event selection by trial-type prefix (finding F1, docs/BLUEPRINT_PHASES.md section 2).

``read_raw_bids`` labels an event ``trial_type/value`` (``stimulus/face/12``) when a trial type
has several codes, and plain ``stimulus/face`` when it has one. Configs therefore never name
full labels, only trial-type prefixes; this module is the only place that matches them, using
MNE's tag semantics (a selector matches every label whose ``/``-separated parts include all of
the selector's parts).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def _parts(label: str) -> set[str]:
    return set(label.split("/"))


def matches(label: str, selector: str) -> bool:
    """MNE tag matching for one label; ``*`` as the last part matches anything below it.

    ``stimulus/*`` and ``stimulus`` both select ``stimulus/face/12``; ``stimulus/face`` does
    not select ``stimulus/scrambled_face/101`` (tags are whole parts, not substrings).
    """
    parts = [p for p in selector.split("/") if p and p != "*"]
    if selector.strip() in ("", "*"):
        return True
    return _parts(label) >= set(parts)


def select(event_id: Mapping[str, int], selector: str | Iterable[str]) -> dict[str, int]:
    """The subset of an MNE ``event_id`` mapping matched by one or several selectors.

    Raises
    ------
    KeyError
        If a selector matches nothing (a typo must not silently give an empty condition).
    """
    selectors = [selector] if isinstance(selector, str) else list(selector)
    out: dict[str, int] = {}
    for sel in selectors:
        hit = {k: v for k, v in event_id.items() if matches(k, sel)}
        if not hit:
            raise KeyError(f"selector {sel!r} matches no event; labels: {sorted(event_id)}")
        out.update(hit)
    return out
