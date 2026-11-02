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
ICA_EXCLUDED_COUNT           ica     warn if 0 excluded while an EOG channel is present, or
                                     > 25 % of the components excluded
ICA_BLINK_RESIDUAL           ica     warn if |VEOG-Fp1 correlation| after cleaning is > 0.3
                                     (1 Hz high-passed copy)
ICA_BRAIN_REMOVED            ica     warn if an excluded component has ICLabel brain prob > 0.5
EPOCHS_REJECTED_FRACTION     epochs  warn > 25 %, fail > 50 % of all epochs rejected
EPOCHS_MIN_TRIALS            epochs  fail if a condition used by a component (or, with no
                                     components, any condition present in a set) has fewer
                                     kept epochs than ``min_trials_per_condition``
EPOCHS_BASELINE_DRIFT        epochs  warn if the mean |baseline| of a condition ERP > 2 µV
ERP_NOISE_SME                erp     warn if the SME of a primary mean amplitude is > 2 µV
ERP_COMPONENT_POLARITY       erp     warn if a primary mean amplitude has the opposite sign
                                     of the component's ``polarity``
MEASURE_NO_LOCAL_PEAK        erp     warn if a peak measure fell back to the absolute extreme
VEP_LATENCY_ATYPICAL         erp     info (pass) if the vep_p100 peak latency is > 115 ms
ERP_REFERENCE_CHANGED        erp     info (pass): components measured with their own reference
                                     (re-referenced copies; e.g. VEP at Oz vs Fz)
GROUP_SUBJECTS_EXCLUDED      group   warn if a configured subject is not in the group (an upstream
                                     stage failed), listing each with its reason
GROUP_UNDERPOWERED           group   info (pass) when the smallest p a sign-flip permutation can
                                     give with N subjects (2/2**N two-tailed, 1/2**N one-tailed)
                                     is above ``group.alpha``
REFERENCE_VALUES_MISMATCH    group   warn if a compared value is outside its tolerance or has
                                     the opposite sign of the published one (section 8.2)
===========================  ======  ============================================================
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mne
import numpy as np

from eegunify.erp.stages.base import write_text_atomic
from eegunify.qa.checks import _ORDER, Level, QACheck

if TYPE_CHECKING:
    from eegunify.erp.ica import Decision

LINE_RESIDUAL_DB = 10.0
BADS_WARN, BADS_FAIL = 0.10, 0.25
ICA_EXCLUDED_WARN = 0.25
BLINK_RESIDUAL_R = 0.3
REJECTED_WARN, REJECTED_FAIL = 0.25, 0.50
BASELINE_DRIFT_UV = 2.0
SME_WARN_UV = 2.0
VEP_P100_ATYPICAL_MS = 115.0
BRAIN_REMOVED_PROB = 0.5
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


# --- ica -------------------------------------------------------------------------------------


def blink_residual_r(raw: mne.io.BaseRaw, eog: str = "VEOG", frontal: str = "Fp1") -> float:
    """Correlation of ``eog`` and ``frontal`` on a 1 Hz high-passed copy (nan if absent)."""
    names = {c.upper(): c for c in raw.ch_names}
    if eog.upper() not in names or frontal.upper() not in names:
        return float("nan")
    picks = [names[eog.upper()], names[frontal.upper()]]
    tmax = min(raw.times[-1], QA_SECONDS)
    copy = raw.copy().crop(tmax=tmax).pick(picks)
    copy.filter(1.0, None, picks="all", verbose=False)
    x = copy.get_data()
    return float(np.corrcoef(x[0], x[1])[0, 1])


