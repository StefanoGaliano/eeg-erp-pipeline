# Changelog

## 0.6.0 (2026-11-02)

**VEP presets on synthetic and real data** (docs/BLUEPRINT_PHASES.md section 12).

- **Component-level reference:** a component's `reference` (ISCEV VEP: Fz; ERP CORE P3: P9/P10)
  is applied when it differs from the data's: the erp stage measures that component on
  re-referenced copies of the averages and epochs (SME too) and records it (`qa.json`
  `component_references`, QA `ERP_REFERENCE_CHANGED`); `_ave.fif` keeps the dataset reference.
  Opt out per component with `components.overrides: {<id>: {reference: null}}`, as the
  `erpcore-exact` recipe does. ERP CORE results are unchanged.
- **Synthetic pattern-reversal VEP** (`make_vep_recording`): the `vep_p100@iscev` latency is
  recovered within 2 ms (one sample), N75/N135 within 4 ms, the amplitude matches the Oz-Fz
  wave.
- **Real data:** `code/configs/gaba_vep.yaml`, five infants of the GABA pattern-reversal VEP
  (HAPPE+ER tutorial, Zenodo 5172962, CC-BY-4.0), EGI HydroCel 129; results and caveats (adult
  ISCEV window, later infant P1) in docs/validation_erp.md.
- **EGI `.raw`:** Net Station files holding a single segment are read as continuous; several
  segments are refused. Synthetic EGI writer and tests.
- **Config:** `preproc.bad_channels.max_fraction` and `epochs.reject.max_rejected_fraction`
  set the fail limits of `PREPROC_BADS_FRACTION` and `EPOCHS_REJECTED_FRACTION` (defaults
  unchanged: 25 %, 50 %).
- **Fixes found on the GABA data:** trigger channels stay triggers (an EGI event track named
  `resp` became a respiration channel; also in the MATLAB `convert.m`); the temporary EGI copy
  left in `raw.filenames` broke QA; text event codes (`vep+`) broke the epochs log.

## 0.5.0 (2026-11-02)

**Python ↔ MATLAB parity** (docs/BLUEPRINT_PHASES.md, Phase M): the same config runs natively in
MATLAB (EEGLAB, FieldTrip, ERPLAB) and gives the same outputs as Python, checked by realdata
tests (MATLAB tests run locally; CI has no MATLAB licence).

- **Config dump:** `eegunify config dump <yaml> --json [--resolved] [-s SUB]` writes the full
  config with subject overrides applied and components resolved, plus a `config_hash`; a
  `matlab` block carries mappings as lists of pairs and the channel-name tables (10-05 names,
  aliases, BioSemi 64 order), so both languages normalize names from one source.
- **M1, ingestion parity:** `matlab/+eegunify/` (`convert`, `read_any`, `normalize_names`,
  `infer_type`, `extract_events`, `canonical_validate`, `write_bids`) for EEGLAB `.set`,
  BrainVision and BioSemi BDF. On ERP CORE N170 sub-015 and synthetic BrainVision/BDF files:
  identical `events.tsv` and `channels.tsv`, data within 0.001 µV, config hash round-trip
  (`tests/integration/test_matlab_parity.py`).
- **M2, ERP-lite N170 parity:** recipe `erp_lite` (the dataset recipe without ICA, resampling
  and interpolation) and `matlab/+eegunify/erp_lite.m` (bipolar EOG, ERPLAB `pop_basicfilter`,
  re-reference, ERPLAB epochs, rejection, averages and `pop_geterpvalues`). On sub-015:
  continuous data within 0.015 µV, identical rejected epochs, ERP RMS difference 0.0005 µV,
  N170 mean amplitudes within 0.004 µV (`tests/integration/test_matlab_erp_lite.py`). The
  ERPLAB code is shared with the M-E5 cross-check (`erplab_erp.m`).
- **Fix:** preproc now filters EOG, EMG and ECG channels like the EEG (MNE's default picks
  skipped EOG, which kept its DC offset); found by the M2 parity test. Preproc caches are
  recomputed (`schema_version` 3). Effect on the ERP CORE comparison: P3 sub-019 (default
  recipe) moves from −0.62 to −0.55 µV; see docs/validation_erp.md.
