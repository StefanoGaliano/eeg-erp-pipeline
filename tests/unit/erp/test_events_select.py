"""Finding F1: trial-type prefixes select every `trial_type/value` label of that type."""

import pytest

from eegunify.erp.events import matches, select

EVENT_ID = {
    "stimulus/face/12": 12,
    "stimulus/face/13": 13,
    "stimulus/car": 41,
    "stimulus/scrambled_face/101": 101,
    "response/correct": 201,
}


def test_trial_type_with_several_codes_fully_selected() -> None:
    assert select(EVENT_ID, "stimulus/face") == {"stimulus/face/12": 12, "stimulus/face/13": 13}


def test_tags_are_whole_parts_not_substrings() -> None:
    assert not matches("stimulus/scrambled_face/101", "stimulus/face")


@pytest.mark.parametrize("sel", ["stimulus/*", "stimulus"])
def test_wildcard_prefix(sel: str) -> None:
    assert set(select(EVENT_ID, sel)) == {k for k in EVENT_ID if k.startswith("stimulus/")}


def test_several_selectors_and_unknown() -> None:
    assert set(select(EVENT_ID, ["stimulus/car", "response"])) == {
        "stimulus/car",
        "response/correct",
    }
    with pytest.raises(KeyError, match="stimulus/house"):
        select(EVENT_ID, ["stimulus/car", "stimulus/house"])
