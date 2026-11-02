# EEG-to-ERP Pipeline: Architecture Blueprint (Phases 2–6, A2, M, BCI)

Project: `eegunify` (repo `StefanoGaliano/eeg-erp-pipeline`, v0.1.0 = Phase 1).
Author: Stefano Galiano. Blueprint date: 2026-10-30.
Audience: a Claude Code instance with zero prior context that builds the downstream phases
milestone by milestone. **Read `docs/BLUEPRINT.md` first** (sections 5, 8, 9, 10, 13 are the
contract this file builds on) and `CLAUDE.md` (commands, gotchas). This file replaces
BLUEPRINT.md section 15 (sketch) and expands sections 22 (Phase M) and 23 (Phase A).
No code is written here; it says what to build, where, in what order, and how to prove it works.

---

## 0. Decisions (resolved with the user on 2026-10-30)

| # | Decision |
|---|---|
| OQ2 | **Decided:** ICA (picard) + ICLabel, with EOG correlation as a second vote; **ASR available as an option, off by default**. |
| OQ3 | **Decided:** ERPLAB-style thresholds by default; autoreject as an option. |
| OQ4 | **Decided:** Phase M = ingestion parity (BrainVision, BDF, EEGLAB) + N170 ERP-lite via EEGLAB/ERPLAB, no MATLAB ICA. |
| OQ5 | **Decided:** install ERPLAB and bva-io into `Practice/toolboxes/eeglab/plugins` (FieldTrip fallback kept). |
| OQ1, OQ6, OQ7, OQ8 | Recommended defaults below apply (not discussed individually). |
| ERP CORE comparison (2026-11-01) | **Decided after reading the published files** (section 8.2): the sign rule is "our sign equals ERP CORE's sign, per subject", not "every N170 difference negative" (ERP CORE's own sub-019 N170 difference is +2.041 µV). Subjects ERP CORE excluded (`<component>_subjects.tsv`, e.g. N170 sub-016, reason TC) are run and reported, labelled "excluded by ERP CORE", but are left out of the tolerance checks and of the subject mean, which is taken over the compared subjects only. This supersedes any other wording in this file. **Refined 2026-11-01:** the sign is only checked when the published value is larger than the tolerance in absolute value (|theirs| > 0.5 µV); near zero either sign is within the tolerance (N2pc sub-019: ours +0.002, theirs −0.151 µV). |

Original questions and rationale:


| # | Question | Recommended default | Why |
|---|----------|---------------------|-----|
| OQ1 | ERP CORE validation target: only the 5 subjects in the BIDS archive, or also fetch the full 40-subject per-component OSF projects? | **Both, staged.** 5-subject BIDS data for the pipeline tests (M-E6a); the published 40-subject *measurement tables* (small CSV/XLSX files, no EEG) for the reference values; the full 40-subject N170 EEG only as an optional `realdata_full` run (M-E6b). | Raw 40-subject EEG is ~GBs per component; the per-subject published values are tiny and give a subject-by-subject comparison for sub-015..019 already. |
| OQ2 | Artifact strategy default: ICA+ICLabel, ASR, or both? | **ICA (picard) + ICLabel, EOG-correlation as a second vote; ASR off by default** (optional `artifacts.asr` block, via `meegkit`). | Matches ERP CORE (ICA for blinks/eye movements), is the field norm for offline ERPs, and keeps results comparable. ASR distorts ERPs if the calibration is poor and is a real-time tool (useful later for BCI). |
| OQ3 | Epoch rejection default: autoreject or thresholds? | **ERPLAB-style thresholds by default** (peak-to-peak moving window on EEG, step function on HEOG), `autoreject` (local) as an option. | Thresholds are what ERP CORE did, so the reproduction is honest; autoreject is slow, stochastic across versions, and hard to report in a methods section. |
| OQ4 | Phase M scope | **Ingestion parity + "ERP-lite" parity for N170 only** (filter, re-ref, epoch, average, mean amplitude) via EEGLAB+ERPLAB; no MATLAB ICA parity. | ICA is not numerically reproducible across implementations; parity tests would be flaky. One component end-to-end shows both ecosystems at much lower cost. |
| OQ5 | Install ERPLAB in `Practice/toolboxes`? | **Yes** (ERPLAB ≥ 10, as EEGLAB plugin). Also install **bva-io** if possible; keep FieldTrip fallback. | ERPLAB is the reference implementation of ERP CORE; cross-checks are much stronger with it. |
| OQ6 | Stats dependency: `pingouin` or plain `scipy` + own effect sizes? | **scipy only** (+ `mne.stats` for clustering). | Paired t, Wilcoxon, Cohen's d_z, bootstrap CIs are ~60 lines; pingouin adds a heavy dep tree for little. |
| OQ7 | Does the user want group stats on own lab data soon (more than ERP CORE)? | Assume **no**; design for N ≥ 1 subjects, test with ERP CORE + synthetic. | Nothing in the repo suggests private data. |
| OQ8 | Release cadence | **v0.2.0 after M-E4 (single-subject ERP), v0.3.0 after M-E6 (group + ERP CORE reproduction), v0.4.0 after A2, v0.5.0 after Phase M.** Make the repo public at v0.3.0. | The portfolio story needs the reproduction number, not just a converter. |

---

## 1. Goals, non-goals, success criteria

**Goal:** from canonical rawdata (Phase 1) to subject ERPs, component measurements and group
statistics, reproducible from one YAML file, for visual paradigms first (VEP, P1/N1, N170, P3, N2pc)
and the other ERP CORE components second (MMN, N400, ERN, LRP).

**Headline acceptance (portfolio number):** ERP CORE N170, sub-015..019, PO8 face−car mean
amplitude in 110–150 ms: our per-subject values within **±0.5 µV** of the ERP CORE published
per-subject values and each subject's sign equal to ERP CORE's sign, for every subject ERP CORE
included (N170: 015, 017, 018, 019; sub-016 was excluded by ERP CORE and is only reported; see
8.2); README shows the table.

**Non-goals now:** source localisation, time-frequency analyses, mixed models, real-time. Seams are
left for TF and BCI (section 13).

---

## 2. Decisions that resolve Phase 1 findings

| Finding | Decision |
|---|---|
| F1 `read_raw_bids` labels `stimulus/face/12` | One library function `eegunify.erp.events.select(epochs_or_ids, selector)` does MNE tag matching; config never names full event labels, only `trial_type` prefixes (`stimulus/face`). A unit test asserts that a trial type with several codes is fully selected. |
| F2 26 ms shift already in rawdata | ERP stages **never shift again**. The `stage_epochs` provenance records the onset shift found in `rawdata` `_eeg.json` `eegunify` block so the measurement is traceable. Response-locked components (ERN, LRP) must not be shifted; Phase 1 already only shifts `stimulus/*`. |
| F3 BioSemi reference-free | `reref` stage is **mandatory** when `_eeg.json` `EEGReference` contains `CMS/DRL` or `reference-free`: QA `REREF_REQUIRED` = fail if the config sets `reference.scheme: none`. |
| F4 N170 window conflict | Component definitions become **data** (`src/eegunify/erp/components/*.yaml`, section 5). ERP CORE preset: N170 = 110–150 ms, PO8 (also reported at P8/PO7/P7 as a secondary ROI). The "130–200 ms P7/P8/PO7/PO8" sketch becomes the `literature` preset (`n170@classic`). BLUEPRINT.md section 15 is superseded (see section 16 of this file). |
| F5 5 subjects only | Section 8.3. |
| F6 −2.24 µV sub-015 preview | Becomes a **regression golden value** for the "preview recipe" (0.1–30 Hz, average ref, ±150 µV, no ICA). Our full ERP CORE recipe will give a different value; both are kept, labelled. |

---

## 3. Derivatives layout

BIDS-derivatives, one pipeline folder per stage, FIF payloads, `desc-` entities. One folder per
stage (not one `eegunify-erp/` folder) because each stage is independently cacheable and
deletable, and BIDS allows any number of derivative pipelines.

```
derivatives/
├── eegunify-qa/                         # Phase 1 (unchanged)
├── eegunify-preproc/
│   ├── dataset_description.json         # GeneratedBy: eegunify <version>, SourceDatasets: rawdata
│   └── sub-015/ses-N170/eeg/
│       ├── sub-015_ses-N170_task-N170_desc-preproc_eeg.fif      # filtered, bads interpolated, re-referenced
│       ├── sub-015_ses-N170_task-N170_desc-preproc_channels.tsv # status + reason (pyprep/ransac/config)
│       ├── sub-015_..._desc-preproc_provenance.json
│       └── sub-015_..._desc-preproc_qa.json / _qa.html
├── eegunify-ica/
│   └── sub-015/.../eeg/
│       ├── sub-015_..._desc-ica_ica.fif             # mne.preprocessing.ICA (fitted on 1 Hz copy)
│       ├── sub-015_..._desc-ica_components.tsv      # idx, iclabel class, prob(7 cols), eog_r, excluded, reason
│       ├── sub-015_..._desc-clean_eeg.fif           # preproc with ICA applied
│       └── provenance/qa
├── eegunify-epochs/
│   └── sub-015/.../eeg/
│       ├── sub-015_..._desc-stim_epo.fif            # one file per epoch set (desc = epoch set name)
│       ├── sub-015_..._desc-stim_log.tsv            # per-epoch: trial_type, code, kept, reason, ptp_uv
│       └── provenance/qa
├── eegunify-erp/
│   └── sub-015/.../eeg/
│       ├── sub-015_..._desc-stim_ave.fif            # all conditions + difference waves (Evoked list, comment = name)
│       ├── sub-015_..._desc-stim_measures.tsv       # long format (section 6.6)
│       ├── sub-015_..._desc-stim_erp.png            # per-component figure
│       └── provenance/qa
└── eegunify-group/
    └── task-N170/
        ├── task-N170_desc-stim_ave.fif                # grand averages (MNE wants *_ave.fif)
        ├── task-N170_measures.tsv                   # all subjects, long format
        ├── task-N170_stats.tsv                      # tests + effect sizes + CIs
        ├── task-N170_desc-<contrast>_clusters.tsv  # cluster permutation output
        ├── task-N170_erpcore-comparison.tsv         # ours vs published (section 8)
        ├── task-N170_report.html
        └── provenance.json
```

Rules: names built with `mne_bids.BIDSPath(root=derivatives/eegunify-<stage>, description=...,
suffix=..., extension=...)` (never hand-formatted); POSIX relative paths in every JSON/TSV;
TSVs utf-8, `n/a` for missing. A `derivatives/eegunify-<stage>/summary.tsv` per stage, one row per
recording (status, config hash, n_kept, ...), same upsert logic as `qa/report.py`.

The `-ica` stage writes `desc-clean`; when `artifacts.method: none`, the epochs stage reads
`desc-preproc` directly (no copy). `desc-` values are a closed set: `preproc`, `ica`, `clean`, `epochs` (QA file),
and epoch-set names (`[A-Za-z0-9]+`).

---

## 4. Stage-runner architecture

**Decision: independent stages, each a pure-ish function, orchestrated by a small runner that
caches by a per-stage hash. Not one monolithic `run()` and not a workflow engine (Snakemake/
pydra/Prefect).** A workflow engine is overkill for 6 linear stages and would hide logic from the
app and the tests.

### 4.1 Stage contract

```
src/eegunify/erp/
├── __init__.py
├── stages/
│   ├── base.py        # Stage protocol, StageResult, StageContext, registry (@register_stage)
│   ├── preproc.py     # line noise, filter, bad channels, interpolation, reref (one stage, 5 steps)
│   ├── ica.py         # fit, classify (ICLabel + EOG), apply
│   ├── epochs.py      # epoch sets, baseline, rejection
│   ├── erp.py         # averaging, difference waves, measurement (single subject)
│   └── group.py       # grand averages, stats, ERP CORE comparison
├── runner.py          # plan + execute + cache
├── hashing.py         # stage key computation
├── paths.py           # BIDSPath builders for every stage output
├── events.py          # tag selection (F1)
├── components/        # component library (YAML data + loader)
│   ├── library.py
│   ├── visual.yaml    # VEP P100, P1, N1, N170, N2pc
│   ├── cognitive.yaml # P3, MMN, N400
│   └── motor.yaml     # ERN, LRP
├── measure.py         # mean amp, peak, fractional-area latency, jackknife
├── stats.py           # paired tests, effect sizes, bootstrap CIs, cluster wrapper
├── reference_values.py# ERP CORE published values loader
├── qa.py              # per-stage checks (stable IDs, section 7)
├── report.py          # mne.Report per stage + group report
└── plots.py
```

Each stage module exposes `run(ctx: StageContext, inputs: StageInputs, cfg: StageConfig) ->
StageResult`. A stage: (1) reads its inputs from paths (never from another stage's memory),
(2) computes, (3) writes outputs + provenance + QA atomically (write to `*.tmp`, then rename),
(4) returns `StageResult(status, outputs, qa)`. Logic lives in plain helper functions that take
and return MNE objects, so tests and the app call them in memory without disk.

Pre-stage 0 is **`load`**: `mne_bids.read_raw_bids` on rawdata + verify canonical contract
(BLUEPRINT.md §5) + read the `eegunify` sidecar block. Not cached (it is the input).

### 4.2 Cache key and resume

`stage_key = sha256(canonical-JSON of {stage name, stage schema version, resolved stage config
section, component definitions used (for erp/group), input file sha256s (or the upstream
stage_keys), eegunify minor version, MNE minor version})`.

- Stored in the output provenance (`stage_key`, `upstream_keys`).
- Runner decides per (subject, stage): **skip** if output exists and its `stage_key` equals the
  computed one; **run** otherwise. Changing `epochs.reject` changes only the epochs key, so preproc
  and ICA are reused; erp and group are invalidated because their input keys changed.
- Upstream keys (not file hashes) chain the cache: cheap, no rehash of GB files. File hashes are
  only used for rawdata inputs (recorded by Phase 1 already).
- `--force <stage>` reruns that stage and everything downstream. `--from <stage>`/`--to <stage>`
  limit the range. `eegunify erp status <config>` prints a subject×stage table: `ok`, `stale`,
  `missing`, `failed`.
- Random seeds (ICA, autoreject, permutations) are config keys with defaults (`random_state: 97`)
  and part of the hash.
- Refuse rather than guess: if an output exists but has no provenance or a different
  `eegunify` major version, the runner refuses with "delete derivatives/eegunify-ica/sub-015 or
  use --force ica".

Group stage depends on *all* subjects' erp keys; it reruns when any changes. Subjects failing a
stage are listed and excluded from group with a `GROUP_SUBJECTS_EXCLUDED` warning; never silently.

### 4.3 Parallelism

`--jobs N` over subjects with `joblib` (already a transitive MNE dep). Stages within a subject are
sequential. Logs per subject to `derivatives/eegunify-<stage>/logs/`.

---

## 5. Config design

### 5.1 Where ERP config lives

**Same YAML file as Phase 1, new top-level sections**, all optional (a Phase 1 config stays
valid). `schema_version` stays `1`: new optional sections are additive (pydantic `extra="forbid"`
means old eegunify versions reject new configs, which is the correct "refuse" behaviour).
Overrides per subject reuse the existing `overrides: {sub-015: {...}}` mechanism
(`config.resolve_for_subject`), which must be extended to deep-merge the new sections.

```yaml
# additions to code/configs/erpcore_n170.yaml (illustrative; all keys documented in config_reference.md)
preproc:
  line_noise: {method: none}            # none | notch | zapline (meegkit); freqs from QA LINE_NOISE if "auto"
  highpass_hz: 0.1                      # ERP CORE: 0.1 Hz noncausal Butterworth, 12 dB/oct
  lowpass_hz: null                      # ERP CORE measures unfiltered-lowpass; plots use plot_lowpass_hz
  filter: {method: iir, iir_order: 2, phase: zero}   # iir Butterworth (ERPLAB-like) | fir (MNE default)
  resample_hz: null
  bad_channels: {method: config_only}   # config_only | pyprep_ransac | pyprep_full | lof
  interpolate: true
reference:                              # extends the existing Phase 1 section (online/ground stay)
  scheme: average                       # average | channels | rest | none
  channels: []                          # e.g. [P9, P10] for ERP CORE non-N170 components
artifacts:
  method: ica                           # ica | none
  ica:
    fit_highpass_hz: 1.0
    n_components: 0.99
    algorithm: picard                   # picard | infomax | fastica
    extended: true
    random_state: 97
    iclabel: {exclude_classes: [eye blink], threshold: 0.8}   # ICLabel class names as mne-icalabel spells them
    eog_correlation: {channels: [VEOG, HEOG], threshold: 3.0} # z-score, second vote
    exclude: []                         # manual component indices (per subject via overrides)
    include: []                         # manual keep, wins over automatic rules
  asr: null                             # {cutoff: 20} enables meegkit ASR before ICA (off by default)
  eog_bipolar: {VEOG: [FP1, VEOG_lower], HEOG: [HEOG_left, HEOG_right]}  # optional derivation
epochs:
  sets:
    stim:                               # becomes desc-stim
      lock: "stimulus/*"                # trial_type prefix selector (tag matching, F1)
      tmin_ms: -200
      tmax_ms: 800
      baseline_ms: [-200, 0]
  reject:
    method: thresholds                  # thresholds | autoreject | none
    ptp_uv: 100                         # moving-window peak-to-peak (ERPLAB "moving window")
    ptp_window_ms: 200
    ptp_step_ms: 100
    step_heog_uv: 32                    # ERPLAB step function on HEOG (only if HEOG present)
    flat_uv: null
    min_trials_per_condition: 30        # fewer -> EPOCHS_MIN_TRIALS fail, subject excluded from group
conditions:                             # named groupings of trial_type prefixes
  face: ["stimulus/face"]
  car: ["stimulus/car"]
  scrambled: ["stimulus/scrambled_face", "stimulus/scrambled_car"]
contrasts:                              # difference waves
  face_minus_car: {plus: face, minus: car}
components:
  preset: erpcore                       # library preset family
  use: [n170, p1]                       # components to measure; ids from the library
  overrides:                            # optional per-dataset tweaks, merged over the preset
    n170: {rois: {primary: [PO8]}}
group:
  tests: [paired_t, wilcoxon]
  cluster: {enabled: true, contrast: face_minus_car, tmin_ms: 0, tmax_ms: 400, n_permutations: 5000, threshold: null, tail: -1, random_state: 97}
  reference_values: erpcore             # erpcore | none : compares with published values (section 8)
```

### 5.2 Component library (data, not code)

YAML files in `src/eegunify/erp/components/`, loaded and validated by a pydantic model
`ComponentDef` (`extra="forbid"`, attribute docstrings, appears in `config_reference.md`). Each
entry is keyed `<id>@<preset>`:

```yaml
n170@erpcore:
  name: N170
  citation: Kappenman et al. 2021, NeuroImage 225:117465, Table 1
  epoch_set: stim
  contrast: face_minus_car          # measured on this wave; conditions measured too
  window_ms: [110, 150]
  rois: {primary: [PO8], secondary: [P7, PO7, P8]}
  measures: [mean_amplitude, peak_latency, fractional_area_latency]
  polarity: negative
  fractional_area: 0.5
  reference: average                # the reference scheme the window was defined for
  notes: Stimulus onsets include the 26 ms monitor delay.
n170@classic:
  window_ms: [130, 200]
  rois: {primary: [P7, P8, PO7, PO8]}
  ...
```

Presets to ship (window and electrode values for `@erpcore` are from Kappenman et al. 2021,
Table 1; **the builder must copy them from the paper/OSF readme and cite the table, not from
memory**; the values below are the expected ones to check against):

| id | erpcore preset | classic/other preset | lock | notes |
|---|---|---|---|---|
| `n170` | 110–150 ms, PO8, face−car | `classic` 130–200, P7/P8/PO7/PO8 | stim | average reference |
| `p3` | 300–600 ms, Pz, rare−frequent | `classic` 250–500, Pz/CPz | stim | P9/P10 reference |
| `n2pc` | 200–275 ms, PO7/PO8 contra−ipsi | `classic` 180–300 | stim | needs lateralised contrast (5.3) |
| `mmn` | 125–225 ms, FCz, deviant−standard | | stim | P9/P10 ref |
| `n400` | 300–500 ms, CPz, unrelated−related | | stim | |
| `ern` | 0–100 ms, FCz, incorrect−correct, response-locked | | resp | baseline −400..−200 |
| `lrp` | −100–0 ms, C3/C4 contra−ipsi, response-locked | | resp | lateralised |
| `p1` | `classic` 80–130 ms, O1/O2/Oz/PO7/PO8 | | stim | no ERP CORE value |
| `n1` | `classic` 150–200 ms, PO7/PO8 | | stim | |
| `vep_p100` | `iscev` P100 peak 90–130 ms at Oz (ref Fz), pattern reversal | | stim | ISCEV 2016 standard; latency norm ~100 ms, flag > 115 ms as `VEP_LATENCY_ATYPICAL` (info) |
| `vep_n75`, `vep_n135` | `iscev` 60–90, 115–160 ms at Oz | | stim | |

Selection rules: `components.preset` picks the family; `components.use: [n170]` resolves to
`n170@erpcore` if it exists, else the first preset that defines it with a warning listing which.
`n170@classic` in `use` picks an explicit preset. `components.overrides` is a deep merge of a
partial `ComponentDef`; the resolved definitions are written into provenance and into the stage
hash. A user can also add `code/configs/components/*.yaml` (`components.library_paths`). Unknown
channels in a ROI: refuse with the list of available channels and a suggested nearest
(10-05 neighbour) channel.

### 5.3 Lateralised components

`contrast` may be `{type: lateralized, left_trials: ..., right_trials: ..., pairs: [[PO7, PO8]]}`.
`measure.contra_ipsi()` builds contra−ipsi waveforms over channel pairs. Covers N2pc and LRP.

### 5.4 ROIs

`rois.py` keeps `VISUAL_ROIS` (Phase 1 QA) and gains `ERP_ROIS` read from
`src/eegunify/erp/components/rois.yaml`. Add `occipital_midline: [Oz, POz]` and `vep: [Oz]`.
Pooling = mean of channel waveforms *before* measurement (ERPLAB-like); documented.

---

## 6. Stage specifications

### 6.1 preproc (Phase 2)
Order fixed and recorded: line noise → (resample) → filter → bad-channel detection → interpolation →
re-reference. Bad detection runs on a 1 Hz high-passed copy. `pyprep` RANSAC needs positions: if
missing, refuse with "set montage or use bad_channels.method: config_only". Interpolation via
`raw.interpolate_bads(reset_bads=True)`; channels.tsv records each bad and why. Re-reference after
interpolation. EOG/EXG channels are never in the average reference. `reference.scheme: channels`
with BioSemi EXG mastoids is supported (types already `misc`/`eog` from Phase 1).

### 6.2 ica (Phase 3)
Fit on a 1 Hz high-pass copy restricted to good EEG, `reject_by_annotation=True`; apply to the
0.1 Hz data. ICLabel (`mne_icalabel.label_components(method="iclabel")`) requires average reference
and 1–100 Hz: do the classification on the fit copy with average reference set, record that in
provenance. Exclusion = (ICLabel class in `exclude_classes` with prob ≥ threshold) OR (EOG z ≥
threshold AND ICLabel class not `brain`) ∪ `exclude` − `include`. Every decision row in
`_components.tsv` with a `reason`.

### 6.3 epochs (Phase 4)
Event selection only via `erp.events.select` (F1). Response-locked sets use `lock: "response/*"`.
Rejection log per epoch. Thresholds implemented in `erp/epochs.py` as numpy (ERPLAB moving-window
peak-to-peak and step-like, both documented with ERPLAB function names) so the rule is identical to
what MATLAB ERP-lite runs. autoreject optional (`autoreject.AutoReject`, `random_state`).

### 6.4 erp (Phase 5)
`Epochs[cond].average()` per condition (by trial-type prefix lists), `mne.combine_evoked([a,b],
[1,-1])` for contrasts, lateralised waves via 5.3. Store all in one `_ave.fif` (comments =
names). Also write the trial count per condition. Plotting lowpass (`plot_lowpass_hz`, default 20)
applies to figures only, never measurement.

### 6.5 measure (Phase 5)
`measure.py`, functions taking `(times, data_uv, window)`:
- `mean_amplitude`
- `peak_amplitude` / `peak_latency` (local peak, ERPLAB "neighborhood" ±n samples, polarity-aware;
  if no local peak, fall back to absolute and flag `MEASURE_NO_LOCAL_PEAK`)
- `fractional_area_latency` (50 % of signed area in polarity direction)
- `jackknife_latency` (group stage, Miller/Ulrich correction) for latency contrasts
Units in the TSV are µV and ms.

### 6.6 measures.tsv (long format, one row per measurement)
`subject, session, task, epoch_set, component, preset, wave (condition or contrast), roi, channels,
measure, window_start_ms, window_end_ms, value, unit, n_trials, stage_key`.

### 6.7 group (Phase 6)
Implemented (2026-11-01) with two refinements: tests run on contrast **mean amplitudes** only
(linear, so a one-sample test on the contrast is exactly the paired test; peak and latency rows
are descriptive, latency tests would need jackknifing), and no d_z bootstrap CI below 8 subjects
(degenerate with fewer). Grand averages are `task-<task>_desc-<set>_ave.fif` (MNE requires
`*_ave.fif`). Original plan: Grand averages (`mne.grand_average`, plus per-subject N), tests per component/measure: paired t
(`scipy.stats.ttest_rel`) / one-sample on the contrast, Wilcoxon, Cohen's d_z with bootstrap 95 % CI
(`scipy.stats.bootstrap`, seed), mean ± SEM, ERP CORE-style **SME** (standardized measurement error,
Luck et al. 2021, bootstrapped per subject from epochs) as a data-quality number. Cluster-based
permutation over time × channels: `mne.stats.spatio_temporal_cluster_1samp_test` with channel
adjacency from `mne.channels.find_ch_adjacency`; with N=5 report it but label it as underpowered
(with 5 subjects there are 2^5 = 32 sign flips, so the smallest possible p is 1/32 = 0.031
one-tailed and 2/32 = 0.0625 two-tailed; a
two-tailed cluster test **cannot** reach p<.05: write that in the report automatically when
`2/2**N > alpha`).

---

## 7. Per-stage QA (stable IDs, never renamed)

Same `QACheck` dataclass as `qa/checks.py`; each stage writes `_qa.json` with the same schema
(BLUEPRINT.md §9) and the `ID` table lives in `erp/qa.py`'s docstring.

| ID | Stage | Fail / warn rule |
|---|---|---|
| `PREPROC_LINE_NOISE_RESIDUAL` | preproc | warn if line peak > 10 dB above neighbours after cleaning |
| `PREPROC_BADS_FRACTION` | preproc | warn > 10 %, fail > 25 % of EEG channels bad |
| `PREPROC_ROI_INTERPOLATED` | preproc | warn if a primary ROI channel of a used component was interpolated |
| `REREF_REQUIRED` | preproc | fail: reference-free data with `scheme: none` |
| `REREF_APPLIED` | preproc | info: scheme and channels |
| `ICA_EXCLUDED_COUNT` | ica | warn if 0 excluded while EOG present, or > 25 % of components excluded |
| `ICA_BLINK_RESIDUAL` | ica | warn if VEOG–Fp1 correlation after cleaning > 0.3 |
| `ICA_BRAIN_REMOVED` | ica | warn if an excluded component has ICLabel brain prob > 0.5 |
| `EPOCHS_REJECTED_FRACTION` | epochs | warn > 25 %, fail > 50 % overall |
| `EPOCHS_MIN_TRIALS` | epochs | fail if a condition used by a component has < `min_trials_per_condition` |
| `EPOCHS_BASELINE_DRIFT` | epochs | warn if mean |baseline| of ERP > 2 µV after baseline (should be 0: catches baseline misuse in difference waves) |
| `ERP_NOISE_SME` | erp | warn if SME of primary measure > 2 µV |
| `ERP_COMPONENT_POLARITY` | erp | info/warn if measured polarity opposite to `polarity` |
| `MEASURE_NO_LOCAL_PEAK` | erp | warn |
| `VEP_LATENCY_ATYPICAL` | erp | info if P100 > 115 ms |
| `GROUP_SUBJECTS_EXCLUDED` | group | warn, list |
| `GROUP_UNDERPOWERED` | group | info when the permutation minimum p > alpha |
| `REFERENCE_VALUES_MISMATCH` | group | warn if any compared value is outside tolerance (section 8) |

---

## 8. Validation

### 8.1 Synthetic ground truth (every stage, CI)
Extend `tests/synthetic/generate.py` with `make_erp_recording(...)` returning `ErpTruth`: 32–64
channel standard_1020 montage, 500 Hz, pink noise + alpha, injected ERPs as known Gaussian/gamma
waveforms projected through a fixed dipole-free spatial pattern (topography = Gaussian over PO8
etc.), condition-specific amplitudes (e.g. face N170 −4 µV, car −1 µV at 130 ms), blink
artifacts from a known frontal topography, line noise at 50 Hz, one dead and one noisy channel,
a few high-amplitude epochs. Truth stores everything. Tests:

| Stage | Assertion |
|---|---|
| preproc | dead/noisy channel detected (pyprep path, `slow` marker); line peak reduced ≥ 20 dB; average reference sums to ~0 |
| ica | blink component found; blink-topography variance reduced ≥ 90 %; ERP at PO8 changed < 0.2 µV |
| epochs | the injected bad epochs rejected exactly (thresholds); counts per condition exact |
| erp | difference wave equals truth within 0.3 µV RMS (noise-limited, n trials known) |
| measure | analytic cases (no noise): mean amplitude exact to 1e-9, peak latency to 1 sample, 50 % area latency exact on a triangle |
| group | 10 synthetic subjects with known effect: t and d_z match scipy on the same numbers; cluster found in the injected window |
| runner | changing `epochs.reject.ptp_uv` reruns only epochs/erp/group (assert via mtimes + `status`) |

Hypothesis property tests for `measure.py` (window inside epoch, sign symmetry, shift invariance).

### 8.2 ERP CORE reproduction (realdata)

**Recipe = ERP CORE's own pipeline translated** (`code/configs/erpcore_n170.yaml` gets
`components.preset: erpcore` and an ERP CORE preproc block; the builder must take filter,
reference, ICA and rejection parameters from the ERP CORE paper and the per-component OSF readme
/ MATLAB scripts and cite them in YAML comments). Add `erpcore_p3.yaml` and `erpcore_n2pc.yaml`
(fetch with existing `eegunify fetch erpcore --component P3`).

What we compare (in `derivatives/eegunify-group/task-N170_erpcore-comparison.tsv`):
1. **Subject level:** our N170 face−car mean amplitude at PO8 (110–150 ms) vs ERP CORE's
   published per-subject value for each of 015..019 that ERP CORE included; same for P3 (rare−frequent, Pz, 300–600) and N2pc
   (contra−ipsi PO7/PO8, 200–275).
2. **Peak latency** (secondary): within ±8 ms per subject.
3. **Subject mean** of our values vs the mean of ERP CORE's per-subject values over the same
   compared subjects (apples to apples), not vs the 40-subject grand mean.
4. **Sign**: per compared subject whose published value is larger than the tolerance in
   absolute value, our sign equals ERP CORE's published sign (near zero either sign is within
   the tolerance; refined 2026-11-01). Not "all N170
   differences negative": ERP CORE's own sub-019 N170 face − car is positive (+2.041 µV).