- **Tests:** the realdata tests count only artifact rejections (not `NO_RESPONSE`) and list the
  documented default-recipe deviations exactly.
- README: "Python ↔ MATLAB parity" table.

## 0.4.0 (2026-11-01)

**The ERP stages in the app** (docs/BLUEPRINT_PHASES.md, Phase A2): five pages that edit the
dataset config and show each stage's outputs; a config built through them runs exactly like a
hand-written one.

- **App, ERP pages (A2):** Preprocess (filter, bad channels, reference; 60 s PSD preview),
  ICA review (decision table, manual exclude/keep written per subject to `overrides`,
  topographies), Epochs (sets, rejection rules, per-condition summary, peak-to-peak histogram
  with the threshold, rejected epochs), Components (conditions, contrasts, preset and
  components, window/ROI overrides shown as a diff, the subject's ERPs and measures) and Group
  (tests, cluster settings, grand average, statistics, ERP CORE comparison). All five edit one
  dataset config, validated on every change, saved as YAML; a run panel calls the stage runner.
  New figure functions in `erp/plots.py`. README: the app's eight steps and a GIF of the
  Components page.

## 0.3.0 (2026-11-01)

**Group statistics and the ERP CORE reproduction** (docs/BLUEPRINT_PHASES.md, M-E5 and M-E6a).
N170, P3 and N2pc on ERP CORE sub-015..019 against ERP CORE's published per-subject values:
with ERP CORE's own per-subject choices (`erpcore-exact`) every compared subject is within
±0.5 µV; the automatic recipe is within tolerance for 10 of 13 subject-component pairs, the
other three documented in docs/validation_erp.md.

- **M-E5, ERPLAB cross-check:** rejection window sizes, the step function's half window and the
  fractional-area latency now follow ERPLAB 13.10 exactly. On ERP CORE N170 sub-015 ERPLAB
  rejects the same epochs and gives the same mean amplitudes (|Δ| < 1e-6 µV);
  `tests/integration/test_erplab_crosscheck.py`, `matlab/erplab_crosscheck.m`.
- Bipolar EOG is derived in preproc, so the HEOG step rejection also runs without ICA.
- **ERP CORE published values:** `eegunify fetch erpcore-values` downloads the per-subject
  measurement files and subject summaries of N170, P3 and N2pc from OSF and writes tidy TSVs to
  `src/eegunify/erp/reference/` (CC-BY-4.0).
- **Correct-response trials:** `epochs.sets.<name>.response: {selector, window_ms}` keeps only
  events followed by a matching response in the window (ERPLAB binlister `{t<lo-hi>code}`);
  the others are logged as `NO_RESPONSE` and not counted as artifact rejections. The ERP CORE
  N170 config uses it, as ERP CORE's bins do.
- **ERP CORE comparison:** `eegunify.erp.comparison` compares measures with the published values
  (sign equal to ERP CORE's, ±0.5 µV per subject, ±0.3 µV mean, ±8 ms; ERP CORE's excluded
  subjects only reported). N170 sub-015..019: see docs/validation_erp.md.
- **P3 and N2pc:** `code/configs/erpcore_p3.yaml` and `erpcore_n2pc.yaml` (P9/P10 reference,
  ERP CORE's event codes, correct-response bins, 26 ms shift), and lateralized contrasts
  (`contrasts.<name>.type: lateralized`, `left`/`right` conditions, channel `pairs`): contra and
  ipsi waves as ERP CORE's ERPLAB bin operations, measured at PO7/PO8 by the `n2pc` preset.
  Published values are matched by exact wave label.
- OSF downloads retry HTTP 403 (OSF sometimes answers bursts with it for public files).
- **`erpcore-exact` for P3 and N2pc:** their parameter files and ICA weights are pinned too;
  the reference is read from ERP CORE's channel-operation file (all 33 sites for N170, P9/P10
  for P3 and N2pc) and the recording's channel order is checked against theirs. Group
  comparison rows for measures a component does not take are `not_measured`.
- **group stage:** grand averages, all subjects' measures, `stats.tsv` (paired t and Wilcoxon on
  contrast mean amplitudes, Cohen's d_z with a bootstrap CI from 8 subjects up, descriptive rows
  for the rest), cluster permutation test, the ERP CORE comparison
  (`group.reference_values: erpcore`), QA `GROUP_SUBJECTS_EXCLUDED`, `GROUP_UNDERPOWERED`,
  `REFERENCE_VALUES_MISMATCH`. ERP CORE's published tables now ship in
  `src/eegunify/erp/reference/`.
