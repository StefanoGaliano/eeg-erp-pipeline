"""ERP CORE's own per-subject processing choices (``erpcore-exact`` recipe, BLUEPRINT 8.2a).

ERP CORE publishes, next to its MATLAB scripts, the parameters each subject was processed with:
the interpolated channels, the ICA components removed (with the ICA weights themselves, inside
each subject's ``*_ica_weighted.set``) and five artifact-rejection tests with per-subject
settings. This module downloads those files (sha256 in the manifest), and turns them into
``src/eegunify/erp/reference/<component>_exact.json``, which
:func:`eegunify.erp.recipes.erpcore_exact` reads. Channel numbers in their spreadsheets index
the epoched data of ``Script6_Artifact_Rejection.m``, whose channel list is the one written by
``Add_Corrected_Bipolars_<component>.txt``; they are resolved to channel names here.

N170, P3 and N2pc are described (their scripts differ only in the reference: N170 averages all
33 recorded sites, P3 and N2pc use the mean of P9/P10).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eegunify.datasets import Manifest
from eegunify.reference_values import OSF_DOWNLOAD, _download, _xlsx_rows

#: Names eegunify uses for ERP CORE's derived channels (``Add_Corrected_Bipolars``).
DERIVED_NAMES = {
    "(corr) HEOG": "HEOG",
    "(corr) VEOG": "VEOG",
    "(uncorr) HEOG": "HEOG_uncorr",
    "(uncorr) VEOG": "VEOG_uncorr",
}
#: ``Script6_Artifact_Rejection.m``: what "default" means in the channel column of the
#: SVT and moving-window spreadsheets (``Channels = 1:31`` and ``Channels = 1:28``).
SCRIPT6_DEFAULT_CHANNELS = {"svt": 31, "mw": 28}


@dataclass(frozen=True)
class ExactFiles:
    """OSF ids of one component's parameter files (listed from the OSF API on 2026-11-01)."""

    node: str
    bipolars: str  # Add_Corrected_Bipolars_<c>.txt (channel list of the epoched data)
    reref: str  # Rereference_Add_Uncorrected_Bipolars_<c>.txt
    ica_components: str
    interpolate: str
    svt: str
    mw_crap: str
    mw_blinks: str
    sl_heog_stim: str
    sl_heog: str
    ica_weights: dict[str, str]  # subject -> osf id of <sub>_<c>_..._ica_weighted.set
    ar_percentages: dict[str, str]  # subject -> osf id of <sub>_AR_Percentages_<c>.csv


EXACT_FILES: dict[str, ExactFiles] = {
    "N170": ExactFiles(
        node="pfde9",
        bipolars="37pjr",
        reref="y6p8s",
        ica_components="f9r7c",
        interpolate="dsyke",
        svt="4dmrj",
        mw_crap="e8cyf",
        mw_blinks="79k42",
        sl_heog_stim="92fkr",
        sl_heog="d35zu",
        ica_weights={
            "015": "5f1623c50870f2012709cc3a",
            "016": "5f1624790596f6011d79e293",
            "017": "5f1625450596f601227a0bba",
            "018": "wzfyd",
            "019": "5f1626c06ef4400130bd18db",
        },
        ar_percentages={
            "015": "f9nmw",
            "016": "5f1623e30870f2011709ba4f",
            "017": "5f1624940596f6012179e490",
            "018": "5f1625606ef4400137bcb1a0",
            "019": "5f1626200870f201330988ce",
        },
    ),
    "P3": ExactFiles(
        node="etdkz",
        bipolars="wm6xv",
        reref="w4z75",
        ica_components="6qyhj",
        interpolate="y5ntr",
        svt="yrgdb",
        mw_crap="xp36w",
        mw_blinks="pj8wm",
        sl_heog_stim="4ba9v",
        sl_heog="k875y",
        ica_weights={
            "015": "x84fs",
            "016": "t5e9v",
            "017": "f93hy",
            "018": "7u2yv",
            "019": "jsdpc",
        },
        ar_percentages={
            "015": "zjv7g",
            "016": "wa6hu",
            "017": "qyjzt",
            "018": "6gj95",
            "019": "ywnz5",
        },
    ),
    "N2pc": ExactFiles(
        node="yefrq",
        bipolars="un54k",
        reref="n5wp8",
        ica_components="bvgkm",
        interpolate="kd3u9",
        svt="zyrb5",
        mw_crap="9xwyr",
        mw_blinks="jg3cp",
        sl_heog_stim="crnxs",
        sl_heog="w38gz",
        ica_weights={
            "015": "612d6fcc4e5ee50050290077",
            "016": "612d70d9ab8bca0053edbab6",
            "017": "612d719d4e5ee5004a2906bb",
            "018": "612d726aab8bca004fedbd7e",
            "019": "612d7305af610c004cdfd875",
        },
        ar_percentages={
            "015": "612d6ebaaf610c004bdfcb8f",
            "016": "612d6ff2ab8bca0053edb7a7",
            "017": "612d70f8ab8bca0052edb782",
            "018": "qkhgc",
            "019": "612d7289ab8bca0053edc10e",
        },
    ),
}


