"""ERP CORE published per-subject measurements (docs/BLUEPRINT_PHASES.md section 8.2).

Each ERP CORE component has its own OSF project (Kappenman et al., 2021, NeuroImage 225:117465;
CC-BY-4.0). Its ``EEG_ERP_Processing/ERP_Measurements`` folder holds ERPLAB measurement text
files, one row per included subject, and ``<component>_Subject_Summary.xlsx`` lists the
subjects ERP CORE excluded and why. This module downloads those files (sha256 in the
manifest, like :mod:`eegunify.datasets`) and turns them into two tidy TSVs per component:

* ``<component>.tsv``: ``subject, wave, channels, measure, value, unit, lowpass, source``.
  ``value`` is ``n/a`` where ERPLAB wrote NaN (e.g. no negative area for a 50 % area latency).
* ``<component>_subjects.tsv``: ``subject, included, reason`` for all 40 subjects.

The numbers are only ever taken from these files, never typed in (CLAUDE.md rule).
"""

from __future__ import annotations

import logging
import math
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd

from eegunify.datasets import USER_AGENT, Manifest, _retry

log = logging.getLogger(__name__)

OSF_DOWNLOAD = "https://osf.io/download/{}/"
CITATION = (
    "Kappenman, E. S., Farrens, J. L., Zhang, W., Stewart, A. X., & Luck, S. J. (2021). "
    "ERP CORE: An open resource for human event-related potential research. "
    "NeuroImage, 225, 117465. CC-BY-4.0."
)
N_SUBJECTS = 40
#: Where the tidy tables ship with the package (small, CC-BY-4.0; rewritten by
#: ``eegunify fetch erpcore-values``).
REFERENCE_DIR = Path(__file__).parent / "erp" / "reference"


@dataclass(frozen=True)
class MeasureFile:
    """One ERPLAB measurement file of a component's ``ERP_Measurements`` folder."""

    osf_id: str
    name: str
    measure: str  # eegunify measure name (measures.tsv)
    lowpass: bool  # measured on the 20 Hz low-passed ERPs (``*_lpfilt``)


@dataclass(frozen=True)
class ComponentValues:
    """Where a component's published values live on OSF."""

    node: str
    summary: tuple[str, str]  # (osf id, file name) of the subject summary
    files: tuple[MeasureFile, ...]


# OSF file ids listed from the API (nodes/<node>/files/osfstorage, folder
# "<component> All Data and Scripts/EEG_ERP_Processing/ERP_Measurements") on 2026-11-01.
ERPCORE_VALUES: dict[str, ComponentValues] = {
    "N170": ComponentValues(
        node="pfde9",
        summary=("uyb3t", "N170_Subject_Summary.xlsx"),
        files=(
            MeasureFile("x4a2q", "Mean_Amplitude_Diff_Waves_N170.txt", "mean_amplitude", False),
            MeasureFile("dbth6", "Mean_Amplitude_Parent_Waves_N170.txt", "mean_amplitude", False),
            MeasureFile(
                "sju5k", "50%_Area_Latency_Diff_Waves_N170.txt", "fractional_area_latency", False
            ),
            MeasureFile("yxnfk", "Peak_Latency_Diff_Waves_N170.txt", "peak_latency", True),
        ),
    ),
    "P3": ComponentValues(
        node="etdkz",
        summary=("g5x2w", "P3_Subject_Summary.xlsx"),
        files=(
            MeasureFile("xshdk", "Mean_Amplitude_Diff_Waves_P3.txt", "mean_amplitude", False),
            MeasureFile("nat8v", "Mean_Amplitude_Parent_Waves_P3.txt", "mean_amplitude", False),
            MeasureFile(
                "k63sh", "50%_Area_Latency_Diff_Waves_P3.txt", "fractional_area_latency", False
            ),
            MeasureFile("s45qt", "Peak_Latency_Diff_Waves_P3.txt", "peak_latency", True),
        ),
    ),
    "N2pc": ComponentValues(
        node="yefrq",
        summary=("spqtv", "N2pc_Subject_Summary.xlsx"),
        files=(
            MeasureFile("5evzf", "Mean_Amplitude_Diff_Waves_N2pc.txt", "mean_amplitude", False),
            MeasureFile("c9ukf", "Mean_Amplitude_Parent_Waves_N2pc.txt", "mean_amplitude", False),
            MeasureFile(
                "s7vgy", "50%_Area_Latency_Diff_Waves_N2pc.txt", "fractional_area_latency", False
            ),
            MeasureFile("pnq6v", "Peak_Latency_Diff_Waves_N2pc.txt", "peak_latency", True),
        ),
    ),
}