5. **Subjects ERP CORE excluded** (`src/eegunify/erp/reference/<component>_subjects.tsv`,
   `included = False`; N170: sub-016, TC = fewer than 50 % of face trials left after artifact
   rejection) are still run and listed in the comparison TSV with `status = excluded_by_erpcore`
   and their reason, but they are not part of checks 1-4 or of the subject mean. If our own
   pipeline excludes a subject ERP CORE kept, that is a documented deviation, not a silent skip.

Tolerances: ±0.5 µV mean amplitude per compared subject, ±0.3 µV for the subject mean over the
compared subjects, ±8 ms latency. A missing published value (NaN, e.g. N170 sub-019's 50 % area
latency, which has no negative area) is reported as "not available" for that measure.
Why not tighter: ICA decompositions differ between EEGLAB runica and picard; their rejection used
their artifact flags. The **deterministic comparison** is 8.4.

Where published values come from (located 2026-11-01): each component's OSF project (N170
`pfde9`, P3 `etdkz`, N2pc `yefrq`, children of ERP CORE `thsqg`) holds, under
`<component> All Data and Scripts/EEG_ERP_Processing/ERP_Measurements/`, ERPLAB text files with
one row per included subject (`Mean_Amplitude_Diff_Waves_*`, `Mean_Amplitude_Parent_Waves_*`,
`50%_Area_Latency_Diff_Waves_*`, `Peak_Latency_Diff_Waves_*` on the 20 Hz low-passed ERPs), and
`<component>_Subject_Summary.xlsx` with the excluded subjects and reasons.
`eegunify.reference_values` + `eegunify fetch erpcore-values [-c N170]` downloads them (sha256
in the manifest), checks that the measured subjects are exactly the included ones, and writes
`src/eegunify/erp/reference/<component>.tsv` and `<component>_subjects.tsv` (small,
committed; CC-BY-4.0 attribution in the file header). If the files for a component can't be located, the builder writes the OSF URLs it
searched into `docs/validation_erp.md` and marks the comparison as "not available" instead of
inventing numbers. **Never type reference numbers from memory.**

