"""Group-level computations (Phase 6, docs/BLUEPRINT_PHASES.md section 6.7).

Plain functions on in-memory objects; ``erp/stages/group.py`` only wires files to them.

* **Grand averages**: ``mne.grand_average`` per wave over the included subjects.
* **Tests** (``stats_table``): for each component's *contrast* wave, primary ROI, the mean
  amplitude across subjects is tested against 0. Mean amplitude is linear, so this one-sample
  test on ``plus - minus`` *is* the paired test of the two conditions (``paired_t`` =
  ``scipy.stats.ttest_1samp``, identical to ``ttest_rel``; ``wilcoxon`` = signed-rank on the
  differences). Effect size: Cohen's ``d_z`` = mean / SD of the differences, with a bootstrap
  95 % CI (percentile, seeded) from :data:`MIN_BOOTSTRAP_N` subjects up. Every other
  wave/measure gets descriptive rows only (n, mean, SD, SEM): peak and latency measures of a
  difference wave are not differences of condition measures, and latency tests need
  jackknifing (not implemented).
* **Cluster permutation** (``cluster_test``): ``mne.stats.spatio_temporal_cluster_1samp_test``
  on the contrast (subjects x times x EEG channels) in the configured window, channel adjacency
  from ``mne.channels.find_ch_adjacency``. With N subjects there are only ``2**N`` sign flips:
  the smallest possible p is ``1/2**N`` one-tailed and ``2/2**N`` two-tailed
  (:func:`min_permutation_p`), which the stage reports as ``GROUP_UNDERPOWERED``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import mne
import numpy as np
import pandas as pd
from scipy import stats

from eegunify.config.schema import Config

STATS_COLUMNS = [
    "component",
    "wave",
    "roi",
    "measure",
    "unit",
    "n",
    "mean",
    "sd",
    "sem",
    "test",
    "statistic",
    "p",
    "d_z",
    "d_z_ci_low",
    "d_z_ci_high",
    "note",
]
CLUSTER_COLUMNS = ["cluster", "tmin_ms", "tmax_ms", "channels", "t_sum", "p", "n_subjects"]
TESTED_MEASURES = ("mean_amplitude",)
#: Below this many subjects the d_z bootstrap is degenerate (many resamples repeat one value,
#: so their SD is 0 and d_z explodes: with N = 4 the "CI" reached -45); no CI is given.
MIN_BOOTSTRAP_N = 8


def grand_averages(per_subject: Mapping[str, Mapping[str, mne.Evoked]]) -> dict[str, mne.Evoked]:
    """``{wave: grand average}`` over the subjects that have the wave (``nave`` = subjects)."""
    waves = sorted({w for evs in per_subject.values() for w in evs})
    out = {}
    for w in waves:
        evs = [evs[w] for evs in per_subject.values() if w in evs]
        ga = mne.grand_average(evs, interpolate_bads=False, drop_bads=False)
        ga.comment = w
        out[w] = ga
    return out


def _dz(x: np.ndarray, axis: int = -1) -> np.ndarray:
    sd = np.std(x, ddof=1, axis=axis)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(np.mean(x, axis=axis) / sd)


def effect_size(values: np.ndarray, n_boot: int, seed: int) -> tuple[float, float, float]:
    """Cohen's d_z and its percentile bootstrap 95 % CI (CI NaN below :data:`MIN_BOOTSTRAP_N`)."""
    x = np.asarray(values, dtype=float)
    if len(x) < 2 or np.std(x, ddof=1) == 0:
        return math.nan, math.nan, math.nan
    dz = float(_dz(x))
    if len(x) < MIN_BOOTSTRAP_N:
        return dz, math.nan, math.nan
    res = stats.bootstrap(
        (x,),
        _dz,
        n_resamples=n_boot,
        method="percentile",
        random_state=np.random.default_rng(seed),
        vectorized=True,
    )
    return dz, float(res.confidence_interval.low), float(res.confidence_interval.high)


