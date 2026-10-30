"""Channel name normalization (blueprint section 7.1).

The pipeline is applied in this order, and every rename is logged:

1. positional cap layouts (BioSemi 64 ``A1..B32``) are mapped to 10-20 names;
2. vendor prefixes/suffixes are stripped (``EEG Fp1-Ref`` -> ``Fp1``);
3. names are case-normalized against the standard 10-05 set (``FP1`` -> ``Fp1``);
4. old 10-20 aliases are applied (``T3`` -> ``T7``), plus per-config extras;
5. explicit ``channels.rename`` entries from the config are applied last.

Two channels ending up with the same name is a hard error.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable, Mapping
from functools import cache
from importlib.resources import files

import mne
import yaml

_PREFIX_RE = re.compile(r"^(EEG|EOG|EMG|ECG|EKG)[\s_:-]+", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"[-_](REF|A1|A2|M1|M2|LE|AVG|CZ|AR)$", re.IGNORECASE)
_BIOSEMI_POS_RE = re.compile(r"^[A-H]([1-9]|[12]\d|3[0-2])$")


class ChannelNameCollisionError(ValueError):
    """Two source channels normalize to the same name."""


@cache
def standard_1005_names() -> tuple[str, ...]:
    """Names of the 10-05 system as shipped with MNE (canonical case)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        for name in ("colin27_1005", "standard_1005"):
            try:
                return tuple(mne.channels.make_standard_montage(name).ch_names)
            except ValueError:
                continue
    raise RuntimeError("MNE ships neither 'colin27_1005' nor 'standard_1005'")


@cache
def _standard_lookup() -> dict[str, str]:
    return {n.lower(): n for n in standard_1005_names()}


@cache
def default_aliases() -> dict[str, str]:
    text = files("eegunify.channels").joinpath("aliases.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return {str(k): str(v) for k, v in data.items()}


@cache
def _biosemi64_order() -> tuple[str, ...]:
    return tuple(mne.channels.make_standard_montage("biosemi64").ch_names)


def is_standard(name: str) -> bool:
    """True if ``name`` is a 10-05 name in canonical case."""
    return _standard_lookup().get(name.lower()) == name


def biosemi_positional_map(names: Iterable[str]) -> dict[str, str]:
    """Map BioSemi 64-channel positional labels (``A1..B32``) to 10-20 names.

    Only applies when exactly the 64 labels ``A1..A32, B1..B32`` are present (in any order);
    otherwise returns an empty mapping, because 128+ channel caps have no 10-20 equivalent
    and keep their positional names (with the ``biosemi128``/``biosemi256`` montages).
    """
    expected = [f"{b}{i}" for b in "AB" for i in range(1, 33)]
    present = [n for n in names if _BIOSEMI_POS_RE.match(n)]
    if sorted(present) != sorted(expected):
        return {}
    return dict(zip(expected, _biosemi64_order(), strict=True))


def normalize_one(name: str, aliases: Mapping[str, str]) -> str:
    """Normalize a single channel name (steps 2-4)."""
    new = name.strip()
    new = _PREFIX_RE.sub("", new)
    new = _SUFFIX_RE.sub("", new)
    new = new.strip()
    new = _standard_lookup().get(new.lower(), new)
    if new in aliases:
        new = aliases[new]
    else:
        alias_ci = {k.lower(): v for k, v in aliases.items()}
        new = alias_ci.get(new.lower(), new)
    return new


def normalize_names(
    names: Iterable[str],
    *,
    aliases_extra: Mapping[str, str] | None = None,
    rename: Mapping[str, str] | None = None,
    protect: Iterable[str] = (),
) -> dict[str, str]:
    """Compute the full rename map ``original -> canonical`` for a recording.

    Parameters
    ----------
    names
        Channel names as read from the file.
    aliases_extra
        Additional aliases from the config (applied with the defaults).
    rename
        Explicit renames from the config, keyed by the *original* or the normalized name.
    protect
        Names left untouched (e.g. stim channels such as ``Status``).

    Returns
    -------
    dict
        Mapping for every channel (unchanged names map to themselves).

    Raises
    ------
    ChannelNameCollisionError
        If two channels end up with the same name.
    """
    names = list(names)
    protected = set(protect)
    aliases = {**default_aliases(), **(aliases_extra or {})}
    rename = dict(rename or {})
    positional = biosemi_positional_map(n for n in names if n not in protected)

    result: dict[str, str] = {}
    for orig in names:
        if orig in protected:
            new = orig
        elif orig in positional:
            new = positional[orig]
        else:
            new = normalize_one(orig, aliases)
        new = rename.get(orig, rename.get(new, new))
        result[orig] = new

    seen: dict[str, str] = {}
    for orig, new in result.items():
        if new in seen:
            raise ChannelNameCollisionError(
                f"channels {seen[new]!r} and {orig!r} both normalize to {new!r}; "
                "add a channels.rename entry to disambiguate"
            )
        seen[new] = orig
    return result