def ica_checks(raw: mne.io.BaseRaw, decision: Decision) -> list[QACheck]:
    """The three ica checks on the cleaned data and the component decisions."""
    table = decision.table
    n, excluded = len(table), decision.excluded
    frac = len(excluded) / n if n else 0.0
    has_eog = bool(mne.pick_types(raw.info, eog=True).size)
    count_level: Level = (
        "warn" if (not excluded and has_eog) or frac > ICA_EXCLUDED_WARN else "pass"
    )
    checks = [
        QACheck(
            "ICA_EXCLUDED_COUNT",
            count_level,
            f"{len(excluded)}/{n} components excluded: {excluded or 'none'}"
            + (" (EOG present but nothing excluded)" if not excluded and has_eog else ""),
            len(excluded),
            ICA_EXCLUDED_WARN,
        )
    ]
    r = blink_residual_r(raw)
    if np.isfinite(r):
        checks.append(
            QACheck(
                "ICA_BLINK_RESIDUAL",
                "warn" if abs(r) > BLINK_RESIDUAL_R else "pass",
                f"VEOG-Fp1 correlation after cleaning r = {r:.2f}",
                round(r, 3),
                BLINK_RESIDUAL_R,
            )
        )
    else:
        checks.append(QACheck("ICA_BLINK_RESIDUAL", "pass", "VEOG or Fp1 absent: not measured"))
    brainy = table[table["excluded"] & (table["p_brain"] > BRAIN_REMOVED_PROB)]
    idx = [int(i) for i in brainy["idx"]]
    checks.append(
        QACheck(
            "ICA_BRAIN_REMOVED",
            "warn" if idx else "pass",
            f"excluded components with brain prob > {BRAIN_REMOVED_PROB}: {idx or 'none'}",
            idx,
            BRAIN_REMOVED_PROB,
        )
    )
    return checks


# --- epochs ----------------------------------------------------------------------------------


def rejected_fraction_check(n_rejected: int, n_total: int, by_set: dict[str, str]) -> QACheck:
    frac = n_rejected / n_total if n_total else 0.0
    level: Level = "fail" if frac > REJECTED_FAIL else "warn" if frac > REJECTED_WARN else "pass"
    detail = "; ".join(f"{k}: {v}" for k, v in sorted(by_set.items()))
    return QACheck(
        "EPOCHS_REJECTED_FRACTION",
        level,
        f"{n_rejected}/{n_total} epochs rejected ({100 * frac:.0f} %) [{detail}]",
        round(frac, 4),
        [REJECTED_WARN, REJECTED_FAIL],
    )


def min_trials_check(counts: dict[str, int], minimum: int) -> QACheck:
    """``counts``: ``<set>/<condition>`` -> kept epochs (only the conditions that matter)."""
    if not counts:
        return QACheck("EPOCHS_MIN_TRIALS", "pass", "no condition to check")
    low = {k: v for k, v in sorted(counts.items()) if v < minimum}
    shown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    if low:
        msg = f"fewer than {minimum} kept epochs: " + ", ".join(f"{k}={v}" for k, v in low.items())
        return QACheck("EPOCHS_MIN_TRIALS", "fail", msg, counts, minimum)
    return QACheck("EPOCHS_MIN_TRIALS", "pass", f"kept epochs: {shown}", counts, minimum)


def baseline_drift_check(offsets: dict[str, float]) -> QACheck:
    """``offsets``: ``<set>/<condition>`` -> mean |baseline| of its ERP in µV."""
    finite = {k: v for k, v in offsets.items() if np.isfinite(v)}
    if not finite:
        return QACheck("EPOCHS_BASELINE_DRIFT", "pass", "no ERP to check")
    worst_key = max(finite, key=finite.__getitem__)
    val = finite[worst_key]
    return QACheck(
        "EPOCHS_BASELINE_DRIFT",
        "warn" if val > BASELINE_DRIFT_UV else "pass",
        f"largest mean |baseline| {val:.2f} uV ({worst_key})",
        round(val, 4),
        BASELINE_DRIFT_UV,
    )


# --- erp -------------------------------------------------------------------------------------


def reference_check(rerefs: dict[str, dict[str, str]]) -> QACheck:
    """``rerefs``: component id -> {"data": ..., "component": ...} for re-referenced ones."""
    if not rerefs:
        return QACheck("ERP_REFERENCE_CHANGED", "pass", "every component uses the data reference")
    shown = "; ".join(
        f"{cid}: {r['data']} -> {r['component']}" for cid, r in sorted(rerefs.items())
    )
    return QACheck(
        "ERP_REFERENCE_CHANGED", "pass", f"measured with the component's reference ({shown})"
    )


