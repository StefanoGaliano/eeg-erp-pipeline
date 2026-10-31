"""Per-stage QA for the ERP phases (docs/BLUEPRINT_PHASES.md section 7).

Check IDs are API, as in ``qa/checks.py``: never rename one. Levels are ``pass``/``warn``/
``fail``; informational checks are ``pass`` with a message. A ``fail`` makes the stage fail for
that subject (its outputs are still written, for inspection), which excludes the subject from
the group stage.

===========================  ======  ============================================================
ID                           Stage   Rule
===========================  ======  ============================================================
PREPROC_LINE_NOISE_RESIDUAL  preproc warn if the line peak is > 10 dB above its neighbours
PREPROC_BADS_FRACTION        preproc warn > 10 %, fail > 25 % of EEG channels bad
PREPROC_ROI_INTERPOLATED     preproc warn if a primary ROI channel of a used component was
                                     interpolated
REREF_REQUIRED               preproc fail: reference-free recording with ``scheme: none``
REREF_APPLIED                preproc info: the scheme and channels used
ICA_EXCLUDED_COUNT           ica     (M-E2)
ICA_BLINK_RESIDUAL           ica     (M-E2)
ICA_BRAIN_REMOVED            ica     (M-E2)
EPOCHS_REJECTED_FRACTION     epochs  (M-E3)
EPOCHS_MIN_TRIALS            epochs  (M-E3)
EPOCHS_BASELINE_DRIFT        epochs  (M-E3)
ERP_NOISE_SME                erp     (M-E4)
ERP_COMPONENT_POLARITY       erp     (M-E4)
MEASURE_NO_LOCAL_PEAK        erp     (M-E4)
VEP_LATENCY_ATYPICAL         erp     (VEP)
GROUP_SUBJECTS_EXCLUDED      group   (M-E6a)
GROUP_UNDERPOWERED           group   (M-E6a)
REFERENCE_VALUES_MISMATCH    group   (M-E6a)
===========================  ======  ============================================================
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import mne
import numpy as np

from eegunify.erp.stages.base import write_text_atomic
from eegunify.qa.checks import _ORDER, Level, QACheck

LINE_RESIDUAL_DB = 10.0
BADS_WARN, BADS_FAIL = 0.10, 0.25
QA_SECONDS = 300.0
"""Spectral QA uses at most this much data (as Phase 1 QA)."""


def worst(checks: Sequence[QACheck]) -> Level:
    return max((c.level for c in checks), key=_ORDER.__getitem__, default="pass")


def line_peak_db(raw: mne.io.BaseRaw, freq: float) -> float:
    """Median over EEG channels of the power at ``freq`` above its 2-4 Hz neighbours, in dB."""
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    if not len(picks) or freq >= raw.info["sfreq"] / 2 - 4:
        return float("nan")
    tmax = min(raw.times[-1], QA_SECONDS)
    spec = raw.compute_psd(
        method="welch",
        picks=picks,
        fmin=freq - 5,
        fmax=freq + 5,
        tmax=tmax,
        n_fft=_nfft(raw),
        verbose=False,
    )
    psd, freqs = spec.get_data(return_freqs=True)
    peak = psd[:, np.argmin(np.abs(freqs - freq))]
    side = (np.abs(freqs - freq) >= 2) & (np.abs(freqs - freq) <= 4)
    ratio = peak / np.maximum(psd[:, side].mean(axis=1), np.finfo(float).tiny)
    return float(np.median(10 * np.log10(np.maximum(ratio, np.finfo(float).tiny))))


def _nfft(raw: mne.io.BaseRaw) -> int:
    # 0.25 Hz resolution, but never longer than the data.
    return int(min(4 * raw.info["sfreq"], raw.n_times))


def line_noise_check(raw: mne.io.BaseRaw, line_freq: float | None) -> QACheck:
    if not line_freq:
        return QACheck("PREPROC_LINE_NOISE_RESIDUAL", "pass", "no line frequency known")
    db = line_peak_db(raw, line_freq)
    if not np.isfinite(db):
        return QACheck("PREPROC_LINE_NOISE_RESIDUAL", "pass", f"{line_freq:g} Hz not measurable")
    level: Level = "warn" if db > LINE_RESIDUAL_DB else "pass"
    return QACheck(
        "PREPROC_LINE_NOISE_RESIDUAL",
        level,
        f"{line_freq:g} Hz peak {db:.1f} dB above neighbours after cleaning",
        round(db, 2),
        LINE_RESIDUAL_DB,
    )


def bads_fraction_check(bads: Sequence[str], n_eeg: int) -> QACheck:
    frac = len(bads) / n_eeg if n_eeg else 0.0
    level: Level = "fail" if frac > BADS_FAIL else "warn" if frac > BADS_WARN else "pass"
    names = ", ".join(bads) if bads else "none"
    return QACheck(
        "PREPROC_BADS_FRACTION",
        level,
        f"{len(bads)}/{n_eeg} EEG channels bad ({100 * frac:.0f} %): {names}",
        round(frac, 4),
        [BADS_WARN, BADS_FAIL],
    )


def roi_interpolated_check(
    interpolated: Sequence[str], roi_channels: dict[str, list[str]]
) -> QACheck:
    """``roi_channels``: component id -> its primary ROI channels."""
    hits = {cid: sorted(set(chs) & set(interpolated)) for cid, chs in sorted(roi_channels.items())}
    hits = {cid: chs for cid, chs in hits.items() if chs}
    if not hits:
        return QACheck("PREPROC_ROI_INTERPOLATED", "pass", "no primary ROI channel interpolated")
    detail = "; ".join(f"{cid}: {', '.join(chs)}" for cid, chs in hits.items())
    return QACheck(
        "PREPROC_ROI_INTERPOLATED",
        "warn",
        f"primary ROI channels were interpolated ({detail}); ERP CORE excludes such channels",
        hits,
    )


def write_qa_json(path: Path, stage: str, checks: Sequence[QACheck], extra: dict[str, Any]) -> None:
    data = {
        "stage": stage,
        "status": worst(checks).upper(),
        "checks": [asdict(c) for c in checks],
        **extra,
    }
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
