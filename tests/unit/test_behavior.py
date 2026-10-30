"""Behavioural log alignment."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eegunify.config.loader import ConfigError, parse_config
from eegunify.config.schema import BehaviorSection
from eegunify.events.behavior import align, read_eprime, read_presentation, read_psychopy
from eegunify.pipeline import convert
from tests.synthetic.behavior_logs import (
    DRIFT,
    OFFSET_S,
    write_eprime,
    write_presentation,
    write_psychopy,
)
from tests.synthetic.generate import SFREQ, Truth


def _events(truth: Truth) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "onset": truth.samples / SFREQ,
            "duration": 0.0,
            "sample": truth.samples,
            "value": truth.codes,
            "trial_type": [f"stimulus/{c}" for c in truth.codes],
        }
    )


def _cfg(**align_on: object) -> BehaviorSection:
    return BehaviorSection.model_validate(
        {
            "source": "psychopy",
            "file": "x.csv",
            "columns": ["stim_file", "rt"],
            "align_on": {
                "eeg_trial_types": ["stimulus/*"],
                "beh_filter": "block == 'main'",
                **align_on,
            },
        }
    )


def test_aligns_by_time_despite_offset_and_drift(tmp_path: Path, truth: Truth) -> None:
    beh = read_psychopy(write_psychopy(tmp_path / "x.csv", truth))
    out, rep = align(_events(truth), beh, _cfg(), "psychopy", "x.csv")
    assert rep.method == "time" and rep.matched == 100
    assert rep.unmatched_eeg == rep.unmatched_beh == 0
    assert rep.offset_s == pytest.approx(OFFSET_S, abs=0.01)
    assert rep.drift_ppm == pytest.approx(DRIFT * 1e6, abs=15)
    assert rep.max_residual_ms is not None and rep.max_residual_ms < 5
    assert out["beh_stim_file"].tolist() == [f"img_{i:03d}.png" for i in range(100)]


def test_dropped_trigger_does_not_shift_later_trials(tmp_path: Path, truth: Truth) -> None:
    """Pairing by order would put every trial after #40 on the wrong log row."""
    beh = read_psychopy(write_psychopy(tmp_path / "x.csv", truth))
    events = _events(truth).drop(index=40).reset_index(drop=True)
    out, rep = align(events, beh, _cfg(), "psychopy", "x.csv")
    assert rep.matched == 99 and rep.unmatched_beh == 1
    expected = [f"img_{i:03d}.png" for i in range(100) if i != 40]
    assert out["beh_stim_file"].tolist() == expected


def test_order_pairing_without_times(tmp_path: Path, truth: Truth) -> None:
    beh = read_eprime(write_eprime(tmp_path / "x.txt", truth))
    cfg = BehaviorSection.model_validate(
        {"source": "eprime", "file": "x.txt", "columns": ["stim_file"]}
    )
    out, rep = align(_events(truth), beh, cfg, "eprime", "x.txt")
    assert rep.method == "order" and rep.matched == 100
    assert out["beh_stim_file"].iloc[-1] == "img_099.png"
    with pytest.raises(ValueError, match="by order"):
        align(_events(truth).iloc[:-1], beh, cfg, "eprime", "x.txt")


def test_presentation_reader(tmp_path: Path, truth: Truth) -> None:
    beh = read_presentation(write_presentation(tmp_path / "x.log", truth))
    assert len(beh) == 100  # the summary table after the blank line is not included
    assert beh["Code"].iloc[0] == "img_000.png"
    cfg = BehaviorSection.model_validate(
        {"source": "presentation", "file": "x.log", "columns": ["Code"]}
    )
    _, rep = align(_events(truth), beh, cfg, "presentation", "x.log")
    assert rep.method == "time" and rep.matched == 100
    assert rep.offset_s == pytest.approx(OFFSET_S - 100, abs=0.01)


def test_eprime_utf16(tmp_path: Path, truth: Truth) -> None:
    beh = read_eprime(write_eprime(tmp_path / "x.txt", truth))
    assert list(beh.columns) == ["condition", "stim_file", "rt"]


def test_behavior_config_needs_file() -> None:
    base = {
        "dataset": {"name": "x", "source_root": ".", "task": "t"},
        "recordings": {"pattern": "{subject}.vhdr", "subjects": ["1"]},
    }
    with pytest.raises(ConfigError, match=r"behavior\.file"):
        parse_config({**base, "behavior": {"source": "psychopy"}})


@pytest.mark.parametrize(("drop", "level"), [(None, "pass"), (40, "warn")])
def test_convert_with_behavior(
    synthetic_files: dict[str, Path], make_project, truth: Truth, drop: int | None, level: str
) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(
        synthetic_files["brainvision"],
        "brainvision",
        {
            "behavior": {
                "source": "psychopy",
                "file": "beh/sub-{subject}_log.csv",
                "columns": ["stim_file", "rt"],
                "align_on": {"eeg_trial_types": ["stimulus/*"], "beh_filter": "block == 'main'"},
            }
        },
    )
    beh_dir = root / "sourcedata" / "synth" / "beh"
    beh_dir.mkdir()
    log = write_psychopy(beh_dir / "sub-01_log.csv", truth)
    if drop is not None:  # a trial the log has but the EEG lost
        table = pd.read_csv(log)
        table.loc[table["stim_file"] == f"img_{drop:03d}.png", "stim.started"] += 3.7
        table.to_csv(log, index=False)
    [res] = convert(cfg_path, project_root=root)
    assert res.error is None, res.error
    events = pd.read_csv(res.output.with_name("sub-01_task-faces_events.tsv"), sep="\t")  # type: ignore[union-attr]
    assert {"beh_stim_file", "beh_rt"} <= set(events.columns)
    qa = json.loads(res.qa_report.with_suffix(".json").read_text(encoding="utf-8"))  # type: ignore[union-attr]
    check = next(c for c in qa["checks"] if c["id"] == "BEHAVIOR_ALIGNMENT")
    assert check["level"] == level
    if drop is not None:
        assert events["beh_stim_file"].isna().sum() == 1
        assert np.all(events["beh_stim_file"].dropna().str.match(r"img_\d{3}\.png"))
