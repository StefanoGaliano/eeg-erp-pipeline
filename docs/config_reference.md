# Config reference

Generated from the pydantic schema by `eegunify config schema --markdown`; do not edit by hand
(a test fails when this file is out of date). Unknown keys are errors. Start from a draft:
`eegunify config init <recording>`, or see `code/configs/erpcore_n170.yaml`.

## `(top level)`

A complete, validated per-dataset configuration.

| Key | Type | Default | Description |
|---|---|---|---|
| `schema_version` | `1` | `1` | Config format version. |
| `dataset` | DatasetSection | **required** | Name, paths, BIDS task, authors, license. |
| `recordings` | RecordingsSection | **required** | Which files and subjects. |
| `format` | FormatSection | see section | Reader selection and format-specific hints. |
| `units` | UnitsSection | see section | Unit correction and the plausibility range. |
| `reference` | ReferenceSection | see section | Online reference and ground as recorded. |
| `channels` | ChannelsSection | see section | Renames, types, drops and bad channels. |
| `montage` | MontageSection | see section | Electrode positions: standard layout, position file, embedded, or none. |
| `events` | EventsSection | see section | Event extraction, mapping, expected counts, onset shifts. |
| `behavior` | BehaviorSection | see section | Behavioural log alignment. |
| `preproc` | PreprocSection | see section | ERP phases: continuous-data cleaning (unused by ``convert``). |
| `artifacts` | ArtifactsSection | see section | ERP phases: ICA / ASR artifact correction. |
| `epochs` | EpochsSection | see section | ERP phases: epoch sets and rejection. |
| `erp` | ErpSection | see section | ERP phases: averaging, measurement and figures. |
| `conditions` | map[str, list[string]] | `{}` | ERP phases: condition name -> trial-type prefixes pooled into it (``face: [stimulus/face]``). |
| `contrasts` | map[str, Contrast] | `{}` | ERP phases: difference waves by name (``face_minus_car: {plus: face, minus: car}``). |
| `components` | ComponentsSection | see section | ERP phases: components to measure, from the component library. |
| `group` | GroupSection | see section | ERP phases: group statistics and comparison with published values. |
| `overrides` | map[str, map] | `{}` | Per-subject patches: ``{"sub-01": {"channels": {...}}}``. |

## `AlignOn`

Which EEG events and log rows are aligned, and how.

