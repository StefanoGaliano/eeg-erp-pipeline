"""Preview of the downstream ERP phases (blueprint Phases 4-5) on canonical ERP CORE data.

Reads the canonical BIDS output of ``eegunify convert code/configs/erpcore_n170.yaml``,
applies a minimal ERP pipeline (0.1-30 Hz, average reference, -200..800 ms epochs, baseline,
±150 µV rejection) and plots faces vs cars at PO8 and their difference with ERP CORE's N170
window (110-150 ms). Event onsets already include ERP CORE's 26 ms monitor delay
(``events.onset_shift_ms`` in the config). Not part of the eegunify package: the real ERP
stages come later.

    python code/scripts/erp_preview.py --root . --subject 015 \
        --out docs/img/erpcore_n170_preview.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mne
import mne_bids

# Validated with the dataviz palette validator (categorical slots 1-2, light surface).
FACE, CAR = "#2a78d6", "#eb6834"
INK, INK_2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
N170_WINDOW = (0.110, 0.150)  # ERP CORE measurement window (Kappenman et al., 2021)
CHANNEL = "PO8"


def evokeds(root: Path, subject: str) -> dict[str, mne.Evoked]:
    bp = mne_bids.BIDSPath(
        subject=subject, session="N170", task="N170", datatype="eeg", root=root / "rawdata"
    )
    raw = mne_bids.read_raw_bids(bp, verbose="error").load_data(verbose="error")
    raw.filter(0.1, 30.0, verbose="error")
    raw.set_eeg_reference("average", verbose="error")
    events, ids = mne.events_from_annotations(raw, verbose="error")
    # read_raw_bids labels events "<trial_type>/<value>" (stimulus/face/12) because several
    # codes share one trial type; MNE's tag selection ("stimulus/face") matches them all.
    wanted = {k: v for k, v in ids.items() if k.startswith(("stimulus/face/", "stimulus/car/"))}
    epochs = mne.Epochs(
        raw,
        events,
        wanted,
        tmin=-0.2,
        tmax=0.8,
        baseline=(None, 0),
        reject=dict(eeg=150e-6),
        preload=True,
        verbose="error",
    )
    return {
        "face": epochs["stimulus/face"].average(),
        "car": epochs["stimulus/car"].average(),
    }


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.axhline(0, color=GRID, lw=1, zorder=0)
    ax.axvline(0, color=GRID, lw=1, zorder=0)
    ax.grid(axis="y", color=GRID, lw=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.set_xlabel("Time from stimulus (ms)", color=INK_2, fontsize=9)
    ax.axvspan(*(t * 1000 for t in N170_WINDOW), color=GRID, alpha=0.6, lw=0, zorder=0)


def plot(ev: dict[str, mne.Evoked], subject: str, out: Path) -> None:
    times = ev["face"].times * 1000
    face = ev["face"].copy().pick([CHANNEL]).data[0] * 1e6
    car = ev["car"].copy().pick([CHANNEL]).data[0] * 1e6
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(10, 3.8), sharey=True, layout="constrained", facecolor=SURFACE
    )
    for ax in (left, right):
        _style(ax)
    left.plot(times, face, color=FACE, lw=2, label=f"Faces (n={ev['face'].nave})")
    left.plot(times, car, color=CAR, lw=2, label=f"Cars (n={ev['car'].nave})")
    left.legend(frameon=False, fontsize=9, labelcolor=INK_2, loc="upper right")
    for y, name, color in ((face, "Faces", FACE), (car, "Cars", CAR)):
        left.annotate(
            name,
            (times[-1], y[-1]),
            xytext=(4, 0),
            textcoords="offset points",
            color=INK_2,
            fontsize=9,
            va="center",
        )
        left.plot(times[-1], y[-1], "o", color=color, ms=4)
    left.set_ylabel(f"Amplitude at {CHANNEL} (µV)", color=INK_2, fontsize=9)
    left.set_title("Faces vs cars", color=INK, fontsize=11, loc="left")

    diff = face - car
    right.plot(times, diff, color=INK, lw=2)
    right.set_title("Difference (faces \u2212 cars)", color=INK, fontsize=11, loc="left")
    right.text(
        N170_WINDOW[1] * 1000 + 8,
        right.get_ylim()[0] * 0.85,
        "N170 window\n110\u2013150 ms",
        color=INK_2,
        fontsize=8,
        va="bottom",
    )
    fig.suptitle(
        f"ERP CORE N170, sub-{subject}: canonical data from eegunify (preview of the ERP phases)",
        color=INK,
        fontsize=12,
        x=0.01,
        ha="left",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--subject", default="015")
    parser.add_argument("--out", type=Path, default=Path("docs/img/erpcore_n170_preview.png"))
    args = parser.parse_args()
    ev = evokeds(args.root, args.subject)
    plot(ev, args.subject, args.out)
    face = ev["face"].copy().pick([CHANNEL]).crop(*N170_WINDOW).data.mean() * 1e6
    car = ev["car"].copy().pick([CHANNEL]).crop(*N170_WINDOW).data.mean() * 1e6
    print(
        f"{CHANNEL} mean amplitude 110-150 ms: faces {face:.2f} µV, cars {car:.2f} µV, "
        f"difference {face - car:.2f} µV"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
