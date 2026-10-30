"""QA checks with stable IDs.

Check IDs are API: tests and ``_qa.json`` consumers rely on them, so never rename one.

========================  ======================================================
ID                        Meaning
========================  ======================================================
UNITS_RANGE               median robust SD of EEG (1 Hz high-passed) in range
CHANNEL_FLAT              EEG channels with robust SD < 0.5 µV
CHANNEL_NOISY             EEG channels with robust SD > 200 µV
CHANNEL_CLIPPED           channels with > 0.1 % of samples stuck at their extremes
CHANNEL_NO_POSITION       EEG channels without electrode positions
ROI_MISSING               visual ROI channels absent or marked bad
REFERENCE_UNKNOWN         online reference not recorded
EVENTS_NONE               recording has no events
EVENTS_UNMAPPED           event codes without a mapping
EVENTS_EXPECTED_COUNT     per-condition counts vs ``events.expected_counts``
LINE_NOISE                detected power-line frequency (informational)
========================  ======================================================
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import mne
import numpy as np
import pandas as pd

from eegunify.canonical import Recording
from eegunify.config.schema import Config
from eegunify.rois import missing_visual_channels

Level = Literal["pass", "warn", "fail"]
_ORDER = {"pass": 0, "warn": 1, "fail": 2}

FLAT_UV = 0.5
NOISY_UV = 200.0
CLIP_FRACTION = 0.001
UNITS_FAIL_RATIO = 10.0
MAX_SECONDS = 300.0  # QA statistics use at most this much data, for speed


@dataclass(frozen=True)
class QACheck:
    id: str
    level: Level
    message: str
    value: Any = None
    threshold: Any = None


@dataclass
class QAResult:
    checks: list[QACheck]
    channels: pd.DataFrame
    """One row per channel: original/canonical name, type, unit, position, status, SD, flags."""
    line_freq: float | None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Level:
        return max((c.level for c in self.checks), key=_ORDER.__getitem__, default="pass")

    def by_id(self, check_id: str) -> QACheck:
        return next(c for c in self.checks if c.id == check_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status.upper(),
            "checks": [asdict(c) for c in self.checks],
            "line_freq": self.line_freq,
            **self.extra,
        }


def _robust_sd_uv(data: np.ndarray) -> np.ndarray:
    centred = data - np.median(data, axis=1, keepdims=True)
    return np.asarray(np.median(np.abs(centred), axis=1) * 1.4826 * 1e6)


def _clipped_fraction(data: np.ndarray) -> np.ndarray:
    """Fraction of samples *stuck* at the channel's own min or max.

    Saturation shows up as runs of identical samples at an extreme. Merely touching the
    extreme (every peak of a clean sine) does not count, so a sample only counts when it equals
    its predecessor.
    """
    lo = data.min(axis=1, keepdims=True)
    hi = data.max(axis=1, keepdims=True)
    at_rail = (data == lo) | (data == hi)
    repeated = np.zeros_like(at_rail)
    repeated[:, 1:] = data[:, 1:] == data[:, :-1]
    frac = (at_rail & repeated).mean(axis=1)
    # A channel where min == max is flat, not clipped.
    return np.asarray(np.where((hi - lo).ravel() == 0, 0.0, frac))


def detect_line_freq(raw: mne.io.BaseRaw) -> float | None:
    """50 or 60 Hz, whichever stands out more above its neighbourhood; None if neither does."""
    nyq = raw.info["sfreq"] / 2
    if nyq <= 65:
        return None
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    if not len(picks):
        return None
    tmax = min(raw.times[-1], MAX_SECONDS)
    psd = raw.compute_psd(fmin=40, fmax=70, tmax=tmax, picks=picks, verbose="error")
    freqs, power = psd.freqs, psd.get_data().mean(axis=0)

    def prominence(f0: float) -> float:
        peak = power[np.abs(freqs - f0) <= 1].max()
        base = np.median(power[(np.abs(freqs - f0) > 2) & (np.abs(freqs - f0) <= 6)])
        return float(peak / base) if base > 0 else 0.0

    scores = {50.0: prominence(50.0), 60.0: prominence(60.0)}
    best = max(scores, key=lambda f: scores[f])
    return best if scores[best] > 3.0 else None


def channel_table(rec: Recording) -> pd.DataFrame:
    raw = rec.raw
    stop = min(raw.n_times, int(MAX_SECONDS * raw.info["sfreq"]))
    hp = raw.copy().crop(tmax=(stop - 1) / raw.info["sfreq"])
    data_picks = mne.pick_types(
        hp.info, meg=False, eeg=True, eog=True, emg=True, ecg=True, misc=True, exclude=[]
    )
    hp.filter(1.0, None, picks=data_picks, verbose="error")
    data = hp.get_data()
    sd = _robust_sd_uv(data)
    clipped = _clipped_fraction(raw.get_data(stop=stop))
    new_to_orig = {new: orig for orig, new in rec.rename_log.items()}
    rows = []
    for i, ch in enumerate(raw.info["chs"]):
        name = ch["ch_name"]
        loc = ch["loc"][:3]
        rows.append(
            {
                "name": name,
                "original_name": new_to_orig.get(name, name),
                "type": raw.get_channel_types(picks=[i])[0],
                "unit": "µV" if ch["unit"] == mne.io.constants.FIFF.FIFF_UNIT_V else "n/a",
                "position": bool(np.isfinite(loc).all() and loc.any()),
                "status": "bad" if name in raw.info["bads"] else "good",
                "robust_sd_uV": round(float(sd[i]), 3),
                "flat": bool(sd[i] < FLAT_UV),
                "clipped": bool(clipped[i] > CLIP_FRACTION),
            }
        )
    return pd.DataFrame(rows)


def _units_check(table: pd.DataFrame, cfg: Config) -> QACheck:
    lo, hi = cfg.units.expected_eeg_range_uV
    eeg = table[(table["type"] == "eeg") & (table["status"] == "good") & ~table["flat"]]
    if eeg.empty:
        return QACheck("UNITS_RANGE", "fail", "no usable EEG channels", None, [lo, hi])
    med = float(eeg["robust_sd_uV"].median())
    if lo <= med <= hi:
        return QACheck("UNITS_RANGE", "pass", f"median robust SD {med:.3g} µV", med, [lo, hi])
    ratio = max(lo / med, med / hi)
    # How many powers of ten (rounded to unit steps of 10^3) from the middle of the range?
    steps = round(np.log10(med / np.sqrt(lo * hi)) / 3) * 3
    diagnosis = {
        6: " (~1e6x: µV labelled as V?)",
        3: " (~1e3x: mV labelled as V?)",
        -3: " (~1e-3x: V labelled as mV?)",
        -6: " (~1e-6x: V labelled as µV?)",
    }.get(steps, "")
    level: Level = "fail" if ratio > UNITS_FAIL_RATIO else "warn"
    return QACheck(
        "UNITS_RANGE",
        level,
        f"median robust SD {med:.3g} µV outside [{lo}, {hi}]{diagnosis}",
        med,
        [lo, hi],
    )


def _list_check(check_id: str, names: list[str], what: str, level: Level = "warn") -> QACheck:
    if not names:
        return QACheck(check_id, "pass", f"no {what}", [], None)
    return QACheck(check_id, level, f"{len(names)} {what}: {', '.join(names)}", names, None)


def _event_checks(rec: Recording, cfg: Config) -> list[QACheck]:
    table = rec.events.table
    out = []
    if table.empty:
        out.append(QACheck("EVENTS_NONE", "warn", "recording has no events", 0, None))
    else:
        out.append(QACheck("EVENTS_NONE", "pass", f"{len(table)} events", len(table), None))
    unmapped = [str(c) for c in rec.events.unmapped_codes]
    if cfg.events.mapping:
        out.append(_list_check("EVENTS_UNMAPPED", unmapped, "unmapped event codes"))
    expected = cfg.events.expected_counts
    if expected:
        counts = table["trial_type"].value_counts().to_dict() if not table.empty else {}
        tol = cfg.events.count_tolerance
        bad = {}
        for tt, n_exp in expected.items():
            n = int(counts.get(tt, 0))
            if abs(n - n_exp) > tol * n_exp:
                bad[tt] = {"expected": n_exp, "found": n}
        if bad:
            msg = "; ".join(f"{k}: {v['found']} (expected {v['expected']})" for k, v in bad.items())
            out.append(QACheck("EVENTS_EXPECTED_COUNT", "fail", msg, bad, tol))
        else:
            out.append(
                QACheck("EVENTS_EXPECTED_COUNT", "pass", "all counts within tolerance", None, tol)
            )
    return out


def run_checks(rec: Recording, cfg: Config) -> QAResult:
    """Run every QA check on a canonical recording."""
    raw = rec.raw
    table = channel_table(rec)
    eeg = table[table["type"] == "eeg"]
    checks = [_units_check(table, cfg)]
    checks.append(
        _list_check("CHANNEL_FLAT", eeg.loc[eeg["flat"], "name"].tolist(), "flat EEG channels")
    )
    checks.append(
        _list_check(
            "CHANNEL_NOISY",
            eeg.loc[eeg["robust_sd_uV"] > NOISY_UV, "name"].tolist(),
            "noisy EEG channels (> 200 µV)",
        )
    )
    checks.append(
        _list_check(
            "CHANNEL_CLIPPED", table.loc[table["clipped"], "name"].tolist(), "clipped channels"
        )
    )
    if cfg.montage.source != "none":
        checks.append(
            _list_check(
                "CHANNEL_NO_POSITION", list(rec.missing_positions), "EEG channels without positions"
            )
        )
    roi = missing_visual_channels(raw.ch_names, raw.info["bads"])
    if roi:
        msg = "; ".join(f"{k}: {', '.join(v)}" for k, v in roi.items())
        checks.append(
            QACheck("ROI_MISSING", "warn", f"visual ROI channels missing/bad: {msg}", roi, None)
        )
    else:
        checks.append(QACheck("ROI_MISSING", "pass", "all visual ROI channels present", {}, None))
    known = bool(rec.reference) and rec.reference != "n/a"
    checks.append(
        QACheck(
            "REFERENCE_UNKNOWN",
            "pass" if known else "warn",
            f"online reference: {rec.reference}",
            rec.reference,
            None,
        )
    )
    checks.extend(_event_checks(rec, cfg))
    line = detect_line_freq(raw)
    checks.append(
        QACheck(
            "LINE_NOISE",
            "pass",
            f"line noise at {line:g} Hz" if line else "no clear line-noise peak",
            line,
            None,
        )
    )
    counts = (
        rec.events.table["trial_type"].value_counts().to_dict()
        if not rec.events.table.empty
        else {}
    )
    return QAResult(
        checks=checks,
        channels=table,
        line_freq=line,
        extra={
            "sfreq": raw.info["sfreq"],
            "duration_s": round(float(raw.times[-1]), 3),
            "n_channels": {
                str(k): int(v) for k, v in pd.Series(raw.get_channel_types()).value_counts().items()
            },
            "event_counts": {str(k): int(v) for k, v in counts.items()},
            "reference": rec.reference,
            "format": rec.format,
            "versions": rec.provenance.get("versions", {}),
        },
    )
