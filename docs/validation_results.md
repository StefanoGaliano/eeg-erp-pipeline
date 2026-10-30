# Validation results: eegunify on real open datasets

Run of `eegunify validate-datasets` on the catalog in [validation_datasets.md](validation_datasets.md),
on 2026-10-30, with eegunify at commit after M3. One subject (first recording) per dataset,
with a config **drafted automatically** (no per-dataset tuning): this measures how eegunify does
on data it has never seen. Size cap: 250 MB per subject.

## Summary

| Outcome | Datasets |
|---|---|
| Converted, QA **PASS** | 2 |
| Converted, QA **WARN** | 17 |
| Refused, correctly (defect in the source data) | 2 |
| Not run: over the 250 MB cap | 11 |
| Not run: source not automated yet | 9 |

Every downloaded dataset either converted or was refused for a real defect in its files.

WARN means converted and valid, with something worth knowing. The warnings are expected for an
automatic config: `REFERENCE_UNKNOWN` (no reference given), `EVENTS_UNMAPPED` (codes kept as
numbers until a mapping is written), `ROI_MISSING` (caps without some visual channels), and
`UNITS_RANGE` (amplitude just above the plausible range).

## Converted

| Dataset | Format | QA | EEG ch | Hz | Events | Warnings |
|---|---|---|---|---|---|---|
| ds001810 | brainvision | **WARN** | 64 | 512 | 1471 | CHANNEL_FLAT, REFERENCE_UNKNOWN |
| ds002718 | eeglab | **WARN** | 74 | 250 | 1474 | CHANNEL_NO_POSITION, ROI_MISSING, REFERENCE_UNKNOWN |
| ds003190 | brainvision | **WARN** | 8 | 256 | 1219 | CHANNEL_CLIPPED, ROI_MISSING, REFERENCE_UNKNOWN |
| ds003645 | bdf | **WARN** | 74 | 1100 | 406 | CHANNEL_NO_POSITION, ROI_MISSING |
| ds004324 | edf | **WARN** | 28 | 500 | 911 | ROI_MISSING, REFERENCE_UNKNOWN |
| ds004362 | eeglab | **WARN** | 64 | 160 | 30 | REFERENCE_UNKNOWN |
| ds004745 | eeglab | **WARN** | 8 | 1000 | 54 | CHANNEL_NO_POSITION, ROI_MISSING, REFERENCE_UNKNOWN |
| ds005106 | eeglab | **WARN** | 32 | 500 | 2396 | CHANNEL_FLAT, CHANNEL_CLIPPED, ROI_MISSING, REFERENCE_UNKNOWN, EVENTS_UNMAPPED |
| ds005811 | eeglab | **WARN** | 62 | 500 | 1701 | REFERENCE_UNKNOWN |
| ds005841 | bdf | **PASS** | 64 | 512 | 129 | - |
| ds005863 | brainvision | **WARN** | 26 | 500 | 840 | ROI_MISSING, REFERENCE_UNKNOWN |
| ds006768 | edf | **WARN** | 64 | 1000 | 200 | REFERENCE_UNKNOWN |
| ds007012 | bdf | **PASS** | 64 | 512 | 3084 | - |
| ds007056 | eeglab | **WARN** | 29 | 500 | 433 | REFERENCE_UNKNOWN |
| ds007096 | eeglab | **WARN** | 29 | 500 | 678 | REFERENCE_UNKNOWN |
| ds007137 | eeglab | **WARN** | 29 | 500 | 678 | REFERENCE_UNKNOWN |
| ds007172 | brainvision | **WARN** | 32 | 500 | 324 | ROI_MISSING, REFERENCE_UNKNOWN |
| ds008092 | edf | **WARN** | 14 | 128 | 73 | ROI_MISSING, REFERENCE_UNKNOWN |
| erpcore | eeglab | **WARN** | 30 | 1024 | 640 | REFERENCE_UNKNOWN |

## Refused (source data defects, correctly detected)

| Dataset | Problem | What eegunify does |
|---|---|---|
| ds005028 | EDF scaled 10^6 too large (µV stored as V) | Refuses; suggests `units.declared: uV` |
| ds006979 | `sub-01_task-rest_eeg.set` is an 11-byte file (broken upload) | Refuses with the reader error |

## Bugs this run found in eegunify (all fixed, each with a regression test)

| Seen in | Problem | Fix |
|---|---|---|
| ds001810 | BrainVision file (converted from BioSemi) with both a scaled `Status` channel and `STATUS/61694` markers: 0 events | Auto mode falls back to markers when the stim channel is empty; `STATUS/n` markers masked to 8 bits |
| ds005028 | Line-noise detection crashed on coarse frequency resolution | Nearest-bin search; detection can never fail a conversion |
| ds005106, ds007096 | Infant / high-amplitude data 1.5-2x above the range were refused as unit errors | Refuse only when > 10x outside and a 10^3/10^6 factor explains it; otherwise warn |
| ds003645, ds006768 | No triggers in the file; events only in the BIDS `events.tsv` | New `events.source: bids_events`, used automatically when the file has no events |
| ds003645 | OpenNeuro lists optional top-level files with dead links (404) | Optional top-level files are skipped with a warning |
| 11 datasets | Over-cap datasets reported as errors | Reported as SKIP |

## Not run

- **Over the 250 MB cap:** ds002578 (728 MB), ds003505 (797 MB), ds003825 (773 MB), ds004018 (553 MB), ds004357 (1255 MB), ds004621 (731 MB), ds005586 (1290 MB), ds005642 (725 MB), ds005648 (839 MB), ds006171 (444 MB), ds006563 (526 MB). The weekly CI run uses a 150 MB cap; run locally with
  `--max-mb 2000` to include them.
- **Sources not automated yet:** bciciv-2a, consumer-eeg-eyetracking, mne-sample, mne-ssvep, mne-testing, physionet-eegmmidb, things-eeg2, tsinghua-beta, wang2021-ssvep. These are format-coverage entries (GDF, Neuroscan, XDF,
  FIF, MAT) planned for M4.