def _files(component: str) -> ExactFiles:
    if component not in EXACT_FILES:
        raise ValueError(f"no erpcore-exact parameters for {component!r}; have {list(EXACT_FILES)}")
    return EXACT_FILES[component]


def file_names(component: str) -> dict[str, str]:
    """Local file name -> OSF id for everything :func:`fetch_exact` downloads."""
    f = _files(component)
    c = component
    names = {
        f"Add_Corrected_Bipolars_{c}.txt": f.bipolars,
        f"Rereference_Add_Uncorrected_Bipolars_{c}.txt": f.reref,
        f"ICA_Components_{c}.xlsx": f.ica_components,
        f"Interpolate_Channels_{c}.xlsx": f.interpolate,
        f"AR_Parameters_for_SVT_CRAP_{c}.xlsx": f.svt,
        f"AR_Parameters_for_MW_CRAP_{c}.xlsx": f.mw_crap,
        f"AR_Parameters_for_MW_Blinks_{c}.xlsx": f.mw_blinks,
        f"AR_Parameters_for_SL_HEOG_Stim_Pres_{c}.xlsx": f.sl_heog_stim,
        f"AR_Parameters_for_SL_HEOG_{c}.xlsx": f.sl_heog,
    }
    for sub, osf_id in f.ica_weights.items():
        names[f"{int(sub)}_{c}_ica_weighted.set"] = osf_id
    for sub, osf_id in f.ar_percentages.items():
        names[f"{int(sub)}_AR_Percentages_{c}.csv"] = osf_id
    return names


def fetch_exact(dest: Path, component: str) -> Path:
    """Download the parameter files and ICA weight headers into ``dest/erpcore_exact/<c>/``."""
    f = _files(component)
    folder = dest / "erpcore_exact" / component
    folder.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(dest)
    for name, osf_id in file_names(component).items():
        rel = (folder / name).relative_to(dest).as_posix()
        if not manifest.is_current(dest, rel):
            _download(OSF_DOWNLOAD.format(osf_id), dest / rel)
            manifest.record(dest, rel, f"osf:{f.node}/{osf_id}")
    return folder


def channel_list(path: Path) -> list[str]:
    """Channel names, in order, of an ERPLAB channel-operations file (``nchN = ... Label X``).

    ERP CORE's derived bipolars get eegunify names (:data:`DERIVED_NAMES`).
    """
    out: dict[int, str] = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        m = re.match(r"\s*nch(\d+)\s*=.*?Label\s+(.+?)\s*$", line)
        if m:
            label = m.group(2).strip()
            out[int(m.group(1))] = DERIVED_NAMES.get(label, label)
    if sorted(out) != list(range(1, len(out) + 1)):
        raise ValueError(f"{path.name}: channel numbers are not 1..n: {sorted(out)}")
    return [out[i] for i in range(1, len(out) + 1)]


