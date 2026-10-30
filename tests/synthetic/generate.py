"""Synthetic recordings with known content, written in every supported format.

The signal is fully determined, so a round trip ``write -> eegunify -> BIDS -> read`` can be
checked exactly: each channel is a unique sine plus, on visual channels, 10 Hz alpha, and an
evoked "P1/N1" bump (+5 µV at 100 ms, -8 µV at 170 ms) after every face/car event.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import edfio
import mne
import numpy as np
import pybv

SFREQ = 512.0
DURATION = 60.0
N_EVENTS = 100
CODES = {1: "face", 2: "car"}
VISUAL = ("O1", "Oz", "O2", "PO7", "PO8", "P7", "P8")

#: 32 EEG channels in canonical names (includes every visual ROI channel).
EEG32 = (
    "Fp1",
    "Fp2",
    "F7",
    "F3",
    "Fz",
    "F4",
    "F8",
    "FC5",
    "FC1",
    "FC2",
    "FC6",
    "T7",
    "C3",
    "Cz",
    "C4",
    "T8",
    "CP5",
    "CP1",
    "CP2",
    "CP6",
    "P7",
    "P3",
    "Pz",
    "P4",
    "P8",
    "PO7",
    "PO3",
    "POz",
    "PO8",
    "O1",
    "Oz",
    "O2",
)
EOG = ("HEOG", "VEOG")


@dataclass(frozen=True)
class Truth:
    """What the reader must recover."""

    eeg: tuple[str, ...]
    eog: tuple[str, ...]
    samples: np.ndarray  # event onsets in samples
    codes: np.ndarray
    data_uV: np.ndarray  # (n_eeg + n_eog, n_times), µV, before any file quantization


def make_truth(eeg: tuple[str, ...] = EEG32, seed: int = 0) -> Truth:
    rng = np.random.default_rng(seed)
    n_times = int(SFREQ * DURATION)
    t = np.arange(n_times) / SFREQ
    names = eeg + EOG
    data = np.empty((len(names), n_times))
    for i, _name in enumerate(names):
        freq = 1.0 + 0.37 * i  # unique per channel
        data[i] = 20.0 * np.sin(2 * np.pi * freq * t + i)
    for i, name in enumerate(names):
        if name in VISUAL:
            data[i] += 10.0 * np.sin(2 * np.pi * 10.0 * t)

    # Events: evenly spread with jitter, never in the first or last second.
    base = np.linspace(SFREQ, n_times - 2 * SFREQ, N_EVENTS).astype(int)
    samples = base + rng.integers(0, int(0.1 * SFREQ), N_EVENTS)
    codes = np.tile([1, 2], N_EVENTS // 2)
    erp_t = np.arange(int(0.4 * SFREQ)) / SFREQ
    erp = 5.0 * np.exp(-(((erp_t - 0.100) / 0.015) ** 2)) - 8.0 * np.exp(
        -(((erp_t - 0.170) / 0.020) ** 2)
    )
    for s in samples:
        for i, name in enumerate(names):
            if name in VISUAL:
                data[i, s : s + erp.size] += erp
    return Truth(eeg, EOG, samples, codes, data)


def to_raw(truth: Truth, descriptions: dict[int, str] | None = None) -> mne.io.RawArray:
    """RawArray in Volts with events as annotations (description per code)."""
    descriptions = descriptions or {c: str(c) for c in CODES}
    names = list(truth.eeg + truth.eog)
    info = mne.create_info(names, SFREQ, ["eeg"] * len(truth.eeg) + ["eog"] * len(truth.eog))
    raw = mne.io.RawArray(truth.data_uV * 1e-6, info, verbose="error")
    raw.set_annotations(
        mne.Annotations(
            truth.samples / SFREQ,
            np.zeros(len(truth.samples)),
            [descriptions[int(c)] for c in truth.codes],
        )
    )
    return raw


def write_brainvision(path: Path, truth: Truth) -> Path:
    # pybv directly, with integer sample positions: mne.export goes through float annotation
    # onsets and truncates (554.9998 -> 554), which would corrupt the ground truth.
    pybv.write_brainvision(
        data=truth.data_uV * 1e-6,  # pybv takes Volts and scales to `unit`
        sfreq=SFREQ,
        ch_names=list(truth.eeg + truth.eog),
        fname_base=path.name,
        folder_out=path.parent,
        events=np.column_stack([truth.samples, truth.codes]),
        unit="µV",
        overwrite=True,
    )
    return path.with_suffix(".vhdr")


def write_edf(path: Path, truth: Truth, *, vendor_names: bool = True) -> Path:
    """EDF+ with annotations; by default with vendor-style labels (``EEG Fp1-Ref``, ``T3``)."""
    raw = to_raw(truth)
    if vendor_names:
        old = {"T7": "T3", "T8": "T4", "P7": "T5", "P8": "T6"}
        raw.rename_channels(
            {n: f"EEG {old.get(n, n).upper()}-Ref" for n in truth.eeg}
            | {"HEOG": "EOG HEOG", "VEOG": "EOG VEOG"}
        )
    out = path.with_suffix(".edf")
    mne.export.export_raw(out, raw, fmt="edf", verbose="error")
    return out


def write_eeglab(path: Path, truth: Truth) -> Path:
    raw = to_raw(truth)
    out = path.with_suffix(".set")
    mne.export.export_raw(out, raw, fmt="eeglab", verbose="error")
    return out


def biosemi64_truth(seed: int = 0) -> Truth:
    """Truth for a 64-channel BioSemi cap (10-20 names in BioSemi A1..B32 order)."""
    names = tuple(mne.channels.make_standard_montage("biosemi64").ch_names)
    return make_truth(names, seed)


STATUS_HIGH_BITS = 0x010000  # e.g. "CMS in range" flag that must be masked away


def write_bdf(path: Path, truth: Truth) -> Path:
    """BioSemi-style BDF: positional labels A1..B32, EXG1/EXG2 for EOG, and a Status channel
    whose upper bits are set (only the low 8 bits are triggers)."""
    n_times = truth.data_uV.shape[1]
    status = np.full(n_times, STATUS_HIGH_BITS, dtype=float)
    width = int(0.01 * SFREQ)  # 10 ms trigger pulses
    for s, c in zip(truth.samples, truth.codes, strict=True):
        status[s : s + width] += c
    labels = [f"{b}{i}" for b in "AB" for i in range(1, 33)][: len(truth.eeg)]
    labels += ["EXG1", "EXG2"]
    signals = [
        edfio.BdfSignal(
            row, SFREQ, label=label, physical_dimension="uV", physical_range=(-262144.0, 262143.0)
        )
        for row, label in zip(truth.data_uV, labels, strict=True)
    ]
    signals.append(
        edfio.BdfSignal(
            status,
            SFREQ,
            label="Status",
            physical_dimension="Boolean",
            physical_range=(-8388608.0, 8388607.0),
        )
    )
    out = path.with_suffix(".bdf")
    edfio.Bdf(signals, data_record_duration=1.0).write(out)
    return out


#: The marker PC's clock runs this far behind the EEG PC (true = marker timestamp + offset).
XDF_MARKER_CLOCK_OFFSET = 0.5
XDF_T0 = 1000.0  # LSL clocks start at an arbitrary value


def write_xdf_recording(
    path: Path,
    truth: Truth,
    *,
    jitter_s: float = 0.003,
    extra_eeg_stream: bool = False,
    actual_rate: float = SFREQ,
) -> Path:
    """LSL-style XDF: EEG stream (µV, jittered timestamps) + marker stream on another clock."""
    from tests.synthetic.xdf_writer import Stream, write_xdf

    rng = np.random.default_rng(1)
    n = truth.data_uV.shape[1]
    # ``actual_rate`` != SFREQ simulates an amplifier clock running fast or slow while the
    # header still claims SFREQ.
    true_t = XDF_T0 + np.arange(n) / actual_rate
    eeg_ts = true_t + rng.uniform(-jitter_s, jitter_s, n)
    names = list(truth.eeg + truth.eog)
    offsets = [(XDF_T0 + t, 0.0) for t in np.arange(0, DURATION, 5.0)]
    eeg = Stream(
        1,
        "BrainAmp",
        "EEG",
        SFREQ,
        "float32",
        eeg_ts,
        truth.data_uV.T,
        labels=names,
        units=["microvolts"] * len(names),
        types=["EEG"] * len(truth.eeg) + ["EOG"] * len(truth.eog),
        clock_offsets=offsets,
    )
    marker_true = XDF_T0 + truth.samples / SFREQ
    markers = Stream(
        2,
        "PsychoPyMarkers",
        "Markers",
        0.0,
        "string",
        marker_true - XDF_MARKER_CLOCK_OFFSET,
        [str(c) for c in truth.codes],
        clock_offsets=[(t - XDF_MARKER_CLOCK_OFFSET, XDF_MARKER_CLOCK_OFFSET) for t, _ in offsets],
    )
    streams = [eeg, markers]
    if extra_eeg_stream:
        streams.append(
            Stream(
                3,
                "SecondAmp",
                "EEG",
                SFREQ,
                "float32",
                true_t,
                truth.data_uV[:2].T,
                labels=["X1", "X2"],
                units=["microvolts"] * 2,
                types=["EEG"] * 2,
                clock_offsets=offsets,
            )
        )
    return write_xdf(path.with_suffix(".xdf"), streams)


WRITERS = {
    "brainvision": write_brainvision,
    "edf": write_edf,
    "eeglab": write_eeglab,
    "bdf": write_bdf,
    "xdf": write_xdf_recording,
}
