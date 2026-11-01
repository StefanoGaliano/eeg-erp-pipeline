"""Apply an ICA decomposition computed in EEGLAB (``artifacts.method: precomputed``).

ERP CORE publishes each subject's ``*_ica_weighted.set``. EEGLAB stores the decomposition in
the dataset header: ``icaweights`` and ``icasphere`` (unmixing = weights @ sphere) over the
channels ``icachansind`` (1-based). Removing components is linear and does not depend on the
data's units: ``X_clean = X - A[:, k] @ (W[k, :] @ X)`` with ``A = pinv(W)``, applied to those
channels only, in the data's current reference (the one the decomposition was computed in).
Channel labels are matched case-insensitively (ERP CORE writes ``FP1``, eegunify ``Fp1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
import scipy.io

from eegunify.config.erp_sections import PrecomputedIcaSpec
from eegunify.erp.ica import Decision, IcaError


@dataclass
class EeglabDecomposition:
    channels: list[str]
    """Labels of the ``icachansind`` channels, in the unmixing matrix's column order."""
    unmixing: np.ndarray
    """``icaweights @ icasphere``: (n_components, n_channels)."""


def _field(eeg: Any, name: str) -> Any:
    if isinstance(eeg, dict):
        return eeg[name]
    return getattr(eeg, name)


def read_eeglab_decomposition(path: Path) -> EeglabDecomposition:
    """Read the ICA fields of an EEGLAB ``.set`` (MATLAB v5 file; the ``.fdt`` is not needed)."""
    try:
        mat = scipy.io.loadmat(path, squeeze_me=True, struct_as_record=False)
    except NotImplementedError as err:  # MATLAB v7.3 (HDF5)
        raise IcaError(f"{path.name}: MATLAB v7.3 .set files are not supported") from err
    eeg: Any = mat["EEG"] if "EEG" in mat else {k: v for k, v in mat.items() if k[:2] != "__"}
    weights = np.atleast_2d(np.asarray(_field(eeg, "icaweights"), dtype=float))
    sphere = np.atleast_2d(np.asarray(_field(eeg, "icasphere"), dtype=float))
    if weights.size == 0 or sphere.size == 0:
        raise IcaError(f"{path.name}: no ICA decomposition (icaweights/icasphere empty)")
    chanind = np.atleast_1d(np.asarray(_field(eeg, "icachansind"), dtype=int))
    labels = [str(c.labels) for c in np.atleast_1d(_field(eeg, "chanlocs"))]
    channels = [labels[i - 1] for i in chanind]
    unmixing = weights @ sphere
    if unmixing.shape[1] != len(channels):
        raise IcaError(
            f"{path.name}: unmixing has {unmixing.shape[1]} columns for {len(channels)} channels"
        )
    return EeglabDecomposition(channels, unmixing)


def resolve_weights(spec: PrecomputedIcaSpec, project_root: Path) -> Path:
    p = Path(spec.weights)
    return p if p.is_absolute() else project_root / p


def apply_precomputed(
    raw: mne.io.BaseRaw, decomp: EeglabDecomposition, exclude_eeglab: list[int]
) -> Decision:
    """Remove the components (EEGLAB numbers) from ``raw`` in place; one table row each."""
    n = decomp.unmixing.shape[0]
    bad = [k for k in exclude_eeglab if not 1 <= k <= n]
    if bad:
        raise IcaError(f"artifacts.precomputed.exclude_eeglab {bad}: only {n} components")
    lower = {c.lower(): c for c in raw.ch_names}
    missing = [c for c in decomp.channels if c.lower() not in lower]
    if missing:
        raise IcaError(f"precomputed ICA channels {missing} not in the data")
    idx = [raw.ch_names.index(lower[c.lower()]) for c in decomp.channels]
    k = [i - 1 for i in exclude_eeglab]
    w = decomp.unmixing
    a = np.linalg.pinv(w)
    x = raw.get_data(picks=idx)
    raw._data[idx] = x - a[:, k] @ (w[k, :] @ x)
    rows = [
        {
            "idx": i,
            "eeglab_component": i + 1,
            "iclabel": "n/a",
            "iclabel_prob": np.nan,
            "p_brain": np.nan,
            "excluded": i in k,
            "reason": "precomputed exclude" if i in k else "kept",
        }
        for i in range(n)
    ]
    return Decision(pd.DataFrame(rows), [])
