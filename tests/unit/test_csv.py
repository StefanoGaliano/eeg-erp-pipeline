"""CSV / OpenBCI specifics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from eegunify.config.loader import ConfigError, parse_config
from eegunify.pipeline import default_config, read_recording
from tests.synthetic.generate import SFREQ, Truth, write_openbci

NAMES = ["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"]


def test_openbci_header_rate_and_names(tmp_path: Path, truth: Truth) -> None:
    path = write_openbci(tmp_path / "OpenBCI-RAW-2026", truth)
    cfg = default_config(path)
    cfg.format.hints.csv.channel_names = NAMES
    rec, _ = read_recording(path, cfg)
    assert rec.format == "csv"
    assert rec.raw.info["sfreq"] == SFREQ
    assert any("from file header" in n for n in rec.notes)
    assert rec.raw.ch_names == NAMES  # accelerometer, index and timestamp columns left out
    np.testing.assert_allclose(rec.raw.get_data() * 1e6, truth.data_uV[:8], atol=1e-3)


def test_openbci_without_names_keeps_pin_labels(tmp_path: Path, truth: Truth) -> None:
    path = write_openbci(tmp_path / "x", truth)
    rec, warnings = read_recording(path)
    assert rec.raw.ch_names == [f"EXG{i}" for i in range(1, 9)]
    assert any(w.rule == 2 for w in warnings)  # not 10-05 names: say so


def test_unknown_rate_is_an_error(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text("Fz,Cz\n1,2\n3,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"csv\.sfreq"):
        read_recording(p)


def test_explicit_columns_and_units(tmp_path: Path) -> None:
    p = tmp_path / "x.tsv"
    rng = np.random.default_rng(0)
    rows = ["junk\tA\tB\tC"] + [
        f"{i}\t{a}\t{b}\t9" for i, (a, b) in enumerate(rng.standard_normal((500, 2)) * 0.02)
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    cfg = default_config(p)
    hints = cfg.format.hints.csv
    hints.sfreq, hints.unit = 250.0, "mV"
    hints.channel_columns, hints.channel_names = ["A", "B"], ["Oz", "Pz"]
    rec, _ = read_recording(p, cfg)
    assert rec.raw.ch_names == ["Oz", "Pz"]
    assert 5 < rec.units.robust_sd_uV < 50  # 0.02 mV -> 20 µV


def test_csv_hints_validated() -> None:
    base = {
        "dataset": {"name": "x", "source_root": ".", "task": "t"},
        "recordings": {"pattern": "{subject}.csv", "subjects": ["1"]},
    }
    with pytest.raises(ConfigError, match="differ in length"):
        parse_config(
            {
                **base,
                "format": {
                    "hints": {"csv": {"channel_columns": ["a", "b"], "channel_names": ["Fz"]}}
                },
            }
        )
    with pytest.raises(ConfigError, match=r"csv\.sfrq"):
        parse_config({**base, "format": {"hints": {"csv": {"sfrq": 250}}}})
