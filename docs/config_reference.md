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

## `MontageSection`

Electrode positions.

| Key | Type | Default | Description |
|---|---|---|---|
| `source` | `standard` \| `file` \| `embedded` \| `none` | `"standard"` | ``standard`` layout, a position ``file``, positions ``embedded`` in the recording, or ``none`` (also drops embedded positions). |
| `standard_name` | string | `"standard_1005"` | Any MNE built-in montage, e.g. ``biosemi64``, ``GSN-HydroCel-128``. |
| `file` | string \| null | `null` | Position file (``.elc``, ``.sfp``, ``.bvct``, ...) relative to the recording's folder; may contain ``{subject}``. |
| `on_missing` | `raise` \| `warn` \| `ignore` | `"warn"` | What to do with EEG channels that get no position. |

## `RecordingsSection`

Which files make up the dataset.

| Key | Type | Default | Description |
|---|---|---|---|
| `pattern` | string | **required** | Path of one subject's recording relative to ``source_root``, with ``{subject}``. |
| `subjects` | list[string] | **required** | Subject labels (alphanumeric, without ``sub-``). |
| `session` | string \| null | `null` | BIDS session label for the output, if any. |
| `run` | string \| null | `null` | BIDS run label for the output, if any. |

## `ReferenceSection`

Online reference and ground as recorded (Phase 1 never re-references).

| Key | Type | Default | Description |
|---|---|---|---|
| `online` | string | `"n/a"` | E.g. ``Cz``, ``linked mastoids``; BioSemi defaults to ``CMS/DRL (reference-free)``. |
| `ground` | string \| null | `null` | Ground electrode, if known. |

## `UnitsSection`

Amplitude units and the plausibility check.

| Key | Type | Default | Description |
|---|---|---|---|
| `declared` | `auto` \| `V` \| `mV` \| `uV` | `"auto"` | ``auto`` trusts the header. Otherwise the unit the values are really in when the header is wrong; data are rescaled and the factor logged. ``eegunify inspect`` suggests it. |
| `expected_eeg_range_uV` | list[…] | `[0.5, 500.0]` | Plausible median robust SD of EEG channels, in µV (unit check and QA). |
