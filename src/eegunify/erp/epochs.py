"""Epoching and epoch rejection (Phase 4, docs/BLUEPRINT_PHASES.md section 6.3).

Pure helpers, used by the epochs stage, the app and the tests. The rejection rules are plain
numpy so that the MATLAB ERP-lite cross-check (M-E5) runs the identical rule in ERPLAB:

- :func:`moving_window_ptp`: ERPLAB ``pop_artmwppth`` (moving-window peak-to-peak). Windows of
  ``window`` samples start at sample 0 and every ``step`` samples while the window fits in the
  epoch; the epoch's value on a channel is the largest peak-to-peak of any window, and an epoch
  is flagged when any channel exceeds the threshold.
- :func:`step_like`: ERPLAB ``pop_artstep`` (step-like artifacts, used on HEOG for saccades).
  In each moving window, the mean of the second half minus the mean of the first half (the
  first half has MATLAB ``round(window / 2)`` samples); the epoch's value is the largest
  absolute difference.
- flat: peak-to-peak over the whole epoch below ``flat_uv`` on any EEG channel.

The test period is the whole epoch, as ERPLAB's default. Window and step sizes are
``floor(ms * fs / 1000)`` samples, as in ERPLAB (checked against ERPLAB 13.10 source). One
difference: ERPLAB refuses a window longer than the epoch, whereas here it becomes a single
window over the whole epoch (MNE's ``reject``, used by the preview recipe). Values are in µV;
data come in Volts.
Event onsets are never shifted here (Phase 1 already applied ``onset_shift_ms``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd

from eegunify.config.erp_sections import EpochSet, RejectSpec
from eegunify.erp.events import matches, select

UV = 1e6


class EpochsError(ValueError):
    """The data or config can't be epoched as asked (the message says how to fix it)."""


def _windows(n_times: int, window: int, step: int) -> list[int]:
    if window > n_times:
        return [0]  # epoch shorter than the window: one window, the whole epoch
    return list(range(0, n_times - window + 1, step))


def moving_window_ptp(data: np.ndarray, window: int, step: int) -> np.ndarray:
    """(n_epochs, n_channels) largest moving-window peak-to-peak of (n_epochs, n_ch, n_times)."""
    starts = _windows(data.shape[-1], window, step)
    out = np.zeros(data.shape[:2])
    for s in starts:
        seg = data[..., s : s + window]
        out = np.maximum(out, seg.max(axis=-1) - seg.min(axis=-1))
    return out


def step_like(data: np.ndarray, window: int, step: int) -> np.ndarray:
    """(n_epochs, n_channels) largest |mean(second half) - mean(first half)| over windows."""
    starts = _windows(data.shape[-1], window, step)
    half = max(matlab_round(window / 2), 1)  # ERPLAB: round(winpnts/2) samples in the first half
    out = np.zeros(data.shape[:2])
    for s in starts:
        seg = data[..., s : s + window]
        diff = np.abs(seg[..., half:].mean(axis=-1) - seg[..., :half].mean(axis=-1))
        out = np.maximum(out, diff)
    return out


def ms_to_samples(ms: float, sfreq: float) -> int:
    """``floor(ms * fs / 1000)``, as ERPLAB ``pop_artmwppth``/``pop_artstep``; at least 1."""
    return max(int(np.floor(ms * sfreq / 1000.0 + 1e-9)), 1)


def matlab_round(x: float) -> int:
    """MATLAB ``round``: halves away from zero (Python's ``round`` goes to even)."""
    return int(np.sign(x) * np.floor(abs(x) + 0.5))


def _heog(epochs: mne.BaseEpochs) -> str | None:
    names = {c.upper(): c for c in epochs.ch_names}
    return names.get("HEOG")


@dataclass
class Rejection:
    """Per-epoch values (µV) and the reason each epoch was rejected (``""`` = kept)."""

    ptp_uv: np.ndarray
    step_uv: np.ndarray
    reasons: list[str]
    heog: str | None
    details: dict[str, Any] = field(default_factory=dict)