What 5 subjects can and cannot show:
- **Can:** per-subject agreement with published values (strongest test, independent of N);
  waveform shape; that the pipeline is wired correctly; sign consistency.
- **Cannot:** reproduce the published grand mean, effect size, or significance (40 subjects);
  cluster permutation significance at α=.05 two-tailed (impossible with N=5, section 6.7);
  SME distributions. Optional M-E6b fetches the full 40-subject N170 EEG (OQ1) to reproduce the
  grand-average and d_z; marked `realdata_full` and run manually.

### 8.2a `erpcore-exact` recipe (decided 2026-11-01)

The default ERP CORE recipe (`erpcore_n170.yaml`) is automatic: picard + ICLabel, one rejection
rule set for everyone. `erpcore-exact` instead reproduces ERP CORE's **own per-subject choices**,
all taken from their files on OSF (never typed in), so a remaining difference is down to the
implementation, not to different decisions. It is a recipe (`eegunify.erp.recipes`), applied
over the dataset config with per-subject `overrides`; the default recipe stays the default.

| ERP CORE step (N170 scripts 1-7) | Their file | eegunify feature |
|---|---|---|
| average reference over all 33 sites, EOG electrodes and P9/P10 included; EOG electrodes re-referenced too | `Rereference_Add_Uncorrected_Bipolars_N170.txt` | `reference.scheme: channels` with EOG channels allowed in `channels`, `reference.apply_to_eog: true` |
| uncorrected bipolar HEOG/VEOG (before ICA) | same file | `artifacts.eog_bipolar` (exists; derived in preproc) |
| their ICA decomposition (runica on 31 channels) and their removed components | `<sub>/<sub>_N170_..._ica_weighted.set` (header only), `ICA_Components_N170.xlsx` | `artifacts.method: precomputed`, `artifacts.precomputed: {weights, exclude}` (EEGLAB 1-based component numbers) |
| corrected bipolars after ICA | `Add_Corrected_Bipolars_N170.txt` | `artifacts.corrected_bipolar` (derived by the ica stage after cleaning) |
| interpolation after ICA (spherical, EOG ignored) | `Interpolate_Channels_N170.xlsx` | `preproc.interpolate_after_ica: true`, bads per subject |
| rejection: `pop_artextval` ±200 µV, `pop_artmwppth` on EEG (per-subject threshold/window), `pop_artmwppth` blinks on uncorrected VEOG, `pop_artstep` on uncorrected HEOG (stimulus period) and on corrected HEOG | `AR_Parameters_for_{SVT_CRAP,MW_CRAP,MW_Blinks,SL_HEOG_Stim_Pres,SL_HEOG}_N170.xlsx` | `epochs.reject.method: rules` with a list of `{test: extval|ptp|step, channels, window_ms, ...}`; ERPLAB semantics incl. `window2sample` (`round(t*fs) + round(|xmin|*fs)`, clipped, inclusive) and `>=`/`<=` for extval |
| 20 Hz low-pass, 48 dB/oct, before peak measures only | README_N170, Script #7 | `erp.peak_lowpass_hz: 20`, `erp.peak_lowpass_order: 8` |

