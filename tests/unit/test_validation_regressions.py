"""Regressions found by the real-world validation run (docs/validation_results.md)."""

from __future__ import annotations

from pathlib import Path

import mne
import numpy as np
import pytest

from eegunify import datasets
from eegunify.config.schema import EventsSection
from eegunify.events.extract import _annotation_code, extract_events
from eegunify.pipeline import read_recording
from eegunify.qa.checks import detect_line_freq
from eegunify.validation import validate_entry
from tests.synthetic.generate import Truth, to_raw


@pytest.mark.parametrize(
    ("desc", "code"),
    [("STATUS/61694", 254), ("STATUS/61450", 10), ("STATUS/61440", 0), ("Stimulus/S  3", 3)],
)
def test_status_markers_masked(desc: str, code: int) -> None:
    """ds001810: BioSemi Status values kept as BrainVision markers (0xF0FE -> 254)."""
    assert _annotation_code(desc, 0xFF) == code


def test_empty_stim_channel_falls_back_to_markers(truth: Truth) -> None:
    """ds001810: a Status channel stored as a scaled voltage decodes to no events."""
    raw = to_raw(truth)
    stim = mne.io.RawArray(
        np.zeros((1, raw.n_times)),
        mne.create_info(["Status"], raw.info["sfreq"], "stim"),
        verbose="error",
    )
    raw.add_channels([stim], force_update_info=True)
    result = extract_events(raw, EventsSection())
    assert result.method == "annotations"
    assert len(result.table) == len(truth.samples)
    assert "Status" not in raw.ch_names


def test_line_freq_on_short_low_rate_data() -> None:
    """ds005028: coarse PSD resolution left no bin within ±1 Hz of 50 Hz and crashed."""
    info = mne.create_info(["Fz", "Cz", "Pz"], 160.0, "eeg")
    raw = mne.io.RawArray(
        np.random.default_rng(0).standard_normal((3, 300)) * 1e-5, info, verbose="error"
    )
    assert detect_line_freq(raw) is None


@pytest.mark.parametrize(("factor", "refused"), [(30.0, False), (1e6, True)])
def test_slightly_high_amplitude_is_a_warning(
    tmp_path: Path, truth: Truth, factor: float, refused: bool
) -> None:
    """ds005106 / ds007096: 1.5-2x above range is plausible EEG, not a unit error."""
    from eegunify.canonical import CanonicalError

    raw = to_raw(truth)
    raw._data *= factor
    out = tmp_path / "x.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    if refused:
        with pytest.raises(CanonicalError):
            read_recording(out)
    else:
        _, warnings = read_recording(out)
        assert any(w.rule == 1 and w.level == "warning" for w in warnings)


def test_too_large_is_skip_not_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def too_big(*args: object, **kwargs: object) -> None:
        raise datasets.TooLargeError("ds1 sub-01: 900 MB exceeds the 250 MB limit")

    monkeypatch.setattr(datasets, "fetch_openneuro", too_big)
    out = validate_entry({"id": "ds1", "source": "openneuro", "format": "bdf"}, tmp_path, 250)
    assert (out.status, out.stage) == ("SKIP", "skipped")


def test_events_from_bids_sidecar(tmp_path: Path, truth: Truth) -> None:
    """ds003645 / ds006768: no triggers in the file, events only in the BIDS events.tsv."""
    raw = to_raw(truth).set_annotations(None)
    out = tmp_path / "sub-01_task-x_eeg.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    sfreq = raw.info["sfreq"]
    rows = ["onset\tduration\tvalue\ttrial_type"] + [
        f"{s / sfreq:.6f}\tn/a\t{c}\tstim" for s, c in zip(truth.samples, truth.codes, strict=True)
    ]
    (tmp_path / "sub-01_task-x_events.tsv").write_text("\n".join(rows) + "\n")
    rec, _ = read_recording(out)
    assert rec.events.method == "bids_events:sub-01_task-x_events.tsv"
    np.testing.assert_array_equal(rec.events.table["sample"], truth.samples)
    assert rec.events.table["value"].astype(int).tolist() == truth.codes.tolist()