def threshold_rejection(epochs: mne.BaseEpochs, spec: RejectSpec) -> Rejection:
    """Apply the ``thresholds`` rules (module docstring) to every epoch; drops nothing."""
    sfreq = epochs.info["sfreq"]
    win = ms_to_samples(spec.ptp_window_ms, sfreq)
    stp = ms_to_samples(spec.ptp_step_ms, sfreq)
    swin = ms_to_samples(spec.step_window_ms, sfreq)
    sstp = ms_to_samples(spec.step_step_ms, sfreq)
    eeg_picks = mne.pick_types(epochs.info, eeg=True, exclude="bads")
    eeg = epochs.get_data(picks=eeg_picks) * UV
    names = [epochs.ch_names[i] for i in eeg_picks]
    n = len(epochs)
    ptp = moving_window_ptp(eeg, win, stp) if eeg.size else np.zeros((n, 0))
    heog = _heog(epochs)
    step = np.full(n, np.nan)
    if heog is not None and spec.step_heog_uv is not None:
        step = step_like(epochs.get_data(picks=[heog]) * UV, swin, sstp)[:, 0]
    whole = (eeg.max(axis=-1) - eeg.min(axis=-1)) if eeg.size else np.zeros((n, 0))
    reasons = []
    for i in range(n):
        why = []
        if spec.ptp_uv is not None and ptp.shape[1] and ptp[i].max() > spec.ptp_uv:
            worst = names[int(ptp[i].argmax())]
            why.append(f"ptp {ptp[i].max():.1f} uV at {worst}")
        if spec.step_heog_uv is not None and np.isfinite(step[i]) and step[i] > spec.step_heog_uv:
            why.append(f"step {step[i]:.1f} uV at {heog}")
        if spec.flat_uv is not None and whole.shape[1] and whole[i].min() < spec.flat_uv:
            flat = names[int(whole[i].argmin())]
            why.append(f"flat {whole[i].min():.2f} uV at {flat}")
        reasons.append("; ".join(why))
    max_ptp = ptp.max(axis=1) if ptp.shape[1] else np.full(n, np.nan)
    details = {
        "ptp_window_samples": win,
        "ptp_step_samples": stp,
        "step_window_samples": swin,
        "step_step_samples": sstp,
        "heog": heog,
    }
    return Rejection(max_ptp, step, reasons, heog, details)


def autoreject_rejection(epochs: mne.BaseEpochs, spec: RejectSpec) -> Rejection:
    try:
        from autoreject import AutoReject
    except ImportError as err:
        raise EpochsError(
            "epochs.reject.method 'autoreject' needs autoreject: pip install autoreject "
            "(or use method: thresholds)"
        ) from err
    ar = AutoReject(random_state=spec.random_state, verbose=False)
    ar.fit(epochs.copy().pick("eeg", exclude="bads"))
    log = ar.get_reject_log(epochs.copy().pick("eeg", exclude="bads"))
    reasons = ["autoreject" if bad else "" for bad in log.bad_epochs]
    n = len(epochs)
    return Rejection(
        np.full(n, np.nan), np.full(n, np.nan), reasons, None, {"method": "autoreject"}
    )


def erplab_window(
    times_s: np.ndarray, window_ms: tuple[float, float] | None, sfreq: float
) -> tuple[int, int]:
    """ERPLAB ``window2sample``: 0-based inclusive (p1, p2) of a test period.

    ``p = round(t * fs) + round(|xmin| * fs)`` (MATLAB rounding), clipped to the epoch.
    """
    n = len(times_s)
    if window_ms is None:
        return 0, n - 1
    off = matlab_round(abs(times_s[0]) * sfreq)
    p1 = matlab_round(window_ms[0] / 1000 * sfreq) + off
    p2 = matlab_round(window_ms[1] / 1000 * sfreq) + off
    if p1 < -2 or p2 > n + 1:
        raise EpochsError(f"rejection window {window_ms} ms is outside the epoch")
    return max(p1, 0), min(p2, n - 1)