def erp_checks(
    sme_uv: dict[str, float],
    mean_uv: dict[str, tuple[float, str]],
    no_local_peak: dict[str, list[str]],
    vep_p100_ms: float | None = None,
) -> list[QACheck]:
    """``sme_uv``/``mean_uv``/``no_local_peak`` are keyed by component id; ``mean_uv`` holds
    (primary mean amplitude, expected polarity)."""
    if not mean_uv:
        return [QACheck("ERP_NOISE_SME", "pass", "no component measured")]
    finite = {k: v for k, v in sme_uv.items() if np.isfinite(v)}
    high = {k: v for k, v in sorted(finite.items()) if v > SME_WARN_UV}
    shown = ", ".join(f"{k} {v:.2f}" for k, v in sorted(finite.items())) or "n/a"
    checks = [
        QACheck(
            "ERP_NOISE_SME",
            "warn" if high else "pass",
            f"SME of the primary mean amplitude (uV): {shown}",
            {k: round(v, 4) for k, v in finite.items()},
            SME_WARN_UV,
        )
    ]
    wrong = {
        k: v
        for k, (v, pol) in sorted(mean_uv.items())
        if np.isfinite(v) and (v > 0) != (pol == "positive") and v != 0
    }
    checks.append(
        QACheck(
            "ERP_COMPONENT_POLARITY",
            "warn" if wrong else "pass",
            "opposite polarity: " + ", ".join(f"{k} {v:+.2f} uV" for k, v in wrong.items())
            if wrong
            else "every primary mean amplitude has the expected sign",
            {k: round(v, 4) for k, (v, _) in mean_uv.items()},
        )
    )
    flat = {k: v for k, v in sorted(no_local_peak.items()) if v}
    checks.append(
        QACheck(
            "MEASURE_NO_LOCAL_PEAK",
            "warn" if flat else "pass",
            "absolute extreme used (no local peak): "
            + "; ".join(f"{k}: {', '.join(v)}" for k, v in flat.items())
            if flat
            else "every peak is a local peak",
            flat or None,
        )
    )
    if vep_p100_ms is not None and np.isfinite(vep_p100_ms):
        late = vep_p100_ms > VEP_P100_ATYPICAL_MS
        checks.append(
            QACheck(
                "VEP_LATENCY_ATYPICAL",
                "pass",
                f"P100 latency {vep_p100_ms:.0f} ms"
                + (f" (> {VEP_P100_ATYPICAL_MS:g} ms: atypically late)" if late else ""),
                round(vep_p100_ms, 2),
                VEP_P100_ATYPICAL_MS,
            )
        )
    return checks


# --- group -----------------------------------------------------------------------------------


def subjects_excluded_check(excluded: dict[str, str]) -> QACheck:
    if not excluded:
        return QACheck("GROUP_SUBJECTS_EXCLUDED", "pass", "every configured subject included")
    listed = "; ".join(f"sub-{s}: {why}" for s, why in sorted(excluded.items()))
    return QACheck(
        "GROUP_SUBJECTS_EXCLUDED", "warn", f"{len(excluded)} excluded: {listed}", sorted(excluded)
    )


def underpowered_check(n_subjects: int, min_p: float, alpha: float, tail: int) -> QACheck:
    sides = "two-tailed" if tail == 0 else "one-tailed"
    if min_p > alpha:
        return QACheck(
            "GROUP_UNDERPOWERED",
            "pass",
            f"N = {n_subjects}: the smallest possible {sides} permutation p is {min_p:.4g} "
            f"> alpha {alpha:g}, so no cluster can reach significance; report effect sizes, "
            "not p-values",
            min_p,
            alpha,
        )
    return QACheck(
        "GROUP_UNDERPOWERED",
        "pass",
        f"N = {n_subjects}: smallest possible {sides} permutation p {min_p:.4g} <= alpha",
        min_p,
        alpha,
    )


def reference_values_check(n_failed: int, n_compared: int, failing: list[str]) -> QACheck:
    if n_failed:
        return QACheck(
            "REFERENCE_VALUES_MISMATCH",
            "warn",
            f"{n_failed}/{n_compared} compared values outside tolerance: " + ", ".join(failing),
            n_failed,
        )
    return QACheck(
        "REFERENCE_VALUES_MISMATCH",
        "pass",
        f"all {n_compared} compared values within tolerance",
        0,
    )