def bipolar_definitions(path: Path, inputs: dict[int, str]) -> dict[str, list[str]]:
    """``nchN = (chA - chB) Label X`` lines -> ``{X: [name of chA, name of chB]}``.

    ``inputs`` names the file's input channels (``chA``) by number.
    """
    out = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        m = re.match(r"\s*nch\d+\s*=\s*\(\s*ch(\d+)\s*-\s*ch(\d+)\s*\)\s*Label\s+(.+?)\s*$", line)
        if m:
            a, b, label = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            if a not in inputs or b not in inputs:
                raise ValueError(f"{path.name}: {label} uses unknown input channel ch{a}/ch{b}")
            out[DERIVED_NAMES.get(label, label)] = [inputs[a], inputs[b]]
    return out


def recorded_inputs(path: Path) -> dict[int, str]:
    """Rereference file: ``nchN = chM - ( <reference> ) Label X`` -> ``{M: X}`` (raw numbering)."""
    out = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        m = re.match(r"\s*nch\d+\s*=\s*ch(\d+)\s*-\s*\(.*?\)\s*\)?\s*Label\s+(.+?)\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _rows_by_subject(path: Path) -> dict[str, list[str]]:
    out = {}
    for row in _xlsx_rows(path):
        if row and re.fullmatch(r"\d+(\.0)?", row[0]):
            out[f"{int(float(row[0])):03d}"] = row[1:]
    return out


def _num(x: str) -> float:
    v = float(x)
    return int(v) if v.is_integer() else v


def _channels(cell: str, default_n: int, chans: list[str]) -> list[str]:
    """Channel column: ``default`` (Script6's 1:n) or 1-based numbers into ``chans``."""
    if cell.strip().lower() == "default":
        idx = list(range(1, default_n + 1))
    else:
        idx = [int(float(x)) for x in re.split(r"[\s,;]+", cell.strip()) if x]
    bad = [i for i in idx if not 1 <= i <= len(chans)]
    if bad:
        raise ValueError(f"channel numbers {bad} outside 1..{len(chans)}")
    return [chans[i - 1] for i in idx]


def subject_parameters(folder: Path, component: str, subject: str) -> dict[str, Any]:
    """One subject's interpolation, ICA and rejection settings, with channel names."""
    c = component
    chans = channel_list(folder / f"Add_Corrected_Bipolars_{c}.txt")

    def row(name: str) -> list[str]:
        rows = _rows_by_subject(folder / name)
        if subject not in rows:
            raise ValueError(f"{name}: no row for subject {subject}")
        return rows[subject]

    interp = row(f"Interpolate_Channels_{c}.xlsx")
    # names: one cell, comma-separated when several ("*F8" or "F8, C6" in N2pc)
    named = [x.strip().lstrip("*").strip() for x in ",".join(interp[1:2]).split(",") if x.strip()]
    nums = interp[0].strip() if interp else ""
    interpolate: list[str] = []
    if nums and nums.lower() != "none":
        interpolate = _channels(nums, 0, chans)
        if named and [n.lower() for n in named] != [i.lower() for i in interpolate]:
            raise ValueError(f"{subject}: interpolation numbers {nums} != names {named}")
    ica = [int(float(x)) for x in row(f"ICA_Components_{c}.xlsx") if x.strip()]

    svt = row(f"AR_Parameters_for_SVT_CRAP_{c}.xlsx")
    mw = row(f"AR_Parameters_for_MW_CRAP_{c}.xlsx")
    blink = row(f"AR_Parameters_for_MW_Blinks_{c}.xlsx")
    stim = row(f"AR_Parameters_for_SL_HEOG_Stim_Pres_{c}.xlsx")
    heog = row(f"AR_Parameters_for_SL_HEOG_{c}.xlsx")
    rules = [
        {
            "test": "extval",
            "erplab": "pop_artextval",
            "channels": _channels(svt[0], SCRIPT6_DEFAULT_CHANNELS["svt"], chans),
            "min_uv": _num(svt[1]),
            "max_uv": _num(svt[2]),
            "window_ms": [_num(svt[3]), _num(svt[4])],
        }
    ]
    for test, erplab, r, default in (
        ("ptp", "pop_artmwppth", mw, SCRIPT6_DEFAULT_CHANNELS["mw"]),
        ("ptp", "pop_artmwppth", blink, 0),
        ("step", "pop_artstep", stim, 0),
        ("step", "pop_artstep", heog, 0),
    ):
        rules.append(
            {
                "test": test,
                "erplab": erplab,
                "channels": _channels(r[0], default, chans),
                "threshold_uv": _num(r[1]),
                "window_ms": [_num(r[2]), _num(r[3])],
                "win_ms": _num(r[4]),
                "step_ms": _num(r[5]),
            }
        )
    return {
        "interpolate": interpolate,
        "ica_weights": f"{int(subject)}_{c}_ica_weighted.set",
        "ica_exclude_eeglab": ica,
        "reject": rules,
        "ar_percentages": _ar_percentages(folder / f"{int(subject)}_AR_Percentages_{c}.csv"),
    }


def _ar_percentages(path: Path) -> dict[str, dict[str, float]]:
    """``<sub>_AR_Percentages_<c>.csv``: accepted/rejected per bin (and ``Total``)."""
    out: dict[str, dict[str, float]] = {}
    lines = path.read_text(encoding="latin-1").splitlines()
    for line in lines[1:]:
        cells = [x.strip() for x in line.split(",")]
        if len(cells) >= 5 and cells[1]:
            out[cells[1]] = {"accepted": _num(cells[2]), "rejected": _num(cells[3])}
    return out


def build(folder: Path, component: str) -> dict[str, Any]:
    """The ``<component>_exact.json`` content for the subjects whose weights are published."""
    f = _files(component)
    corrected_file = folder / f"Add_Corrected_Bipolars_{component}.txt"
    reref_file = folder / f"Rereference_Add_Uncorrected_Bipolars_{component}.txt"
    chans = channel_list(corrected_file)
    reref = channel_list(reref_file)
    # Uncorrected bipolars use the recorded channel numbers; corrected ones the re-referenced
    # list (the input of Add_Corrected_Bipolars).
    bipolar = bipolar_definitions(reref_file, recorded_inputs(reref_file))
    bipolar |= bipolar_definitions(corrected_file, dict(enumerate(reref, start=1)))
    return {
        "component": component,
        "source": f"ERP CORE OSF project {f.node}; generated by `eegunify fetch erpcore-values`",
        "epoch_channels": chans,
        "reference": {
            # Rereference_Add_Uncorrected_Bipolars: every channel minus the mean of these
            # recorded channels (1-based, the recording's own order): N170 all 33 sites (EOG
            # electrodes and P9/P10 included), P3/N2pc P9 and P10 (9, 27).
            "recorded_channels": reference_channels(reref_file),
            # recorded number -> label, for checking that a recording has the same order
            "recorded_labels": {str(k): v for k, v in sorted(recorded_inputs(reref_file).items())},
            "kept_channels": reref,
        },
        "bipolar": bipolar,
        "subjects": {
            sub: subject_parameters(folder, component, sub) for sub in sorted(f.ica_weights)
        },
    }


def reference_channels(path: Path) -> list[int]:
    """Recorded channel numbers (1-based) the re-reference subtracts the mean of.

    ``avgchan(1:33)`` (N170) -> 1..33; ``( (ch9 + ch27)/2 )`` (P3, N2pc) -> [9, 27]. Every
    re-referenced channel must use the same reference.
    """
    found = set()
    for line in path.read_text(encoding="latin-1").splitlines():
        m = re.match(r"\s*nch\d+\s*=\s*ch\d+\s*-\s*\((.*)\)\s*Label", line)
        if m:
            found.add(re.sub(r"\s+", "", m.group(1)))
    if len(found) != 1:
        raise ValueError(f"{path.name}: expected one reference expression, got {sorted(found)}")
    expr = found.pop()
    avg = re.fullmatch(r"avgchan\(1:(\d+)\)", expr)
    if avg:
        return list(range(1, int(avg.group(1)) + 1))
    mean = re.fullmatch(r"\(((?:ch\d+\+)*ch\d+)\)/(\d+)", expr)
    if mean:
        chans = [int(c) for c in re.findall(r"ch(\d+)", mean.group(1))]
        if len(chans) != int(mean.group(2)):
            raise ValueError(f"{path.name}: {expr} is not a mean of its channels")
        return chans
    raise ValueError(f"{path.name}: unsupported reference expression {expr!r}")


def write_exact(data: dict[str, Any], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{data['component']}_exact.json"
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return path