def stats_table(measures: pd.DataFrame, cfg: Config, contrasts: Sequence[str]) -> pd.DataFrame:
    """One row per (component, wave, measure) of the primary ROI, plus test rows."""
    prim = measures[measures["roi"] == "primary"]
    rows: list[dict[str, Any]] = []
    for (comp, wave, measure), grp in prim.groupby(["component", "wave", "measure"], sort=True):
        x = grp["value"].to_numpy(dtype=float)
        x = x[np.isfinite(x)]
        n = len(x)
        base: dict[str, Any] = {
            "component": comp,
            "wave": wave,
            "roi": "primary",
            "measure": measure,
            "unit": grp["unit"].iloc[0],
            "n": n,
            "mean": float(np.mean(x)) if n else math.nan,
            "sd": float(np.std(x, ddof=1)) if n > 1 else math.nan,
            "sem": float(stats.sem(x)) if n > 1 else math.nan,
        }
        tested = wave in contrasts and measure in TESTED_MEASURES
        if not tested:
            rows.append({**base, "test": "descriptive", "note": ""})
            continue
        dz, lo, hi = effect_size(x, cfg.group.n_bootstrap, cfg.group.random_state)
        for test in cfg.group.tests:
            stat, p = math.nan, math.nan
            if n >= 2 and test == "paired_t":
                r = stats.ttest_1samp(x, 0.0)
                stat, p = float(r.statistic), float(r.pvalue)
            elif n >= 1 and test == "wilcoxon" and np.any(x != 0):
                r = stats.wilcoxon(x)
                stat, p = float(r.statistic), float(r.pvalue)
            rows.append(
                {
                    **base,
                    "test": test,
                    "statistic": stat,
                    "p": p,
                    "d_z": dz,
                    "d_z_ci_low": lo,
                    "d_z_ci_high": hi,
                    "note": "contrast vs 0 = paired test of the two conditions (linear measure)"
                    + (
                        f"; no d_z CI below {MIN_BOOTSTRAP_N} subjects"
                        if n < MIN_BOOTSTRAP_N
                        else ""
                    ),
                }
            )
    return pd.DataFrame(rows, columns=STATS_COLUMNS)


def min_permutation_p(n_subjects: int, tail: int) -> float:
    """Smallest p a sign-flip permutation test can give with ``n_subjects``."""
    if n_subjects < 1:
        return 1.0
    return float((1.0 if tail != 0 else 2.0) / 2**n_subjects)


def cluster_test(
    contrast: Sequence[mne.Evoked], cfg: Config
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Cluster permutation test of the contrast against 0 (subjects x times x EEG channels)."""
    spec = cfg.group.cluster
    evs = [
        ev.copy().pick("eeg", exclude="bads").crop(spec.tmin_ms / 1000, spec.tmax_ms / 1000)
        for ev in contrast
    ]
    chans = evs[0].ch_names
    if any(ev.ch_names != chans for ev in evs):
        raise ValueError("cluster test: the subjects' contrast waves have different channels")
    x = np.stack([ev.data.T for ev in evs])  # (subjects, times, channels)
    adjacency, _ = mne.channels.find_ch_adjacency(evs[0].info, ch_type="eeg")
    n = len(evs)
    n_perm = min(spec.n_permutations, 2**n)  # MNE enumerates all flips when possible
    t_obs, clusters, pvals, _ = mne.stats.spatio_temporal_cluster_1samp_test(
        x,
        threshold=spec.threshold,
        n_permutations=n_perm,
        tail=spec.tail,
        adjacency=adjacency,
        seed=spec.random_state,
        out_type="indices",
        verbose=False,
    )
    times_ms = evs[0].times * 1000
    rows = []
    for k, (cl, p) in enumerate(zip(clusters, pvals, strict=True)):
        t_idx, ch_idx = cl
        rows.append(
            {
                "cluster": k + 1,
                "tmin_ms": float(times_ms[np.min(t_idx)]),
                "tmax_ms": float(times_ms[np.max(t_idx)]),
                "channels": "+".join(sorted({chans[i] for i in ch_idx})),
                "t_sum": float(t_obs[t_idx, ch_idx].sum()),
                "p": float(p),
                "n_subjects": n,
            }
        )
    table = pd.DataFrame(rows, columns=CLUSTER_COLUMNS).sort_values("p", kind="stable")
    info = {
        "n_subjects": n,
        "n_permutations": n_perm,
        "tail": spec.tail,
        "window_ms": [spec.tmin_ms, spec.tmax_ms],
        "min_possible_p": min_permutation_p(n, spec.tail),
        "n_clusters": len(rows),
    }
    return table.reset_index(drop=True), info
