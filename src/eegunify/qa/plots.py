"""QA figures. All functions return a matplotlib Figure and never call ``plt.show``."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import mne
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from eegunify.qa.checks import MAX_SECONDS
from eegunify.rois import VISUAL_ROIS

_ALPHA = (8.0, 12.0)


def psd_figure(raw: mne.io.BaseRaw, line_freq: float | None) -> Figure:
    """Welch PSD 1-80 Hz of good EEG channels; occipital ROI highlighted, alpha band shaded."""
    fmax = min(80.0, raw.info["sfreq"] / 2 - 1)
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
    if len(picks):
        tmax = min(raw.times[-1], MAX_SECONDS)
        spec = raw.compute_psd(fmin=1.0, fmax=fmax, tmax=tmax, picks=picks, verbose="error")
        db = 10 * np.log10(spec.get_data() * 1e12)  # µV²/Hz in dB
        names = [raw.ch_names[i] for i in picks]
        occ = [
            i
            for i, n in enumerate(names)
            if n in VISUAL_ROIS["occipital"] + VISUAL_ROIS["parieto_occipital"]
        ]
        ax.plot(spec.freqs, db.T, color="0.75", lw=0.6)
        if occ:
            ax.plot(
                spec.freqs,
                db[occ].mean(axis=0),
                color="C3",
                lw=2,
                label="occipital / parieto-occipital mean",
            )
        ax.plot(spec.freqs, db.mean(axis=0), color="k", lw=1.5, label="all EEG mean")
        ax.legend(loc="upper right", frameon=False)
    ax.axvspan(*_ALPHA, color="C1", alpha=0.15, label="alpha")
    if line_freq:
        ax.axvline(line_freq, color="C0", ls="--", lw=1)
        ax.text(line_freq, ax.get_ylim()[1], f" {line_freq:g} Hz", va="top", color="C0")
    ax.set(
        xlabel="Frequency (Hz)",
        ylabel="Power (dB re 1 µV²/Hz)",
        title="Power spectrum (alpha band shaded)",
    )
    return fig


def sensor_figure(raw: mne.io.BaseRaw, missing: list[str]) -> Figure:
    """2D sensor layout; channels without positions are listed in red."""
    fig, ax = plt.subplots(figsize=(5, 5), layout="constrained")
    eeg = raw.copy().pick("eeg", exclude=[])
    with_pos = [n for n in eeg.ch_names if n not in missing]
    if with_pos:
        eeg.pick(with_pos).plot_sensors(axes=ax, show_names=True, show=False)
    else:
        ax.set_axis_off()
    if missing:
        ax.text(
            0.0,
            -0.05,
            "no position: " + ", ".join(missing[:20]),
            color="C3",
            transform=ax.transAxes,
            fontsize=8,
            wrap=True,
        )
    ax.set_title("Electrode positions")
    return fig


def event_figure(events: pd.DataFrame) -> Figure:
    """Raster of events over time, one row per trial type."""
    fig, ax = plt.subplots(figsize=(8, 2.5), layout="constrained")
    if events.empty:
        ax.text(0.5, 0.5, "no events", ha="center", va="center", transform=ax.transAxes)
    else:
        types = sorted(events["trial_type"].unique())
        for i, tt in enumerate(types):
            onsets = events.loc[events["trial_type"] == tt, "onset"]
            ax.vlines(onsets, i - 0.4, i + 0.4, color=f"C{i % 10}", lw=0.8)
        ax.set_yticks(range(len(types)), types)
    ax.set(xlabel="Time (s)", title="Events over time")
    return fig


def roi_trace_figure(raw: mne.io.BaseRaw, seconds: float = 10.0) -> Figure:
    """First ``seconds`` of the visual ROI channels (1 Hz high-passed copy)."""
    roi = [
        n
        for n in dict.fromkeys(
            VISUAL_ROIS["occipital"] + VISUAL_ROIS["parieto_occipital"] + ("P7", "P8")
        )
        if n in raw.ch_names
    ]
    fig, ax = plt.subplots(figsize=(8, 4), layout="constrained")
    if not roi:
        ax.text(
            0.5, 0.5, "no visual ROI channels", ha="center", va="center", transform=ax.transAxes
        )
        return fig
    stop = min(raw.n_times, int(seconds * raw.info["sfreq"]))
    seg = raw.copy().pick(roi).crop(tmax=(stop - 1) / raw.info["sfreq"])
    seg.filter(1.0, None, verbose="error")
    data = seg.get_data() * 1e6
    spacing = max(float(np.percentile(np.abs(data), 99)) * 2, 1.0)
    for i in range(len(roi)):
        ax.plot(seg.times, data[i] - i * spacing, lw=0.6, color="k")
    ax.set_yticks([-i * spacing for i in range(len(roi))], roi)
    ax.set(
        xlabel="Time (s)",
        title=f"Visual ROI channels, first {seconds:g} s "
        f"(1 Hz high-pass, spacing {spacing:.0f} µV)",
    )
    return fig
