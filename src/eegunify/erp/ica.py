"""Artifact correction with ICA (Phase 3, docs/BLUEPRINT_PHASES.md section 6.2).

Pure helpers on ``mne.io.Raw``, used by the ica stage, the app and the tests:

1. :func:`fit_copy`: the copy ICA is fitted on, good EEG high-passed at ``fit_highpass_hz``
   (1 Hz) and low-passed at 100 Hz, average-referenced. ICLabel was trained on exactly that
   (average reference, 1-100 Hz), so classification runs on this copy too; the provenance
   records it.
2. :func:`fit_ica`: picard / infomax / fastica with a fixed seed, ``reject_by_annotation``.
3. :func:`classify`: ICLabel probabilities (7 classes) plus the EOG correlation z-scores.
4. :func:`decide`: the exclusion rule, one row per component with its ``reason``:
   (ICLabel class in ``exclude_classes`` with probability >= threshold) OR (|EOG z| >=
   threshold AND ICLabel class is not ``brain``), plus ``exclude``, minus ``include``.
5. :func:`apply_ica`: the solution is applied to the preprocessed (0.1 Hz) data. ICA was
   fitted on average-referenced data, so the data are average-referenced first and the
   configured reference is then applied again (re-referencing is linear, so ``channels`` and
   ``rest`` come out exactly as if ICA had never changed the reference).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import mne
import numpy as np
import pandas as pd

from eegunify.config.erp_sections import (
    ICLABEL_CLASSES,
    ArtifactsSection,
    IcaFitExcludeSpec,
    IcaSpec,
)
from eegunify.config.schema import ReferenceSection
from eegunify.erp.events import matches
from eegunify.erp.preprocess import add_bipolar_eog, rereference

__all__ = ["add_bipolar_eog"]  # moved to preprocess (derived in preproc since 0.3.0)

ICLABEL_LOWPASS_HZ = 100.0


class IcaError(ValueError):
    """The data or config can't be cleaned with ICA as asked (the message says how to fix it)."""


def run_asr(raw: mne.io.BaseRaw, cutoff: float) -> dict[str, Any]:
    """Artifact subspace reconstruction (meegkit) on the EEG channels, in place."""
    try:
        from meegkit.asr import ASR
    except ImportError as err:
        raise IcaError(
            "artifacts.asr needs meegkit: pip install meegkit (or set artifacts.asr: null)"
        ) from err
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    x = raw.get_data(picks=picks)
    asr = ASR(sfreq=raw.info["sfreq"], cutoff=cutoff)
    asr.fit(x)
    raw._data[picks] = asr.transform(x)
    return {"cutoff": cutoff, "channels": len(picks)}


FIT_EXCLUDE_LABEL = "BAD_ica_fit"


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def break_spans(raw: mne.io.BaseRaw, spec: IcaFitExcludeSpec) -> list[tuple[float, float]]:
    """(start, end) seconds of the breaks: gaps >= ``break_gap_ms`` between block events,
    shrunk by ``break_buffer_ms`` on each side, plus the stretches before the first and after
    the last block event. Times are relative to the first sample."""
    if spec.break_selector is None:
        return []
    t0 = raw.first_time
    onsets = sorted(
        float(a["onset"]) - t0
        for a in raw.annotations
        if matches(str(a["description"]), spec.break_selector)
    )
    if not onsets:
        raise IcaError(
            f"artifacts.ica.fit_exclude.break_selector {spec.break_selector!r} matches no event"
        )
    buf, gap = spec.break_buffer_ms / 1000, spec.break_gap_ms / 1000
    end = raw.times[-1]
    spans = [(0.0, onsets[0] - buf), (onsets[-1] + buf, end)]
    spans += [(a + buf, b - buf) for a, b in itertools.pairwise(onsets) if b - a >= gap]
    return [(a, b) for a, b in spans if b > a]


def ptp_spans(raw: mne.io.BaseRaw, spec: IcaFitExcludeSpec) -> list[tuple[float, float]]:
    """(start, end) seconds of the moving windows whose peak-to-peak exceeds ``ptp_uv`` on any
    good EEG channel, merged."""
    if spec.ptp_uv is None:
        return []
    x = raw.get_data(picks="eeg", exclude="bads")
    sf = raw.info["sfreq"]
    win = max(1, round(spec.ptp_window_ms / 1000 * sf))
    step = max(1, round(spec.ptp_step_ms / 1000 * sf))
    n = x.shape[1]
    starts = np.arange(0, max(1, n - win + 1), step)
    thr = spec.ptp_uv * 1e-6
    spans = [
        (s / sf, min(s + win, n) / sf)
        for s in starts
        if np.ptp(x[:, s : s + win], axis=1).max() > thr
    ]
    return _merge(spans)


