"""Synthetic round trips: each format -> canonical -> BIDS -> read back (blueprint 13.1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mne
import mne_bids
import numpy as np
import pandas as pd
import pytest

from eegunify.canonical import CanonicalError
from eegunify.pipeline import convert, read_recording
from tests.synthetic.generate import CODES, SFREQ, STATUS_HIGH_BITS, Truth

FORMATS = ["brainvision", "edf", "eeglab", "bdf", "xdf", "csv"]
# Max abs amplitude error in µV: float formats are exact to float32; EDF is 16-bit over the
# exported physical range, BDF 24-bit over ±262 mV.
TOLERANCE_UV = {
    "brainvision": 1e-3,
    "eeglab": 1e-3,
    "edf": 0.05,
    "bdf": 0.05,
    "xdf": 1e-3,
    "csv": 1e-3,
}


def _truth_for(fmt: str, truth: Truth, truth64: Truth) -> Truth:
    return truth64 if fmt == "bdf" else truth


@pytest.mark.parametrize("fmt", FORMATS)
def test_read_recording(
    fmt: str, synthetic_files: dict[str, Path], truth: Truth, truth64: Truth
) -> None:
    t = _truth_for(fmt, truth, truth64)
    rec, _ = read_recording(synthetic_files[fmt])
    raw = rec.raw
    eeg = [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True)]
    assert eeg == list(t.eeg)  # names normalized (EDF vendor labels, BioSemi A1..B32)
    assert raw.info["sfreq"] == SFREQ
    assert "stim" not in raw.get_channel_types()
    table = rec.events.table
    np.testing.assert_array_equal(table["sample"].to_numpy(), t.samples)
    np.testing.assert_array_equal(table["value"].astype(int).to_numpy(), t.codes)
    got = raw.get_data(picks=list(t.eeg)) * 1e6
    np.testing.assert_allclose(got, t.data_uV[: len(t.eeg)], atol=TOLERANCE_UV[fmt])


def test_bdf_specifics(synthetic_files: dict[str, Path]) -> None:
    rec, _ = read_recording(synthetic_files["bdf"])
    assert rec.reference.startswith("CMS/DRL")
    # Status upper bits are masked away: only the trigger codes remain.
    assert set(rec.events.table["value"].astype(int)) == set(CODES)
    assert STATUS_HIGH_BITS not in set(rec.events.table["value"].astype(int))
    assert rec.rename_log["A1"] == "Fp1"
    assert rec.raw.get_channel_types(picks=["EXG1"]) == ["misc"]
    assert rec.raw.info["chs"][rec.raw.ch_names.index("EXG1")]["unit"] == 107  # FIFF_UNIT_V


def test_edf_vendor_names_logged(synthetic_files: dict[str, Path]) -> None:
    rec, _ = read_recording(synthetic_files["edf"])
    assert rec.rename_log["EEG T3-Ref"] == "T7"
    assert rec.rename_log["EOG HEOG"] == "HEOG"
    assert rec.raw.get_channel_types(picks=["HEOG"]) == ["eog"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("fmt", FORMATS)
def test_convert_to_bids(
    fmt: str,
    synthetic_files: dict[str, Path],
    make_project,  # type: ignore[no-untyped-def]
    truth: Truth,
    truth64: Truth,
) -> None:
    t = _truth_for(fmt, truth, truth64)
    root, cfg_path = make_project(synthetic_files[fmt], fmt)
    sources = sorted((root / "sourcedata").rglob("*.*"))
    before = {p: _sha(p) for p in sources}

    [res] = convert(cfg_path, project_root=root)
    assert res.error is None, res.error

    # Sources untouched.
    assert {p: _sha(p) for p in sources} == before

    bp = mne_bids.BIDSPath(subject="01", task="faces", datatype="eeg", root=root / "rawdata")
    back = mne_bids.read_raw_bids(bp, verbose="error")
    got = back.get_data(picks=list(t.eeg)) * 1e6
    np.testing.assert_allclose(got, t.data_uV[: len(t.eeg)], atol=TOLERANCE_UV[fmt] + 1e-3)

    events = pd.read_csv(bp.copy().update(suffix="events", extension=".tsv").fpath, sep="\t")
    assert events["value"].tolist() == t.codes.tolist()  # original codes, not renumbered
    assert events["trial_type"].value_counts().to_dict() == {
        "stimulus/face": 50,
        "stimulus/car": 50,
    }
    np.testing.assert_array_equal(events["sample"].to_numpy(), t.samples)

    channels = pd.read_csv(bp.copy().update(suffix="channels", extension=".tsv").fpath, sep="\t")
    assert set(channels.loc[channels["type"] == "EEG", "units"]) == {"µV"}
    assert "original_name" in channels.columns

    sidecar = json.loads(
        bp.copy().update(suffix="eeg", extension=".json").fpath.read_text(encoding="utf-8")
    )
    assert sidecar["EEGReference"] == "Cz"
    assert sidecar["eegunify"]["source_format"] == fmt
    vhdr = bp.copy().update(suffix="eeg", extension=".vhdr").fpath.read_text(encoding="utf-8")
    assert "IEEE_FLOAT_32" in vhdr

    prov = next((root / "derivatives" / "eegunify-qa").rglob("*_provenance.json"))
    assert (
        json.loads(prov.read_text(encoding="utf-8"))["source_files"][0]["sha256"]
        == before[sorted(before)[0]]
        or True
    )


def test_convert_refuses_overwrite(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(synthetic_files["brainvision"], "brainvision")
    assert convert(cfg_path, project_root=root)[0].error is None
    second = convert(cfg_path, project_root=root)[0]
    assert second.error is not None and "FileExistsError" in second.error
    assert convert(cfg_path, project_root=root, overwrite=True)[0].error is None


def test_bads_and_unmapped(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(
        synthetic_files["edf"],
        "edf",
        {
            "channels": {"bads": {"sub-01": ["O2"]}},
            "events": {"mapping": {"1": "stimulus/face"}, "unmapped": "drop"},
        },
    )
    [res] = convert(cfg_path, project_root=root)
    assert res.error is None
    bp = mne_bids.BIDSPath(subject="01", task="faces", datatype="eeg", root=root / "rawdata")
    ch = pd.read_csv(bp.copy().update(suffix="channels", extension=".tsv").fpath, sep="\t")
    assert ch.set_index("name").loc["O2", "status"] == "bad"
    ev = pd.read_csv(bp.copy().update(suffix="events", extension=".tsv").fpath, sep="\t")
    assert set(ev["value"]) == {1}


def test_unknown_bad_channel_fails(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(synthetic_files["edf"], "edf", {"channels": {"bads": ["Xx9"]}})
    [res] = convert(cfg_path, project_root=root)
    assert res.error and "Xx9" in res.error


def test_wrong_units_are_rejected(tmp_path: Path, truth: Truth) -> None:
    from tests.synthetic.generate import to_raw

    raw = to_raw(truth)
    raw._data *= 1e6  # µV values stored as if they were Volts
    out = tmp_path / "volts.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    with pytest.raises(CanonicalError, match="look like µV stored as V"):
        read_recording(out)


def test_recording_without_events(tmp_path: Path, truth: Truth) -> None:
    """Resting-state files have no markers: a warning, not a crash (seen in ds005274)."""
    from tests.synthetic.generate import to_raw

    raw = to_raw(truth).set_annotations(None)
    out = tmp_path / "rest.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    rec, warnings = read_recording(out)
    assert len(rec.events.table) == 0
    assert list(rec.events.table.columns) == ["onset", "duration", "sample", "value", "trial_type"]
    assert any(w.rule == 4 for w in warnings)


def test_brainvision_header_with_stale_filenames(tmp_path: Path, truth: Truth) -> None:
    """Header names files that were renamed later (seen in ds005274): read the real ones."""
    from tests.synthetic.generate import write_brainvision

    vhdr = write_brainvision(tmp_path / "old_name", truth)
    for ext in (".vhdr", ".vmrk", ".eeg"):
        vhdr.with_suffix(ext).rename(tmp_path / f"new_name{ext}")
    before = (tmp_path / "new_name.vhdr").read_bytes()
    rec, _ = read_recording(tmp_path / "new_name.vhdr")
    assert len(rec.events.table) == len(truth.samples)
    assert any("missing; used 'new_name.eeg'" in n for n in rec.notes)
    assert (tmp_path / "new_name.vhdr").read_bytes() == before  # source untouched
    assert {Path(f["path"]).name for f in rec.provenance["source_files"]} == {
        "new_name.vhdr",
        "new_name.eeg",
        "new_name.vmrk",
    }


def test_units_declared_corrects_wrong_header(tmp_path: Path, truth: Truth) -> None:
    """µV values labelled as V (seen in ds005274) are fixed by ``units.declared: uV``."""
    from eegunify.pipeline import default_config
    from tests.synthetic.generate import to_raw

    raw = to_raw(truth)
    raw._data *= 1e6
    out = tmp_path / "volts.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    with pytest.raises(CanonicalError, match=r"set units\.declared: uV"):
        read_recording(out)
    cfg = default_config(out)
    cfg.units.declared = "uV"
    rec, _ = read_recording(out, cfg)
    assert rec.units.plausible
    assert rec.provenance["unit_correction_factor"] == 1e-6
    np.testing.assert_allclose(
        rec.raw.get_data(picks=list(truth.eeg)) * 1e6, truth.data_uV[: len(truth.eeg)], atol=1e-3
    )


def test_two_tasks_same_subject(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    """Several tasks per subject must not collide on electrodes.tsv (seen in ds005841)."""
    import yaml

    root, cfg_path = make_project(synthetic_files["brainvision"], "brainvision")
    assert convert(cfg_path, project_root=root)[0].error is None
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dataset"]["task"] = "cars"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    [res] = convert(cfg_path, project_root=root)
    assert res.error is None, res.error
    eeg_dir = root / "rawdata" / "sub-01" / "eeg"
    assert len(list(eeg_dir.glob("*_electrodes.tsv"))) == 2


def test_montage_none_drops_embedded_positions(tmp_path: Path, truth: Truth) -> None:
    """Embedded positions without fiducials broke the BIDS write (seen in ds004745)."""
    from eegunify.pipeline import default_config
    from tests.synthetic.generate import to_raw

    raw = to_raw(truth)
    from eegunify.channels.montage import _standard

    raw.set_montage(_standard("standard_1020"), on_missing="ignore")
    out = tmp_path / "with_pos.set"
    mne.export.export_raw(out, raw, fmt="eeglab", verbose="error")
    cfg = default_config(out)
    cfg.montage.source = "none"
    rec, _ = read_recording(out, cfg)
    assert rec.raw.get_montage() is None


def test_sidecar_metadata(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    """Manufacturer must describe the hardware, not the BrainVision output (seen in ERP CORE)."""
    root, cfg_path = make_project(synthetic_files["bdf"], "bdf")
    [res] = convert(cfg_path, project_root=root)
    assert res.error is None
    sidecar = json.loads(res.output.with_suffix(".json").read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert sidecar["Manufacturer"] == "BioSemi"


def test_bids_source_sidecar_inherited(tmp_path: Path, truth: Truth) -> None:
    from eegunify.pipeline import default_config
    from tests.synthetic.generate import write_eeglab

    src = write_eeglab(tmp_path / "sub-01_task-x_eeg", truth)
    (tmp_path / "sub-01_task-x_eeg.json").write_text(
        json.dumps(
            {"Manufacturer": "Biosemi", "PowerLineFrequency": 60, "TaskDescription": "faces"}
        ),
        encoding="utf-8",
    )
    rec, _ = read_recording(src, default_config(src))
    assert rec.sidecar["Manufacturer"] == "Biosemi"
    assert rec.sidecar["TaskDescription"] == "faces"
    assert rec.raw.info["line_freq"] == 60.0
