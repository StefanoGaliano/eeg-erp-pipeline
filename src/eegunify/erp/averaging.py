"""Single-subject ERPs and component measurement (Phase 5, docs/BLUEPRINT_PHASES.md 6.4-6.6).

- :func:`average_conditions`: one ``Evoked`` per condition (trial-type prefix lists, MNE tag
  matching), ``comment`` = condition name. Conditions with no epoch in the set are skipped.
- :func:`add_contrasts`: difference waves (``mne.combine_evoked([a, b], [1, -1])``).
- :func:`measure_component`: the long-format ``measures.tsv`` rows of one component (section
  6.6), on its contrast and the contrast's conditions, at the pooled primary ROI and at each
  secondary channel; plus the facts the erp QA needs (SME, polarity, local peaks).

Waveforms are measured as they are; the figure low-pass (``erp.plot_lowpass_hz``) never
touches them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import mne
import numpy as np

from eegunify.config.erp_sections import Contrast
from eegunify.config.schema import Config
from eegunify.erp.components.library import ResolvedComponent
from eegunify.erp.events import matches
from eegunify.erp.measure import (
    fractional_area_latency,
    mean_amplitude,
    peak,
    pool,
    sme_mean_amplitude,
)

UV = 1e6
MEASURES_COLUMNS = [
    "subject",
    "session",
    "task",
    "epoch_set",
    "component",
    "preset",
    "wave",
    "roi",
    "channels",
    "measure",
    "window_start_ms",
    "window_end_ms",
    "value",
    "unit",
    "n_trials",
    "stage_key",
]


class ErpError(ValueError):
    """A component can't be measured with this config or data (the message says why)."""


def condition_ids(epochs: mne.BaseEpochs, selectors: list[str]) -> list[str]:
    return [k for k in epochs.event_id if any(matches(k, s) for s in selectors)]


def average_conditions(
    epochs: mne.BaseEpochs, conditions: dict[str, list[str]]
) -> dict[str, mne.Evoked]:
    out = {}
    for name, sels in conditions.items():
        ids = condition_ids(epochs, sels)
        if not ids or not len(epochs[ids]):
            continue
        ev = epochs[ids].average(picks=["eeg", "eog"])
        ev.comment = name
        out[name] = ev
    return out


def lateralized_waves(
    left: mne.Evoked, right: mne.Evoked, pairs: list[tuple[str, str]]
) -> tuple[mne.Evoked, mne.Evoked]:
    """Contra and ipsi waves for left-/right-target averages (``Contrast.pairs`` docstring).

    Same channel layout as the inputs (MNE stores one layout per file): both channels of a
    pair carry the pair's value, every other channel is NaN.
    """
    names = left.ch_names
    missing = sorted({c for p in pairs for c in p} - set(names))
    if missing:
        raise ErpError(f"lateralized pairs: channels {missing} not in the data")
    contra = left.copy()
    ipsi = left.copy()
    contra.data[:] = np.nan
    ipsi.data[:] = np.nan
    for lh, rh in pairs:
        i, j = names.index(lh), names.index(rh)
        c = (left.data[j] + right.data[i]) / 2
        p = (left.data[i] + right.data[j]) / 2
        contra.data[[i, j]] = c
        ipsi.data[[i, j]] = p
    for ev in (contra, ipsi):
        ev.nave = int(left.nave + right.nave)
    return contra, ipsi


def add_contrasts(evokeds: dict[str, mne.Evoked], cfg: Config) -> dict[str, mne.Evoked]:
    out = dict(evokeds)
    for name, c in cfg.contrasts.items():
        a, b = c.conditions
        if a not in evokeds or b not in evokeds:
            continue
        if c.type == "lateralized":
            contra, ipsi = lateralized_waves(evokeds[a], evokeds[b], list(c.pairs))
            contra.comment, ipsi.comment = c.contra_wave, c.ipsi_wave
            out[c.contra_wave], out[c.ipsi_wave] = contra, ipsi
            a, b = c.contra_wave, c.ipsi_wave
        ev = mne.combine_evoked([out[a], out[b]], [1, -1])
        ev.comment = name
        ev.nave = round(ev.nave)  # FIF stores an integer; round instead of MNE's truncation
        out[name] = ev
    return out


