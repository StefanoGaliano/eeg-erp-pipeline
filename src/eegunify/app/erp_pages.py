"""ERP pages of the app (Phase A2, docs/BLUEPRINT_PHASES.md section 10).

Preprocess, ICA review, Epochs, Components and Group, in the same sidebar radio as Phase 1's
pages. All five edit one dataset config, kept as a plain dict in
``session_state["erp_cfg"]`` (the YAML as loaded, so saving writes back only the keys that are
there), validated with ``parse_config`` on every rerun. Pages read stage outputs from
``derivatives/`` through ``eegunify.erp.paths``, compute only through library functions and
plot only through ``eegunify.erp.plots``. The saved YAML reproduces everything with
``eegunify erp run``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mne
import pandas as pd
import streamlit as st
import yaml

from eegunify.config.draft import to_yaml
from eegunify.config.erp_sections import EpochSet
from eegunify.config.loader import ConfigError, load_config, parse_config, resolve_for_subject
from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.erp import plots as erp_plots
from eegunify.erp.components.library import load_library, presets, resolve
from eegunify.erp.runner import Runner

ERP_PAGES = ("4 · Preprocess", "5 · ICA review", "6 · Epochs", "7 · Components", "8 · Group")
STATUS_COLOURS = {  # text tokens of the reference palette's status steps (with a word, not alone)
    "ok": "#0f7a3c",
    "sign_mismatch": "#b42318",
    "out_of_tolerance": "#b42318",
    "missing_ours": "#b42318",
}


# --------------------------------------------------------------------------- config state
def dataset_configs(root: Path) -> list[str]:
    """YAML files under code/configs that are dataset configs (others are skipped)."""
    out = []
    for p in sorted((root / "code" / "configs").glob("*.yaml")):
        try:
            load_config(p)
        except ConfigError:
            continue
        out.append(p.relative_to(root).as_posix())
    return out


def get(d: dict[str, Any], dotted: str, default: Any = None) -> Any:
    """``d["a"]["b"]`` for ``"a.b"``, or ``default``."""
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def put(d: dict[str, Any], dotted: str, value: Any) -> None:
    """Set ``d["a"]["b"] = value`` for ``"a.b"``, creating the dicts on the way."""
    *parents, last = dotted.split(".")
    cur = d
    for part in parents:
        cur = cur.setdefault(part, {})
    cur[last] = value


def drop(d: dict[str, Any], dotted: str) -> None:
    """Remove ``"a.b"`` (back to the default); empty parent dicts go too."""
    *parents, last = dotted.split(".")
    chain = [d]
    for part in parents:
        nxt = chain[-1].get(part)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    chain[-1].pop(last, None)
    for parent, part in zip(reversed(chain[:-1]), reversed(parents), strict=True):
        if not parent[part]:
            parent.pop(part)


def _select_config(root: Path) -> tuple[str, dict[str, Any]] | None:
    configs = dataset_configs(root)
    if not configs:
        st.info("No dataset configs in code/configs/. Create one on the Configure page.")
        return None
    current = st.session_state.get("erp_config")
    rel = st.selectbox(
        "Dataset config",
        configs,
        index=configs.index(current) if current in configs else 0,
        key="erp_config_select",
    )
    if st.session_state.get("erp_config") != rel or "erp_cfg" not in st.session_state:
        text = (root / rel).read_text(encoding="utf-8")
        st.session_state["erp_cfg"] = yaml.safe_load(text)
        st.session_state["erp_config"] = rel
    return rel, st.session_state["erp_cfg"]


def _validated(cfg: dict[str, Any]) -> Config | None:
    try:
        return parse_config(copy.deepcopy(cfg))
    except ConfigError as err:
        st.error(str(err))
        return None


def _footer(root: Path, rel: str, cfg: dict[str, Any], parsed: Config | None, to: str) -> None:
    """Save/download the YAML, and run the ERP stages up to ``to``."""
    with st.expander("Config (YAML): save or download", expanded=False):
        text = to_yaml(cfg)
        st.code(text, language="yaml")
        if parsed is None:
            st.warning("Fix the errors above to save.")
            return
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.download_button("Download YAML", text, file_name=Path(rel).name, mime="text/yaml")
        name = c3.text_input("Save as", rel, key="erp_save_as")
        if c2.button("Save to project", key="erp_save"):
            out = root / name
            if not out.resolve().is_relative_to(root.resolve()):
                st.error("Save inside the project root.")
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
                st.session_state["erp_config"] = name
                st.success(f"Saved `{name}`.")
    if parsed is None:
        return
    with st.expander(f"Run the ERP stages up to {to}", expanded=False):
        subjects = st.multiselect(
            "Subjects", parsed.recordings.subjects, parsed.recordings.subjects, key=f"run_{to}"
        )
        st.caption(
            f"Same as: `eegunify erp run {rel} --to {to}`"
            + "".join(f" -s {s}" for s in subjects)
            + " (with the config as edited here; save it to keep the change)"
        )
        if st.button("Run", type="primary", key=f"run_btn_{to}", disabled=not subjects):
            with st.spinner("Running…"):
                outs = Runner(parsed, root).run(subjects=subjects, to=to)
            st.session_state["erp_outcomes"] = [
                {
                    "stage": o.item.stage,
                    "subject": o.item.label,
                    "status": o.status,
                    "message": o.message,
                }
                for o in outs
            ]
        rows = st.session_state.get("erp_outcomes")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)


def _subject(parsed: Config, key: str) -> str | None:
    subs = parsed.recordings.subjects
    if not subs:
        st.info("The config lists no subjects.")
        return None
    return str(st.selectbox("Subject", subs, key=key))


def _output(
    root: Path, cfg: Config, stage: str, sub: str, desc: str, suffix: str, ext: str
) -> Path:
    return paths.stage_path(root, stage, cfg, sub, description=desc, suffix=suffix, extension=ext)


def _missing(what: str, stage: str) -> None:
    st.info(f"No {what} yet: run the {stage} stage (expander below).")


def _optional_number(
    cfg: dict[str, Any], dotted: str, label: str, default: float | None, key: str, **kw: Any
) -> None:
    """A number field with an 'off' checkbox for ``null`` (e.g. no low-pass)."""
    current = get(cfg, dotted, default)
    c1, c2 = st.columns([1, 3])
    on = c1.checkbox(f"{label}", value=current is not None, key=f"{key}_on")
    if on:
        val = c2.number_input(
            label,
            value=float(current if current is not None else (default or 1.0)),
            key=key,
            label_visibility="collapsed",
            **kw,
        )
        put(cfg, dotted, val)
    else:
        put(cfg, dotted, None)


# --------------------------------------------------------------------------- pages
def page_preprocess(root: Path) -> None:
    st.header("Preprocess")
    st.caption("Filter, bad channels, interpolation and re-reference (the preproc stage).")
    sel = _select_config(root)
    if sel is None:
        return
    rel, cfg = sel
    parsed0 = _validated(cfg)
    defaults = parsed0.preproc if parsed0 else None
    k = f"pp_{rel}"
    st.subheader("Filter")
    _optional_number(
        cfg,
        "preproc.highpass_hz",
        "High-pass (Hz)",
        defaults.highpass_hz if defaults else 0.1,
        f"{k}_hp",
        min_value=0.0,
    )
    _optional_number(
        cfg,
        "preproc.lowpass_hz",
        "Low-pass (Hz)",
        defaults.lowpass_hz if defaults else None,
        f"{k}_lp",
        min_value=0.0,
    )
    _optional_number(
        cfg,
        "preproc.resample_hz",
        "Resample (Hz)",
        defaults.resample_hz if defaults else None,
        f"{k}_rs",
        min_value=1.0,
    )
    c1, c2, c3 = st.columns(3)
    method = c1.selectbox(
        "Design",
        ["iir", "fir"],
        index=["iir", "fir"].index(get(cfg, "preproc.filter.method", "iir")),
        key=f"{k}_fm",
    )
    put(cfg, "preproc.filter.method", method)
    if method == "iir":
        order = c2.number_input(
            "IIR order (total, ERPLAB)",
            2,
            16,
            int(get(cfg, "preproc.filter.iir_order", 2)),
            2,
            key=f"{k}_fo",
        )
        put(cfg, "preproc.filter.iir_order", int(order))
    phase = c3.selectbox(
        "Phase",
        ["zero", "forward"],
        index=["zero", "forward"].index(get(cfg, "preproc.filter.phase", "zero")),
        key=f"{k}_fp",
    )
    put(cfg, "preproc.filter.phase", phase)

    st.subheader("Bad channels and reference")
    methods = ["config_only", "pyprep_ransac", "pyprep_full", "lof"]
    bm = st.selectbox(
        "Bad-channel detection",
        methods,
        index=methods.index(get(cfg, "preproc.bad_channels.method", "config_only")),
        key=f"{k}_bm",
    )
    put(cfg, "preproc.bad_channels.method", bm)
    interp = st.checkbox(
        "Interpolate bad channels", bool(get(cfg, "preproc.interpolate", True)), key=f"{k}_in"
    )
    put(cfg, "preproc.interpolate", interp)
    schemes = ["average", "channels", "rest", "none"]
    scheme = st.selectbox(
        "Re-reference",
        schemes,
        index=schemes.index(get(cfg, "reference.scheme", "average")),
        key=f"{k}_rf",
    )
    put(cfg, "reference.scheme", scheme)
    channels = _rawdata_channels(root, parsed0)
    if scheme == "channels":
        chosen = st.multiselect(
            "Reference channels",
            channels,
            [c for c in get(cfg, "reference.channels", []) if c in channels],
            key=f"{k}_rc",
        )
        put(cfg, "reference.channels", chosen)
    else:
        drop(cfg, "reference.channels")
    parsed = _validated(cfg)

    st.subheader("Preview (first 60 s of one subject)")
    if parsed is not None:
        sub = _subject(parsed, f"{k}_sub")
        if sub and st.button("Preview", key=f"{k}_preview"):
            with st.spinner("Preprocessing 60 s…"):
                result = _preview(root, parsed, sub)
            if isinstance(result, str):
                st.error(result)
            else:
                before, after, bads, steps = result
                st.pyplot(erp_plots.psd_comparison(before, after))
                st.write(
                    "**Bad channels:**",
                    ", ".join(f"{c} ({why})" for c, why in bads.items()) or "none",
                )
                st.json(steps, expanded=False)
    _footer(root, rel, cfg, parsed, "preproc")


def _rawdata_channels(root: Path, parsed: Config | None) -> list[str]:
    if parsed is None or not parsed.recordings.subjects:
        return []
    for sub in parsed.recordings.subjects:
        tsv = next(
            (p for p in paths.rawdata_files(root, parsed, sub) if p.name.endswith("_channels.tsv")),
            None,
        )
        if tsv is not None:
            return list(pd.read_csv(tsv, sep="\t", encoding="utf-8")["name"])
    return []


def _preview(
    root: Path, cfg: Config, sub: str
) -> tuple[mne.io.BaseRaw, mne.io.BaseRaw, dict[str, str], dict[str, Any]] | str:
    from eegunify.erp.preprocess import is_reference_free, load_rawdata, preprocess

    try:
        loaded = load_rawdata(root, resolve_for_subject(cfg, sub), sub)
    except Exception as err:  # shown, not raised
        return f"Could not read rawdata for sub-{sub}: {err}"
    raw = loaded.raw.crop(0, min(60.0, loaded.raw.times[-1]))
    before = raw.copy()
    res = preprocess(
        raw, resolve_for_subject(cfg, sub), reference_free=is_reference_free(loaded.sidecar)
    )
    if res.failed:
        return "; ".join(c.message for c in res.qa if c.level == "fail")
    return before, res.raw, res.bads, res.steps


def page_ica(root: Path) -> None:
    st.header("ICA review")
    st.caption("Check the automatic component decisions; manual choices are per subject.")
    sel = _select_config(root)
    if sel is None:
        return
    rel, cfg = sel
    parsed = _validated(cfg)
    if parsed is None:
        return
    sub = _subject(parsed, "ica_sub")
    if sub is None:
        return
    rcfg = resolve_for_subject(parsed, sub)
    tsv = _output(root, rcfg, "ica", sub, "ica", "components", ".tsv")
    if not tsv.exists():
        _missing("ICA decision table", "ica")
        _footer(root, rel, cfg, parsed, "ica")
        return
    table = pd.read_csv(tsv, sep="\t")
    over = f"overrides.sub-{sub}.artifacts.ica"
    exclude = set(get(cfg, f"{over}.exclude", []))
    include = set(get(cfg, f"{over}.include", []))
    table["manual"] = [
        "exclude" if i in exclude else "keep" if i in include else "auto" for i in table["idx"]
    ]
    cols = [c for c in ("idx", "iclabel", "iclabel_prob", "excluded", "reason") if c in table]
    cols += [c for c in table.columns if c.startswith("eog_z_")] + ["manual"]
    edited = st.data_editor(
        table[cols],
        disabled=[c for c in cols if c != "manual"],
        column_config={
            "manual": st.column_config.SelectboxColumn(options=["auto", "exclude", "keep"]),
        },
        hide_index=True,
        key=f"ica_table_{sub}",
    )
    new_ex = sorted(
        int(i) for i, m in zip(edited["idx"], edited["manual"], strict=True) if m == "exclude"
    )
    new_in = sorted(
        int(i) for i, m in zip(edited["idx"], edited["manual"], strict=True) if m == "keep"
    )
    for name, vals in (("exclude", new_ex), ("include", new_in)):
        if vals:
            put(cfg, f"{over}.{name}", vals)
        else:
            drop(cfg, f"{over}.{name}")
    if new_ex or new_in:
        st.caption(f"Written to `overrides: sub-{sub}: artifacts: ica:` (rerun ica to apply).")
    ica_path = _output(root, rcfg, "ica", sub, "ica", "ica", ".fif")
    if ica_path.exists() and st.checkbox("Show topographies", key=f"ica_topo_{sub}"):
        ica = mne.preprocessing.read_ica(ica_path, verbose=False)
        st.pyplot(
            erp_plots.ica_topomaps(ica, [int(i) for i in table.loc[table["excluded"], "idx"]])
        )
    _footer(root, rel, cfg, _validated(cfg), "ica")


def page_epochs(root: Path) -> None:
    st.header("Epochs")
    st.caption("Rejection rules and what they removed (the epochs stage).")
    sel = _select_config(root)
    if sel is None:
        return
    rel, cfg = sel
    parsed0 = _validated(cfg)
    rej = parsed0.epochs.reject if parsed0 else None
    k = f"ep_{rel}"
    st.subheader("Epoch sets")
    _sets_editor(cfg, k)
    st.subheader("Rejection (ERPLAB semantics)")
    methods = ["thresholds", "rules", "autoreject", "none"]
    method = st.selectbox(
        "Method",
        methods,
        index=methods.index(get(cfg, "epochs.reject.method", "thresholds")),
        key=f"{k}_m",
    )
    put(cfg, "epochs.reject.method", method)
    if method == "thresholds":
        _optional_number(
            cfg,
            "epochs.reject.ptp_uv",
            "Peak-to-peak (µV)",
            rej.ptp_uv if rej else 100.0,
            f"{k}_ptp",
            min_value=1.0,
        )
        c1, c2 = st.columns(2)
        win = c1.number_input(
            "Window (ms)",
            1.0,
            10_000.0,
            float(get(cfg, "epochs.reject.ptp_window_ms", 200.0)),
            key=f"{k}_w",
        )
        step = c2.number_input(
            "Step (ms)",
            1.0,
            10_000.0,
            float(get(cfg, "epochs.reject.ptp_step_ms", 100.0)),
            key=f"{k}_s",
        )
        put(cfg, "epochs.reject.ptp_window_ms", win)
        put(cfg, "epochs.reject.ptp_step_ms", step)
        _optional_number(
            cfg,
            "epochs.reject.step_heog_uv",
            "HEOG step (µV)",
            rej.step_heog_uv if rej else 32.0,
            f"{k}_heog",
            min_value=1.0,
        )
    elif method == "rules":
        st.info("Rules are edited in the YAML (one ERPLAB call each); see the config reference.")
    mt = st.number_input(
        "Minimum kept trials per condition",
        1,
        10_000,
        int(get(cfg, "epochs.reject.min_trials_per_condition", 30)),
        key=f"{k}_mt",
    )
    put(cfg, "epochs.reject.min_trials_per_condition", int(mt))
    parsed = _validated(cfg)
    if parsed is None:
        return
    sub = _subject(parsed, f"{k}_sub")
    sets = list(parsed.epochs.sets)
    if sub is None or not sets:
        _footer(root, rel, cfg, parsed, "epochs")
        return
    set_name = st.selectbox("Epoch set", sets, key=f"{k}_set")
    log_path = _output(
        root, resolve_for_subject(parsed, sub), "epochs", sub, set_name, "log", ".tsv"
    )
    if not log_path.exists():
        _missing("epoch log", "epochs")
    else:
        log = pd.read_csv(log_path, sep="\t")
        log["why"] = log["reason"].str.split(" ").str[0].where(~log["kept"], "kept")
        summary = log.pivot_table(
            index="trial_type", columns="why", values="idx", aggfunc="count", fill_value=0
        )
        st.subheader(f"sub-{sub}: {int(log['kept'].sum())} of {len(log)} epochs kept")
        st.dataframe(summary)
        threshold = parsed.epochs.reject.ptp_uv if method == "thresholds" else None
        st.pyplot(erp_plots.ptp_histogram(log["ptp_uv"].to_numpy(float), threshold))
        if threshold is not None:
            would = int((log["ptp_uv"] > threshold).sum())
            st.caption(
                f"With {threshold:g} µV, {would} epochs exceed the peak-to-peak rule (logged "
                "values; rerun epochs to apply a change)."
            )
        st.dataframe(
            log.loc[~log["kept"], ["idx", "onset_s", "trial_type", "code", "reason"]],
            hide_index=True,
        )
    _footer(root, rel, cfg, parsed, "epochs")


def page_components(root: Path) -> None:
    st.header("Components")
    st.caption("Which components to measure, where and when (the erp stage).")
    sel = _select_config(root)
    if sel is None:
        return
    rel, cfg = sel
    library = load_library()
    k = f"cp_{rel}"
    _conditions_editor(cfg, k)
    family = presets(library)
    preset = st.selectbox(
        "Preset",
        family,
        index=family.index(get(cfg, "components.preset", "erpcore"))
        if get(cfg, "components.preset", "erpcore") in family
        else 0,
        key=f"{k}_preset",
    )
    put(cfg, "components.preset", preset)
    ids = sorted({key.split("@")[0] for key in library})
    use = st.multiselect(
        "Components", ids, [c for c in get(cfg, "components.use", []) if c in ids], key=f"{k}_use"
    )
    put(cfg, "components.use", use)
    parsed0 = _validated(cfg)
    if parsed0 is None or not use:
        _footer(root, rel, cfg, parsed0, "erp")
        return
    res = resolve(parsed0.components.model_copy(update={"overrides": {}}), library)
    for cid in use:
        rc = res.components.get(cid)
        if rc is None:
            continue
        d = rc.definition
        st.subheader(f"{d.name} ({rc.key})")
        over = get(cfg, f"components.overrides.{cid}", {}) or {}
        c1, c2, c3 = st.columns(3)
        w0 = over.get("window_ms", list(d.window_ms))
        lo = c1.number_input("Window start (ms)", value=float(w0[0]), key=f"{k}_{cid}_lo")
        hi = c2.number_input("Window end (ms)", value=float(w0[1]), key=f"{k}_{cid}_hi")
        roi0 = get(over, "rois.primary", list(d.rois.primary))
        roi = c3.text_input("Primary ROI (comma-separated)", ", ".join(roi0), key=f"{k}_{cid}_roi")
        new: dict[str, Any] = {k_: v for k_, v in over.items() if k_ not in ("window_ms", "rois")}
        if [lo, hi] != [float(x) for x in d.window_ms]:
            new["window_ms"] = [lo, hi]
        roi_list = [r.strip() for r in roi.split(",") if r.strip()]
        rois = {k_: v for k_, v in (over.get("rois") or {}).items() if k_ != "primary"}
        if roi_list != list(d.rois.primary):
            rois["primary"] = roi_list
        if rois:
            new["rois"] = rois
        if new:
            put(cfg, f"components.overrides.{cid}", new)
            st.caption("Override of the preset:")
            st.json(new)
        else:
            drop(cfg, f"components.overrides.{cid}")
    parsed = _validated(cfg)
    if parsed is not None:
        sub = _subject(parsed, f"{k}_sub")
        if sub:
            _subject_erp(root, parsed, sub)
    _footer(root, rel, cfg, parsed, "erp")


def _conditions_editor(cfg: dict[str, Any], k: str) -> None:
    """``conditions`` (name -> trial-type selectors) and difference ``contrasts`` as tables.

    Lateralized contrasts are kept as they are (edited in the YAML)."""
    st.subheader("Conditions and contrasts")
    conds = get(cfg, "conditions", {}) or {}
    rows = [{"condition": n, "selectors": ", ".join(s)} for n, s in conds.items()]
    edited = st.data_editor(
        pd.DataFrame(rows, columns=["condition", "selectors"]),
        num_rows="dynamic",
        hide_index=True,
        key=f"{k}_conds",
    )
    new_conds = {
        str(r["condition"]).strip(): [
            s.strip() for s in str(r["selectors"]).split(",") if s.strip()
        ]
        for r in edited.to_dict("records")
        if str(r.get("condition") or "").strip() and str(r.get("selectors") or "").strip()
    }
    if new_conds:
        put(cfg, "conditions", new_conds)
    else:
        drop(cfg, "conditions")
    contrasts = get(cfg, "contrasts", {}) or {}
    diff = {n: c for n, c in contrasts.items() if c.get("type", "difference") == "difference"}
    other = {n: c for n, c in contrasts.items() if n not in diff}
    rows = [
        {"contrast": n, "plus": c.get("plus"), "minus": c.get("minus")} for n, c in diff.items()
    ]
    names = list(new_conds)
    edited = st.data_editor(
        pd.DataFrame(rows, columns=["contrast", "plus", "minus"]),
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "plus": st.column_config.SelectboxColumn(options=names),
            "minus": st.column_config.SelectboxColumn(options=names),
        },
        key=f"{k}_contrasts",
    )
    new_contrasts: dict[str, Any] = {
        str(r["contrast"]).strip(): {"plus": r["plus"], "minus": r["minus"]}
        for r in edited.to_dict("records")
        if str(r.get("contrast") or "").strip() and r.get("plus") and r.get("minus")
    }
    new_contrasts |= other
    if new_contrasts:
        put(cfg, "contrasts", new_contrasts)
    else:
        drop(cfg, "contrasts")
    if other:
        st.caption(f"Lateralized contrasts {sorted(other)} are kept as in the YAML.")


def _sets_editor(cfg: dict[str, Any], k: str) -> None:
    """``epochs.sets`` as a table (response rules are kept as they are, edited in the YAML)."""
    sets = get(cfg, "epochs.sets", {}) or {}
    # Fields absent from the YAML show (and keep) the schema defaults: an absent baseline_ms
    # means -200..0 ms, not "no baseline".
    default = EpochSet(lock="x")

    def baseline(s: dict[str, Any]) -> list[float | None]:
        b = s.get("baseline_ms", default.baseline_ms)
        return [None, None] if b is None else [float(b[0]), float(b[1])]

    rows = [
        {
            "set": n,
            "lock": s.get("lock", ""),
            "tmin_ms": float(s.get("tmin_ms", default.tmin_ms)),
            "tmax_ms": float(s.get("tmax_ms", default.tmax_ms)),
            "baseline_from_ms": baseline(s)[0],
            "baseline_to_ms": baseline(s)[1],
        }
        for n, s in sets.items()
    ]
    edited = st.data_editor(
        pd.DataFrame(
            rows,
            columns=["set", "lock", "tmin_ms", "tmax_ms", "baseline_from_ms", "baseline_to_ms"],
        ),
        num_rows="dynamic",
        hide_index=True,
        key=f"{k}_sets",
    )
    new: dict[str, Any] = {}
    for r in edited.to_dict("records"):
        name = str(r.get("set") or "").strip()
        if not name or not str(r.get("lock") or "").strip():
            continue
        entry: dict[str, Any] = dict(sets.get(name, {}))  # keeps e.g. a response rule
        entry |= {"lock": r["lock"], "tmin_ms": r["tmin_ms"], "tmax_ms": r["tmax_ms"]}
        b0, b1 = r.get("baseline_from_ms"), r.get("baseline_to_ms")
        value: list[float] | None = None
        if b0 is not None and b1 is not None and not (pd.isna(b0) or pd.isna(b1)):
            value = [float(b0), float(b1)]
        if "baseline_ms" in entry or value != list(default.baseline_ms or []):
            entry["baseline_ms"] = value  # written only when set before or changed
        new[name] = entry
    if new:
        put(cfg, "epochs.sets", new)
    else:
        drop(cfg, "epochs.sets")


def _subject_erp(root: Path, cfg: Config, sub: str) -> None:
    rcfg = resolve_for_subject(cfg, sub)
    res = resolve(rcfg.components)
    for rc in res.components.values():
        d = rc.definition
        ave = _output(root, rcfg, "erp", sub, d.epoch_set, "ave", ".fif")
        if not ave.exists():
            _missing(f"averages for sub-{sub}", "erp")
            return
        evokeds = {e.comment: e for e in mne.read_evokeds(ave, verbose=False)}
        if d.contrast and d.contrast in rcfg.contrasts:
            parents = rcfg.contrasts[d.contrast].parents
            conds = {w: evokeds[w] for w in parents if w in evokeds}
            diff = (d.contrast, evokeds[d.contrast]) if d.contrast in evokeds else None
        else:
            conds = {n: e for n, e in evokeds.items() if n in rcfg.conditions}
            diff = None
        st.pyplot(
            erp_plots.component_fig(
                title=f"{d.name}, sub-{sub}",
                conditions=conds,
                difference=diff,
                channels=list(d.rois.primary),
                window_ms=(float(d.window_ms[0]), float(d.window_ms[1])),
                lowpass_hz=rcfg.erp.plot_lowpass_hz,
            )
        )
    if not rcfg.epochs.sets:
        st.info("No epoch sets yet: add one on the Epochs page.")
        return
    measures = _output(root, rcfg, "erp", sub, next(iter(rcfg.epochs.sets)), "measures", ".tsv")
    if measures.exists():
        m = pd.read_csv(measures, sep="\t")
        st.dataframe(
            m[["component", "wave", "roi", "channels", "measure", "value", "unit", "n_trials"]],
            hide_index=True,
        )
        st.caption("Measured with the window/ROI the erp stage ran with; rerun erp after edits.")


def page_group(root: Path) -> None:
    st.header("Group")
    st.caption("Grand averages, tests and the published-value comparison (the group stage).")
    sel = _select_config(root)
    if sel is None:
        return
    rel, cfg = sel
    parsed0 = _validated(cfg)
    k = f"gr_{rel}"
    tests = st.multiselect(
        "Tests",
        ["paired_t", "wilcoxon"],
        get(cfg, "group.tests", ["paired_t", "wilcoxon"]),
        key=f"{k}_t",
    )
    put(cfg, "group.tests", tests)
    refs = ["none", "erpcore"]
    ref = st.selectbox(
        "Compare with published values",
        refs,
        index=refs.index(get(cfg, "group.reference_values", "none")),
        key=f"{k}_r",
    )
    put(cfg, "group.reference_values", ref)
    contrasts = list(parsed0.contrasts) if parsed0 else []
    on = st.checkbox(
        "Cluster permutation test", bool(get(cfg, "group.cluster.enabled", False)), key=f"{k}_c"
    )
    put(cfg, "group.cluster.enabled", on)
    if on and contrasts:
        cur = get(cfg, "group.cluster.contrast")
        c = st.selectbox(
            "Contrast",
            contrasts,
            index=contrasts.index(cur) if cur in contrasts else 0,
            key=f"{k}_cc",
        )
        put(cfg, "group.cluster.contrast", c)
        tails = {"two-tailed": 0, "negative": -1, "positive": 1}
        t0 = {v: n for n, v in tails.items()}[int(get(cfg, "group.cluster.tail", 0))]
        tail = st.radio(
            "Tail", list(tails), index=list(tails).index(t0), horizontal=True, key=f"{k}_tail"
        )
        put(cfg, "group.cluster.tail", tails[tail])
    parsed = _validated(cfg)
    if parsed is not None:
        _group_results(root, parsed)
    _footer(root, rel, cfg, parsed, "group")


def _group_results(root: Path, cfg: Config) -> None:
    gdir = paths.group_dir(root, cfg)
    task = cfg.dataset.task
    qa_path = gdir / f"task-{task}_qa.json"
    if not qa_path.exists():
        _missing("group results", "group")
        return
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    for c in qa["checks"]:
        (st.warning if c["level"] == "warn" else st.error if c["level"] == "fail" else st.info)(
            f"{c['id']}: {c['message']}"
        )
    res = resolve(cfg.components)
    for rc in res.components.values():
        d = rc.definition
        ga_path = gdir / f"task-{task}_desc-{d.epoch_set}_ave.fif"
        if not (d.contrast and ga_path.exists()):
            continue
        ga = {e.comment: e for e in mne.read_evokeds(ga_path, verbose=False)}
        subjects = {}
        for sub in qa.get("subjects", []):
            ave = _output(
                root, resolve_for_subject(cfg, sub), "erp", sub, d.epoch_set, "ave", ".fif"
            )
            if ave.exists():
                evs = {e.comment: e for e in mne.read_evokeds(ave, verbose=False)}
                if d.contrast in evs:
                    subjects[sub] = evs[d.contrast]
        if d.contrast in ga:
            st.pyplot(
                erp_plots.grand_average_fig(
                    d.contrast,
                    ga[d.contrast],
                    subjects,
                    list(d.rois.primary),
                    (float(d.window_ms[0]), float(d.window_ms[1])),
                    cfg.erp.plot_lowpass_hz,
                )
            )
    stats = gdir / f"task-{task}_stats.tsv"
    if stats.exists():
        st.subheader("Statistics")
        st.dataframe(pd.read_csv(stats, sep="\t"), hide_index=True)
    comp = gdir / f"task-{task}_erpcore-comparison.tsv"
    if comp.exists():
        st.subheader("Comparison with ERP CORE's published values")
        table = pd.read_csv(comp, sep="\t", dtype={"subject": str})
        st.dataframe(table.style.map(_status_style, subset=["status"]), hide_index=True)


def _status_style(status: object) -> str:
    colour = STATUS_COLOURS.get(str(status))
    return f"color: {colour}; font-weight: 600" if colour else ""


PAGE_FUNCS: dict[str, Callable[[Path], None]] = dict(
    zip(
        ERP_PAGES,
        (page_preprocess, page_ica, page_epochs, page_components, page_group),
        strict=True,
    )
)