`eegunify fetch erpcore-values` also writes `src/eegunify/erp/reference/<component>_exact.json`
(per-subject parameters from the files above) and fetches the ICA weight headers (~1.5 MB each,
cache only, sha256 in the manifest). Acceptance: the M-E6a comparison run with `erpcore-exact`,
reported next to the default recipe in `docs/validation_erp.md`; trial counts per bin are also
compared with ERP CORE's `<sub>_AR_Percentages_N170.csv`.

### 8.3 Golden files
`tests/golden/erpcore_n170_sub-015/erp/`: `measures.tsv` (our values, recipe-labelled),
`epochs_log.tsv` counts per condition, `ica_components.tsv` excluded indices (picard with fixed
seed is deterministic on one platform; compare *count and class*, not indices, across platforms),
`preview_recipe.json` with −2.24 µV ±0.05 (F6). Updated with `--update-golden` after reviewing the
diff, as in Phase 1.

### 8.4 ERPLAB cross-check (deterministic, realdata + MATLAB)
Without ICA: same filtered/re-referenced data → ERPLAB `pop_epochbin`, `pop_artmwppth`,
`pop_averager`, `pop_geterpvalues` (mean amplitude 110–150 at PO8) vs eegunify. Expected agreement
**< 0.05 µV** and identical rejected-epoch sets (thresholds are implemented to ERPLAB semantics).
This is the test that proves the measurement code, and it doubles as Phase M ERP-lite parity
(section 11). Filter difference (ERPLAB IIR Butterworth vs ours) is the main risk: use
`filter.method: iir` with matched order; compare filtered continuous data first (< 0.1 µV).