def _n_trials(wave: str, evokeds: dict[str, mne.Evoked], cfg: Config) -> int:
    """Trials behind a wave; for a contrast the smaller of its two conditions (the limiting
    count; MNE's ``nave`` of a difference is an effective, non-integer number)."""
    if wave in cfg.contrasts:
        c = cfg.contrasts[wave]
        if c.type == "lateralized":  # contra and ipsi both use every left and right trial
            return int(evokeds[c.contra_wave].nave)
        a, b = c.conditions
        return min(int(evokeds[a].nave), int(evokeds[b].nave))
    return int(evokeds[wave].nave)


@dataclass
class ComponentMeasures:
    rows: list[dict[str, Any]]
    primary_wave: str
    mean_uv: float
    """Primary mean amplitude (primary ROI, primary wave)."""
    sme_uv: float
    no_local_peak: list[str] = field(default_factory=list)
    peak_latency_ms: float = float("nan")


def _waves(rc: ResolvedComponent, cfg: Config, evokeds: dict[str, mne.Evoked]) -> list[str]:
    d = rc.definition
    if d.contrast is None:
        return [w for w in cfg.conditions if w in evokeds]
    if d.contrast not in cfg.contrasts:
        raise ErpError(
            f"component {rc.id}: contrast {d.contrast!r} is not in the config's contrasts "
            f"({sorted(cfg.contrasts)})"
        )
    c = cfg.contrasts[d.contrast]
    if bool(d.lateralized_pairs) != (c.type == "lateralized"):
        raise ErpError(
            f"component {rc.id}: lateralized_pairs need a lateralized contrast and vice versa "
            f"(contrast {d.contrast!r} is {c.type})"
        )
    missing_pairs = [p for p in d.lateralized_pairs if tuple(p) not in set(c.pairs)]
    if missing_pairs:
        raise ErpError(
            f"component {rc.id}: pairs {missing_pairs} are not in contrast {d.contrast!r}"
        )
    waves = [d.contrast, *c.parents]
    missing = [w for w in waves if w not in evokeds]
    if missing:
        raise ErpError(f"component {rc.id}: no epochs for {missing} in set {d.epoch_set!r}")
    return waves


def _trials(epochs: mne.BaseEpochs, cfg: Config, wave: str, picks: list[str]) -> np.ndarray:
    ids = condition_ids(epochs, cfg.conditions[wave])
    data: np.ndarray = epochs[ids].get_data(picks=picks).mean(axis=1) * UV
    return data  # (n_trials, n_times), ROI pooled


def sme(
    epochs: mne.BaseEpochs,
    cfg: Config,
    wave: str,
    picks: list[str],
    times_ms: np.ndarray,
    window: tuple[float, float],
) -> float:
    """SME of the mean amplitude; for a contrast, the two independent SMEs combined."""
    if wave in cfg.contrasts:
        c = cfg.contrasts[wave]
        if c.type == "lateralized":
            return lateralized_sme(epochs, cfg, c, picks, times_ms, window)
        parts = [sme(epochs, cfg, w, picks, times_ms, window) for w in c.conditions]
        return float(np.sqrt(sum(p**2 for p in parts)))
    for c in cfg.contrasts.values():  # a lateralized parent wave: no per-trial SME
        if c.type == "lateralized" and wave in c.parents:
            return float("nan")
    return sme_mean_amplitude(times_ms, _trials(epochs, cfg, wave, picks), window)


def lateralized_sme(
    epochs: mne.BaseEpochs,
    cfg: Config,
    c: Contrast,
    picks: list[str],
    times_ms: np.ndarray,
    window: tuple[float, float],
) -> float:
    """SME of a contra - ipsi mean amplitude over the ``picks`` pairs.

    contra - ipsi = (mean over left-target trials of (R - L) + mean over right-target trials of
    (L - R)) / 2, so its SME is half the root sum of squares of the two per-trial SMEs.
    """
    pairs = [p for p in c.pairs if p[0] in picks or p[1] in picks]
    parts = []
    for cond, sign in ((c.conditions[0], 1.0), (c.conditions[1], -1.0)):
        ids = condition_ids(epochs, cfg.conditions[cond])
        sub = epochs[ids]
        diff = np.mean(
            [sub.get_data(picks=[rh])[:, 0] - sub.get_data(picks=[lh])[:, 0] for lh, rh in pairs],
            axis=0,
        )
        parts.append(sme_mean_amplitude(times_ms, sign * diff * UV, window))
    return float(np.sqrt(sum(p**2 for p in parts)) / 2)