| Key | Type | Default | Description |
|---|---|---|---|
| `eeg_trial_types` | list[string] | `["*"]` | Glob patterns of EEG trial types that correspond to log rows (e.g. ``stimulus/*``). |
| `beh_filter` | string \| null | `null` | pandas ``query`` selecting the log rows to align (e.g. ``"block != 'practice'"``). |
| `beh_time_column` | string \| null | `null` | Stimulus time in the log, in seconds (auto: PsychoPy ``*.started``, Presentation ``Time``). Without one, rows are paired in order and the counts must match. |

## `Anonymize`

Shift the measurement date back for anonymization (MNE-BIDS ``anonymize``).

| Key | Type | Default | Description |
|---|---|---|---|
| `daysback` | integer | **required** | Days to subtract from the recording date (same for every subject). |

## `ArtifactsSection`

Artifact correction (Phase 3).

| Key | Type | Default | Description |
|---|---|---|---|
| `method` | `ica` \| `none` | `"ica"` | ``ica`` runs the ICA stage; ``none`` epochs the preprocessed data directly. |
| `ica` | IcaSpec | see section | ICA settings. |
| `asr` | AsrSpec \| null | `null` | ASR settings; ``null`` (default) keeps ASR off. |
| `eog_bipolar` | map[str, list[…]] | `{}` | Bipolar EOG derivations, ``{VEOG: [FP1, VEOG_lower]}`` = first minus second. |

## `AsrSpec`

Artifact subspace reconstruction (meegkit), run before ICA when enabled.

| Key | Type | Default | Description |
|---|---|---|---|
| `cutoff` | number | `20.0` | Standard-deviation cutoff for burst rejection. |

## `BadChannelsSpec`

How bad channels are found before interpolation.

| Key | Type | Default | Description |
|---|---|---|---|
| `method` | `config_only` \| `pyprep_ransac` \| `pyprep_full` \| `lof` | `"config_only"` | ``config_only`` uses ``channels.bads`` alone; the others add automatic detection on a 1 Hz high-passed copy (pyprep needs electrode positions). |
| `random_state` | integer | `97` | Seed for pyprep RANSAC; part of the cache key. |

## `BehaviorSection`

Behavioural log merged into ``events.tsv``.

| Key | Type | Default | Description |
|---|---|---|---|
| `source` | `psychopy` \| `presentation` \| `eprime` \| `none` | `"none"` | Log format, or ``none``. |
| `file` | string \| null | `null` | Path relative to ``dataset.source_root``; may contain ``{subject}`` and glob ``*``. |
| `align_on` | AlignOn | see section | Alignment settings. |
| `columns` | list[string] | `[]` | Log columns appended to ``events.tsv`` (as ``beh_<column>``). |
| `max_drift_ms` | number | `20.0` | Largest EEG-vs-log timing residual accepted for a match. |

## `ChannelsSection`

Channel names, types and bad channels (applied after automatic normalization).

| Key | Type | Default | Description |
|---|---|---|---|
| `rename` | map[str, string] | `{}` | Explicit renames, keyed by original or normalized name. |
| `types` | map[str, `eeg` \| `eog` \| `emg` \| `ecg` \| `misc` \| `stim` \| `resp` \| `temperature`] | `{}` | Channel types that override the automatic inference (e.g. ``EXG3: eog``). |
| `drop` | list[string] | `[]` | Channels to leave out of the output. |
| `bads` | map[str, list[string]] \| list[string] | `{}` | Bad channels: a list for all subjects, or ``{"sub-01": [...]}`` per subject. |
| `aliases_extra` | map[str, string] | `{}` | Extra name aliases on top of the old 10-20 ones (``T3`` -> ``T7``, ...). |

## `ClusterSpec`

Cluster-based permutation test over time x channels.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Run the test. |
| `contrast` | string \| null | `null` | Contrast (difference wave) to test against zero. |
| `tmin_ms` | number | `0.0` | Start of the tested window. |
| `tmax_ms` | number | `400.0` | End of the tested window. |
| `n_permutations` | integer | `5000` | Number of sign-flip permutations. |
| `threshold` | number \| null | `null` | Cluster-forming threshold (t); ``null`` uses MNE's default. |
| `tail` | `-1` \| `0` \| `1` | `0` | -1 negative, 0 two-tailed, 1 positive. |
| `random_state` | integer | `97` | Seed; part of the cache key. |

## `ComponentsSection`

Which ERP components to measure, from the component library.

| Key | Type | Default | Description |
|---|---|---|---|
| `preset` | string | `"erpcore"` | Preset family tried first (``erpcore``, ``classic``, ``iscev``). |
| `use` | list[string] | `[]` | Component ids (``n170``) or explicit ``id@preset`` keys. |
| `overrides` | map[str, map] | `{}` | Partial component definitions deep-merged over the resolved preset, by component id. |
| `library_paths` | list[string] | `[]` | Extra component YAML files (relative to the project root), e.g. ``code/configs/components/mine.yaml``. |

## `Contrast`

A difference wave between two named conditions.

| Key | Type | Default | Description |
|---|---|---|---|
| `plus` | string | **required** | Condition with weight +1. |
| `minus` | string | **required** | Condition with weight -1. |

## `CsvHints`

How to read a delimited text recording (OpenBCI GUI exports, generic CSV). Everything is optional: OpenBCI files declare their sampling rate in a ``%Sample Rate`` header line and name channels ``EXG Channel N``; generic files are recognised by a time column (``time``/``timestamp``) and a marker column (``marker``/``trigger``/``event``).

| Key | Type | Default | Description |
|---|---|---|---|
| `sfreq` | number \| null | `null` | Sampling rate in Hz (default: from an OpenBCI header or the time column). |
| `channel_columns` | list[string] \| null | `null` | Columns holding EEG/EOG data (default: OpenBCI ``EXG Channel N`` columns, else every numeric column except time, marker and sample-index columns). |
| `channel_names` | list[string] \| null | `null` | Names for those columns, in order (e.g. the 10-20 position of each OpenBCI pin). |
| `unit` | `V` \| `mV` \| `uV` | `"uV"` | Unit of the values in the file. |
| `delimiter` | string \| null | `null` | Column separator (default: sniffed from the first lines). |
| `comment` | string | `"%"` | Lines starting with this are header comments. |
| `time_column` | string \| null | `null` | Column with time in seconds (default: ``time``/``timestamp`` if present). |
| `marker_column` | string \| null | `null` | Column with event codes; non-zero values become events (default: auto). |

## `DatasetSection`

What the dataset is and where it lives (paths relative to the project root).

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | **required** | Dataset name, written to ``dataset_description.json``. |
| `bids_root` | string | `"rawdata"` | Output folder for the canonical BIDS dataset. |
| `source_root` | string | **required** | Folder holding the original recordings (never modified). |
| `task` | string | **required** | BIDS task label (letters and digits only). |
| `authors` | list[string] | `[]` | Dataset authors for ``dataset_description.json``. |
| `license` | string \| null | `null` | Data license, e.g. ``CC0`` or ``CC-BY-4.0``. |
| `anonymize` | Anonymize \| null | `null` | Shift measurement dates back; ``null`` keeps them. |

## `EogCorrelationSpec`

Second vote: components correlated with EOG channels.

| Key | Type | Default | Description |
|---|---|---|---|
| `channels` | list[string] | `["VEOG", "HEOG"]` | EOG channels to correlate with (missing ones are skipped). |
| `threshold` | number | `3.0` | z-score above which a component counts as ocular. |

## `EpochSet`

One set of epochs, written as ``desc-<name>``.

| Key | Type | Default | Description |
|---|---|---|---|
| `lock` | string | **required** | Trial-type prefix selector the epochs are time-locked to (``stimulus/*``, ``response/*``); matched with MNE tags, never exact labels. |
| `tmin_ms` | number | `-200.0` | Epoch start relative to the event. |
| `tmax_ms` | number | `800.0` | Epoch end relative to the event. |
| `baseline_ms` | list[…] \| null | `[-200.0, 0.0]` | Baseline window; ``null`` for no baseline correction. |

## `EpochsSection`

Epoching and rejection (Phase 4).

| Key | Type | Default | Description |
|---|---|---|---|
| `sets` | map[str, EpochSet] | `{}` | Epoch sets by name (letters and digits; the name becomes the ``desc-`` entity). |
| `reject` | RejectSpec | see section | Rejection rule shared by all sets. |

## `ErpSection`

Averaging, measurement and figures (Phase 5).

| Key | Type | Default | Description |
|---|---|---|---|
| `peak_neighborhood` | integer | `3` | Samples on each side a local peak must exceed on average (ERPLAB "neighborhood"). |
| `plot_lowpass_hz` | number \| null | `20.0` | Low-pass applied to figures only, never to the measured waveforms; ``null`` plots the data as measured. |
| `figures` | boolean | `true` | Write one PNG per measured component. |

## `EventsSection`

Event extraction and code -> condition mapping.

| Key | Type | Default | Description |
|---|---|---|---|
| `source` | `auto` \| `stim_channel` \| `annotations` \| `bids_events` | `"auto"` | ``auto``: stim channel, else file markers, else the source's BIDS ``*_events.tsv``. ``bids_events`` forces the sidecar (for files without triggers). |
| `stim_channel` | string \| null | `null` | Trigger channel name (default: the first stim channel). |
| `mapping` | map[str, string] | `{}` | Code (``"5"``), inclusive range (``"1-40"``) or marker label -> trial type (``stimulus/face``). Ranges must not overlap. |
| `unmapped` | `keep_as_code` \| `drop` \| `error` | `"keep_as_code"` | What to do with codes missing from ``mapping``. |
| `ignore_codes` | list[integer] | `[0]` | Codes that are never events. |
| `min_duration_samples` | integer | `1` | Debounce for stim channels: shorter pulses are ignored. |
| `expected_counts` | map[str, integer] | `{}` | Trial type -> expected count per subject (QA ``EVENTS_EXPECTED_COUNT``). |
| `count_tolerance` | number | `0.05` | Allowed relative deviation from ``expected_counts``. |
| `onset_shift_ms` | map[str, number] | `{}` | Known trigger-to-stimulus delays, by trial-type glob (e.g. ``{"stimulus/*": 26}`` for an LCD monitor that shows the image 26 ms after the trigger). Responses are usually not shifted. Applied to onsets and samples and recorded in provenance. |

## `FilterSpec`

Filter design shared by the high-pass and low-pass steps.

| Key | Type | Default | Description |
|---|---|---|---|
| `method` | `iir` \| `fir` | `"iir"` | ``iir`` Butterworth (as ERPLAB ``pop_basicfilter``) or ``fir`` (MNE default design). |
| `iir_order` | integer | `2` | Butterworth order counted as ERPLAB does: the total order of the forward-backward filter, so 2 = 12 dB/oct with a half-amplitude (-6 dB) cutoff. Must be even for ``phase: zero`` (each pass is order/2). |
| `phase` | `zero` \| `minimum` | `"zero"` | ``zero`` (non-causal, offline ERPs) or ``minimum`` (causal; the seam for real-time use). |

## `FormatHints`

Format-specific settings; each applies to one reader only.

| Key | Type | Default | Description |
|---|---|---|---|
| `neuroscan_data_format` | `auto` \| `int16` \| `int32` | `"auto"` | Neuroscan ``.cnt`` sample width; ``auto`` keeps the width with plausible amplitudes. |
| `bdf_status_mask` | integer | `255` | Bits of the BioSemi Status channel (or ``STATUS/<n>`` markers) that carry triggers. |
| `xdf_eeg_stream` | map[str, string] | `{"type": "EEG"}` | Which XDF stream is the EEG, by ``type`` and/or ``name``. |
| `xdf_marker_stream` | map[str, string] | `{"type": "Markers"}` | Which XDF stream holds the markers (optional; missing means no events). |
| `csv` | CsvHints | see section | CSV / OpenBCI settings. |

## `FormatSection`

How the recordings are read.

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | `auto` \| `brainvision` \| `bdf` \| `edf` \| `eeglab` \| `xdf` \| `gdf` \| `ant` \| `neuroscan` \| `curry` \| `egi` \| `csv` | `"auto"` | Reader to use; ``auto`` detects it from the extension (and magic bytes for ``.cnt``). |
| `hints` | FormatHints | see section | Format-specific settings. |

## `GroupSection`

Group statistics (Phase 6).

| Key | Type | Default | Description |
|---|---|---|---|
| `tests` | list[`paired_t` \| `wilcoxon`] | `["paired_t", "wilcoxon"]` | Tests run per component and measure. |
| `cluster` | ClusterSpec | see section | Cluster permutation settings. |
| `reference_values` | `erpcore` \| `none` | `"none"` | Compare measures with published values (``erpcore``) or not. |

## `IcLabelSpec`

Automatic component exclusion by ICLabel class.

| Key | Type | Default | Description |
|---|---|---|---|
| `exclude_classes` | list[string] | `["eye blink"]` | ICLabel classes to remove, spelled as mne-icalabel spells them (``eye blink``, ...). |
| `threshold` | number | `0.8` | Minimum class probability for exclusion. |

## `IcaSpec`

ICA fit, classification and manual choices.

| Key | Type | Default | Description |
|---|---|---|---|
| `fit_highpass_hz` | number | `1.0` | High-pass of the copy ICA is fitted on (the solution is applied to the original). |
| `n_components` | number \| integer | `0.99` | Number of components, or the fraction of variance to keep when below 1. |
| `algorithm` | `picard` \| `infomax` \| `fastica` | `"picard"` | ICA algorithm. |
| `extended` | boolean | `true` | Extended (sub- and super-Gaussian) variant for picard/infomax. |
| `random_state` | integer | `97` | Seed; part of the cache key. |
| `iclabel` | IcLabelSpec | see section | ICLabel exclusion rule. |
| `eog_correlation` | EogCorrelationSpec | see section | EOG-correlation rule. |
| `exclude` | list[integer] | `[]` | Component indices to remove by hand (usually per subject, in ``overrides``). |
| `include` | list[integer] | `[]` | Component indices to keep whatever the automatic rules say. |

## `LineNoiseSpec`

Line-noise removal, the first preprocessing step.

| Key | Type | Default | Description |
|---|---|---|---|
| `method` | `none` \| `notch` \| `zapline` | `"none"` | ``notch`` (MNE FIR notch), ``zapline`` (meegkit, needs the ``erp`` extra) or ``none``. |
| `freqs` | list[number] \| `auto` | `"auto"` | Frequencies to remove; ``auto`` takes the line frequency and harmonics found by QA. |

## `MontageSection`

Electrode positions.

| Key | Type | Default | Description |
|---|---|---|---|
| `source` | `standard` \| `file` \| `embedded` \| `none` | `"standard"` | ``standard`` layout, a position ``file``, positions ``embedded`` in the recording, or ``none`` (also drops embedded positions). |
| `standard_name` | string | `"standard_1005"` | Any MNE built-in montage, e.g. ``biosemi64``, ``GSN-HydroCel-128``. |
| `file` | string \| null | `null` | Position file (``.elc``, ``.sfp``, ``.bvct``, ...) relative to the recording's folder; may contain ``{subject}``. |
| `on_missing` | `raise` \| `warn` \| `ignore` | `"warn"` | What to do with EEG channels that get no position. |

## `PreprocSection`

Continuous-data cleaning (Phase 2): line noise, filter, bad channels, interpolation.

| Key | Type | Default | Description |
|---|---|---|---|
| `line_noise` | LineNoiseSpec | see section | Line-noise removal. |
| `highpass_hz` | number \| null | `0.1` | High-pass cutoff in Hz (ERP CORE: 0.1 Hz); ``null`` skips it. |
| `lowpass_hz` | number \| null | `null` | Low-pass cutoff in Hz applied to the data that get measured; ``null`` skips it. Figures use their own plotting low-pass. |
| `filter` | FilterSpec | see section | Filter design. |
| `resample_hz` | number \| null | `null` | Resample to this rate before filtering; ``null`` keeps the recorded rate. |
| `bad_channels` | BadChannelsSpec | see section | Bad-channel detection. |
| `interpolate` | boolean | `true` | Spherical-spline interpolation of bad channels before re-referencing. |

## `RecordingsSection`

Which files make up the dataset.

| Key | Type | Default | Description |
|---|---|---|---|
| `pattern` | string | **required** | Path of one subject's recording relative to ``source_root``, with ``{subject}``. |
| `subjects` | list[string] | **required** | Subject labels (alphanumeric, without ``sub-``). |
| `session` | string \| null | `null` | BIDS session label for the output, if any. |
| `run` | string \| null | `null` | BIDS run label for the output, if any. |

## `ReferenceSection`

Reference: ``online``/``ground`` describe the recording (Phase 1 never re-references); ``scheme``/``channels`` are the offline reference the ERP preproc stage applies.

| Key | Type | Default | Description |
|---|---|---|---|
| `online` | string | `"n/a"` | Recorded (online) reference, e.g. ``Cz``, ``linked mastoids``; BioSemi defaults to ``CMS/DRL (reference-free)``. Written to ``EEGReference``; never changes the data. |
| `ground` | string \| null | `null` | Ground electrode, if known. |
| `scheme` | `average` \| `channels` \| `rest` \| `none` | `"average"` | Offline re-reference applied by the ERP preproc stage (not by ``convert``): ``average`` of good EEG channels, the mean of ``channels``, ``rest``, or ``none`` (refused for reference-free recordings such as BioSemi). |
| `channels` | list[string] | `[]` | Reference channels for ``scheme: channels``, e.g. ``[P9, P10]``. |

## `RejectSpec`

Epoch rejection.

| Key | Type | Default | Description |
|---|---|---|---|
| `method` | `thresholds` \| `autoreject` \| `none` | `"thresholds"` | ``thresholds`` (ERPLAB semantics), ``autoreject`` (local) or ``none``. |
| `ptp_uv` | number \| null | `100.0` | Moving-window peak-to-peak threshold on EEG (ERPLAB ``pop_artmwppth``). |
| `ptp_window_ms` | number | `200.0` | Moving-window width. |
| `ptp_step_ms` | number | `100.0` | Moving-window step. |
| `step_heog_uv` | number \| null | `32.0` | Step-function threshold on HEOG (ERPLAB ``pop_artstep``); only if HEOG exists. |
| `step_window_ms` | number | `200.0` | Step-function window: second-half mean minus first-half mean within it. |
| `step_step_ms` | number | `50.0` | Step-function window step. |
| `flat_uv` | number \| null | `null` | Reject epochs whose peak-to-peak on any EEG channel is below this. |
| `min_trials_per_condition` | integer | `30` | Fewer kept trials fails ``EPOCHS_MIN_TRIALS`` and excludes the subject from group. |
| `random_state` | integer | `97` | Seed for autoreject; part of the cache key. |

## `UnitsSection`

Amplitude units and the plausibility check.

| Key | Type | Default | Description |
|---|---|---|---|
| `declared` | `auto` \| `V` \| `mV` \| `uV` | `"auto"` | ``auto`` trusts the header. Otherwise the unit the values are really in when the header is wrong; data are rescaled and the factor logged. ``eegunify inspect`` suggests it. |
| `expected_eeg_range_uV` | list[…] | `[0.5, 500.0]` | Plausible median robust SD of EEG channels, in µV (unit check and QA). |

# Component library entries

Not config keys: the format of the component YAML files (`src/eegunify/erp/components/*.yaml`
and `components.library_paths`), one entry per `<id>@<preset>` key. `components.overrides`
takes a partial entry. List the library with `eegunify erp components`.

## `ComponentDef`

One ERP component under one preset (a library entry ``<id>@<preset>``).

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | **required** | Display name (``N170``). |
| `citation` | string | **required** | Where the window and sites come from (paper and table). |
| `epoch_set` | string | `"stim"` | Epoch set (``epochs.sets`` name) the component is measured on. |
| `contrast` | string \| null | `null` | Difference wave measured (``contrasts`` name); the conditions are measured too. |
| `window_ms` | list[…] | **required** | Measurement window, relative to the time-locking event. |
| `rois` | Rois | **required** | Measurement sites. |
| `lateralized_pairs` | list[list[…]] | `[]` | Left/right channel pairs for contralateral-minus-ipsilateral waves (N2pc, LRP). |
| `measures` | list[`mean_amplitude` \| `peak_amplitude` \| `peak_latency` \| `fractional_area_latency`] | `["mean_amplitude"]` | Measurements taken in the window. |
| `polarity` | `positive` \| `negative` | **required** | Expected sign of the component (peak search direction, polarity QA). |
| `fractional_area` | number | `0.5` | Fraction of the signed area for ``fractional_area_latency``. |
| `reference` | string \| null | `null` | Reference the window was defined for (``average``, ``P9/P10``, ``Fz``). |
| `baseline_ms` | list[…] \| null | `null` | Baseline the source used, when it differs from the epoch set's (ERN, LRP). |
| `notes` | string | `""` | Free text: caveats, onset delays, what the values were checked against. |

## `Rois`

Electrodes a component is measured at; each list is pooled (mean waveform) first.

| Key | Type | Default | Description |
|---|---|---|---|
| `primary` | list[string] | **required** | Main measurement site(s); these values are compared with published ones. |
| `secondary` | list[string] | `[]` | Extra sites reported alongside, each measured separately. |
