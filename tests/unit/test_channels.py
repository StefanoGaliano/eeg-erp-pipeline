import pytest
from hypothesis import given
from hypothesis import strategies as st

from eegunify.channels.naming import (
    ChannelNameCollisionError,
    biosemi_positional_map,
    normalize_names,
    normalize_one,
    standard_1005_names,
)
from eegunify.channels.types import infer_type, infer_types
from eegunify.rois import missing_visual_channels

ALIASES = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EEG Fp1-Ref", "Fp1"),
        ("FP1", "Fp1"),
        ("OZ", "Oz"),
        ("poz", "POz"),
        (" O1 ", "O1"),
        ("EEG T3-REF", "T7"),
        ("T5", "P7"),
        ("t6", "P8"),
        ("PO8-AVG", "PO8"),
        ("EEG_Cz", "Cz"),
        ("HEOG", "HEOG"),
        ("E12", "E12"),
    ],
)
def test_normalize_one(raw: str, expected: str) -> None:
    assert normalize_one(raw, ALIASES) == expected


def test_collision_raises() -> None:
    with pytest.raises(ChannelNameCollisionError, match=r"T3.*T7|T7.*T3"):
        normalize_names(["T3", "T7"])


def test_rename_wins_and_protect() -> None:
    m = normalize_names(["EEG Fp1-Ref", "Status", "X1"], rename={"X1": "HEOG"}, protect=["Status"])
    assert m == {"EEG Fp1-Ref": "Fp1", "Status": "Status", "X1": "HEOG"}


def test_biosemi64_positional() -> None:
    names = [f"{b}{i}" for b in "AB" for i in range(1, 33)]
    m = biosemi_positional_map([*names, "EXG1"])
    assert m["A1"] == "Fp1" and m["A27"] == "O1" and m["B16"] == "Cz" and m["B32"] == "O2"
    assert len(set(m.values())) == 64
    # 128-channel caps keep positional names.
    assert biosemi_positional_map([f"{b}{i}" for b in "ABCD" for i in range(1, 33)]) == {}


@given(st.permutations(list(standard_1005_names()[:40])))
def test_standard_names_are_fixed_points(names: list[str]) -> None:
    m = normalize_names(names)
    assert all(k == v for k, v in m.items() if k not in ALIASES)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Fp1", "eeg"),
        ("HEOG", "eog"),
        ("VEOG_lower", "eog"),
        ("LO1", "eog"),
        ("EMG_chin", "emg"),
        ("ECG", "ecg"),
        ("Status", "stim"),
        ("STI 014", "misc"),
        ("STI014", "stim"),
        ("A12", "eeg"),
        ("E101", "eeg"),
        ("EXG1", "misc"),
        ("Resp", "resp"),
        ("foo", "misc"),
    ],
)
def test_infer_type(name: str, expected: str) -> None:
    assert infer_type(name) == expected


def test_configured_type_wins() -> None:
    assert infer_types(["EXG1", "Fp1"], configured={"EXG1": "eog"}) == {"EXG1": "eog", "Fp1": "eeg"}


def test_missing_visual_channels() -> None:
    miss = missing_visual_channels(["O1", "Oz", "O2", "P7", "PO7", "P8"], bads=["O2"])
    assert miss == {"occipital": ["O2"], "lateral_occipitotemporal_right": ["PO8"]}