def rules_rejection(epochs: mne.BaseEpochs, spec: RejectSpec) -> Rejection:
    """Apply ``spec.rules`` (ERPLAB ``pop_artextval``/``pop_artmwppth``/``pop_artstep``)."""
    sfreq = epochs.info["sfreq"]
    lower = {c.lower(): c for c in epochs.ch_names}
    n = len(epochs)
    why: list[list[str]] = [[] for _ in range(n)]
    ptp_max = np.full(n, np.nan)
    step_max = np.full(n, np.nan)
    details: list[dict[str, Any]] = []
    for r, rule in enumerate(spec.rules):
        missing = [c for c in rule.channels if c.lower() not in lower]
        if missing:
            raise EpochsError(f"epochs.reject.rules[{r}]: channels {missing} not in the epochs")
        names = [lower[c.lower()] for c in rule.channels]
        p1, p2 = erplab_window(epochs.times, rule.window_ms, sfreq)
        x = epochs.get_data(picks=names)[..., p1 : p2 + 1] * UV
        if rule.test == "extval":
            assert rule.min_uv is not None and rule.max_uv is not None
            # ERPLAB's bounds are inclusive; 1e-9 µV keeps them so after the V <-> µV round
            # trip (200 µV stored in volts reads back as 199.99999999999997).
            tol = 1e-9
            hit = (x.max(axis=-1) >= rule.max_uv - tol) | (x.min(axis=-1) <= rule.min_uv + tol)
            value = np.abs(x).max(axis=-1)
            label = f"extval [{rule.min_uv:g}, {rule.max_uv:g}]"
        else:
            assert rule.threshold_uv is not None
            win = ms_to_samples(rule.win_ms, sfreq)
            stp = ms_to_samples(rule.step_ms, sfreq)
            fn = moving_window_ptp if rule.test == "ptp" else step_like
            value = fn(x, win, stp)
            hit = value > rule.threshold_uv
            label = f"{rule.test} > {rule.threshold_uv:g}"
            target = ptp_max if rule.test == "ptp" else step_max
            np.fmax(target, value.max(axis=1), out=target)
        for i in np.flatnonzero(hit.any(axis=1)):
            ch = names[int(np.argmax(np.where(hit[i], value[i], -np.inf)))]
            why[i].append(f"rule {r + 1} {label} uV at {ch}")
        details.append({"rule": r + 1, "test": rule.test, "samples": [p1, p2]})
    reasons = ["; ".join(w) for w in why]
    return Rejection(ptp_max, step_max, reasons, None, {"method": "rules", "rules": details})


def reject(epochs: mne.BaseEpochs, spec: RejectSpec) -> Rejection:
    if spec.method == "thresholds":
        return threshold_rejection(epochs, spec)
    if spec.method == "rules":
        return rules_rejection(epochs, spec)
    if spec.method == "autoreject":
        return autoreject_rejection(epochs, spec)
    n = len(epochs)
    return Rejection(np.full(n, np.nan), np.full(n, np.nan), [""] * n, None, {"method": "none"})


def trial_type(label: str) -> str:
    """``stimulus/face/12`` -> ``stimulus/face`` (read_raw_bids appends the value when a trial
    type has several codes); labels without a numeric last part are returned unchanged."""
    head, _, last = label.rpartition("/")
    return head if head and last.isdigit() else label


def rawdata_codes(events_tsv: Path | None) -> pd.DataFrame | None:
    """Onset (s) and original ``value`` of every rawdata event, for the per-epoch log."""
    if events_tsv is None or not events_tsv.exists():
        return None
    ev = pd.read_csv(events_tsv, sep="\t", na_values="n/a", encoding="utf-8")
    return ev[["onset", "value"]] if {"onset", "value"} <= set(ev.columns) else None


def _codes_for(onsets: np.ndarray, codes: pd.DataFrame | None, sfreq: float) -> list[Any]:
    if codes is None or codes.empty:
        return ["n/a"] * len(onsets)
    ref = codes["onset"].to_numpy(dtype=float)
    out: list[Any] = []
    for t in onsets:
        j = int(np.argmin(np.abs(ref - t)))
        hit = abs(ref[j] - t) <= 1.0 / sfreq
        val = codes["value"].iloc[j] if hit else "n/a"
        out.append(int(val) if hit and float(val).is_integer() else val)
    return out


@dataclass
class EpochSetResult:
    name: str
    epochs: mne.BaseEpochs
    """The kept epochs."""
    log: pd.DataFrame
    """One row per selected event: idx, onset_s, label, trial_type, code, kept, reason,
    ptp_uv, step_uv."""
    details: dict[str, Any]


NO_RESPONSE = "NO_RESPONSE"


def followed_by_response(
    all_events: np.ndarray,
    event_id: dict[str, int],
    events: np.ndarray,
    sfreq: float,
    eset: EpochSet,
    name: str,
) -> np.ndarray:
    """For each row of ``events``: is a response matching ``eset.response`` in its window?

    ERPLAB binlister semantics for a timed sequencer ``{t<lo-hi>code}``: any matching event
    with ``lo <= lag_ms <= hi`` counts, whether or not other events come between.
    """
    if eset.response is None:
        return np.ones(len(events), dtype=bool)
    try:
        resp_ids = select(event_id, eset.response.selector)
    except KeyError as err:
        raise EpochsError(f"epochs.sets.{name}.response.selector: {err.args[0]}") from err
    resp = np.sort(all_events[np.isin(all_events[:, 2], list(resp_ids.values())), 0])
    lo, hi = eset.response.window_ms
    eps = 1e-9  # lags are sample counts / sfreq: exact edges must stay inclusive
    out = np.zeros(len(events), dtype=bool)
    for i, onset in enumerate(events[:, 0]):
        lag = (resp - onset) / sfreq * 1000.0
        out[i] = bool(np.any((lag >= lo - eps) & (lag <= hi + eps)))
    return out


