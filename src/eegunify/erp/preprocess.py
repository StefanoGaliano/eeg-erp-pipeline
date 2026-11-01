"""Continuous-data cleaning (Phase 2, docs/BLUEPRINT_PHASES.md section 6.1).

Pure helpers on ``mne.io.Raw``, used by the preproc stage, the app and the tests. The order is
fixed and recorded: bipolar EOG derivation -> line noise -> resample -> filter -> bad-channel
detection -> interpolation -> re-reference. Everything works in place on a preloaded Raw and
returns a short description of what it did, which goes into provenance.

Filter semantics follow ERPLAB ``pop_basicfilter`` so that the MATLAB cross-check (M-E5) can
agree to < 0.1 µV: Butterworth, ``iir_order`` = the *total* order of the forward-backward
filter (2 = 12 dB/oct), half-amplitude cutoff. MNE's zero-phase IIR runs the designed filter
forward and backward, so it gets order ``iir_order // 2``.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mne
import numpy as np
from mne_bids import BIDSPath, read_raw_bids

from eegunify.config.erp_sections import BadChannelsSpec, LineNoiseSpec, PreprocSection
from eegunify.config.schema import Config, ReferenceSection
from eegunify.erp import qa
from eegunify.qa.checks import QACheck, detect_line_freq

REFERENCE_FREE = re.compile(r"CMS/DRL|reference[- ]free", re.IGNORECASE)
"""``EEGReference`` values of recordings that have no usable reference (BioSemi)."""
BAD_DETECTION_HIGHPASS_HZ = 1.0


class PreprocError(ValueError):
    """The data or config can't be preprocessed as asked (the message says how to fix it)."""


@dataclass
class Loaded:
    raw: mne.io.BaseRaw
    sidecar: dict[str, Any]
    bids_path: BIDSPath


def load_rawdata(project_root: Path, cfg: Config, subject: str) -> Loaded:
    """Pre-stage ``load``: canonical rawdata of one subject, preloaded, plus its ``_eeg.json``."""
    bp = BIDSPath(
        root=project_root / cfg.dataset.bids_root,
        subject=subject,
        session=cfg.recordings.session,
        task=cfg.dataset.task,
        run=cfg.recordings.run,
        datatype="eeg",
        suffix="eeg",
        extension=".vhdr",
    )
    raw = read_raw_bids(bp, verbose=False)
    raw.load_data(verbose=False)
    sidecar_path = Path(bp.copy().update(extension=".json").fpath)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    return Loaded(raw, sidecar, bp)


def is_reference_free(sidecar: dict[str, Any]) -> bool:
    return bool(REFERENCE_FREE.search(str(sidecar.get("EEGReference", ""))))


def _data_picks(raw: mne.io.BaseRaw) -> np.ndarray:
    # Reference candidates (BioSemi EXG mastoids are `misc`) must be filtered like the EEG.
    picks: np.ndarray = mne.pick_types(raw.info, eeg=True, eog=True, emg=True, ecg=True, misc=True)
    return picks


