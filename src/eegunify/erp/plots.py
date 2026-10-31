"""Figures of the ERP stages (one per measured component).

Left panel: the contrast's conditions at the pooled primary ROI; right panel: the difference
wave; the measurement window is shaded. ``lowpass_hz`` filters copies for display only.
Colours were validated with the dataviz palette validator (categorical slots 1-2, light surface);
the README figure uses the same style.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mne
import numpy as np
from matplotlib.axes import Axes

COND_COLORS = ("#2a78d6", "#eb6834", "#1f9e6a", "#8a5cc2")
INK, INK_2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"


def _display(ev: mne.Evoked, chs: list[str], lowpass_hz: float | None) -> np.ndarray:
    ev = ev.copy().pick(chs)
    if lowpass_hz is not None and lowpass_hz < ev.info["sfreq"] / 2:
        # IIR: an FIR long enough for a 20 Hz cut-off can exceed a 1 s epoch.
        ev.filter(None, lowpass_hz, method="iir", verbose=False)
    out: np.ndarray = ev.data.mean(axis=0) * 1e6
    return out


def _style(ax: Axes, window_ms: tuple[float, float]) -> None:
    ax.set_facecolor(SURFACE)
    ax.axhline(0, color=GRID, lw=1, zorder=0)
    ax.axvline(0, color=GRID, lw=1, zorder=0)
    ax.grid(axis="y", color=GRID, lw=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.set_xlabel("Time from event (ms)", color=INK_2, fontsize=9)
    ax.axvspan(*window_ms, color=GRID, alpha=0.6, lw=0, zorder=0)


def component_figure(
    out: Path,
    *,
    title: str,
    conditions: dict[str, mne.Evoked],
    difference: tuple[str, mne.Evoked] | None,
    channels: list[str],
    window_ms: tuple[float, float],
    lowpass_hz: float | None,
) -> None:
    roi = "+".join(channels)
    ncols = 2 if difference is not None else 1
    fig, axes = plt.subplots(
        1,
        ncols,
        figsize=(5 * ncols, 3.8),
        sharey=True,
        layout="constrained",
        facecolor=SURFACE,
        squeeze=False,
    )
    left = axes[0, 0]
    _style(left, window_ms)
    for (name, ev), color in zip(conditions.items(), COND_COLORS, strict=False):
        times = ev.times * 1000
        y = _display(ev, channels, lowpass_hz)
        left.plot(times, y, color=color, lw=2, label=f"{name} (n={ev.nave})")
    left.legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="upper right")
    left.set_ylabel(f"Amplitude at {roi} (µV)", color=INK_2, fontsize=9)
    left.set_title("Conditions", color=INK, fontsize=11, loc="left")
    if difference is not None:
        name, ev = difference
        right = axes[0, 1]
        _style(right, window_ms)
        right.plot(ev.times * 1000, _display(ev, channels, lowpass_hz), color=INK, lw=2)
        right.set_title(f"Difference ({name})", color=INK, fontsize=11, loc="left")
        right.text(
            window_ms[1] + 8,
            right.get_ylim()[1] * 0.95,
            f"window\n{window_ms[0]:g}-{window_ms[1]:g} ms",
            color=INK_2,
            fontsize=8,
            va="top",
        )
    note = f"display low-pass {lowpass_hz:g} Hz" if lowpass_hz else "unfiltered display"
    fig.suptitle(f"{title}; {note}", color=INK, fontsize=12, x=0.01, ha="left")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