def make_epochs(
    raw: mne.io.BaseRaw,
    name: str,
    eset: EpochSet,
    spec: RejectSpec,
    codes: pd.DataFrame | None = None,
) -> EpochSetResult:
    """Epoch ``raw`` at the events ``eset.lock`` selects, baseline-correct, then reject.

    Events whose epoch would extend past the data or overlaps a ``BAD_`` annotation are
    logged as rejected with MNE's reason. The log lists every selected event.
    """
    all_events, event_id = mne.events_from_annotations(raw, verbose=False)
    event_id = {k: v for k, v in event_id.items() if not k.upper().startswith("BAD")}
    try:
        chosen = select(event_id, eset.lock)
    except KeyError as err:
        raise EpochsError(f"epochs.sets.{name}.lock: {err.args[0]}") from err
    keep = np.isin(all_events[:, 2], list(chosen.values()))
    events = all_events[keep]
    responded = followed_by_response(all_events, event_id, events, raw.info["sfreq"], eset, name)
    labels = {v: k for k, v in chosen.items()}
    epochs = mne.Epochs(
        raw,
        events,
        event_id=chosen,
        tmin=eset.tmin_ms / 1000,
        tmax=eset.tmax_ms / 1000,
        baseline=None if eset.baseline_ms is None else tuple(b / 1000 for b in eset.baseline_ms),
        picks=["eeg", "eog"],
        reject=None,
        flat=None,
        reject_by_annotation=True,
        preload=True,
        verbose=False,
    )
    sfreq = raw.info["sfreq"]
    onsets = (events[:, 0] - raw.first_samp) / sfreq
    log = pd.DataFrame(
        {
            "idx": np.arange(len(events)),
            "onset_s": np.round(onsets, 6),
            "label": [labels[c] for c in events[:, 2]],
        }
    )
    log["trial_type"] = log["label"].map(trial_type)
    log["code"] = _codes_for(onsets, codes, sfreq)
    # MNE's reasons: BAD_ annotations, TOO_SHORT (past the data end), NO_DATA.
    reason = ["; ".join(map(str, d)) for d in epochs.drop_log]
    rej = reject(epochs, spec)
    kept_idx = np.flatnonzero([not r for r in reason])  # rows that became epochs
    ptp = np.full(len(events), np.nan)
    step = np.full(len(events), np.nan)
    for j, i in enumerate(kept_idx):
        reason[i] = rej.reasons[j]
        ptp[i], step[i] = rej.ptp_uv[j], rej.step_uv[j]
    for i in np.flatnonzero(~responded):
        reason[i] = NO_RESPONSE  # not in a bin (ERPLAB): not an artifact rejection
    log["kept"] = [not r for r in reason]
    log["reason"] = [r or "kept" for r in reason]
    log["ptp_uv"] = np.round(ptp, 3)
    log["step_uv"] = np.round(step, 3)
    bad = [j for j, i in enumerate(kept_idx) if rej.reasons[j] or not responded[i]]
    epochs.drop(bad, reason="eegunify", verbose=False)
    details = {
        "lock": eset.lock,
        "labels": sorted(chosen),
        "n_events": len(events),
        "n_no_response": int((~responded).sum()),
        "response": None if eset.response is None else eset.response.model_dump(mode="json"),
        "n_kept": int(log["kept"].sum()),
        "reject": {"method": spec.method, **rej.details},
    }
    return EpochSetResult(name, epochs, log, details)


def condition_counts(log: pd.DataFrame, conditions: dict[str, list[str]]) -> dict[str, int]:
    """Kept epochs per condition (a condition = trial-type prefix selectors, tag matching)."""
    kept = log[log["kept"]]
    return {
        cond: int(sum(any(matches(lab, s) for s in sels) for lab in kept["label"]))
        for cond, sels in conditions.items()
    }


def baseline_offset_uv(epochs: mne.BaseEpochs, window_ms: tuple[float, float]) -> float:
    """Mean over EEG channels of |mean of the average ERP in ``window_ms``| (µV)."""
    if not len(epochs):
        return float("nan")
    evoked = epochs.average(picks="eeg")
    lo, hi = (w / 1000 for w in window_ms)
    mask = (evoked.times >= lo) & (evoked.times <= hi)
    return float(np.abs(evoked.data[:, mask].mean(axis=1)).mean() * UV)