- **`erpcore-exact` recipe:** follows ERP CORE's own per-subject processing (their ICA
  weights and components, reference over all 33 sites, interpolation after ICA, five ERPLAB
  rejection tests with their parameters, 20 Hz peak low-pass), built from their OSF files.
  N170 face − car within 0.5 µV for every compared subject (mean of 4 within 0.05 µV). New
  config: `reference.apply_to_eog/allow_bad_channels`, `artifacts.method: precomputed`,
  `artifacts.corrected_bipolar`, `preproc.interpolate_after_ica`,
  `preproc.bad_channels.extra`, `epochs.reject.method: rules`, `erp.peak_lowpass_hz/order`.

## 0.2.0 (2026-10-31)

**Single-subject ERPs**: the ERP stages on canonical data (docs/BLUEPRINT_PHASES.md, M-E0 to
M-E4).

- **Stage runner:** `eegunify erp run/status`, cached per subject and stage by a key over the
  config section, component definitions, upstream keys and versions; `--from/--to/--force`.
- **preproc:** line noise, ERPLAB-style Butterworth filters, bad channels (config, pyprep, LOF),
  interpolation, re-reference (average, channels, REST); BioSemi data must be re-referenced.
- **ica:** picard (extended infomax) on a 1–100 Hz average-referenced copy, ICLabel plus an EOG
  correlation vote, `_components.tsv` with a reason per component; optional bipolar EOG and ASR.
- **epochs:** epoch sets selected by trial-type prefix, ERPLAB-style moving-window peak-to-peak
  and HEOG step rejection, a per-epoch log with the rawdata code and reason.
- **erp:** averages per condition, difference waves, `measures.tsv` (mean amplitude, peak,
  fractional-area latency) from the component library, one figure per component.
- **QA:** 15 new checks with stable IDs (`erp/qa.py`).
- **Validation:** synthetic ground truth for every stage; ERP CORE N170 sub-015 through all
  stages; the v0.1.0 preview value (−2.24 µV) is a golden regression test.

## 0.1.0 (2026-10-30)

First release: **Phase 1, unified ingestion**. Any supported raw EEG recording becomes one
canonical BIDS-EEG form (BrainVision, float32, µV), with a QA report.

- **Formats:** BrainVision, BioSemi BDF, EDF/EDF+, EEGLAB, LSL XDF, GDF, ANT eego, Neuroscan,
  Curry 7/8, EGI `.mff`, CSV/OpenBCI; events from trigger channels, markers, XDF marker streams
  or BIDS `events.tsv`. See `docs/formats.md`.
- **Canonical checks:** 10 invariants, including unit sanity (µV/mV/V mix-ups refused with the
  fix suggested). See `docs/canonical_contract.md`.
- **Configs:** per-dataset YAML, strictly validated; `config init` drafts one from a recording.
  See `docs/config_reference.md`.
- **QA reports:** HTML + JSON per recording and a dataset `summary.tsv`, with 12 checks that have
  stable IDs and a visual-ROI focus.
- **Behavioural logs:** PsychoPy, Presentation and E-Prime, aligned by time.
- **Interactive app:** `eegunify app` (Streamlit): Inspect → Configure → Convert + QA.
- **MATLAB:** `eegunify export --to eeglab` and `matlab/load_canonical.m`, verified in MATLAB
  against Python.
- **Validation:**
  - ERP CORE N170 golden tests, with events matching the dataset authors' own files.
  - Cross-format tests on vendor files (ANT vs BrainVision, Curry vs BDF).
  - 21 open datasets run with automatic configs: 19 convert and 2 are refused for real defects
    (`docs/validation_results.md`).
