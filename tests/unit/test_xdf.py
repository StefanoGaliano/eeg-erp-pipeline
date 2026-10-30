"""XDF specifics: clock synchronization, stream selection, rate drift."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eegunify.pipeline import default_config, read_recording
from tests.synthetic.generate import SFREQ, XDF_MARKER_CLOCK_OFFSET, Truth, write_xdf_recording


def test_marker_clock_offset_is_applied(tmp_path: Path, truth: Truth) -> None:
    """Markers come from a PC whose clock is 0.5 s behind; without LSL clock sync every event
    would be 256 samples early."""
    rec, _ = read_recording(write_xdf_recording(tmp_path / "x", truth))
    onsets = rec.events.table["onset"].to_numpy()
    assert np.abs(onsets - truth.samples / SFREQ).max() < 0.002
    assert XDF_MARKER_CLOCK_OFFSET * SFREQ == 256  # what the error would have been


def test_ambiguous_eeg_streams_need_a_hint(tmp_path: Path, truth: Truth) -> None:
    path = write_xdf_recording(tmp_path / "x", truth, extra_eeg_stream=True)
    with pytest.raises(ValueError, match=r"2 EEG streams match.*BrainAmp.*SecondAmp"):
        read_recording(path)
    cfg = default_config(path)
    cfg.format.hints.xdf_eeg_stream = {"name": "SecondAmp"}
    rec, _ = read_recording(path, cfg)
    assert [c for c in rec.raw.ch_names] == ["X1", "X2"]


def test_missing_marker_stream_is_optional(tmp_path: Path, truth: Truth) -> None:
    path = write_xdf_recording(tmp_path / "x", truth)
    cfg = default_config(path)
    cfg.format.hints.xdf_marker_stream = {"name": "does-not-exist"}
    rec, warnings = read_recording(path, cfg)
    assert rec.events.table.empty
    assert any(w.rule == 4 for w in warnings)


def test_rate_drift_is_resampled(tmp_path: Path, truth: Truth) -> None:
    """An amplifier clock running 0.5 % fast must be put back on the nominal 512 Hz grid."""
    path = write_xdf_recording(tmp_path / "x", truth, jitter_s=0.0, actual_rate=SFREQ * 1.005)
    rec, _ = read_recording(path)
    assert rec.raw.info["sfreq"] == SFREQ
    assert any("resampled onto a uniform grid" in n for n in rec.notes)
    # 60 s of data at a 0.5 % fast clock spans 59.7 s of real time.
    assert rec.raw.times[-1] == pytest.approx(60 / 1.005, abs=0.01)