UNITS = {"mean_amplitude": "uV", "fractional_area_latency": "ms", "peak_latency": "ms"}
_BIN = re.compile(r"^bin(\d+)_(.+?)_+([A-Za-z0-9/]+)$")
_ERPSET = re.compile(r"^(\d+)_")


def fetch_erpcore_values(dest: Path, component: str) -> Path:
    """Download one component's measurement files and subject summary into ``dest``.

    Files go to ``dest/erpcore_values/<component>/``; returns that folder.
    """
    spec = _spec(component)
    folder = dest / "erpcore_values" / component
    folder.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(dest)
    for osf_id, name in [spec.summary, *((f.osf_id, f.name) for f in spec.files)]:
        rel = (folder / name).relative_to(dest).as_posix()
        if manifest.is_current(dest, rel):
            continue
        log.info("fetching %s", rel)
        _download(OSF_DOWNLOAD.format(osf_id), dest / rel)
        manifest.record(dest, rel, f"osf:{spec.node}/{osf_id}")
    return folder


def _download(url: str, target: Path) -> None:
    def get() -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data: bytes = resp.read()
            return data

    # OSF's file server sometimes answers a burst of requests with 403 (seen 2026-11-01 on a
    # public file that downloads fine a second later); _retry treats 4xx as permanent, so 403 is
    # retried here, a few times with backoff.
    for attempt in range(4):
        try:
            data = _retry(get, url)
            break
        except urllib.error.HTTPError as err:
            if err.code != 403 or attempt == 3:
                raise
            log.warning("%s: HTTP 403, retrying in %ss", url, 2**attempt)
            time.sleep(2**attempt)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)


def _spec(component: str) -> ComponentValues:
    if component not in ERPCORE_VALUES:
        raise ValueError(f"no published values for {component!r}; choose {list(ERPCORE_VALUES)}")
    return ERPCORE_VALUES[component]


def parse_measurement_file(path: Path) -> pd.DataFrame:
    """Read an ERPLAB measurement text file into ``subject, wave, channels, value`` rows.

    Header: one ``bin<n>_<description>_<channels>`` column per measured wave, then ``ERPset``.
    Rows: one value per wave, then the ERPset name, which starts with the subject number.
    """
    lines = [ln for ln in path.read_text(encoding="latin-1").splitlines() if ln.strip()]
    header = [h.strip() for h in lines[0].split("\t") if h.strip()]
    if header[-1] != "ERPset":
        raise ValueError(f"{path.name}: unexpected header {header}")
    waves = []
    for h in header[:-1]:
        m = _BIN.match(h)
        if not m:
            raise ValueError(f"{path.name}: cannot parse bin label {h!r}")
        desc = m.group(2).replace("_", " ").replace("  ", " ").strip()
        waves.append((desc, m.group(3)))
    rows = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split("\t") if c.strip()]
        if len(cells) != len(waves) + 1:
            raise ValueError(f"{path.name}: expected {len(waves) + 1} cells, got {cells}")
        m = _ERPSET.match(cells[-1])
        if not m:
            raise ValueError(f"{path.name}: ERPset {cells[-1]!r} has no subject number")
        subject = f"{int(m.group(1)):03d}"
        for (desc, chans), cell in zip(waves, cells[:-1], strict=True):
            value = float(cell)  # "NaN" parses to nan
            rows.append({"subject": subject, "wave": desc, "channels": chans, "value": value})
    return pd.DataFrame(rows)