def fit_exclusion(
    raw: mne.io.BaseRaw, spec: IcaFitExcludeSpec | None
) -> tuple[mne.Annotations | None, dict[str, Any]]:
    """``BAD_ica_fit`` annotations for the fit copy and a summary for the provenance."""
    if spec is None:
        return None, {}
    brk, ptp = break_spans(raw, spec), ptp_spans(raw, spec)
    spans = _merge(brk + ptp)
    total = float(raw.times[-1])
    excluded = sum(b - a for a, b in spans)
    ann = mne.Annotations(
        [raw.first_time + a for a, _ in spans],
        [b - a for a, b in spans],
        [FIT_EXCLUDE_LABEL] * len(spans),
        orig_time=raw.annotations.orig_time,
    )
    summary = {
        "breaks_s": round(sum(b - a for a, b in brk), 2),
        "ptp_s": round(sum(b - a for a, b in ptp), 2),
        "excluded_s": round(excluded, 2),
        "excluded_fraction": round(excluded / total, 4) if total else 0.0,
    }
    return ann, summary


def fit_copy(raw: mne.io.BaseRaw, spec: IcaSpec) -> mne.io.BaseRaw:
    """Good EEG + EOG, band-passed ``fit_highpass_hz``-100 Hz, average reference (ICLabel's
    training conditions). The low-pass is skipped when 100 Hz is above Nyquist."""
    copy = raw.copy().pick(["eeg", "eog"], exclude="bads")
    nyq = copy.info["sfreq"] / 2
    h_freq = ICLABEL_LOWPASS_HZ if nyq - 1 > ICLABEL_LOWPASS_HZ else None
    copy.filter(spec.fit_highpass_hz, h_freq, picks=["eeg", "eog"], verbose=False)
    copy.set_eeg_reference("average", projection=False, ch_type="eeg", verbose=False)
    return copy


def fit_ica(fit_raw: mne.io.BaseRaw, spec: IcaSpec) -> mne.preprocessing.ICA:
    if spec.algorithm == "picard":
        # ortho=False + extended=True is extended infomax, what ICLabel was trained on.
        params: dict[str, Any] = {"ortho": False, "extended": spec.extended}
        try:
            import picard  # noqa: F401
        except ImportError as err:
            raise IcaError(
                "algorithm 'picard' needs python-picard: pip install 'eegunify[erp]'"
            ) from err
    elif spec.algorithm == "infomax":
        params = {"extended": spec.extended}
    else:
        params = {}
    ica = mne.preprocessing.ICA(
        n_components=spec.n_components,
        method=spec.algorithm,
        fit_params=params,
        rng=spec.random_state,
        max_iter="auto",
    )
    ica.fit(fit_raw, picks="eeg", reject_by_annotation=True, verbose=False)
    return ica


def iclabel_probabilities(fit_raw: mne.io.BaseRaw, ica: mne.preprocessing.ICA) -> np.ndarray:
    """(n_components, 7) ICLabel probabilities, columns in :data:`ICLABEL_CLASSES` order."""
    try:
        from mne_icalabel.iclabel import iclabel_label_components
    except ImportError as err:
        raise IcaError("ICLabel needs mne-icalabel: pip install 'eegunify[erp]'") from err
    with mne.utils.use_log_level("ERROR"):
        probs = iclabel_label_components(fit_raw, ica, inplace=False, backend="onnx")
    return np.asarray(probs, dtype=float)


def eog_scores(
    fit_raw: mne.io.BaseRaw, ica: mne.preprocessing.ICA, channels: list[str]
) -> tuple[list[str], np.ndarray]:
    """z-scored correlation of each component with each present EOG channel.

    Returns the channels used and a (n_channels, n_components) array (empty when none is
    present). The z-score is over components, as ``ICA.find_bads_eog(measure="zscore")``.
    """
    present = [c for c in channels if c in fit_raw.ch_names]
    if not present:
        return [], np.empty((0, ica.n_components_))
    sources = ica.get_sources(fit_raw).get_data()
    z = []
    for ch in present:
        eog = fit_raw.get_data(picks=[ch])[0]
        r = np.array([np.corrcoef(s, eog)[0, 1] for s in sources])
        r = np.nan_to_num(r)
        sd = r.std()
        z.append((r - r.mean()) / sd if sd > 0 else np.zeros_like(r))
    return present, np.vstack(z)


@dataclass
class Decision:
    table: pd.DataFrame
    """One row per component: idx, iclabel class/prob, 7 probability columns, ``eog_z_<ch>``,
    excluded, reason."""
    eog_channels: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> list[int]:
        return [int(i) for i in self.table.loc[self.table["excluded"], "idx"]]


