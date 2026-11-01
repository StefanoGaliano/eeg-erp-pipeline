"""Named recipes: ERP-section patches applied over a dataset config.

``preview`` is the minimal recipe of the original README figure (finding F6 in
docs/BLUEPRINT_PHASES.md section 2): 0.1-30 Hz MNE FIR (zero phase), recorded rate, bad channels
left out (not interpolated), average reference, no ICA, -200..800 ms epochs with a -200..0 ms
baseline, and one peak-to-peak rule of 150 µV over the whole epoch on EEG (MNE's
``reject=dict(eeg=150e-6)``). On ERP CORE N170 sub-015 it gives the regression golden value
face - car = -2.24 µV at PO8, 110-150 ms. It is a regression anchor, not ERP CORE's recipe.

``erplab`` is the M-E5 cross-check recipe: the dataset config's own settings without ICA, so
the same preprocessed data can go through ERPLAB.

``erpcore_exact`` (BLUEPRINT_PHASES 8.2a) is built per dataset from ERP CORE's own per-subject
parameters (``tests/golden/erpcore_reference/<component>_exact.json``, written by ``eegunify
fetch erpcore-values``) and their ICA weight files; see :func:`erpcore_exact`.
"""

from __future__ import annotations

from pathlib import Path
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


#: ERP CORE README (Script #7): peaks are measured on ERPs low-passed at 20 Hz, Butterworth,
#: 48 dB/oct (= ERPLAB total order 8), half-amplitude cutoff.
ERPCORE_PEAK_LOWPASS_HZ = 20.0
ERPCORE_PEAK_LOWPASS_ORDER = 8


class RecipeError(ValueError):
    """The recipe can't be built for this config or data (the message says why)."""


def erpcore_exact(
    cfg: Config, params: dict[str, Any], recorded: list[str], weights_dir: Path
) -> Config:
    """``cfg`` following ERP CORE's own processing of every configured subject.

    ``params`` is the ``<component>_exact.json`` content, ``recorded`` the recording's EEG and
    EOG channel names (rawdata ``channels.tsv``; ERP CORE averages all of them), and
    ``weights_dir`` the folder holding the ``*_ica_weighted.set`` files. Settings that differ
    per subject go into ``overrides``; the base config carries the first subject's values so it
    validates, and every subject gets its own override.
    """
    canon = {c.lower(): c for c in recorded}

    def name(x: str) -> str:
        if x.lower() not in canon:
            raise RecipeError(f"ERP CORE channel {x!r} not in the recording {sorted(recorded)}")
        return canon[x.lower()]

    n_sites = int(params["reference"]["n_sites"])
    if len(recorded) != n_sites:
        raise RecipeError(
            f"ERP CORE averaged {n_sites} recorded sites; this recording has {len(recorded)}"
        )
    subjects = list(cfg.recordings.subjects)
    missing = [s for s in subjects if s not in params["subjects"]]
    if missing:
        raise RecipeError(f"no ERP CORE parameters for subjects {missing}")
    bip = {k: [name(a), name(b)] for k, (a, b) in params["bipolar"].items()}
    canon |= {k.lower(): k for k in bip}  # rules may test the derived bipolar channels
    uncorrected = {k: v for k, v in bip.items() if k.endswith("_uncorr")}
    corrected = {k: v for k, v in bip.items() if not k.endswith("_uncorr")}

    def subject_patch(sub: str) -> dict[str, Any]:
        p = params["subjects"][sub]
        weights = weights_dir / p["ica_weights"]
        if not weights.exists():
            raise RecipeError(f"ICA weights for sub-{sub} not found: {weights}")
        rules = [
            {
                **{k: v for k, v in r.items() if k not in ("erplab", "channels")},
                "channels": [name(c) for c in r["channels"]],
            }
            for r in p["reject"]
        ]
        return {
            "preproc": {"bad_channels": {"extra": [name(c) for c in p["interpolate"]]}},
            "artifacts": {
                "precomputed": {
                    "weights": weights.as_posix(),
                    "exclude_eeglab": p["ica_exclude_eeglab"],
                }
            },
            "epochs": {"reject": {"rules": rules}},
        }

    patches = {s: subject_patch(s) for s in subjects}
    data = cfg.model_dump(mode="json")
    first = patches[subjects[0]]
    data["preproc"] |= {
        "bad_channels": {"method": "config_only", "extra": []},
        "interpolate": True,
        "interpolate_after_ica": True,
    }
    data["reference"] |= {
        "scheme": "channels",
        "channels": list(recorded),
        "apply_to_eog": True,
        "allow_bad_channels": True,
    }
    data["artifacts"] = {
        **data["artifacts"],
        "method": "precomputed",
        "asr": None,
        "eog_bipolar": uncorrected,
        "corrected_bipolar": corrected,
        "precomputed": first["artifacts"]["precomputed"],
    }
    data["epochs"]["reject"] |= {"method": "rules", "rules": first["epochs"]["reject"]["rules"]}
    data["erp"] |= {
        "peak_lowpass_hz": ERPCORE_PEAK_LOWPASS_HZ,
        "peak_lowpass_order": ERPCORE_PEAK_LOWPASS_ORDER,
    }
    overrides = dict(data.get("overrides") or {})
    for s, patch in patches.items():
        overrides[f"sub-{s}"] = _merge(overrides.get(f"sub-{s}", {}), patch)
    data["overrides"] = overrides
    return parse_config(data)


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
