"""Tier-2 formats on real files from mne-testing-data (pinned commit), ~40 MB, cached.

The strongest checks are cross-format: the same recording exported by the vendor software in
two formats must give the same canonical data and events.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eegunify.datasets import fetch_mne_testing
from eegunify.pipeline import convert, default_config, read_recording

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]

FILES = [
    "GDF/test_gdf_2.20.gdf",
    "GDF/test_gdf_1.25.gdf",
    "antio/CA_208/test_CA_208.cnt",
    "antio/CA_208/test_CA_208.evt",
    "antio/CA_208/test_CA_208.vhdr",
    "antio/CA_208/test_CA_208.vmrk",
    "antio/CA_208/test_CA_208.eeg",
    "CNT/scan41_short.cnt",
    "curry/test_bdf_stim_channel Curry 8.cdt",
    "curry/test_bdf_stim_channel Curry 8.cdt.dpa",
    "curry/test_bdf_stim_channel Curry 8.cdt.cef",
    "curry/test_bdf_stim_channel Curry 7.dap",
    "curry/test_bdf_stim_channel Curry 7.dat",
    "curry/test_bdf_stim_channel Curry 7.rs3",
    "curry/test_bdf_stim_channel Curry 7.cef",
    "BDF/test_bdf_stim_channel.bdf",
    "EGI/test_egi.mff",
]


@pytest.fixture(scope="module")
def mnedata(data_cache: Path) -> Path:
    root = data_cache / "mne-testing"
    try:
        fetch_mne_testing(FILES, root)
    except OSError as err:
        pytest.skip(f"cannot fetch mne-testing-data: {err}")
    return root


@pytest.mark.parametrize(
    ("rel", "fmt", "n_eeg"),
    [
        ("GDF/test_gdf_2.20.gdf", "gdf", 64),
        ("GDF/test_gdf_1.25.gdf", "gdf", 16),
        ("antio/CA_208/test_CA_208.cnt", "ant", 63),
        ("CNT/scan41_short.cnt", "neuroscan", 126),
        ("curry/test_bdf_stim_channel Curry 8.cdt", "curry", 3),
        ("curry/test_bdf_stim_channel Curry 7.dap", "curry", 3),
        ("EGI/test_egi.mff", "egi", 128),
    ],
)
def test_reads_canonically(mnedata: Path, rel: str, fmt: str, n_eeg: int) -> None:
    rec, _ = read_recording(mnedata / rel)
    assert rec.format == fmt
    assert len(rec.raw.copy().pick("eeg").ch_names) == n_eeg
    assert rec.units.plausible
    if fmt != "ant":  # the ANT test file only holds impedance-check markers (housekeeping)
        assert not rec.events.table.empty


def _same(a: Path, b: Path) -> None:
    ra, _ = read_recording(a)
    rb, _ = read_recording(b)
    assert ra.raw.ch_names == rb.raw.ch_names
    assert ra.raw.info["sfreq"] == rb.raw.info["sfreq"]
    np.testing.assert_allclose(ra.raw.get_data(), rb.raw.get_data(), atol=1e-7)  # 0.1 µV
    ta, tb = ra.events.table, rb.events.table
    assert ta["value"].astype(str).tolist() == tb["value"].astype(str).tolist()
    np.testing.assert_allclose(
        ta["onset"].to_numpy(float), tb["onset"].to_numpy(float), atol=1.5 / ra.raw.info["sfreq"]
    )


def test_ant_equals_its_brainvision_export(mnedata: Path) -> None:
    _same(mnedata / "antio/CA_208/test_CA_208.cnt", mnedata / "antio/CA_208/test_CA_208.vhdr")


@pytest.mark.parametrize("curry", ["Curry 8.cdt", "Curry 7.dap"])
def test_curry_equals_bdf_original(mnedata: Path, curry: str) -> None:
    """Curry's trigger-off code 50000 must not add events the BDF original doesn't have."""
    _same(
        mnedata / f"curry/test_bdf_stim_channel {curry}", mnedata / "BDF/test_bdf_stim_channel.bdf"
    )


def test_egi_keeps_every_event_type(mnedata: Path) -> None:
    """One stim channel per event type: reading only the first lost DIN2..DIN7."""
    rec, _ = read_recording(mnedata / "EGI/test_egi.mff")
    assert set(rec.events.table["value"]) == {"DIN1", "DIN2", "DIN3", "DIN4", "DIN5", "DIN7"}


def test_neuroscan_width_detected(mnedata: Path) -> None:
    """Header says 0 samples; int32 would give 2.6 V, int16 gives 40 µV."""
    rec, _ = read_recording(mnedata / "CNT/scan41_short.cnt")
    assert "int16" in rec.notes[0]
    assert 10 < rec.units.robust_sd_uV < 100


@pytest.mark.parametrize(
    "rel",
    [
        "GDF/test_gdf_2.20.gdf",
        "EGI/test_egi.mff",
        "CNT/scan41_short.cnt",
        "antio/CA_208/test_CA_208.cnt",
    ],
)
def test_converts_to_bids(mnedata: Path, tmp_path: Path, rel: str) -> None:
    src = mnedata / rel
    cfg = default_config(src)
    cfg.dataset.source_root = str(src.parent)
    cfg.recordings.pattern = src.name
    cfg.recordings.subjects = ["01"]
    cfg.dataset.task = "test"
    cfg.montage.on_missing = "ignore"
    [res] = convert(cfg, project_root=tmp_path)
    assert res.error is None, res.error
    assert res.output is not None and res.output.exists()
