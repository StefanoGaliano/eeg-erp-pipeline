from pathlib import Path

import pytest

from eegunify.config import ConfigError, load_config, resolve_for_subject
from eegunify.config.loader import parse_config

MINIMAL = {
    "dataset": {"name": "x", "source_root": "sourcedata/x", "task": "faces"},
    "recordings": {"pattern": "sub-{subject}.bdf", "subjects": ["001", "002"]},
}


def test_minimal_config_gets_defaults() -> None:
    cfg = parse_config(MINIMAL)
    assert cfg.format.name == "auto"
    assert cfg.events.ignore_codes == [0]
    assert cfg.montage.on_missing == "warn"


def test_unknown_key_is_rejected_with_path() -> None:
    bad = {**MINIMAL, "events": {"mapingg": {}}}
    with pytest.raises(ConfigError, match=r"events\.mapingg"):
        parse_config(bad)


@pytest.mark.parametrize(
    ("section", "value", "msg"),
    [
        ("recordings", {"pattern": "no-placeholder.bdf", "subjects": ["1"]}, "subject"),
        ("recordings", {"pattern": "{subject}", "subjects": ["sub_1"]}, "alphanumeric"),
        ("events", {"mapping": {"1-10": "a", "5-20": "b"}}, "overlap"),
        ("events", {"mapping": {"9-3": "a"}}, "low > high"),
        ("units", {"expected_eeg_range_uV": [10, 1]}, "low < high"),
        ("montage", {"source": "file"}, "requires montage.file"),
    ],
)
def test_invalid_values(section: str, value: dict, msg: str) -> None:
    with pytest.raises(ConfigError, match=msg):
        parse_config({**MINIMAL, section: value})


def test_int_mapping_keys_accepted() -> None:
    cfg = parse_config({**MINIMAL, "events": {"mapping": {1: "face", "2-3": "car"}}})
    assert cfg.events.mapping == {"1": "face", "2-3": "car"}


def test_overrides_merge_per_subject() -> None:
    cfg = parse_config(
        {
            **MINIMAL,
            "channels": {"bads": {"sub-001": ["T8"]}},
            "overrides": {"sub-002": {"channels": {"bads": ["O2"]}, "reference": {"online": "Cz"}}},
        }
    )
    assert cfg.bads_for("001") == ["T8"]
    assert cfg.bads_for("002") == []
    sub2 = resolve_for_subject(cfg, "002")
    assert sub2.bads_for("002") == ["O2"]
    assert sub2.reference.online == "Cz"
    assert resolve_for_subject(cfg, "001") is cfg


def test_override_rejects_bad_sections() -> None:
    with pytest.raises(ConfigError, match="dataset"):
        parse_config({**MINIMAL, "overrides": {"sub-001": {"dataset": {"name": "y"}}}})


def test_load_config_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")
    p = tmp_path / "bad.yaml"
    p.write_text("dataset: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(p)
    p.write_text("- a list")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(p)
