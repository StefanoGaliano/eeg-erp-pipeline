"""eegunify app: Inspect -> Configure -> Convert + QA, then the ERP pages (``erp_pages``).

Run with ``eegunify app`` (or ``streamlit run src/eegunify/app/main.py``). Every choice made
here is a config key, and the saved YAML reproduces the run with ``eegunify convert``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from eegunify.app.erp_pages import ERP_PAGES, PAGE_FUNCS, dataset_configs
from eegunify.canonical import CanonicalError, Recording, Violation
from eegunify.config.draft import (
    draft_config,
    draft_paths,
    event_code_table,
    mapping_from_rows,
    to_yaml,
)
from eegunify.config.loader import ConfigError, load_config, parse_config
from eegunify.detect import EXTENSIONS
from eegunify.pipeline import convert, read_recording

PHASE1_PAGES = ("1 · Inspect", "2 · Configure", "3 · Convert + QA")
PAGES = (*PHASE1_PAGES, *ERP_PAGES)
_ENTRY_SUFFIXES = {*EXTENSIONS, ".cnt", ".raw"}


# --------------------------------------------------------------------------- helpers
def project_root() -> Path:
    return Path(st.session_state.get("root", ".")).expanduser().resolve()


def find_recordings(root: Path) -> list[str]:
    """Entry-point EEG files under sourcedata/ (companion files such as .eeg/.fdt excluded).

    Follows symlinks: large datasets are often linked in from another drive.
    """
    base = root / "sourcedata"
    if not base.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
        here = Path(dirpath)
        for name in [*filenames, *dirnames]:
            p = here / name
            if p.suffix.lower() in _ENTRY_SUFFIXES and (p.is_file() or p.suffix == ".mff"):
                found.append(p.relative_to(root).as_posix())
    return sorted(found)


@st.cache_resource(show_spinner="Reading recording…", max_entries=4)
def load(path: str, mtime: float) -> tuple[Recording | None, list[Violation], str | None]:
    try:
        rec, warnings = read_recording(Path(path))
    except CanonicalError as err:
        return None, err.violations, None
    except Exception as err:  # shown to the user, not raised
        return None, [], f"{type(err).__name__}: {err}"
    return rec, warnings, None


def show_violations(violations: list[Violation]) -> None:
    for v in violations:
        (st.error if v.level == "error" else st.warning)(f"[rule {v.rule}] {v.message}")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "dataset"


# --------------------------------------------------------------------------- pages
def page_inspect() -> None:
    st.header("Inspect a recording")
    st.caption("Read any supported file and see what eegunify sees. Nothing is written.")
    root = project_root()
    files = find_recordings(root)
    if not files:
        st.info(
            f"No recordings found under `{root / 'sourcedata'}`. Set the project root in the "
            "sidebar."
        )
        return
    current = st.session_state.get("file")
    choice = st.selectbox(
        "Recording",
        files,
        index=files.index(current) if current in files else 0,
        key="inspect_file",
    )
    path = root / choice
    rec, violations, error = load(str(path), path.stat().st_mtime)
    if error:
        st.error(f"Could not read the file: {error}")
        return
    if rec is None:
        st.error("The recording breaks canonical rules:")
        show_violations(violations)
        st.info("Fix it in the Configure page (e.g. `units.declared`) and convert from there.")
        st.session_state["file"] = choice
        return

    raw = rec.raw
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Format", rec.format)
    c2.metric("Sampling rate", f"{raw.info['sfreq']:g} Hz")
    c3.metric("Duration", f"{raw.times[-1]:.0f} s")
    c4.metric("Events", len(rec.events.table))
    st.write(f"**Reference:** {rec.reference} · **Units:** {rec.units.explanation}")
    show_violations(violations)

    left, right = st.columns(2)
    with left:
        st.subheader("Channels")
        new_to_orig = {n: o for o, n in rec.rename_log.items()}
        st.dataframe(
            pd.DataFrame(
                {
                    "name": raw.ch_names,
                    "original": [new_to_orig.get(n, n) for n in raw.ch_names],
                    "type": raw.get_channel_types(),
                }
            ),
            hide_index=True,
            height=320,
        )
    with right:
        st.subheader("Event codes")
        st.dataframe(event_code_table(rec)[["code", "n"]], hide_index=True, height=320)

    if st.checkbox("Show power spectrum", value=False):
        from eegunify.qa import plots
        from eegunify.qa.checks import detect_line_freq

        st.pyplot(plots.psd_figure(raw, detect_line_freq(raw)))

    if st.button("Configure this dataset →", type="primary"):
        st.session_state["file"] = choice
        st.session_state.pop("draft", None)
        st.session_state["nav"] = PAGES[1]  # applied before the sidebar radio is drawn
        st.rerun()


def _draft_for(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    rec, _, error = load(str(path), path.stat().st_mtime)
    if rec is None:
        # Unreadable as-is (e.g. wrong units): draft from the file's location only.
        cfg = draft_paths(path, root)
        return {
            "cfg": cfg,
            "codes": pd.DataFrame(columns=["code", "n", "trial_type"]),
            "channels": [],
            "error": error,
        }
    codes = event_code_table(rec)
    codes["trial_type"] = ""
    return {
        "cfg": draft_config(rec, path, root),
        "codes": codes,
        "channels": list(rec.raw.ch_names),
        "error": None,
    }


def page_configure() -> None:
    st.header("Configure a dataset")
    st.caption("Every field is a key of the YAML config. Errors are shown as you edit.")
    root = project_root()
    rel = st.session_state.get("file")
    if not rel:
        st.info("Pick a recording on the Inspect page first.")
        return
    if "draft" not in st.session_state:
        st.session_state["draft"] = _draft_for(root, rel)
    draft = st.session_state["draft"]
    base = draft["cfg"]
    st.write(f"Example recording: `{rel}`")

    st.subheader("Dataset")
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Name", base["dataset"]["name"], key="cfg_name")
    task = c2.text_input("BIDS task label", base["dataset"]["task"], key="cfg_task")
    source_root = c3.text_input("Source root", base["dataset"]["source_root"], key="cfg_source")
    c1, c2 = st.columns([2, 1])
    pattern = c1.text_input(
        "File pattern (`{subject}` = subject label)",
        base["recordings"]["pattern"],
        key="cfg_pattern",
    )
    subjects = c2.text_input(
        "Subjects (comma-separated)", ", ".join(base["recordings"]["subjects"]), key="cfg_subjects"
    )

    st.subheader("Reference and channels")
    c1, c2, c3 = st.columns(3)
    reference = c1.text_input(
        "Online reference", base.get("reference", {}).get("online", "n/a"), key="cfg_reference"
    )
    ground = c2.text_input("Ground (optional)", "", key="cfg_ground")
    units = c3.selectbox(
        "Units (if the header is wrong)", ["auto", "uV", "mV", "V"], key="cfg_units"
    )
    bads = st.multiselect("Bad channels (all subjects)", draft["channels"], key="cfg_bads")
    montage_base = base.get("montage", {})
    c1, c2 = st.columns(2)
    montage_source = c1.selectbox(
        "Electrode positions",
        ["standard", "embedded", "none"],
        index=["standard", "embedded", "none"].index(montage_base.get("source", "standard")),
        key="cfg_montage",
    )
    montage_name = c2.text_input(
        "Standard layout",
        montage_base.get("standard_name", "standard_1005"),
        key="cfg_montage_name",
        disabled=montage_source != "standard",
    )

    st.subheader("Events")
    st.caption(
        "Give each code a condition name (`stimulus/face`). Consecutive codes with the "
        "same name become ranges. Leave empty to treat as unmapped."
    )
    if "mapping_rows" not in st.session_state:
        st.session_state["mapping_rows"] = draft["codes"].to_dict("records")
    edited = st.data_editor(
        pd.DataFrame(st.session_state["mapping_rows"], columns=["code", "n", "trial_type"]),
        disabled=["code", "n"],
        hide_index=True,
        key="cfg_mapping",
        width="stretch",
    )
    rows: list[dict[str, Any]] = [
        {str(k): v for k, v in r.items()} for r in edited.to_dict("records")
    ]
    c1, c2 = st.columns(2)
    unmapped = c1.selectbox("Unmapped codes", ["keep_as_code", "drop", "error"], key="cfg_unmapped")
    expect = c2.checkbox("Expect these counts in every subject (QA check)", key="cfg_expect")

    mapping = mapping_from_rows(rows)
    cfg: dict[str, Any] = {
        "dataset": {"name": name, "source_root": source_root, "task": task},
        "recordings": {
            "pattern": pattern,
            "subjects": [s.strip() for s in subjects.split(",") if s.strip()],
        },
        "reference": {"online": reference or "n/a", **({"ground": ground} if ground else {})},
        "events": {"mapping": mapping, "unmapped": unmapped},
    }
    if units != "auto":
        cfg["units"] = {"declared": units}
    if bads:
        cfg["channels"] = {"bads": bads}
    if montage_source != "standard" or montage_name != "standard_1005":
        cfg["montage"] = {"source": montage_source}
        if montage_source == "standard":
            cfg["montage"]["standard_name"] = montage_name
    if expect:
        counts: dict[str, int] = {}
        for row in rows:
            tt = str(row.get("trial_type") or "").strip()
            if tt:
                counts[tt] = counts.get(tt, 0) + int(row["n"])
        cfg["events"]["expected_counts"] = counts

    st.subheader("Result")
    try:
        parse_config(cfg)
    except ConfigError as err:
        st.error(str(err))
        return
    text = to_yaml(cfg)
    st.success("Config is valid.")
    st.code(text, language="yaml")
    out = root / "code" / "configs" / f"{slug(name)}.yaml"
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.download_button("Download YAML", text, file_name=out.name, mime="text/yaml")
    overwrite = c3.checkbox(
        f"Overwrite `{out.relative_to(root).as_posix()}` if it exists", key="cfg_overwrite"
    )
    if c2.button("Save to project", type="primary", key="cfg_save"):
        if out.exists() and not overwrite:
            st.error(f"{out.relative_to(root).as_posix()} exists. Tick overwrite to replace it.")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            st.session_state["config_path"] = out.relative_to(root).as_posix()
            st.success(f"Saved `{out.relative_to(root).as_posix()}`. Next: Convert + QA.")


def page_convert() -> None:
    st.header("Convert + QA")
    st.caption("Writes BIDS to rawdata/ and QA reports to derivatives/eegunify-qa/.")
    root = project_root()
    configs = dataset_configs(root)
    if not configs:
        st.info("No dataset configs in code/configs/. Create one on the Configure page.")
        return
    current = st.session_state.get("config_path")
    rel = st.selectbox(
        "Config",
        configs,
        index=configs.index(current) if current in configs else 0,
        key="cv_config",
    )
    cfg = load_config(root / rel)
    subjects = st.multiselect(
        "Subjects", cfg.recordings.subjects, cfg.recordings.subjects, key="cv_subjects"
    )
    c1, c2 = st.columns(2)
    overwrite = c1.checkbox("Overwrite existing BIDS output", key="cv_overwrite")
    write_bids = not c2.checkbox("QA only (don't write BIDS)", key="cv_qa_only")
    st.caption(
        f"Same as: `eegunify {'convert' if write_bids else 'qa'} {rel}"
        + "".join(f" -s {s}" for s in subjects)
        + (" --overwrite" if overwrite and write_bids else "")
        + "`"
    )

    if st.button("Run", type="primary", disabled=not subjects, key="cv_run"):
        with st.spinner("Converting…"):
            outcome = convert(
                cfg,
                subjects=subjects,
                overwrite=overwrite,
                project_root=root,
                write_bids=write_bids,
            )
        st.session_state["results"] = [
            {
                "subject": r.subject,
                "error": r.error,
                "qa": r.qa_status,
                "output": str(r.output) if r.output else "",
                "report": str(r.qa_report) if r.qa_report else "",
            }
            for r in outcome
        ]

    rows: list[dict[str, Any]] = st.session_state.get("results", [])
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        for r in rows:
            if r["report"] and Path(r["report"]).exists():
                with st.expander(
                    f"QA report: sub-{r['subject']} ({r['qa']})", expanded=len(rows) == 1
                ):
                    qa_json = Path(r["report"]).with_name(
                        Path(r["report"]).name.replace("_qa.html", "_qa.json")
                    )
                    data = json.loads(qa_json.read_text(encoding="utf-8"))
                    st.dataframe(
                        pd.DataFrame(data["checks"])[["id", "level", "message"]], hide_index=True
                    )
                    if st.toggle("Show full report", key=f"full_{r['subject']}"):
                        components.html(
                            Path(r["report"]).read_text(encoding="utf-8"),
                            height=900,
                            scrolling=True,
                        )
    summary = root / "derivatives" / "eegunify-qa" / "summary.tsv"
    if summary.exists():
        st.subheader("Dataset QA summary")
        st.dataframe(
            pd.read_csv(summary, sep="\t", dtype=str, keep_default_na=False), hide_index=True
        )


# --------------------------------------------------------------------------- layout
def _root_from_argv(argv: list[str]) -> str:
    """``eegunify app --root X`` passes ``-- --root X`` through to this script."""
    if "--root" in argv and argv.index("--root") + 1 < len(argv):
        return argv[argv.index("--root") + 1]
    return "."


def main() -> None:
    st.set_page_config(page_title="eegunify", page_icon="🧠", layout="wide")
    st.session_state.setdefault("page", PAGES[0])
    if "nav" in st.session_state:  # a widget's value can only change before it is drawn
        st.session_state["page"] = st.session_state.pop("nav")
    st.session_state.setdefault("root", _root_from_argv(sys.argv))
    with st.sidebar:
        st.title("eegunify")
        st.caption("Any raw EEG → canonical BIDS-EEG")
        st.text_input("Project root", key="root")
        st.radio("Step", PAGES, key="page")
        st.divider()
        st.caption(
            "Every choice here is a config key; the saved YAML reproduces the run with "
            "`eegunify convert` (pages 1-3) and `eegunify erp run` (pages 4-8)."
        )
    page = st.session_state["page"]
    phase1 = {
        PHASE1_PAGES[0]: page_inspect,
        PHASE1_PAGES[1]: page_configure,
        PHASE1_PAGES[2]: page_convert,
    }
    if page in phase1:
        phase1[page]()
    else:
        PAGE_FUNCS[page](project_root())


main()
