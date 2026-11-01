"""README figure: ERP CORE N170, P3 and N2pc grand-average difference waves (v0.3.0).

Reads what the pipeline wrote; computes nothing new. For each component, give the project root
it was run in (``eegunify erp run code/configs/erpcore_<c>.yaml``, group stage included)::

    python code/scripts/erpcore_grand_average.py \\
        --root N170=<root> --root P3=<root> --root N2pc=<root> [--out docs/img/...png]

Each panel: the component's contrast at its primary ROI (pooled), every included subject's wave
(thin grey) and the grand average (blue), the measurement window shaded, from the component
library (``n170/p3/n2pc@erpcore``). Waves are low-passed at the configs' plotting cutoff
(``erp.plot_lowpass_hz``) for display only, as the subject figures.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mne
import numpy as np

from eegunify.config.loader import load_config
from eegunify.erp import paths
from eegunify.erp.components.library import resolve

PROJECT = Path(__file__).parents[2]
# dataviz reference palette: surface, text tokens, categorical slot 1 (validated, light mode)
SURFACE, TEXT, TEXT_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b2"
SERIES_1 = "#2a78d6"
MINUS, EN_DASH = "\u2212", "\u2013"  # typographic minus and range dash in the labels
TITLES = {
    "N170": f"N170: face {MINUS} car",
    "P3": f"P3: rare {MINUS} frequent",
    "N2pc": f"N2pc: contra {MINUS} ipsi",
}


def _lowpass(ev: mne.Evoked, hz: float | None) -> mne.Evoked:
    return ev if hz is None else ev.copy().filter(None, hz, verbose=False)


def panel(ax: plt.Axes, comp: str, root: Path) -> None:
    cfg = load_config(PROJECT / "code" / "configs" / f"erpcore_{comp.lower()}.yaml")
    [rc] = resolve(cfg.components, project_root=PROJECT).components.values()
    d = rc.definition
    assert d.contrast is not None
    chans = list(d.rois.primary)
    gdir = paths.group_dir(root, cfg)
    set_name = d.epoch_set
    hz = cfg.erp.plot_lowpass_hz
    subjects = []
    for sub in cfg.recordings.subjects:
        f = paths.stage_path(
            root, "erp", cfg, sub, description=set_name, suffix="ave", extension=".fif"
        )
        if not f.exists():
            continue
        ev = {e.comment: e for e in mne.read_evokeds(f, verbose=False)}[d.contrast]
        subjects.append(_lowpass(ev, hz))
    ga = {
        e.comment: e
        for e in mne.read_evokeds(
            gdir / f"task-{cfg.dataset.task}_desc-{set_name}_ave.fif", verbose=False
        )
    }[d.contrast]
    ga = _lowpass(ga, hz)
    t = ga.times * 1000
    lo, hi = d.window_ms
    ax.axvspan(lo, hi, color="#e9e8e3", lw=0, zorder=0)
    ax.axhline(0, color=TEXT_2, lw=0.8, zorder=1)
    ax.axvline(0, color=TEXT_2, lw=0.8, ls=(0, (2, 2)), zorder=1)
    for ev in subjects:
        ax.plot(t, ev.copy().pick(chans).data.mean(0) * 1e6, color=MUTED, lw=1.0, zorder=2)
    ax.plot(t, ga.copy().pick(chans).data.mean(0) * 1e6, color=SERIES_1, lw=2.0, zorder=3)
    ax.set_title(TITLES[comp], color=TEXT, fontsize=11, loc="left", pad=8)
    ax.text(
        0.02,
        0.03,
        f"{'/'.join(chans)}, n = {len(subjects)}",
        transform=ax.transAxes,
        color=TEXT_2,
        fontsize=8.5,
    )
    # window label inside the plot, under the top edge (above it collides with the title)
    ax.text(
        (lo + hi) / 2,
        0.98,
        f"{lo:g}{EN_DASH}{hi:g} ms",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="top",
        color=TEXT_2,
        fontsize=8,
    )
    ax.set_xlim(t[0], t[-1])
    ax.set_xlabel("time (ms)", color=TEXT_2, fontsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=TEXT_2, labelsize=8.5)
    ax.set_facecolor(SURFACE)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", action="append", required=True, help="COMPONENT=project root")
    ap.add_argument(
        "--out", type=Path, default=PROJECT / "docs" / "img" / "erpcore_grand_average.png"
    )
    args = ap.parse_args()
    roots = dict(r.split("=", 1) for r in args.root)
    comps = [c for c in ("N170", "P3", "N2pc") if c in roots]
    fig, axes = plt.subplots(1, len(comps), figsize=(3.6 * len(comps), 3.0), facecolor=SURFACE)
    for ax, comp in zip(np.atleast_1d(axes), comps, strict=True):
        panel(ax, comp, Path(roots[comp]))
    np.atleast_1d(axes)[0].set_ylabel("µV", color=TEXT_2, fontsize=9)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150, facecolor=SURFACE)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
