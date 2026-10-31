"""ERP config sections (M-E0): optional, validated, deep-merged per subject."""

from pathlib import Path

import pytest

from eegunify.config import ConfigError, load_config, resolve_for_subject
from eegunify.config.loader import parse_config

MINIMAL = {
    "dataset": {"name": "x", "source_root": "sourcedata/x", "task": "faces"},
    "recordings": {"pattern": "sub-{subject}.bdf", "subjects": ["001", "002"]},
}
CONFIGS = Path(__file__).parents[3] / "code" / "configs"


def test_phase1_configs_still_validate() -> None:
    cfg = load_config(CONFIGS / "erpcore_n170.yaml")
    assert cfg.reference.online.startswith("CMS/DRL")
    assert cfg.reference.scheme == "average"
    assert cfg.preproc.highpass_hz == 0.1
    assert cfg.artifacts.method == "ica"
    assert cfg.epochs.sets == {}
    assert cfg.components.use == []


def test_full_erp_block_validates() -> None:
    cfg = parse_config(
        {
            **MINIMAL,
            "reference": {"online": "Cz", "scheme": "channels", "channels": ["P9", "P10"]},
            "preproc": {"line_noise": {"method": "notch", "freqs": [50]}, "lowpass_hz": 30},
            "artifacts": {"eog_bipolar": {"VEOG": ["FP1", "VEOG_lower"]}},
            "epochs": {"sets": {"stim": {"lock": "stimulus/*"}}},
            "conditions": {"face": ["stimulus/face"], "car": ["stimulus/car"]},
            "contrasts": {"face_minus_car": {"plus": "face", "minus": "car"}},
            "components": {"use": ["n170"]},
            "group": {"cluster": {"enabled": True, "contrast": "face_minus_car", "tail": -1}},
        }
    )
    assert cfg.artifacts.eog_bipolar["VEOG"] == ("FP1", "VEOG_lower")
    assert cfg.epochs.sets["stim"].baseline_ms == (-200.0, 0.0)


@pytest.mark.parametrize(
    ("patch", "msg"),
    [
        ({"reference": {"scheme": "channels"}}, "requires reference.channels"),
        ({"reference": {"channels": ["P9"]}}, "only used with"),
        ({"preproc": {"highpass_hz": 1, "lowpass_hz": 0.5}}, "above preproc.highpass_hz"),
        ({"epochs": {"sets": {"ica": {"lock": "stimulus/*"}}}}, "reserved"),
        ({"epochs": {"sets": {"my-set": {"lock": "stimulus/*"}}}}, "letters and digits"),
        ({"epochs": {"sets": {"s": {"lock": "x", "tmin_ms": 100, "tmax_ms": 0}}}}, "tmax_ms"),
        ({"epochs": {"sets": {"s": {"lock": "x", "baseline_ms": [-500, 0]}}}}, "inside the epoch"),
        ({"contrasts": {"d": {"plus": "a", "minus": "b"}}}, "unknown condition 'a'"),
        ({"group": {"cluster": {"enabled": True}}}, "group.cluster.contrast"),
        ({"artifacts": {"ica": {"algorithm": "jade"}}}, r"artifacts\.ica\.algorithm"),
        ({"preproc": {"filtr": {}}}, r"preproc\.filtr"),
    ],
)
def test_invalid_erp_values(patch: dict, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        parse_config({**MINIMAL, **patch})


def test_overrides_deep_merge_nested_erp_sections() -> None:
    """BLUEPRINT_PHASES 16.4: a per-subject ICA exclusion must not reset sibling keys."""
    cfg = parse_config(
        {
            **MINIMAL,
            "artifacts": {"ica": {"random_state": 7, "iclabel": {"threshold": 0.9}}},
            "components": {"use": ["n170"], "overrides": {"n170": {"window_ms": [100, 160]}}},
            "overrides": {
                "sub-002": {
                    "artifacts": {"ica": {"exclude": [0, 3]}},
                    "components": {"overrides": {"n170": {"rois": {"primary": ["P8"]}}}},
                }
            },
        }
    )
    sub2 = resolve_for_subject(cfg, "002")
    assert sub2.artifacts.ica.exclude == [0, 3]
    assert sub2.artifacts.ica.random_state == 7
    assert sub2.artifacts.ica.iclabel.threshold == 0.9
    assert sub2.components.overrides["n170"] == {
        "window_ms": [100, 160],
        "rois": {"primary": ["P8"]},
    }
    assert resolve_for_subject(cfg, "001").artifacts.ica.exclude == []


def test_overrides_reject_group_section() -> None:
    with pytest.raises(ConfigError, match="group"):
        parse_config({**MINIMAL, "overrides": {"sub-001": {"group": {"tests": []}}}})
