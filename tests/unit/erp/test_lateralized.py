"""Lateralized (contra - ipsi) contrasts for N2pc/LRP (BLUEPRINT 5.3), on known values."""

from __future__ import annotations

from typing import Any

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.config.erp_sections import Contrast
from eegunify.config.loader import parse_config
from eegunify.erp.averaging import (
    MEASURES_COLUMNS,
    add_contrasts,
    average_conditions,
    lateralized_waves,
    measure_component,
)
from eegunify.erp.components.library import resolve
from eegunify.erp.measure import mean_amplitude
from tests.synthetic.erp import erp_config

SF = 250.0
NAMES = ["PO7", "PO8", "Oz", "P7", "P8"]


def _evoked(values: dict[str, float], nave: int = 40) -> mne.Evoked:
    times = np.arange(-50, 200) / SF
    data = np.zeros((len(NAMES), len(times)))
    for ch, v in values.items():
        data[NAMES.index(ch)] = v * 1e-6
    return mne.EvokedArray(data, mne.create_info(NAMES, SF, "eeg"), tmin=times[0], nave=nave)


def test_contra_ipsi_formula_matches_erpcore_bin_operations() -> None:
    # left-target trials: PO7 = 1, PO8 = 3; right-target trials: PO7 = 5, PO8 = 2
    left = _evoked({"PO7": 1.0, "PO8": 3.0, "P7": 10.0, "P8": 20.0})
    right = _evoked({"PO7": 5.0, "PO8": 2.0, "P7": 30.0, "P8": 40.0}, nave=38)
    contra, ipsi = lateralized_waves(left, right, [("PO7", "PO8")])
    # ERP CORE: contra = (b1@RH + b2@LH)/2, ipsi = (b1@LH + b2@RH)/2
    c, i = (3.0 + 5.0) / 2, (1.0 + 2.0) / 2
    for ev, v in ((contra, c), (ipsi, i)):
        po7, po8 = (ev.copy().pick([ch]).data[0] * 1e6 for ch in ("PO7", "PO8"))
        np.testing.assert_allclose(po7, v)
        np.testing.assert_allclose(po8, v)  # both channels of the pair carry its value
        assert np.isnan(ev.copy().pick(["Oz"]).data).all()  # outside every pair: undefined
        assert np.isnan(ev.copy().pick(["P7"]).data).all()  # not a configured pair
        assert ev.nave == 78


def test_unknown_pair_channel_refused() -> None:
    with pytest.raises(ValueError, match="not in the data"):
        lateralized_waves(_evoked({}), _evoked({}), [("PO7", "PO10")])


SECTIONS: dict[str, Any] = {
    "epochs": {"sets": {"stim": {"lock": "stimulus/*"}}},
    "conditions": {"left": ["stimulus/left"], "right": ["stimulus/right"]},
    "contrasts": {
        "contra_minus_ipsi": {
            "type": "lateralized",
            "left": "left",
            "right": "right",
            "pairs": [["PO7", "PO8"], ["P7", "P8"]],
        }
    },
    "components": {"preset": "erpcore", "use": ["n2pc"]},
}


def _epochs(n: int = 30, seed: int = 0) -> mne.EpochsArray:
    """Trials with a -2 µV contralateral negativity at 200-275 ms plus noise."""
    rng = np.random.default_rng(seed)
    times = np.arange(-50, 200) / SF
    win = (times >= 0.2) & (times <= 0.275)
    data = rng.normal(0, 1.0, (2 * n, len(NAMES), len(times)))
    events = []
    for k in range(2 * n):
        side = "left" if k < n else "right"
        contra = "PO8" if side == "left" else "PO7"
        data[k, NAMES.index(contra), win] -= 2.0
        events.append([k * 1000, 0, 1 if side == "left" else 2])
    info = mne.create_info(NAMES, SF, "eeg")
    return mne.EpochsArray(
        data * 1e-6,
        info,
        events=np.array(events),
        tmin=times[0],
        event_id={"stimulus/left": 1, "stimulus/right": 2},
        verbose=False,
    )


def test_n2pc_measured_on_the_contra_minus_ipsi_wave() -> None:
    cfg = parse_config(erp_config(**SECTIONS))
    epochs = _epochs()
    evokeds = add_contrasts(average_conditions(epochs, cfg.conditions), cfg)
    assert {"contra", "ipsi", "contra_minus_ipsi"} <= set(evokeds)
    [rc] = resolve(cfg.components).components.values()
    cm = measure_component(rc, evokeds, epochs, cfg, subject="01", stage_key="k")
    rows = pd.DataFrame(cm.rows, columns=MEASURES_COLUMNS)
    prim = rows.query("roi == 'primary' and measure == 'mean_amplitude'").set_index("wave")
    assert set(prim.index) == {"contra_minus_ipsi", "contra", "ipsi"}
    # expected from the same trials, computed directly
    x = epochs.get_data() * 1e6
    left, right = x[:30], x[30:]
    po7, po8 = NAMES.index("PO7"), NAMES.index("PO8")
    contra = (left[:, po8].mean(0) + right[:, po7].mean(0)) / 2
    ipsi = (left[:, po7].mean(0) + right[:, po8].mean(0)) / 2
    # same window snapping as ERPLAB (closest samples): use the library's mean_amplitude
    expected = mean_amplitude(epochs.times * 1000, contra - ipsi, (200.0, 275.0))
    assert prim.loc["contra_minus_ipsi", "value"] == pytest.approx(expected, abs=1e-5)
    assert prim.loc["contra_minus_ipsi", "value"] == pytest.approx(-2.0, abs=0.5)
    assert prim.loc["contra", "n_trials"] == 60
    # SME: half the root sum of squares of the per-trial (R - L) / (L - R) SMEs
    assert 0 < cm.sme_uv < 0.5


def test_lateralized_contrast_validation() -> None:
    with pytest.raises(ValueError, match="needs left, right and pairs"):
        Contrast(type="lateralized", left="l", right="r")
    with pytest.raises(ValueError, match="plus and minus"):
        Contrast(left="l", right="r")
    with pytest.raises(ValueError, match="repeat a channel"):
        Contrast(type="lateralized", left="l", right="r", pairs=[("PO7", "PO8"), ("PO7", "P8")])
    c = Contrast(type="lateralized", left="l", right="r", pairs=[("PO7", "PO8")])
    assert c.conditions == ("l", "r") and c.parents == ("contra", "ipsi")
    bad = {**SECTIONS, "group": {"cluster": {"enabled": True, "contrast": "contra_minus_ipsi"}}}
    with pytest.raises(ValueError, match="no scalp topography"):
        parse_config(erp_config(**bad))