def _xlsx_rows(path: Path) -> list[list[str]]:
    """The first sheet of an .xlsx as rows of strings (stdlib only; no openpyxl)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")) for si in root]
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", ns):
            col = _col_index(c.get("r", "A"))
            v = c.find("m:v", ns)
            text = v.text if v is not None and v.text is not None else ""
            if c.get("t") == "s" and text:
                text = shared[int(text)]
            cells[col] = text.strip()
        rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    return rows


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + ord(ch) - ord("A") + 1
    return n - 1


def parse_subject_summary(path: Path) -> pd.DataFrame:
    """``subject, included, reason`` for subjects 1-40 from ``<component>_Subject_Summary.xlsx``.

    A non-empty "Reason Rejected" (codes such as ``AR``, ``TC``, ``ACC``, defined in the sheet's
    legend) means ERP CORE left the subject out of its measurements.
    """
    out = []
    for row in _xlsx_rows(path):
        if not row or not re.fullmatch(r"\d+(\.0)?", row[0]):
            continue
        sub = int(float(row[0]))
        if not 1 <= sub <= N_SUBJECTS:
            continue
        reason = row[1] if len(row) > 1 else ""
        out.append({"subject": f"{sub:03d}", "included": not reason, "reason": reason or "n/a"})
    df = pd.DataFrame(out)
    if len(df) != N_SUBJECTS or df["subject"].duplicated().any():
        raise ValueError(f"{path.name}: expected subjects 1-{N_SUBJECTS}, got {len(df)} rows")
    return df


def tidy_component(folder: Path, component: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Both tidy tables for one component from the files in ``folder``.

    Checks that the measurement files hold exactly the subjects the summary marks as included.
    """
    spec = _spec(component)
    subjects = parse_subject_summary(folder / spec.summary[1])
    frames = []
    for f in spec.files:
        df = parse_measurement_file(folder / f.name)
        df["measure"] = f.measure
        df["unit"] = UNITS[f.measure]
        df["lowpass"] = "20 Hz" if f.lowpass else "n/a"
        df["source"] = f"osf:{spec.node}/{f.osf_id} {f.name}"
        frames.append(df)
    values = pd.concat(frames, ignore_index=True)
    included = set(subjects.loc[subjects["included"], "subject"])
    measured = set(values["subject"])
    if measured != included:
        raise ValueError(
            f"{component}: measured subjects differ from the summary's included ones "
            f"(only measured: {sorted(measured - included)}, "
            f"only included: {sorted(included - measured)})"
        )
    cols = ["subject", "wave", "channels", "measure", "value", "unit", "lowpass", "source"]
    values = values[cols].sort_values(["subject", "measure", "wave"], kind="stable")
    return values.reset_index(drop=True), subjects


def write_tidy(values: pd.DataFrame, subjects: pd.DataFrame, out: Path, component: str) -> None:
    """Write ``<component>.tsv`` and ``<component>_subjects.tsv`` with an attribution header."""
    out.mkdir(parents=True, exist_ok=True)
    node = _spec(component).node
    header = (
        f"# ERP CORE {component} published per-subject values (OSF project {node}).\n"
        f"# {CITATION}\n"
        "# Generated by `eegunify fetch erpcore-values`; do not edit by hand.\n"
    )
    for df, name in ((values, f"{component}.tsv"), (subjects, f"{component}_subjects.tsv")):
        body = df.to_csv(sep="\t", index=False, na_rep="n/a", lineterminator="\n")
        (out / name).write_text(header + body, encoding="utf-8")


def read_tidy(out: Path, component: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read back the two tidy tables written by :func:`write_tidy`."""

    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(
            out / name,
            sep="\t",
            comment="#",
            dtype={"subject": str},
            na_values=["n/a"],
            keep_default_na=False,
        )

    values = read(f"{component}.tsv")
    subjects = read(f"{component}_subjects.tsv")
    return values, subjects


def published_value(
    values: pd.DataFrame, subject: str, measure: str, wave_contains: str
) -> float | None:
    """One published value, or None if the subject was excluded or the value is NaN."""
    hit = values[
        (values["subject"] == subject)
        & (values["measure"] == measure)
        & values["wave"].str.contains(wave_contains, case=False, regex=False)
    ]
    if len(hit) > 1:
        raise ValueError(f"{wave_contains!r} matches several waves: {list(hit['wave'])}")
    if hit.empty or math.isnan(float(hit["value"].iloc[0])):
        return None
    return float(hit["value"].iloc[0])