def decide(probs: np.ndarray, eog_ch: list[str], eog_z: np.ndarray, spec: IcaSpec) -> Decision:
    """Apply the exclusion rule (module docstring) and give every component a reason."""
    n = probs.shape[0]
    out_of_range = [i for i in [*spec.exclude, *spec.include] if not 0 <= i < n]
    if out_of_range:
        raise IcaError(f"artifacts.ica exclude/include {out_of_range}: only {n} components")
    top = probs.argmax(axis=1)
    rows = []
    for i in range(n):
        cls = ICLABEL_CLASSES[top[i]]
        reasons = []
        for name in spec.iclabel.exclude_classes:
            p = probs[i, ICLABEL_CLASSES.index(name)]
            if p >= spec.iclabel.threshold:
                reasons.append(f"iclabel {name} {p:.2f}")
        if cls != "brain":
            for ch, z in zip(eog_ch, eog_z[:, i], strict=True):
                if abs(z) >= spec.eog_correlation.threshold:
                    reasons.append(f"eog {ch} z={z:.1f}")
        if i in spec.exclude:
            reasons.append("manual exclude")
        excluded = bool(reasons)
        if i in spec.include:
            excluded, reasons = False, ["manual include", *reasons]
        row: dict[str, Any] = {
            "idx": i,
            "iclabel": cls,
            "iclabel_prob": round(float(probs[i, top[i]]), 4),
        }
        row |= {
            f"p_{c.replace(' ', '_')}": round(float(p), 4)
            for c, p in zip(ICLABEL_CLASSES, probs[i], strict=True)
        }
        row |= {
            f"eog_z_{ch}": round(float(z), 3) for ch, z in zip(eog_ch, eog_z[:, i], strict=True)
        }
        row |= {"excluded": excluded, "reason": "; ".join(reasons) or "kept"}
        rows.append(row)
    return Decision(pd.DataFrame(rows), eog_ch)


def check_reference(reference: ReferenceSection) -> None:
    if reference.scheme == "none":
        raise IcaError(
            "artifacts.method 'ica' needs a re-referenced recording (ICA is fitted on average "
            "reference): set reference.scheme to average, channels or rest, or "
            "artifacts.method: none"
        )


def apply_ica(
    raw: mne.io.BaseRaw,
    ica: mne.preprocessing.ICA,
    exclude: list[int],
    reference: ReferenceSection,
) -> mne.io.BaseRaw:
    """Remove ``exclude`` from ``raw`` (in place; see the module docstring on the reference)."""
    check_reference(reference)
    raw.set_eeg_reference("average", projection=False, ch_type="eeg", verbose=False)
    ica.apply(raw, exclude=list(exclude), verbose=False)
    if reference.scheme != "average":
        rereference(raw, reference, reference_free=False)
    return raw


@dataclass
class IcaResult:
    raw: mne.io.BaseRaw
    """The cleaned data."""
    ica: mne.preprocessing.ICA | None
    """``None`` for a precomputed (EEGLAB) decomposition."""
    decision: Decision
    steps: dict[str, Any]


def run_ica(raw: mne.io.BaseRaw, art: ArtifactsSection, reference: ReferenceSection) -> IcaResult:
    """Every ICA step on preprocessed ``raw`` (modified in place and returned as ``.raw``)."""
    spec = art.ica
    check_reference(reference)
    steps: dict[str, Any] = {}
    if art.asr is not None:
        steps["asr"] = run_asr(raw, art.asr.cutoff)
    fit_ann, fit_excl = fit_exclusion(raw, spec.fit_exclude)
    fit_raw = fit_copy(raw, spec)
    if fit_ann is not None:
        fit_raw.set_annotations(fit_raw.annotations + fit_ann)
        steps["fit_exclude"] = fit_excl
    ica = fit_ica(fit_raw, spec)
    probs = iclabel_probabilities(fit_raw, ica)
    eog_ch, eog_z = eog_scores(fit_raw, ica, spec.eog_correlation.channels)
    decision = decide(probs, eog_ch, eog_z, spec)
    apply_ica(raw, ica, decision.excluded, reference)
    ica.exclude = decision.excluded
    steps |= {
        "fit": {
            "highpass_hz": spec.fit_highpass_hz,
            "lowpass_hz": fit_raw.info["lowpass"],
            "reference": "average",
            "algorithm": spec.algorithm,
            "n_components": int(ica.n_components_),
            "n_iter": int(getattr(ica, "n_iter_", 0)),
        },
        "iclabel": {
            "reference": "average",
            "band_hz": [spec.fit_highpass_hz, fit_raw.info["lowpass"]],
        },
        "eog_channels": eog_ch,
        "excluded": decision.excluded,
        "reference_after": reference.scheme,
    }
    return IcaResult(raw, ica, decision, steps)
