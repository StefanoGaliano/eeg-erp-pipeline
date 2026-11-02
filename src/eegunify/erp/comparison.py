"""Our measurements vs ERP CORE's published per-subject values (docs/BLUEPRINT_PHASES.md 8.2).

Rules (decided 2026-11-01, BLUEPRINT_PHASES section 0):

* a subject is **compared** only if ERP CORE included it (``<component>_subjects.tsv``);
  subjects ERP CORE excluded are listed with ``status = excluded_by_erpcore`` and their reason,
  and are not part of any check or of the subject mean;
* per compared subject: ``|ours - theirs|`` within the tolerance (µV for amplitudes, ms for
  latencies) and, for amplitudes whose published value is larger than the tolerance in
  absolute value, **our sign equal to theirs** (not "all negative"; near zero, either sign is
  within the tolerance);
* the subject mean is taken over the compared subjects with both values, tolerance ``mean_uv``;
* a published NaN (e.g. no negative area for a 50 % area latency) is ``not_available``;
* a measure our component definition does not take is ``not_measured`` (never a failure).

* for difference-wave mean amplitudes, ``subject = d_z`` reports the effect size (mean / SD
  over the same compared subjects, ddof 1) on both sides; no tolerance is defined for it
  (status ``reported``, never a failure), so it is read next to the published one (M-E6b).

``comparison_table`` returns one row per (subject, wave, measure) plus ``subject = mean`` (and
``d_z``) rows.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from eegunify.reference_values import published_value

COLUMNS = [
    "component",
    "subject",
    "wave",
    "measure",
    "unit",
    "ours",
    "theirs",
    "diff",
    "tolerance",
    "status",
    "note",
]


@dataclass(frozen=True)
class Tolerances:
    """Blueprint 8.2: ±0.5 µV per subject, ±0.3 µV for the subject mean, ±8 ms latency."""

    subject_uv: float = 0.5
    mean_uv: float = 0.3
    latency_ms: float = 8.0


@dataclass(frozen=True)
class Pairing:
    """One of our waves/measures and the ERP CORE wave it is compared with."""

    ours_wave: str  # wave in our measures.tsv (condition or contrast name)
    theirs_wave: str  # ERP CORE's wave label (from their measurement files), matched exactly
    measure: str
    note: str = ""


PEAK_NOTE = "ERP CORE measures peak latency on the 20 Hz low-passed ERP; ours is unfiltered"

#: Which waves are compared per component (ERP CORE bin labels from their measurement files).
PAIRINGS: dict[str, tuple[Pairing, ...]] = {
    "N170": (
        Pairing("face_minus_car", "Faces minus Cars", "mean_amplitude"),
        Pairing("face", "Faces, Correct", "mean_amplitude"),
        Pairing("car", "Cars, Correct", "mean_amplitude"),
        Pairing("face_minus_car", "Faces minus Cars", "fractional_area_latency"),
        Pairing("face_minus_car", "Faces minus Cars", "peak_latency", PEAK_NOTE),
    ),
    "P3": (
        Pairing("rare_minus_frequent", "Rare minus Frequent", "mean_amplitude"),
        Pairing("rare", "Rare, Correct", "mean_amplitude"),
        Pairing("frequent", "Frequent, Correct", "mean_amplitude"),
        Pairing("rare_minus_frequent", "Rare minus Frequent", "fractional_area_latency"),
        Pairing("rare_minus_frequent", "Rare minus Frequent", "peak_latency", PEAK_NOTE),
    ),
    "N2pc": (
        Pairing("contra_minus_ipsi", "Contra minus Ipsi, All", "mean_amplitude"),
        Pairing("contra", "Contra, All", "mean_amplitude"),
        Pairing("ipsi", "Ipsi, All", "mean_amplitude"),
        Pairing("contra_minus_ipsi", "Contra minus Ipsi, All", "fractional_area_latency"),
        Pairing("contra_minus_ipsi", "Contra minus Ipsi, All", "peak_latency", PEAK_NOTE),
    ),
}


def _status(ours: float, theirs: float, tol: float, signed: bool) -> str:
    if math.isnan(ours):
        return "missing_ours"
    # The sign is only checked when the published value is clearly away from zero
    # (|theirs| > tolerance): near zero, either sign is within the tolerance (decided
    # 2026-11-01 after N2pc sub-019: ours +0.002 uV, ERP CORE's -0.151 uV).
    if signed and abs(theirs) > tol and (ours > 0) != (theirs > 0):
        return "sign_mismatch"
    return "ok" if abs(ours - theirs) <= tol else "out_of_tolerance"


def comparison_table(
    component: str,
    ours: pd.DataFrame,
    published: pd.DataFrame,
    subjects_table: pd.DataFrame,
    subjects: Iterable[str],
    tol: Tolerances | None = None,
) -> pd.DataFrame:
    """Compare ``ours`` (primary-ROI rows of measures.tsv, all subjects) with ERP CORE.

    ``published``/``subjects_table`` are :func:`eegunify.reference_values.read_tidy` output.
    """
    tol = tol or Tolerances()
    if component not in PAIRINGS:
        raise ValueError(f"no comparison defined for {component!r}; have {list(PAIRINGS)}")
    status_of = subjects_table.set_index("subject")
    prim = ours[ours["roi"] == "primary"]
    rows: list[dict[str, object]] = []
    for p in PAIRINGS[component]:
        if prim[(prim["wave"] == p.ours_wave) & (prim["measure"] == p.measure)].empty:
            # Our component definition does not take this measure (e.g. the p3 preset has no
            # peak latency): nothing to compare, and not a failure.
            for sub in subjects:
                rows.append(
                    {
                        "component": component,
                        "subject": sub,
                        "wave": p.ours_wave,
                        "measure": p.measure,
                        "unit": "ms" if p.measure.endswith("latency") else "uV",
                        "ours": math.nan,
                        "theirs": math.nan,
                        "diff": math.nan,
                        "tolerance": math.nan,
                        "status": "not_measured",
                        "note": "not in the component's measures",
                    }
                )
            continue
        latency = p.measure.endswith("latency")
        unit = "ms" if latency else "uV"
        tolerance = tol.latency_ms if latency else tol.subject_uv
        compared: list[tuple[float, float]] = []
        for sub in subjects:
            hit = prim[(prim["subject"] == sub) & (prim["wave"] == p.ours_wave)]
            hit = hit[hit["measure"] == p.measure]
            mine = float(hit["value"].iloc[0]) if len(hit) else math.nan
            row: dict[str, object] = {
                "component": component,
                "subject": sub,
                "wave": p.ours_wave,
                "measure": p.measure,
                "unit": unit,
                "ours": mine,
                "theirs": math.nan,
                "diff": math.nan,
                "tolerance": tolerance,
                "note": p.note,
            }
            if sub not in status_of.index:
                rows.append({**row, "status": "not_in_erpcore"})
                continue
            if not bool(status_of.loc[sub, "included"]):
                reason = str(status_of.loc[sub, "reason"])
                rows.append({**row, "status": "excluded_by_erpcore", "note": f"reason {reason}"})
                continue
            theirs = published_value(published, sub, p.measure, p.theirs_wave)
            if theirs is None:
                rows.append({**row, "status": "not_available", "note": "published value is NaN"})
                continue
            row.update(theirs=theirs, diff=mine - theirs)
            rows.append({**row, "status": _status(mine, theirs, tolerance, not latency)})
            if not math.isnan(mine):
                compared.append((mine, theirs))
        if compared:
            m_ours = sum(o for o, _ in compared) / len(compared)
            m_theirs = sum(t for _, t in compared) / len(compared)
            mean_tol = tol.latency_ms if latency else tol.mean_uv
            rows.append(
                {
                    "component": component,
                    "subject": "mean",
                    "wave": p.ours_wave,
                    "measure": p.measure,
                    "unit": unit,
                    "ours": m_ours,
                    "theirs": m_theirs,
                    "diff": m_ours - m_theirs,
                    "tolerance": mean_tol,
                    "status": "ok" if abs(m_ours - m_theirs) <= mean_tol else "out_of_tolerance",
                    "note": f"mean over {len(compared)} compared subjects",
                }
            )
            if p.measure == "mean_amplitude" and " minus " in p.theirs_wave and len(compared) >= 3:
                dz_ours = _d_z([o for o, _ in compared])
                dz_theirs = _d_z([t for _, t in compared])
                rows.append(
                    {
                        "component": component,
                        "subject": "d_z",
                        "wave": p.ours_wave,
                        "measure": p.measure,
                        "unit": "d_z",
                        "ours": dz_ours,
                        "theirs": dz_theirs,
                        "diff": dz_ours - dz_theirs,
                        "tolerance": math.nan,
                        "status": "reported",
                        "note": f"mean / SD over {len(compared)} compared subjects",
                    }
                )
    return pd.DataFrame(rows, columns=COLUMNS)


def _d_z(values: list[float]) -> float:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return mean / sd if sd > 0 else math.nan


def failures(table: pd.DataFrame) -> pd.DataFrame:
    """Rows that fail a check (excluded/not-available rows never fail)."""
    bad = {"sign_mismatch", "out_of_tolerance", "missing_ours"}
    return table[table["status"].isin(bad)]