---

## 9. CLI and Python API

CLI (Typer subcommand group `eegunify erp`, lazy imports, same exit codes 0/1/2/3):

```
eegunify erp run <config> [-s 015 ...] [--from preproc] [--to erp] [--force ica] [--jobs 4] [--root .]
eegunify erp status <config>                 # subject × stage table: ok / stale / missing / failed
eegunify erp group <config>                  # = run --from group
eegunify erp components [--preset erpcore] [--show n170]   # print the library
eegunify erp compare <config> --reference erpcore          # writes the comparison TSV, exits 1 on mismatch
eegunify fetch erpcore-values --component N170
```

Rerun one stage after changing a parameter: edit YAML → `eegunify erp run cfg.yaml` (cache does
the rest) → or `--force epochs` to rerun regardless. `status` shows what will rerun before it does.

Python API (`eegunify.erp`): `run(config, subjects=None, from_=None, to=None, force=())`,
`status(config)`, and per-stage pure helpers (`preprocess(raw, cfg) -> raw`, `fit_ica(...)`,
`make_epochs(...)`, `average(...)`, `measure(evoked, component) -> DataFrame`) used by the app.

---

## 10. Phase A2 (app)

Status 2026-11-01: the five pages are implemented (`src/eegunify/app/erp_pages.py`) with the
acceptance test (`tests/unit/erp/test_app_erp.py`: a config built through the pages equals the
hand-written one; `erp run` on it gives identical `measures.tsv`). The README GIF of the
Components page (section 15) is `docs/img/app_components.gif` (recorded from the running app
with headless Chrome). Simplifications: rejection `rules` and lateralized
contrasts are edited in the YAML (kept untouched by the pages); the Preprocess page lists the
detected bad channels instead of drawing them on a topomap.

