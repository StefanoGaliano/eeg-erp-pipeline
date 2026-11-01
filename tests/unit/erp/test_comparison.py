"""Comparison rules vs ERP CORE (BLUEPRINT_PHASES section 0 and 8.2), on made-up numbers."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from eegunify.erp.comparison import Tolerances, comparison_table, failures

SUBJECTS = ["015", "016", "017", "018", "019"]


def _published(diffs: dict[str, float]) -> pd.DataFrame:
    rows = []
    for sub, d in diffs.items():
        rows += [
            {"subject": sub, "wave": "Faces minus Cars", "measure": "mean_amplitude", "value": d},
            {"subject": sub, "wave": "Faces, Correct", "measure": "mean_amplitude", "value": 4.0},
            {"subject": sub, "wave": "Cars, Correct", "measure": "mean_amplitude", "value": 5.0},
            {
                "subject": sub,
                "wave": "Faces minus Cars",
                "measure": "fractional_area_latency",
                "value": math.nan if sub == "019" else 130.0,
            },
            {"subject": sub, "wave": "Faces minus Cars", "measure": "peak_latency", "value": 128.0},
        ]
    return pd.DataFrame(rows)


def _ours(diffs: dict[str, float], latency: float = 133.0) -> pd.DataFrame:
    rows = []
    for sub, d in diffs.items():
        for wave, measure, value in [
            ("face_minus_car", "mean_amplitude", d),
            ("face", "mean_amplitude", 4.2),
            ("car", "mean_amplitude", 5.1),
            ("face_minus_car", "fractional_area_latency", latency),
            ("face_minus_car", "peak_latency", 129.0),
        ]:
            rows.append(
                {"subject": sub, "roi": "primary", "wave": wave, "measure": measure, "value": value}
            )
            rows.append(  # secondary ROI rows must be ignored
                {"subject": sub, "roi": "secondary", "wave": wave, "measure": measure, "value": 99}
            )
    return pd.DataFrame(rows)


SUMMARY = pd.DataFrame(
    {
        "subject": SUBJECTS,
        "included": [True, False, True, True, True],
        "reason": ["n/a", "TC", "n/a", "n/a", "n/a"],
    }
)
THEIRS = {"015": -1.0, "017": -2.9, "018": -0.6, "019": 2.0}


def _diff_rows(t: pd.DataFrame) -> pd.DataFrame:
    return t[(t["wave"] == "face_minus_car") & (t["measure"] == "mean_amplitude")].set_index(
        "subject"
    )


def test_all_within_tolerance_and_excluded_subject_only_reported() -> None:
    ours = _ours({"015": -1.2, "016": -3.0, "017": -2.5, "018": -0.9, "019": 1.8})
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, SUBJECTS)
    d = _diff_rows(t)
    assert d.loc["016", "status"] == "excluded_by_erpcore"
    assert d.loc["016", "note"] == "reason TC"
    assert math.isnan(float(d.loc["016", "theirs"]))
    assert (d.loc[["015", "017", "018", "019"], "status"] == "ok").all()
    # the mean is over the 4 compared subjects, not 5
    mean = d.loc["mean"]
    assert mean["ours"] == pytest.approx((-1.2 - 2.5 - 0.9 + 1.8) / 4)
    assert mean["theirs"] == pytest.approx((-1.0 - 2.9 - 0.6 + 2.0) / 4)
    assert mean["note"] == "mean over 4 compared subjects"
    assert failures(t).empty


def test_positive_published_value_needs_positive_ours() -> None:
    """ERP CORE's sub-019 is positive: matching that sign is right, 'all negative' would fail."""
    ours = _ours({"015": -1.0, "016": 0.0, "017": -2.9, "018": -0.6, "019": -0.2})
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, SUBJECTS)
    assert _diff_rows(t).loc["019", "status"] == "sign_mismatch"
    # 2.2 µV off in one subject also moves the 4-subject mean by 0.55 µV (> 0.3)
    assert list(failures(t)["subject"]) == ["019", "mean"]


def test_out_of_tolerance_amplitude_and_latency() -> None:
    ours = _ours({"015": -1.6, "016": 0.0, "017": -2.9, "018": -0.6, "019": 2.0}, latency=139.0)
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, SUBJECTS)
    assert _diff_rows(t).loc["015", "status"] == "out_of_tolerance"
    lat = t[t["measure"] == "fractional_area_latency"].set_index("subject")
    assert (lat.loc[["015", "017", "018"], "status"] == "out_of_tolerance").all()
    assert lat.loc["019", "status"] == "not_available"  # published NaN
    assert lat.loc["mean", "note"] == "mean over 3 compared subjects"


def test_mean_tolerance_is_tighter_than_subject_tolerance() -> None:
    # every subject 0.4 µV off (inside ±0.5) -> the mean is 0.4 off (outside ±0.3)
    ours = _ours({s: v - 0.4 for s, v in THEIRS.items() if s != "019"} | {"019": 1.6, "016": 0.0})
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, SUBJECTS)
    d = _diff_rows(t)
    assert (d.loc[["015", "017", "018", "019"], "status"] == "ok").all()
    assert d.loc["mean", "status"] == "out_of_tolerance"
    loose = comparison_table(
        "N170", ours, _published(THEIRS), SUMMARY, SUBJECTS, Tolerances(mean_uv=0.5)
    )
    assert _diff_rows(loose).loc["mean", "status"] == "ok"


def test_missing_subject_in_ours_fails() -> None:
    ours = _ours({"015": -1.0, "017": -2.9, "018": -0.6, "019": 2.0})  # no 016 is fine
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, ["015", "017", "018", "019"])
    assert failures(t).empty
    ours = _ours({"015": -1.0, "018": -0.6, "019": 2.0})
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, ["015", "017", "018", "019"])
    assert "017" in set(failures(t)["subject"])
    assert set(failures(t)["status"]) == {"missing_ours"}


def test_peak_latency_rows_carry_the_lowpass_note() -> None:
    ours = _ours(dict.fromkeys(SUBJECTS, -1.0))
    t = comparison_table("N170", ours, _published(THEIRS), SUMMARY, SUBJECTS)
    peak = t[(t["measure"] == "peak_latency") & (t["subject"] == "015")]
    assert "20 Hz" in peak["note"].iloc[0]


def test_unknown_component_refused() -> None:
    with pytest.raises(ValueError, match="no comparison defined"):
        comparison_table("MMN", pd.DataFrame(), pd.DataFrame(), SUMMARY, SUBJECTS)
