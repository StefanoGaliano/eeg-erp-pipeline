"""ERP CORE per-subject parameters for the erpcore-exact recipe (offline parsing + golden JSON)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from eegunify import erpcore_exact as ex

GOLDEN = Path(__file__).parents[1] / "golden" / "erpcore_reference"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(path: Path, rows: list[list[str]]) -> None:
    strings: list[str] = []
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, val in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if val.replace(".", "").lstrip("-").isdigit():
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            elif val:
                strings.append(val)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}">{sst}</sst>')
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{NS}"><sheetData>{"".join(body)}</sheetData></worksheet>',
        )


CHANNEL_OPS = """nch1 = ch1 Label FP1
nch2 = ch2 Label F8
nch3 = ch3 Label PO8
nch4 = ch4 Label HEOG_left
nch5 = ch5 Label HEOG_right
nch6 = (ch4 - ch5) Label (corr) HEOG
nch7 = ch6 Label (uncorr) HEOG
nch8 = ch7 Label (uncorr) VEOG
"""


def _folder(tmp_path: Path) -> Path:
    c = "N170"
    (tmp_path / f"Add_Corrected_Bipolars_{c}.txt").write_text(CHANNEL_OPS, encoding="latin-1")
    (tmp_path / f"Rereference_Add_Uncorrected_Bipolars_{c}.txt").write_text(
        "nch1 = ch1 - ( avgchan(1:33) ) Label FP1\nnch2 = (ch4 - ch5) Label (uncorr) HEOG\n",
        encoding="latin-1",
    )
    head = ["SUBID", "Channel(s)", "Threshold", "Min", "Max", "Size", "Step"]
    _xlsx(
        tmp_path / f"Interpolate_Channels_{c}.xlsx", [["SUBID", "Bad", "Name"], ["15", "2", "*F8"]]
    )
    _xlsx(tmp_path / f"ICA_Components_{c}.xlsx", [["SUBID", "Components"], ["15", "1", "4", "10"]])
    _xlsx(
        tmp_path / f"AR_Parameters_for_SVT_CRAP_{c}.xlsx",
        [
            ["SUBID", "Channel(s)", "Min", "Max", "Tmin", "Tmax"],
            ["15", "1 2 3", "-200", "200", "-200", "800"],
        ],
    )
    _xlsx(
        tmp_path / f"AR_Parameters_for_MW_CRAP_{c}.xlsx",
        [head, ["15", "1 3", "85", "-200", "800", "500", "100"]],
    )
    _xlsx(
        tmp_path / f"AR_Parameters_for_MW_Blinks_{c}.xlsx",
        [head, ["15", "8", "150", "-25", "325", "175", "10"]],
    )
    _xlsx(
        tmp_path / f"AR_Parameters_for_SL_HEOG_Stim_Pres_{c}.xlsx",
        [head, ["15", "7", "32", "-50", "350", "100", "10"]],
    )
    _xlsx(
        tmp_path / f"AR_Parameters_for_SL_HEOG_{c}.xlsx",
        [head, ["15", "6", "64", "-200", "800", "100", "10"]],
    )
    (tmp_path / f"15_AR_Percentages_{c}.csv").write_text(
        "SubID,Bin,Accepted,Rejected,Total Percent Rejected\n15,Total,268,11,3.94\n"
        ",Faces - Correct,73,3,3.95\n",
        encoding="latin-1",
    )
    return tmp_path


def test_channel_list_names_derived_bipolars(tmp_path: Path) -> None:
    chans = ex.channel_list(_folder(tmp_path) / "Add_Corrected_Bipolars_N170.txt")
    names = ["FP1", "F8", "PO8", "HEOG_left", "HEOG_right"]
    assert chans == [*names, "HEOG", "HEOG_uncorr", "VEOG_uncorr"]


def test_channel_list_refuses_gaps(tmp_path: Path) -> None:
    p = tmp_path / "ops.txt"
    p.write_text("nch1 = ch1 Label A\nnch3 = ch3 Label C\n", encoding="latin-1")
    with pytest.raises(ValueError, match=r"not 1\.\.n"):
        ex.channel_list(p)


def test_subject_parameters_resolve_numbers_to_names(tmp_path: Path) -> None:
    p = ex.subject_parameters(_folder(tmp_path), "N170", "015")
    assert p["interpolate"] == ["F8"]
    assert p["ica_exclude_eeglab"] == [1, 4, 10]
    svt, mw, blink, stim, heog = p["reject"]
    assert svt["test"] == "extval" and (svt["min_uv"], svt["max_uv"]) == (-200, 200)
    assert svt["channels"] == ["FP1", "F8", "PO8"]
    assert mw["channels"] == ["FP1", "PO8"] and mw["threshold_uv"] == 85
    assert blink["channels"] == ["VEOG_uncorr"] and blink["window_ms"] == [-25, 325]
    assert stim["channels"] == ["HEOG_uncorr"] and stim["test"] == "step"
    assert heog["channels"] == ["HEOG"] and heog["threshold_uv"] == 64
    assert p["ar_percentages"]["Total"] == {"accepted": 268, "rejected": 11}


def test_interpolation_name_mismatch_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    _xlsx(folder / "Interpolate_Channels_N170.xlsx", [["SUBID", "Bad", "Name"], ["15", "3", "*F8"]])
    with pytest.raises(ValueError, match="!= names"):
        ex.subject_parameters(folder, "N170", "015")


def test_missing_subject_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no row for subject 017"):
        ex.subject_parameters(_folder(tmp_path), "N170", "017")


def test_unknown_component_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no erpcore-exact parameters"):
        ex.fetch_exact(tmp_path, "MMN")


def test_committed_exact_json_is_consistent() -> None:
    data = json.loads((GOLDEN / "N170_exact.json").read_text(encoding="utf-8"))
    chans = set(data["epoch_channels"])
    assert data["reference"]["n_sites"] == 33
    assert sorted(data["subjects"]) == ["015", "016", "017", "018", "019"]
    for sub, p in data["subjects"].items():
        assert set(p["interpolate"]) <= chans, sub
        assert all(r["test"] in {"extval", "ptp", "step"} for r in p["reject"]), sub
        assert all(set(r["channels"]) <= chans for r in p["reject"]), sub
        assert p["ica_exclude_eeglab"] and min(p["ica_exclude_eeglab"]) >= 1, sub
        assert "Total" in p["ar_percentages"], sub


@pytest.mark.realdata
def test_exact_json_matches_osf(data_cache: Path, tmp_path: Path) -> None:
    try:
        folder = ex.fetch_exact(data_cache / "erpcore", "N170")
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE parameters: {err}")
    path = ex.write_exact(ex.build(folder, "N170"), tmp_path)
    assert path.read_text(encoding="utf-8") == (GOLDEN / "N170_exact.json").read_text(
        encoding="utf-8"
    )


def test_default_channels_are_script6_ranges() -> None:
    chans = [f"c{i}" for i in range(1, 36)]
    assert ex._channels("default", ex.SCRIPT6_DEFAULT_CHANNELS["svt"], chans) == chans[:31]
    assert ex._channels("default", ex.SCRIPT6_DEFAULT_CHANNELS["mw"], chans) == chans[:28]
    with pytest.raises(ValueError, match="outside"):
        ex._channels("36", 0, chans)
