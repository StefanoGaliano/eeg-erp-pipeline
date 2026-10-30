"""QA checks fire on deliberately broken synthetic data (blueprint step 12)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from eegunify.canonical import Recording
from eegunify.cli import app
from eegunify.config.schema import Config
from eegunify.pipeline import convert, default_config, read_recording
from eegunify.qa import run_checks
from eegunify.qa.report import update_summary, write_report
from tests.synthetic.generate import SFREQ

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


@pytest.fixture
def rec_cfg(synthetic_files: dict[str, Path]) -> Callable[..., tuple[Recording, Config]]:
    def _make(**events: object) -> tuple[Recording, Config]:
        path = synthetic_files["brainvision"]
        cfg = default_config(path)
        cfg.reference.online = "Cz"
        cfg.events.mapping = {"1": "stimulus/face", "2": "stimulus/car"}
        for key, val in events.items():
            setattr(cfg.events, key, val)
        rec, _ = read_recording(path, cfg)
        return rec, cfg

    return _make


def _ch(rec: Recording, name: str) -> int:
    return rec.raw.ch_names.index(name)


def test_clean_recording_passes(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg(expected_counts={"stimulus/face": 50, "stimulus/car": 50})
    result = run_checks(rec, cfg)
    assert result.status == "pass", [c for c in result.checks if c.level != "pass"]
    ids = {c.id for c in result.checks}
    assert {"UNITS_RANGE", "CHANNEL_FLAT", "ROI_MISSING", "EVENTS_EXPECTED_COUNT"} <= ids


def test_flat_channel(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    rec.raw._data[_ch(rec, "O2")] = 0.0
    check = run_checks(rec, cfg).by_id("CHANNEL_FLAT")
    assert check.level == "warn" and check.value == ["O2"]


def test_missing_visual_channel(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    rec.raw.drop_channels(["PO8"])
    check = run_checks(rec, cfg).by_id("ROI_MISSING")
    assert check.level == "warn"
    assert check.value == {"lateral_occipitotemporal_right": ["PO8"]}


def test_bad_visual_channel_counts_as_missing(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    rec.raw.info["bads"] = ["Oz"]
    assert run_checks(rec, cfg).by_id("ROI_MISSING").value == {"occipital": ["Oz"]}


def test_wrong_event_counts_fail(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg(expected_counts={"stimulus/face": 80, "stimulus/car": 50})
    result = run_checks(rec, cfg)
    check = result.by_id("EVENTS_EXPECTED_COUNT")
    assert check.level == "fail"
    assert check.value == {"stimulus/face": {"expected": 80, "found": 50}}
    assert result.status == "fail"


def test_count_tolerance(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg(expected_counts={"stimulus/face": 52}, count_tolerance=0.05)
    assert run_checks(rec, cfg).by_id("EVENTS_EXPECTED_COUNT").level == "pass"


@pytest.mark.parametrize(
    ("factor", "level", "diagnosis"),
    [(1e6, "fail", "µV labelled as V"), (1e3, "fail", "mV labelled as V"), (60, "warn", "")],
)
def test_units_range(rec_cfg, factor: float, level: str, diagnosis: str) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    rec.raw._data *= factor
    check = run_checks(rec, cfg).by_id("UNITS_RANGE")
    assert check.level == level
    assert diagnosis in check.message


def test_noisy_and_clipped(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    rng = np.random.default_rng(0)
    rec.raw._data[_ch(rec, "Fp1")] += rng.standard_normal(rec.raw.n_times) * 500e-6
    cz = rec.raw._data[_ch(rec, "Cz")]
    rec.raw._data[_ch(rec, "Cz")] = np.clip(cz, -10e-6, 10e-6)  # amplifier saturation
    result = run_checks(rec, cfg)
    assert result.by_id("CHANNEL_NOISY").value == ["Fp1"]
    assert "Cz" in result.by_id("CHANNEL_CLIPPED").value


@pytest.mark.parametrize("freq", [50.0, 60.0])
def test_line_noise_detected(rec_cfg, freq: float) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    t = np.arange(rec.raw.n_times) / SFREQ
    rec.raw._data[:] += 5e-6 * np.sin(2 * np.pi * freq * t)
    assert run_checks(rec, cfg).line_freq == freq


def test_no_line_noise(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    assert run_checks(rec, cfg).by_id("LINE_NOISE").value is None


def test_channel_table(rec_cfg) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg()
    table = run_checks(rec, cfg).channels
    assert list(table.columns) == [
        "name",
        "original_name",
        "type",
        "unit",
        "position",
        "status",
        "robust_sd_uV",
        "flat",
        "clipped",
    ]
    assert table.set_index("name").loc["HEOG", "type"] == "eog"
    assert table["position"][table["type"] == "eeg"].all()


def test_report_and_summary(rec_cfg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rec, cfg = rec_cfg(expected_counts={"stimulus/face": 80})
    result = run_checks(rec, cfg)
    html = write_report(rec, result, tmp_path / "sub-01", "sub-01_task-x")
    assert html.exists() and "EVENTS_EXPECTED_COUNT" in html.read_text()
    data = json.loads((tmp_path / "sub-01" / "sub-01_task-x_qa.json").read_text())
    assert data["status"] == "FAIL"
    assert {c["id"] for c in data["checks"]} >= {"UNITS_RANGE", "LINE_NOISE"}
    assert data["event_counts"] == {"stimulus/face": 50, "stimulus/car": 50}

    summary = tmp_path / "summary.tsv"
    update_summary(summary, rec, result, "sub-01_task-x", html)
    update_summary(summary, rec, result, "sub-01_task-x", html)  # replaces, not appends
    update_summary(summary, rec, result, "sub-02_task-x", html)
    rows = pd.read_csv(summary, sep="\t")
    assert rows["recording"].tolist() == ["sub-01_task-x", "sub-02_task-x"]
    assert rows.loc[0, "failed_checks"] == "EVENTS_EXPECTED_COUNT"
    assert rows.loc[0, "report"] == "sub-01/sub-01_task-x_qa.html"


def test_convert_writes_qa(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(synthetic_files["bdf"], "bdf")
    [res] = convert(cfg_path, project_root=root)
    assert res.error is None and res.qa_status in ("PASS", "WARN")
    assert res.qa_report is not None and res.qa_report.exists()
    summary = pd.read_csv(root / "derivatives" / "eegunify-qa" / "summary.tsv", sep="\t")
    assert summary["recording"].tolist() == ["sub-01_task-faces"]


def test_cli_qa_only_and_fail_exit(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg = make_project(
        synthetic_files["edf"], "edf", {"events": {"expected_counts": {"stimulus/face": 999}}}
    )
    runner = CliRunner()
    res = runner.invoke(app, ["qa", str(cfg), "--root", str(root)])
    assert res.exit_code == 1, res.output  # QA FAIL -> exit 1
    assert not (root / "rawdata").exists()  # qa never writes BIDS
    assert (root / "derivatives" / "eegunify-qa" / "summary.tsv").exists()
    res = runner.invoke(app, ["convert", str(cfg), "--root", str(root), "--no-qa"])
    assert res.exit_code == 0, res.output
