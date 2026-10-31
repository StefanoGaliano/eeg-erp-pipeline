"""Synthetic ERP recordings with known content (docs/BLUEPRINT_PHASES.md section 8.1).

``make_erp_recording`` returns an :class:`ErpTruth`: 32 EEG channels (10-20 positions) plus
VEOG/HEOG at 500 Hz. The signal is pink noise and occipital alpha, with these added:

- an N170-like negativity (Gaussian, 130 ms, sigma 15 ms) whose topography is a Gaussian
  centred on PO8; face -4 µV, car -1 µV at PO8;
- 50 Hz line noise on every EEG channel (amplitude varies by channel);
- one dead channel (``truth.dead``: flat, 0.01 µV noise) and one noisy channel
  (``truth.noisy``: +60 µV white noise);
- optional blinks: a frontal topography plus VEOG, at known samples.

Each component is also stored separately, so tests can check what a stage removed or kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pybv

from eegunify.channels.montage import _standard
from tests.synthetic.generate import EEG32, EOG

ERP_SFREQ = 500.0
LINE_HZ = 50.0
N170_MS, N170_SIGMA_MS = 130.0, 15.0
AMPLITUDE_UV = {"face": -4.0, "car": -1.0}
CODES = {"face": 1, "car": 2}


@dataclass
class ErpTruth:
    names: list[str]
    types: list[str]
    sfreq: float
    data_uV: np.ndarray
    """Everything summed, (n_channels, n_times), µV."""
    samples: np.ndarray
    conditions: list[str]
    topography: np.ndarray
    """Weight of the N170 at each channel (1 at PO8)."""
    line_uV: np.ndarray
    """The line-noise part alone."""
    dead: str = "C4"
    noisy: str = "FC6"
    blink_samples: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    blink_uV: np.ndarray | None = None

    @property
    def codes(self) -> np.ndarray:
        return np.array([CODES[c] for c in self.conditions])

    def index(self, ch: str) -> int:
        return self.names.index(ch)


def _pink(rng: np.random.Generator, n_ch: int, n: int) -> np.ndarray:
    spec = rng.standard_normal((n_ch, n // 2 + 1)) + 1j * rng.standard_normal((n_ch, n // 2 + 1))
    f = np.fft.rfftfreq(n)
    spec[:, 1:] /= np.sqrt(f[1:])
    spec[:, 0] = 0
    x = np.fft.irfft(spec, n)
    return x / x.std(axis=1, keepdims=True)


def _positions() -> dict[str, np.ndarray]:
    mont = _standard("standard_1020")  # colin27 fallback (MNE 1.13 deprecation)
    pos = mont.get_positions()["ch_pos"]
    return {ch: pos[ch] for ch in EEG32}


def make_erp_recording(
    duration_s: float = 120.0,
    *,
    seed: int = 0,
    blinks: bool = False,
    dead: bool = True,
    noisy: bool = True,
    line_uV: float = 20.0,
    spatial_width_m: float = 0.08,
    independent_uV: float = 0.3,
    topo_width_m: float = 0.05,
) -> ErpTruth:
    rng = np.random.default_rng(seed)
    n = int(duration_s * ERP_SFREQ)
    t = np.arange(n) / ERP_SFREQ
    names = list(EEG32) + list(EOG)
    types = ["eeg"] * len(EEG32) + ["eog"] * len(EOG)
    n_eeg = len(EEG32)
    pos = _positions()
    # Spatially smooth background (neighbouring electrodes correlate, as in real EEG; pyprep's
    # correlation and RANSAC criteria rely on that) plus a little independent noise.
    xyz = np.array([pos[ch] for ch in EEG32])
    dist = np.linalg.norm(xyz[:, None] - xyz[None], axis=-1)
    mix = np.exp(-((dist / spatial_width_m) ** 2))
    mix /= np.linalg.norm(mix, axis=1, keepdims=True)
    data = np.empty((len(names), n))
    data[:n_eeg] = 3.0 * mix @ _pink(rng, n_eeg, n) + independent_uV * rng.standard_normal(
        (n_eeg, n)
    )
    data[n_eeg:] = 3.0 * _pink(rng, len(EOG), n)
    # Posterior alpha with a smooth topography (peak 3 µV at Oz).
    d_oz = np.array([np.linalg.norm(pos[ch] - pos["Oz"]) for ch in EEG32])
    data[:n_eeg] += 3.0 * np.exp(-((d_oz / 0.08) ** 2))[:, None] * np.sin(2 * np.pi * 10.0 * t)

    d = np.array([np.linalg.norm(pos[ch] - pos["PO8"]) for ch in EEG32])
    topo = np.zeros(len(names))
    topo[:n_eeg] = np.exp(-((d / topo_width_m) ** 2))

    # One event every 0.9-1.1 s, face/car shuffled, never in the first/last 1.5 s.
    onsets = np.arange(1.5, duration_s - 1.5, 1.0) + rng.uniform(-0.1, 0.1, int(duration_s - 3))
    samples = (onsets[onsets < duration_s - 1.5] * ERP_SFREQ).astype(int)
    conditions = list(rng.permutation(["face", "car"] * (len(samples) // 2 + 1))[: len(samples)])
    erp_t = np.arange(int(0.4 * ERP_SFREQ)) / ERP_SFREQ * 1000
    wave = np.exp(-(((erp_t - N170_MS) / N170_SIGMA_MS) ** 2) / 2)
    for s, c in zip(samples, conditions, strict=True):
        data[:, s : s + wave.size] += AMPLITUDE_UV[c] * np.outer(topo, wave)

    line = np.zeros_like(data)
    gains = rng.uniform(0.5, 1.5, n_eeg)
    line[:n_eeg] = line_uV * gains[:, None] * np.sin(2 * np.pi * LINE_HZ * t)
    data += line

    truth = ErpTruth(names, types, ERP_SFREQ, data, samples, conditions, topo, line)
    if blinks:
        frontal = np.zeros(len(names))
        for ch, w in {
            "Fp1": 1.0,
            "Fp2": 1.0,
            "F3": 0.5,
            "Fz": 0.5,
            "F4": 0.5,
            "F7": 0.6,
            "F8": 0.6,
        }.items():
            frontal[names.index(ch)] = w
        frontal[names.index("VEOG")] = 1.5
        bt = np.arange(int(0.4 * ERP_SFREQ)) / ERP_SFREQ
        shape = 100.0 * np.exp(-(((bt - 0.2) / 0.05) ** 2))
        bs = (np.arange(3.0, duration_s - 3, 4.3) * ERP_SFREQ).astype(int)
        blink = np.zeros_like(data)
        for s in bs:
            blink[:, s : s + shape.size] += np.outer(frontal, shape)
        data += blink
        truth.blink_samples, truth.blink_uV = bs, blink
    if dead:
        data[truth.index(truth.dead)] = 0.01 * rng.standard_normal(n)
    if noisy:
        data[truth.index(truth.noisy)] += 60.0 * rng.standard_normal(n)
    return truth


def write_erp_brainvision(folder: Path, truth: ErpTruth, stem: str = "sub-01_task-faces") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pybv.write_brainvision(
        data=truth.data_uV * 1e-6,  # Volts
        sfreq=truth.sfreq,
        ch_names=truth.names,
        fname_base=stem,
        folder_out=folder,
        events=np.column_stack([truth.samples, truth.codes]),
        unit="µV",
        overwrite=True,
    )
    return folder / f"{stem}.vhdr"


def erp_config(**sections: dict) -> dict:
    """Config dict for a project made by :func:`write_erp_brainvision` in sourcedata/synth."""
    cfg: dict = {
        "dataset": {
            "name": "Synthetic ERP",
            "source_root": "sourcedata/synth",
            "task": "faces",
            "authors": ["Test"],
            "license": "CC0",
        },
        "recordings": {"pattern": "sub-{subject}_task-faces.vhdr", "subjects": ["01"]},
        "reference": {"online": "Cz"},
        "montage": {"source": "standard", "standard_name": "standard_1020"},
        "channels": {"types": {"HEOG": "eog", "VEOG": "eog"}},
        "events": {"mapping": {"1": "stimulus/face", "2": "stimulus/car"}, "unmapped": "error"},
    }
    for key, val in sections.items():
        cfg[key] = {**cfg.get(key, {}), **val}
    return cfg