Extend `app/main.py` with pages in the same sidebar radio (no `st.navigation`, CLAUDE.md):
**Preprocess**, **ICA review**, **Epochs**, **Components**, **Group**. Each page edits a config
section in `session_state["config"]` (a dict validated by `parse_config` on every change, errors
next to the widget) and calls one library function. Download/save YAML as in A1.

| Page | Shows | Config keys written |
|---|---|---|
| Preprocess | PSD before/after (from `erp.preprocess` on a 60 s crop), detected bads on a topomap, reference choice | `preproc.*`, `reference.scheme/channels`, `channels.bads` |
| ICA review | component topomaps grid, ICLabel probabilities table (`_components.tsv`), EOG r, overlay of excluded/kept on Fp1/VEOG; checkboxes | `artifacts.ica.exclude/include` written into `overrides: {sub-XX: ...}` (manual choices are per subject) |
| Epochs | rejection summary per condition, ptp histogram with threshold line, rejected-epoch list | `epochs.reject.*`, `epochs.sets.*` |
| Components | component multiselect with preset selector; window/ROI editable (shown as override diff vs preset); condition ERPs + difference waves with window shaded; measurements table | `components.preset/use/overrides`, `conditions`, `contrasts` |
| Group | grand averages, stats table, ERP CORE comparison table with pass/fail colours | `group.*` |

