import pytest
from hypothesis import given
from hypothesis import strategies as st

from eegunify.events.mapping import EventMapper, parse_code


@pytest.mark.parametrize(
    ("desc", "code"),
    [
        ("12", 12),
        ("Stimulus/S  1", 1),
        ("Response/R 12", 12),
        ("condition 5", 5),
        ("boundary", "boundary"),
        (" face ", "face"),
    ],
)
def test_parse_code(desc: str, code: int | str) -> None:
    assert parse_code(desc) == code


def test_mapper_ranges_ints_and_labels() -> None:
    m = EventMapper.from_config(
        {
            "1-40": "stimulus/face",
            "41-80": "stimulus/car",
            "201": "response/correct",
            "Comment/practice  start": "practice",
        }
    )
    assert m.lookup(1) == "stimulus/face"
    assert m.lookup(40) == "stimulus/face"
    assert m.lookup(41) == "stimulus/car"
    assert m.lookup(201) == "response/correct"
    assert m.lookup(202) is None
    assert m.lookup("Stimulus/S 41") == "stimulus/car"
    assert m.lookup("Comment/practice start") == "practice"
    assert m.lookup("unknown") is None


@given(st.integers(min_value=0, max_value=255))
def test_every_code_maps_to_at_most_one_condition(code: int) -> None:
    m = EventMapper.from_config({"1-40": "a", "41-80": "b", "81": "c"})
    hits = [tt for lo, hi, tt in m.ranges if lo <= code <= hi]
    assert len(hits) <= 1
    assert m.lookup(code) == (hits[0] if hits else None)


def test_onset_shift_only_matching_trial_types() -> None:
    import mne
    import numpy as np

    from eegunify.config.schema import EventsSection
    from eegunify.events.extract import extract_events

    info = mne.create_info(["Cz"], 1000.0, "eeg")
    raw = mne.io.RawArray(np.zeros((1, 5000)), info, verbose="error")
    raw.set_annotations(mne.Annotations([1.0, 2.0], [0, 0], ["1", "201"]))
    cfg = EventsSection(
        mapping={"1": "stimulus/face", "201": "response/correct"}, onset_shift_ms={"stimulus/*": 26}
    )
    table = extract_events(raw, cfg).table
    assert table["onset"].tolist() == [1.026, 2.0]
    assert table["sample"].tolist() == [1026, 2000]
