"""ERP CORE published values: parsing, tidy tables, consistency checks (offline)."""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from eegunify import reference_values as rv

GOLDEN = Path(__file__).parents[1] / "golden" / "erpcore_reference"

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _write_xlsx(path: Path, rows: list[list[str | int]]) -> None:
    """A minimal .xlsx (shared strings + one sheet), as Excel writes the subject summaries."""
    strings: list[str] = []
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, val in enumerate(row):
            ref = f"{chr(ord('A') + c)}{r}"
            if isinstance(val, int):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            elif val:
                strings.append(val)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{SHEET_NS}">{sst}</sst>')
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{SHEET_NS}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
        )


def _summary_rows(excluded: dict[int, str]) -> list[list[str | int]]:
    rows: list[list[str | int]] = [["SubID", "Reason Rejected"]]
    rows += [[s, excluded.get(s, "")] for s in range(1, rv.N_SUBJECTS + 1)]
    rows += [["Total # Subs", rv.N_SUBJECTS - len(excluded), "", "AR = over 25% trials"]]
    return rows


def _write_n170_folder(folder: Path, excluded: dict[int, str], measured: list[int]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _write_xlsx(folder / "N170_Subject_Summary.xlsx", _summary_rows(excluded))
    diff = "bin5_Faces_minus_Cars_PO8\t\t      ERPset\r\n" + "".join(
        f"            {-s / 10:.3f}\t{s}_N170_erp_ar_diff_waves\r\n" for s in measured
    )
    parent = "bin1_Faces,_Correct_PO8\tbin2_Cars,_Correct__PO8\t\t      ERPset\r\n" + "".join(
        f"  {s:.3f}\t  {s + 1:.3f}\t{s}_N170_erp_ar_diff_waves\r\n" for s in measured
    )
    lat = "bin5_Faces_minus_Cars_PO8\t\t      ERPset\r\n" + "".join(
        f"  {'NaN' if s == measured[-1] else '125.000'}\t{s}_N170_erp_ar_diff_waves\r\n"
        for s in measured
    )
    files = {f.measure + str(f.lowpass) + f.name: f for f in rv.ERPCORE_VALUES["N170"].files}
    for f in files.values():
        if "Parent" in f.name:
            text = parent
        elif f.measure == "mean_amplitude":
            text = diff
        else:
            text = lat
        (folder / f.name).write_bytes(text.encode("latin-1"))


def test_parse_measurement_file_reads_bins_channels_and_subjects(tmp_path: Path) -> None:
    p = tmp_path / "m.txt"
    p.write_bytes(
        b"bin4_Contra,_All_PO7/PO8\tbin5_Ipsi,_All_PO7/PO8\t\t      ERPset\r\n"
        b"        3.464\t      4.130\t1_N2pc_erp_ar_diff_waves\r\n"
        b"          NaN\t     -1.000\t15_N2pc_erp_ar_diff_waves_lpfilt\r\n"
    )
    df = rv.parse_measurement_file(p)
    assert list(df["subject"]) == ["001", "001", "015", "015"]
    assert list(df["wave"].unique()) == ["Contra, All", "Ipsi, All"]
    assert set(df["channels"]) == {"PO7/PO8"}
    assert df["value"].iloc[1] == pytest.approx(4.13)
    assert math.isnan(df["value"].iloc[2])


def test_parse_measurement_file_double_underscore_before_channel(tmp_path: Path) -> None:
    p = tmp_path / "m.txt"
    p.write_text("bin2_Cars,_Correct__PO8\t\tERPset\n1.0\t2_N170\n", encoding="latin-1")
    df = rv.parse_measurement_file(p)
    assert (df["wave"].iloc[0], df["channels"].iloc[0]) == ("Cars, Correct", "PO8")


@pytest.mark.parametrize(
    "text",
    ["bin1_A_PO8\tbin2\tERPset\n1\t2\t3_x\n", "bin1_A_PO8\tERPset\n1\t2\t3_x\n", "x\ty\n1\t2\n"],
)
def test_parse_measurement_file_refuses_malformed(tmp_path: Path, text: str) -> None:
    p = tmp_path / "m.txt"
    p.write_text(text, encoding="latin-1")
    with pytest.raises(ValueError):
        rv.parse_measurement_file(p)


def test_subject_summary_reads_reasons_and_skips_legend(tmp_path: Path) -> None:
    p = tmp_path / "s.xlsx"
    _write_xlsx(p, _summary_rows({16: "TC", 7: "AR, TC"}))
    df = rv.parse_subject_summary(p)
    assert len(df) == rv.N_SUBJECTS
    assert df.set_index("subject").loc["016", "reason"] == "TC"
    assert set(df.loc[~df["included"], "subject"]) == {"007", "016"}


def test_subject_summary_refuses_missing_subjects(tmp_path: Path) -> None:
    p = tmp_path / "s.xlsx"
    _write_xlsx(p, _summary_rows({})[:-5])
    with pytest.raises(ValueError, match="expected subjects"):
        rv.parse_subject_summary(p)


def test_tidy_component_roundtrip(tmp_path: Path) -> None:
    measured = [s for s in range(1, rv.N_SUBJECTS + 1) if s != 16]
    _write_n170_folder(tmp_path / "raw", {16: "TC"}, measured)
    values, subjects = rv.tidy_component(tmp_path / "raw", "N170")
    assert set(values["measure"]) == {"mean_amplitude", "fractional_area_latency", "peak_latency"}
    assert set(values.loc[values["measure"] == "peak_latency", "lowpass"]) == {"20 Hz"}
    rv.write_tidy(values, subjects, tmp_path / "out", "N170")
    text = (tmp_path / "out" / "N170.tsv").read_text(encoding="utf-8")
    assert text.startswith("# ERP CORE N170") and "CC-BY-4.0" in text
    back, subs = rv.read_tidy(tmp_path / "out", "N170")
    assert back["subject"].iloc[0] == "001"  # leading zeros kept
    assert rv.published_value(back, "015", "mean_amplitude", "minus") == pytest.approx(-1.5)
    assert rv.published_value(back, "016", "mean_amplitude", "minus") is None  # excluded
    last = f"{measured[-1]:03d}"
    assert rv.published_value(back, last, "fractional_area_latency", "minus") is None  # NaN
    assert not bool(subs.set_index("subject").loc["016", "included"])


def test_tidy_component_refuses_inconsistent_summary(tmp_path: Path) -> None:
    measured = list(range(1, rv.N_SUBJECTS + 1))  # 016 measured although excluded
    _write_n170_folder(tmp_path, {16: "TC"}, measured)
    with pytest.raises(ValueError, match="only measured: \\['016'\\]"):
        rv.tidy_component(tmp_path, "N170")


def test_published_value_refuses_ambiguous_wave() -> None:
    df = pd.DataFrame(
        {
            "subject": ["015", "015"],
            "measure": ["mean_amplitude"] * 2,
            "wave": ["Faces, Correct", "Cars, Correct"],
            "value": [1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="several waves"):
        rv.published_value(df, "015", "mean_amplitude", "Correct")


def test_unknown_component_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no published values"):
        rv.fetch_erpcore_values(tmp_path, "MMN")


@pytest.mark.parametrize("component", list(rv.ERPCORE_VALUES))
def test_committed_golden_tables_are_consistent(component: str) -> None:
    values, subjects = rv.read_tidy(GOLDEN, component)
    assert len(subjects) == rv.N_SUBJECTS
    assert set(values["subject"]) == set(subjects.loc[subjects["included"], "subject"])
    assert set(values["measure"]) == set(rv.UNITS)
    # the 5 subjects of the BIDS archive are in the summary, whatever their status
    assert {"015", "016", "017", "018", "019"} <= set(subjects["subject"])


@pytest.mark.realdata
@pytest.mark.parametrize("component", list(rv.ERPCORE_VALUES))
def test_golden_tables_match_osf(component: str, data_cache: Path, tmp_path: Path) -> None:
    """Re-fetch from OSF and rebuild: the committed tables must be what the files say."""
    try:
        folder = rv.fetch_erpcore_values(data_cache / "erpcore", component)
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE values: {err}")
    rv.write_tidy(*rv.tidy_component(folder, component), tmp_path, component)
    for name in (f"{component}.tsv", f"{component}_subjects.tsv"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (GOLDEN / name).read_text(
            encoding="utf-8"
        )