def harmonics(freq: float, sfreq: float) -> list[float]:
    """``freq`` and its multiples below Nyquist."""
    return [freq * k for k in range(1, int((sfreq / 2) // freq) + 1) if freq * k < sfreq / 2]


def add_bipolar_eog(raw: mne.io.BaseRaw, bipolar: dict[str, tuple[str, str]]) -> list[str]:
    """Add each ``{name: (a, b)}`` derivation (a minus b) as an EOG channel, in place.

    An existing channel of the same name is replaced. Returns the names added.
    """
    added = []
    for name, (a, b) in sorted(bipolar.items()):
        missing = [c for c in (a, b) if c not in raw.ch_names]
        if missing:
            raise PreprocError(f"artifacts.eog_bipolar {name}: channels {missing} not in the data")
        data = raw.get_data(picks=[a]) - raw.get_data(picks=[b])
        if name in raw.ch_names:
            raw.drop_channels([name])
        info = mne.create_info([name], raw.info["sfreq"], "eog")
        new = mne.io.RawArray(data, info, first_samp=raw.first_samp, verbose=False)
        raw.add_channels([new], force_update_info=True)
        added.append(name)
    return added


def remove_line_noise(
    raw: mne.io.BaseRaw, spec: LineNoiseSpec, line_freq: float | None
) -> dict[str, Any]:
    if spec.method == "none":
        return {"method": "none"}
    if spec.freqs == "auto":
        if not line_freq:
            raise PreprocError("line_noise.freqs 'auto' but no line frequency known; set freqs")
        freqs = harmonics(line_freq, raw.info["sfreq"])
    else:
        freqs = [f for f in spec.freqs if f < raw.info["sfreq"] / 2]
    picks = _data_picks(raw)
    if spec.method == "notch":
        raw.notch_filter(freqs, picks=picks, verbose=False)
    else:
        try:
            from meegkit.dss import dss_line
        except ImportError as err:
            raise PreprocError(
                "line_noise.method 'zapline' needs meegkit: pip install meegkit "
                "(or use method: notch)"
            ) from err
        data = raw.get_data(picks=picks).T
        # Zapline removes the fundamental and its harmonics in one pass.
        clean, _ = dss_line(data, fline=freqs[0], sfreq=raw.info["sfreq"], nremove=1)
        raw._data[picks] = clean.T
    return {"method": spec.method, "freqs": freqs}


def _filter_all(raw: mne.io.BaseRaw, l_freq: float | None, h_freq: float | None, **kw: Any) -> None:
    """Filter data channels and ``misc`` channels alike (reference candidates such as BioSemi
    EXG mastoids are ``misc``). MNE only updates ``info["highpass"/"lowpass"]`` when it picks
    the data channels itself, hence two calls."""
    raw.filter(l_freq, h_freq, verbose=False, **kw)
    misc = mne.pick_types(raw.info, misc=True)
    if len(misc):
        raw.filter(l_freq, h_freq, picks=misc, verbose=False, **kw)


def apply_filter(raw: mne.io.BaseRaw, pre: PreprocSection) -> dict[str, Any]:
    l_freq, h_freq = pre.highpass_hz, pre.lowpass_hz
    if l_freq is None and h_freq is None:
        return {"applied": False}
    f = pre.filter
    if f.method == "iir":
        zero = f.phase == "zero"
        order = f.iir_order // 2 if zero else f.iir_order
        _filter_all(
            raw,
            l_freq,
            h_freq,
            method="iir",
            iir_params={"order": order, "ftype": "butter", "output": "sos"},
            phase="zero" if zero else "forward",
        )
        design = {"method": "iir", "ftype": "butter", "order_total": f.iir_order, "phase": f.phase}
    else:
        _filter_all(raw, l_freq, h_freq, method="fir", phase=f.phase)
        design = {"method": "fir", "phase": f.phase}
    return {"applied": True, "highpass_hz": l_freq, "lowpass_hz": h_freq, **design}


def _missing_positions(raw: mne.io.BaseRaw, names: list[str]) -> list[str]:
    montage = raw.get_montage()
    pos = montage.get_positions()["ch_pos"] if montage is not None else {}
    return [n for n in names if n not in pos or not np.all(np.isfinite(pos[n]))]


def detect_bads(raw: mne.io.BaseRaw, spec: BadChannelsSpec) -> dict[str, str]:
    """Bad EEG channels -> reason. Channels already bad in rawdata are kept (``rawdata``)."""
    bads = {ch: "rawdata channels.tsv" for ch in raw.info["bads"]}
    if spec.method == "config_only":
        return bads
    copy = raw.copy().pick("eeg", exclude="bads")
    copy.filter(BAD_DETECTION_HIGHPASS_HZ, None, verbose=False)
    if spec.method == "lof":
        if importlib.util.find_spec("sklearn") is None:
            raise PreprocError(
                "bad_channels.method 'lof' needs scikit-learn: pip install scikit-learn"
            )
        found = mne.preprocessing.find_bad_channels_lof(copy, verbose=False)
        bads.update({ch: "lof" for ch in found if ch not in bads})
        return bads
    missing = _missing_positions(copy, copy.ch_names)
    if missing:
        raise PreprocError(
            f"pyprep needs electrode positions, missing for {missing}: set the montage in the "
            "config and reconvert, or use preproc.bad_channels.method: config_only"
        )
    try:
        from pyprep.find_noisy_channels import NoisyChannels
    except ImportError as err:
        raise PreprocError("pyprep is not installed: pip install 'eegunify[erp]'") from err
    with mne.utils.use_log_level("ERROR"):  # also hides RANSAC's progress bar
        nc = NoisyChannels(copy, do_detrend=False, random_state=spec.random_state)
        if spec.method == "pyprep_ransac":
            nc.find_bad_by_ransac()
        else:
            nc.find_all_bads(ransac=True)
    for kind, chans in nc.get_bads(as_dict=True).items():
        if kind == "bad_all":
            continue
        for ch in chans:
            bads.setdefault(str(ch), f"pyprep {kind.removeprefix('bad_by_')}")
    return bads


def interpolate(raw: mne.io.BaseRaw, bads: list[str], enabled: bool) -> list[str]:
    """Interpolate ``bads`` (spherical splines); returns the channels interpolated."""
    raw.info["bads"] = list(bads)
    if not bads or not enabled:
        return []
    eeg = [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True, exclude=[])]
    missing = _missing_positions(raw, eeg)
    if missing:
        raise PreprocError(
            f"interpolation needs electrode positions, missing for {missing}: set the montage "
            "or preproc.interpolate: false"
        )
    raw.interpolate_bads(reset_bads=True, verbose=False)
    return list(bads)


def rereference(
    raw: mne.io.BaseRaw,
    ref: ReferenceSection,
    *,
    reference_free: bool,
    derived: Iterable[str] = (),
) -> dict[str, Any]:
    """Apply ``reference.scheme`` to the EEG channels (other types never change, except the
    recorded EOG electrodes with ``apply_to_eog``; ``derived`` bipolar channels never change).

    Raises
    ------
    PreprocError
        For ``scheme: none`` on reference-free data (QA ``REREF_REQUIRED``) or unknown
        reference channels.
    """
    if ref.scheme == "none":
        if reference_free:
            raise PreprocError(
                "recording is reference-free (CMS/DRL): set reference.scheme to average, "
                "channels or rest"
            )
        return {"scheme": "none"}
    if ref.scheme == "average":
        raw.set_eeg_reference("average", projection=False, ch_type="eeg", verbose=False)
        return {"scheme": "average", "channels": "good EEG channels"}
    if ref.scheme == "channels":
        missing = [c for c in ref.channels if c not in raw.ch_names]
        if missing:
            raise PreprocError(f"reference channels {missing} not in the data")
        bad = [c for c in ref.channels if c in raw.info["bads"]]
        if bad and not ref.allow_bad_channels:
            raise PreprocError(f"reference channels {bad} are bad and were not interpolated")
        # Explicit arithmetic rather than set_eeg_reference: the reference may include EOG
        # electrodes and bad channels, and may be applied to the EOG electrodes as well.
        ref_idx = [raw.ch_names.index(c) for c in ref.channels]
        skip = set(derived)
        types = ["eeg", "eog"] if ref.apply_to_eog else ["eeg"]
        targets = [
            i
            for i, name in enumerate(raw.ch_names)
            if raw.get_channel_types(picks=[i])[0] in types and name not in skip
        ]
        data = raw.get_data()
        reference = data[ref_idx].mean(axis=0)
        raw._data[targets] = data[targets] - reference
        return {
            "scheme": "channels",
            "channels": list(ref.channels),
            "applied_to": [raw.ch_names[i] for i in targets],
            "bad_in_reference": bad,
        }
    sphere = mne.make_sphere_model("auto", "auto", raw.info, verbose=False)
    src = mne.setup_volume_source_space(sphere=sphere, exclude=30.0, pos=15.0, verbose=False)
    fwd = mne.make_forward_solution(raw.info, trans=None, src=src, bem=sphere, verbose=False)
    raw.set_eeg_reference("REST", forward=fwd, verbose=False)
    return {"scheme": "rest", "model": "sphere"}


@dataclass
class PreprocResult:
    raw: mne.io.BaseRaw
    bads: dict[str, str]
    interpolated: list[str]
    steps: dict[str, Any]
    qa: list[QACheck] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return qa.worst(self.qa) == "fail"


def preprocess(
    raw: mne.io.BaseRaw,
    cfg: Config,
    *,
    reference_free: bool,
    roi_channels: dict[str, list[str]] | None = None,
) -> PreprocResult:
    """Run every preproc step on ``raw`` (in place) and the preproc QA.

    ``roi_channels`` maps used component ids to their primary ROI channels (for
    ``PREPROC_ROI_INTERPOLATED``). A QA ``fail`` (too many bads, missing re-reference) stops
    before re-referencing; check ``result.failed``.
    """
    pre = cfg.preproc
    if pre.interpolate_after_ica and cfg.artifacts.method == "none":
        raise PreprocError(
            "preproc.interpolate_after_ica needs an ica stage (artifacts.method ica or "
            "precomputed); with method none set interpolate_after_ica: false"
        )
    line_freq = raw.info.get("line_freq") or detect_line_freq(raw)
    steps: dict[str, Any] = {}
    if cfg.artifacts.eog_bipolar:
        # First, from the recorded signals: the derived EOG channels are then filtered like the
        # rest, kept out of the EEG re-reference, and exist whether or not ICA runs (the HEOG
        # step rejection in the epochs stage needs them).
        steps["eog_bipolar"] = add_bipolar_eog(raw, cfg.artifacts.eog_bipolar)
    steps["line_noise"] = remove_line_noise(raw, pre.line_noise, line_freq)
    if pre.resample_hz and pre.resample_hz != raw.info["sfreq"]:
        raw.resample(pre.resample_hz, verbose=False)
        steps["resample_hz"] = pre.resample_hz
    steps["filter"] = apply_filter(raw, pre)
    bads = detect_bads(raw, pre.bad_channels)
    n_eeg = len(mne.pick_types(raw.info, eeg=True, exclude=[]))
    checks = [qa.bads_fraction_check(sorted(bads), n_eeg)]
    result = PreprocResult(raw, bads, [], steps, checks)
    if pre.line_noise.method != "none" or line_freq:
        checks.insert(0, qa.line_noise_check(raw, line_freq))
    if cfg.reference.scheme == "none" and reference_free:
        checks.append(
            QACheck(
                "REREF_REQUIRED",
                "fail",
                "reference-free recording (CMS/DRL) with reference.scheme: none; set average, "
                "channels or rest",
            )
        )
        return result
    if result.failed:
        return result
    # interpolate_after_ica: the bads stay in raw.info["bads"]; the ica stage interpolates them.
    result.interpolated = interpolate(
        raw, sorted(bads), pre.interpolate and not pre.interpolate_after_ica
    )
    steps["interpolated"] = result.interpolated
    checks.append(qa.roi_interpolated_check(result.interpolated, roi_channels or {}))
    steps["reference"] = rereference(
        raw, cfg.reference, reference_free=reference_free, derived=cfg.artifacts.eog_bipolar
    )
    ref = steps["reference"]
    chans = ref.get("channels")
    checks.append(
        QACheck(
            "REREF_APPLIED",
            "pass",
            f"reference: {ref['scheme']}" + (f" ({chans})" if chans else ""),
            ref["scheme"],
        )
    )
    return result
