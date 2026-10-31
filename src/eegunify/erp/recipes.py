"""Named recipes: ERP-section patches applied over a dataset config.

``preview`` is the minimal recipe of the original README figure (finding F6 in
docs/BLUEPRINT_PHASES.md section 2): 0.1-30 Hz MNE FIR (zero phase), recorded rate, bad channels
left out (not interpolated), average reference, no ICA, -200..800 ms epochs with a -200..0 ms
baseline, and one peak-to-peak rule of 150 µV over the whole epoch on EEG (MNE's
``reject=dict(eeg=150e-6)``). On ERP CORE N170 sub-015 it gives the regression golden value
face - car = -2.24 µV at PO8, 110-150 ms. It is a regression anchor, not ERP CORE's recipe.

``erplab`` is the M-E5 cross-check recipe: the dataset config's own settings without ICA, so
the same preprocessed data can go through ERPLAB.
"""

from __future__ import annotations

from typing import Any

from eegunify.config.loader import parse_config
from eegunify.config.schema import Config

PREVIEW: dict[str, Any] = {
    "preproc": {
        "line_noise": {"method": "none"},
        "resample_hz": None,
        "highpass_hz": 0.1,
        "lowpass_hz": 30.0,
        "filter": {"method": "fir", "phase": "zero"},
        "bad_channels": {"method": "config_only"},
        "interpolate": False,
    },
    "reference": {"scheme": "average", "channels": []},
    "artifacts": {"method": "none"},
    "epochs": {
        "sets": {
            "stim": {
                "lock": "stimulus/*",
                "tmin_ms": -200,
                "tmax_ms": 800,
                "baseline_ms": [-200, 0],
            }
        },
        "reject": {
            "method": "thresholds",
            "ptp_uv": 150,
            # Wider than the epoch: one window = the whole epoch, as MNE's reject.
            "ptp_window_ms": 10_000,
            "ptp_step_ms": 10_000,
            "step_heog_uv": None,
            "flat_uv": None,
        },
    },
}
ERPLAB_CROSSCHECK: dict[str, Any] = {
    # M-E5 (section 8.4): the dataset's own recipe without ICA (not reproducible across
    # implementations), so ERPLAB runs pop_artmwppth and pop_artstep on the same preprocessed
    # data. ``eog_bipolar`` stays: preproc derives HEOG whatever the artifact method.
    "artifacts": {"method": "none"},
    "epochs": {"reject": {"method": "thresholds", "flat_uv": None}},
}
RECIPES = {"preview": PREVIEW, "erplab": ERPLAB_CROSSCHECK}


def apply_recipe(cfg: Config, name: str) -> Config:
    """``cfg`` with the recipe's sections replaced (``reference`` and ``epochs`` are merged so
    the recording's online reference and the rejection minimum survive)."""
    data = cfg.model_dump(mode="json")
    for section, patch in RECIPES[name].items():
        if section == "artifacts":
            data[section] = {**data[section], **patch}  # keep eog_bipolar
        elif isinstance(data.get(section), dict) and section in ("reference", "epochs"):
            data[section] = {**data[section], **patch}
            if section == "epochs":
                data[section]["reject"] = {
                    **cfg.epochs.reject.model_dump(mode="json"),
                    **patch["reject"],
                }
        else:
            data[section] = patch
    return parse_config(data)