def measure_component(
    rc: ResolvedComponent,
    evokeds: dict[str, mne.Evoked],
    epochs: mne.BaseEpochs,
    cfg: Config,
    *,
    subject: str,
    stage_key: str,
) -> ComponentMeasures:
    d = rc.definition
    waves = _waves(rc, cfg, evokeds)
    any_ev = evokeds[waves[0]]
    available = set(any_ev.ch_names)
    rois: list[tuple[str, list[str]]] = [("primary", list(d.rois.primary))]
    rois += [("secondary", [ch]) for ch in d.rois.secondary]
    unknown = sorted({ch for _, chs in rois for ch in chs} - available)
    if unknown:
        raise ErpError(
            f"component {rc.id}: ROI channels {unknown} not in the data; available: "
            f"{sorted(available)}"
        )
    times_ms = any_ev.times * 1000
    window = (float(d.window_ms[0]), float(d.window_ms[1]))
    preset = rc.key.split("@", 1)[1]
    base = {
        "subject": subject,
        "session": cfg.recordings.session or "n/a",
        "task": cfg.dataset.task,
        "epoch_set": d.epoch_set,
        "component": rc.id,
        "preset": preset,
        "window_start_ms": window[0],
        "window_end_ms": window[1],
        "stage_key": stage_key,
    }
    rows: list[dict[str, Any]] = []
    no_local: list[str] = []
    primary_mean = float("nan")
    primary_latency = float("nan")
    for wave in waves:
        ev = evokeds[wave]
        ev_peak = ev
        if cfg.erp.peak_lowpass_hz is not None:
            # As ERP CORE: peaks are measured on the low-passed average, everything else not.
            ev_peak = ev.copy().filter(
                None,
                cfg.erp.peak_lowpass_hz,
                method="iir",
                iir_params={
                    "order": cfg.erp.peak_lowpass_order // 2,
                    "ftype": "butter",
                    "output": "sos",
                },
                phase="zero",
                verbose=False,
            )
        for roi, chs in rois:
            x = pool(ev.copy().pick(chs).data * UV)
            x_peak = pool(ev_peak.copy().pick(chs).data * UV) if ev_peak is not ev else x
            common = {
                **base,
                "wave": wave,
                "roi": roi,
                "channels": "+".join(chs),
                "n_trials": _n_trials(wave, evokeds, cfg),
            }
            for m in d.measures:
                if m == "mean_amplitude":
                    vals = [("mean_amplitude", mean_amplitude(times_ms, x, window), "uV")]
                elif m in ("peak_amplitude", "peak_latency"):
                    pk = peak(times_ms, x_peak, window, d.polarity, cfg.erp.peak_neighborhood)
                    if not pk.local:
                        no_local.append(f"{wave}@{roi}:{'+'.join(chs)}")
                    vals = (
                        [("peak_amplitude", pk.amplitude_uv, "uV")]
                        if m == "peak_amplitude"
                        else [("peak_latency", pk.latency_ms, "ms")]
                    )
                else:
                    lat = fractional_area_latency(
                        times_ms, x, window, d.polarity, d.fractional_area
                    )
                    vals = [("fractional_area_latency", lat, "ms")]
                for name, value, unit in vals:
                    rows.append(
                        {**common, "measure": name, "value": round(float(value), 6), "unit": unit}
                    )
                    if wave == waves[0] and roi == "primary":
                        if name == "mean_amplitude":
                            primary_mean = float(value)
                        if name == "peak_latency":
                            primary_latency = float(value)
    if np.isnan(primary_mean):
        x = pool(evokeds[waves[0]].copy().pick(rois[0][1]).data * UV)
        primary_mean = mean_amplitude(times_ms, x, window)
    sme_uv = sme(epochs, cfg, waves[0], rois[0][1], times_ms, window)
    return ComponentMeasures(
        rows, waves[0], primary_mean, sme_uv, sorted(set(no_local)), primary_latency
    )