Rules: pages read stage outputs from derivatives (via `erp/paths.py`) and only run computations
through library functions (`eegunify.erp.*`); heavy runs use `st.cache_data` keyed by `stage_key`.
No plotting code in pages beyond calling `erp/plots.py` functions that return figures.
Tests: `AppTest` per page on a synthetic project (`make_erp_project` fixture with precomputed
derivatives); acceptance: config produced through the pages equals a hand-written one and
`eegunify erp run` on it gives identical `measures.tsv`.

---

## 11. Phase M (MATLAB parity), expands BLUEPRINT.md §22

**Recommended scope (OQ4): M1 ingestion parity for 3 formats + M2 ERP-lite parity for N170.**
Full ingestion parity for 11 formats and MATLAB ICA are out of scope: high cost, and the
portfolio message ("I can do both ecosystems and prove they agree") is fully made by M1+M2.

- **Layout:** `matlab/+eegunify/` package: `config_load.m` (reads JSON), `read_any.m`
  (BrainVision via FieldTrip `ft_read_*` because bva-io is absent; BDF via `pop_biosig`; EEGLAB
  `.set` via `pop_loadset`), `canonical_validate.m`, `write_bids.m` (bids-matlab-tools if
  installed, else a minimal BrainVision writer using `pop_writebva` or a hand-written writer +
  TSV), `erp_lite.m` (filter with ERPLAB `pop_basicfilter`, re-reference, `pop_binlister` from the
  config's conditions, `pop_epochbin`, `pop_artmwppth`, `pop_averager`, `pop_binoperator`,
  `pop_geterpvalues`), `setup.m` (calls `Practice/setup_eeg_toolboxes.m`, no `genpath`). Keep
  `matlab/load_canonical.m` as is.
- **Shared config:** **JSON dump from Python** (`eegunify config dump <yaml> --json --resolved
  -s 015`), read with `jsondecode`. Not a MATLAB YAML parser: the Python side resolves
  overrides, presets and component definitions so MATLAB never reimplements merge logic. The JSON
  includes the config hash; MATLAB writes it back into its provenance so parity tests know they
  used the same config.
- **Tests:** `matlab/tests/` (`matlab.unittest`), driven from `tests/integration/test_matlab_parity.py`
  (`realdata` marker, skipped without `matlab` on PATH or `EEGUNIFY_MATLAB_SETUP`; subprocess
  with `stdin=DEVNULL`). Tolerances: events identical (onset ±0 samples); channel names/types
  identical; continuous data |Δ| < 1e-3 µV (float32); after filtering |Δ| < 0.1 µV; rejected
  epoch sets identical; condition ERP RMS Δ < 0.05 µV; mean amplitude |Δ| < 0.05 µV.
- **Subject:** sub-015 (not sub-001: only 015..019 are in the archive).
- **M1 as built (2026-11-02):** BrainVision and BDF are read with FieldTrip (BDF Status decoded
  in `extract_events.m` with MNE `find_events` semantics instead of `pop_biosig`, so masking
  and onsets match exactly); `.set` with `pop_loadset`. The writer is hand-written (BrainVision
  float32, resolution 1 µV; onsets written as sample/sfreq like MNE-BIDS). No electrodes.tsv
  and no canonical rule 6 in MATLAB. The dump's `matlab` block carries the naming tables.
- **M2 as built (2026-11-02):** recipe `erp_lite` = `erplab` without resampling and
  interpolation (MNE FFT vs EEGLAB polyphase resampling and the two spherical-spline
  implementations differ by design); `erp_lite.m` refuses those steps instead of
  approximating them. The ERPLAB core is shared with M-E5 (`+eegunify/erplab_erp.m`).
  Results in docs/validation_erp.md.
- **bva-io absence:** default to FieldTrip reading everywhere in M; test both paths if bva-io is
  later installed (parametrised MATLAB test, skipped when the plugin is missing).

---

## 12. Visual focus specifics

- **VEP:** pattern-reversal checkerboard (ISCEV 2016 standard: Oz active, Fz reference, P100 ~100 ms,
  N75, N135, 1° and 0.25° checks). Preset `vep@iscev` uses `reference.scheme: channels, channels:
  [Fz]` as a component-level `reference` hint: when a component's reference differs from the
  dataset's, the erp stage re-references a *copy* of the evoked (cheap, linear) and records it.
  Synthetic VEP generator for tests; a public pattern-reversal dataset is to be picked from
  `docs/validation_datasets.md` if one is present (otherwise synthetic only, stated in README).
- Occipital ROIs in 5.4; QA `ROI_MISSING` already guards channel availability.
- N2pc requires lateralised contrasts (5.3) and HEOG rejection (step function) because eye
  movements toward the target fake an N2pc; `EPOCHS_REJECTED_FRACTION` report splits by HEOG.

---

## 13. Later: BCI / real-time (short and honest)

Offline ERPs and closed-loop motor decoding are different problems (latency, single-trial
decoding, adaptive models). What carries over:
- **Readers/naming/contract:** the canonical channel naming and units, `xdf.py` reader (LSL
  recordings are XDF), event `trial_type` hierarchy for labels.
- **Real-time ingestion:** `mne-lsl` (1.14) `StreamLSL` returning chunks that pass the same
  `canonical.validate` rules on a ring buffer.
- **Decoding:** `mne.decoding` (CSP, `SlidingEstimator`) and MOABB (1.7) benchmarks on motor
  imagery datasets; ASR (already optional) becomes the online artifact path.

Seams to keep **now** (cheap): stage helpers take MNE objects, not paths (so a streaming caller can
reuse `preprocess` steps that are causal); `preproc.filter.phase` already a config key (`zero` vs
`minimum` causal); component/epoch selection independent of file layout. Do not add LSL, online
classes or decoding code until motor restoration work starts.

---

## 14. Milestones (recommended order) and acceptance criteria

