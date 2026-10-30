"""Synthetic behavioural logs matching a ``Truth`` recording.

The stimulus PC's clock is offset (12.3 s) and drifts (20 ppm) relative to the EEG, with 1 ms
jitter, like a real PsychoPy/Presentation setup.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tests.synthetic.generate import CODES, SFREQ, Truth

OFFSET_S = 12.3
DRIFT = 20e-6


def log_times(truth: Truth, seed: int = 0) -> np.ndarray:
    """Stimulus times in the log's clock for each EEG event."""
    rng = np.random.default_rng(seed)
    eeg_t = truth.samples / SFREQ
    return (eeg_t - OFFSET_S) / (1 + DRIFT) + rng.normal(0, 0.001, eeg_t.size)


def trial_table(truth: Truth) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "condition": [CODES[int(c)] for c in truth.codes],
            "stim_file": [f"img_{i:03d}.png" for i in range(truth.codes.size)],
            "rt": np.round(0.4 + 0.001 * np.arange(truth.codes.size), 3),
        }
    )


def write_psychopy(path: Path, truth: Truth) -> Path:
    t = trial_table(truth)
    t.insert(0, "block", "main")
    t["stim.started"] = log_times(truth)
    practice = pd.DataFrame(
        {
            "block": ["practice"] * 3,
            "condition": ["face"] * 3,
            "stim_file": ["p.png"] * 3,
            "rt": [0.5] * 3,
            "stim.started": [-30.0, -29.0, -28.0],
        }
    )
    out = pd.concat([practice, t], ignore_index=True)
    out.to_csv(path, index=False)
    return path


def write_presentation(path: Path, truth: Truth) -> Path:
    t = trial_table(truth)
    times = np.round((log_times(truth) + 100) * 10_000).astype(int)  # 0.1 ms units
    lines = [
        "Scenario - faces",
        "Logfile written - 10/30/2026 12:00:00",
        "",
        "Subject\tTrial\tEvent Type\tCode\tTime\tTTime\tUncertainty\tDuration",
    ]
    lines += [
        f"S01\t{i + 1}\tPicture\t{c}\t{tm}\t0\t1\t0"
        for i, (c, tm) in enumerate(zip(t["stim_file"], times, strict=True))
    ]
    lines += ["", "Event Type\tCode\tType\tResponse", "Picture\tx\thit\t1"]
    path.write_text("\n".join(lines) + "\n")
    return path


def write_eprime(path: Path, truth: Truth) -> Path:
    t = trial_table(truth)
    text = f"{path.name}\n" + t.to_csv(sep="\t", index=False)
    path.write_bytes(text.encode("utf-16"))  # E-DataAid exports UTF-16 with a BOM
    return path