| # | Milestone | Depends | Acceptance |
|---|---|---|---|
| M-E0 | Foundations: `erp/` skeleton, stage protocol, runner, hashing, paths, config sections (all optional), component library loader + presets, `erp components` CLI, docs regen | v0.1.0 | Phase 1 configs still validate; `config_reference.md` regenerated; runner unit tests with dummy stages (skip/run/force/stale); presets validate |
| M-E1 | preproc + reref (Phase 2) | M-E0 | synthetic tests 8.1; `REREF_REQUIRED` on BioSemi; ERP CORE sub-015 preproc runs |
| M-E2 | ICA + ICLabel + EOG (Phase 3) | M-E1 | synthetic blink removal; sub-015 excludes blink component(s); `_components.tsv` |
| M-E3 | epochs + rejection (Phase 4) | M-E1 (M-E2 optional) | exact synthetic rejections; per-epoch log; F1 test |
| M-E4 | erp + measure (Phase 5) → **v0.2.0** | M-E3 | analytic measure tests; preview recipe golden −2.24 ±0.05 µV; README updated with first `measures.tsv` |
| M-E5 | ERPLAB cross-check (8.4) | M-E4, ERPLAB installed | Δ mean amplitude < 0.05 µV on sub-015 |
| M-E6a | **done 2026-11-01 (v0.3.0)**: group + stats + ERP CORE comparison (Phase 6), N170/P3/N2pc on 015..019 → **v0.3.0, repo public** | M-E4 | comparison TSV within tolerances (8.2: sign equal to ERP CORE's, subjects ERP CORE excluded reported but not checked) or documented deviations; `docs/validation_erp.md` |
| M-E6b | optional 40-subject N170 | M-E6a | grand average & d_z vs published |
| A2 | **done 2026-11-01 (v0.4.0)**: app pages (section 10) → **v0.4.0** | M-E6a | AppTest green; UI config == hand config output |
| M1 | **done 2026-11-02**: MATLAB ingestion parity (BrainVision, BDF, SET), `matlab/+eegunify`, `tests/integration/test_matlab_parity.py` | v0.1.0 | §11 tolerances |
| M2 | **done 2026-11-02 (v0.5.0)**: MATLAB ERP-lite N170 parity (`erp_lite` recipe, `matlab/+eegunify/erp_lite.m`, `tests/integration/test_matlab_erp_lite.py`) → **v0.5.0** | M-E5, M1 | §11 tolerances |
| VEP | VEP presets + synthetic VEP + public dataset if found | M-E4 | P100 latency recovered ±2 ms on synthetic |

Build steps inside each milestone follow Phase 1 discipline: schema field + code + test + docs in
one commit; `ruff`, `mypy --strict`, `pytest -m "not realdata"` green before moving on; realdata
tests run locally and results recorded in `docs/validation_erp.md`.

---

## 15. README / portfolio evolution

- v0.2.0: replace "preview" figure with pipeline output figure (same style, `erp/plots.py`);
  add a "Pipeline" diagram: rawdata → preproc → ica → epochs → erp → group.
- v0.3.0 (public): headline table "ERP CORE reproduction: ours vs published, sub-015..019" for
  N170/P3/N2pc, with tolerances and an honest "what 5 subjects can't show" paragraph; grand-average
  figure; link to `docs/validation_erp.md`.
- v0.4.0: app GIF (Components page). v0.5.0: "Python ↔ MATLAB parity" badge-style table.
- CHANGELOG per release; CITATION.cff version bump; cite ERP CORE (CC-BY-4.0) prominently.

---

## 16. Changes needed to Phase 1

1. **BLUEPRINT.md §15** N170 "130–200 ms P7/P8/PO7/PO8" and "acceptance at P8 ~130–200 ms":
   superseded; add a pointer to this file and to the `n170@erpcore` preset (110–150, PO8).
2. **BLUEPRINT.md §22 and §23** say ERP CORE "sub-001"; the archive only has sub-015..019 →
   use sub-015 (also in section 0, Q3 "subject 1").
3. **BLUEPRINT.md §6** lists `readers/biosemi.py`; the code has `readers/bdf.py`. Update the doc.
4. **`config.resolve_for_subject`** must deep-merge the new nested sections (ICA exclude per
   subject); check it is not a shallow merge today, add tests.
5. **`ReferenceSection`** gains `scheme`/`channels` (offline reference); keep `online`/`ground`
   semantics; docstrings must make the online/offline distinction explicit.
6. **`rois.py`** gets ERP ROIs loaded from YAML; `VISUAL_ROIS` remains the QA source (no ID
   changes).
7. **Add `eegunify config dump --json --resolved`** (needed by Phase M).
8. **`pyproject` lower bounds** `mne>=1.10`, `mne-bids>=0.16` while blueprint says MNE 1.13 /
   MNE-BIDS 0.20; the `stimulus/face/12` labelling and `extras` columns depend on ≥0.20 → raise to
   `mne>=1.13`, `mne-bids>=0.20`.
9. **`code/scripts/erp_preview.py`** becomes a thin wrapper over the library ("preview recipe"
   config) once M-E4 lands, so the README figure is produced by the package.
10. **CLAUDE.md**: add `eegunify erp ...` commands, the new derivatives folders, and the "never
    shift twice" rule.
11. `derivatives/README.md` documents the new per-stage folders.

---

## 17. Dependencies (checked on PyPI 2026-10-30)

| Package | Version | Use | Where |
|---|---|---|---|
| `mne` | 1.13.2 | everything | core (raise floor) |
| `mne-bids` | 0.20.0 | paths | core (raise floor) |
| `mne-icalabel` | 0.10.0 | ICLabel | extra `erp` (needs `onnxruntime` or torch backend; use onnxruntime) |
| `python-picard` | 0.8.2 | ICA | extra `erp` |
| `pyprep` | 0.10.0 | RANSAC bads | extra `erp` |
| `autoreject` | 0.5.1 | optional rejection | extra `erp` |
| `meegkit` | 0.2.0 | zapline, ASR | extra `erp` (optional at runtime; refuse with install hint) |
| `scipy` | (present) | stats | core |
| `mne-lsl` 1.14.0, `moabb` 1.7.2 | | BCI later | not added now |

New extra: `eegunify[erp]`; `dev` includes it; `uv.lock` and `environment.yml` updated together;
CI matrix installs it on all three OSs (check `onnxruntime` wheels on macOS arm64/Windows).
Not adding `pingouin`/`statsmodels` (OQ6), `asrpy` (unmaintained, 0.0.8).

---

## 18. Risks

| Risk | Mitigation |
|---|---|
| Published ERP CORE per-subject values not machine-readable/locatable | Document, compare waveforms visually + ERPLAB cross-check (8.4) as the hard test |
| ICA nondeterminism across OS/BLAS | Fixed seed; goldens compare counts/classes, not indices; tolerances in 8.2 |
| Filter mismatch Python vs ERPLAB | IIR option matching ERPLAB; compare continuous data first |
| ICLabel prerequisites (avg ref, 1–100 Hz) silently violated | Enforced in code, recorded in provenance, unit test |
| Cache bugs serving stale outputs | Hash includes versions and component defs; `--force`; runner tests |
| N=5 over-interpreted | Automatic `GROUP_UNDERPOWERED` note; README paragraph |
| Scope creep into BCI | Section 13: seams only |
| CI time with ICA/pyprep | `slow` marker, small synthetic (32 ch, 60 s), xdist |

---

## 19. CLAUDE.md additions for the builder

Append to the existing `CLAUDE.md` (do not replace it):

```
## ERP phases (docs/BLUEPRINT_PHASES.md)
- `eegunify erp run <config> [-s ..] [--from/--to/--force <stage>]`; `eegunify erp status <config>`
- Stages: preproc -> ica -> epochs -> erp -> group, outputs in derivatives/eegunify-<stage>/, cached by stage_key
- Component windows/ROIs are data: src/eegunify/erp/components/*.yaml; never hard-code a window
- Never shift event onsets in ERP stages (Phase 1 already applied onset_shift_ms)
- Select events only through erp.events.select (MNE tag matching)
- Reference numbers (ERP CORE) only from fetched files, never typed from memory
- New QA IDs go in erp/qa.py docstring table; IDs are API
```

Skills for the builder: none required beyond the Phase 1 set (BLUEPRINT.md §21); use `dataviz`
for README figures.
