# Validation datasets: does eegunify work on real-world data?

These 40 open datasets are the real-world test bed for `eegunify convert`. They were chosen to
cover the **file formats** eegunify claims to read, and are mostly **visual paradigms**
(the project focus). Each one also exposes **edge cases** that synthetic tests don't: odd channel
names, trigger encodings, mixed formats and compressed files.

**How it is used (Phase 1, after M1):** the harness downloads **one subject per dataset**
(OpenNeuro: `openneuro-py download --include sub-XX`), converts it with `eegunify convert`, and
records pass/warn/fail per QA check in `derivatives/validation/summary.tsv`. The machine-readable
list is `code/configs/validation_datasets.yaml`.

**Verification status**
- **Verified (OpenNeuro):** checked against the OpenNeuro GraphQL API on 2026-10-30. The
  snapshot tag, subject count, license and EEG file extensions were read from the actual files
  of the first subject.
- **Per source:** taken from the hosting page or papers. The builder must confirm the URL and
  format when pinning the hash.

Licences: everything verified on OpenNeuro is CC0 except where noted.

## A. Visual ERP benchmarks (headline)

| # | ID | Dataset | Paradigm | Format | Subj | Why it's here |
|---|---|---|---|---|---|---|
| 1 | ERP CORE (OSF) | ERP CORE N170 / P3 / N2pc | faces, visual oddball, visual search | EEGLAB `.set` (BioSemi origin) | 40 | **Headline demo**; published reference ERP values to compare against |
| 2 | ds007096 | PURSUE N170 Face Perception | faces vs cars | `.set` | 292 | Large ERP CORE-style replication; N170 at P7/P8/PO7/PO8 |
| 3 | ds007056 | PURSUE P300 Visual Oddball | visual oddball | `.set` | 286 | Visual P3 at Pz |
| 4 | ds007137 | PURSUE N2pc Visual Search | visual search | `.set` | 294 | Lateralized posterior ERP (PO7/PO8) |
| 5 | ds002718 | Face processing EEG for EEGLAB (Wakeman & Henson) | famous/unfamiliar/scrambled faces | `.set` | 18 | Classic EEGLAB tutorial dataset |
| 6 | ds003645 | Face processing MEEG with HED annotation | faces | `.bdf` | 19 | Same paradigm as #5 as BDF + HED event tags |
| 7 | ds003505 | VEPCON | faces + motion VEPs, 128 ch | `.bdf` | 20 | High-density BioSemi VEPs; large files (31 GB) |
| 8 | ds005863 | Cognitive Electrophysiology in Socioeconomic Context | active visual oddball + others | BrainVision | 127 | ERP battery; multiple tasks per subject |

## B. Object / face / scene recognition (RSVP, decoding)

| # | ID | Dataset | Format | Subj | Edge case |
|---|---|---|---|---|---|
| 9 | ds003825 | THINGS-EEG (22,248 images, RSVP) | BrainVision + `.csv` | 50 | Very many events; no EOG channels |
| 10 | ds004357 | Features-EEG (RSVP 6.67/20 Hz) | BrainVision | 16 | Fast stimulation; overlapping ERPs |
| 11 | ds004018 | 200 object images RSVP 5/20 Hz | BrainVision | 16 | Same lab as #9; consistency check |
| 12 | ds005642 | Illusory faces (pareidolia) | `.bdf` | 21 | BioSemi Status trigger masking |
| 13 | ds005648 | Mapping object space dimensions | `.bdf` | 21 | BioSemi |
| 14 | ds007012 | FOODEEG (food image categorisation) | `.bdf` | 117 | Large BioSemi set |
| 15 | ds005811 | NOD-EEG (ImageNet) | `.set` | 19 | EEGLAB single-file `.set` |
| 16 | ds007172 | EEG-Asymmetries (faces, houses, words) | BrainVision | 100 | Several visual tasks + rest |
| 17 | ds004324 | ToonFaces RSVP | `.edf` | 26 | EDF events from annotations |
| 18 | ds008092 | Food-image categorization, consumer EEG | `.edf` | 77 | **Low-cost headset**: few channels, non-10-20 names |
| 19 | ds005106 | 200 Objects, Infants | `.set` + `.fdt` | 42 | Infant nets; split EEGLAB files |

## C. Visual attention, perception, working memory

| # | ID | Dataset | Format | Subj | Edge case |
|---|---|---|---|---|---|
| 20 | ds006171 | Near-threshold visual detection (3 tasks) | `.bdf` | 36 | BioSemi; multiple tasks |
| 21 | ds006563 | Dimension-based attention, early visual processing | `.set` + `.fdt` | 12 | P1/N1 focus |
| 22 | ds003190 | Visual stimulus properties in P300 paradigm | BrainVision | 19 | Visual P300 BCI |
| 23 | ds001810 | Attentional blink (+ tDCS) | BrainVision | 47 | Multi-session (`ses-`) layout |
| 24 | ds005586 | Number of objects in partially occluded scenes | BrainVision | 23 | Passive viewing |
| 25 | ds002578 | Visual oddball, **256 channels** | `.set` | 2 | EGI-origin high density; non-10-20 names (E1…E256) |
| 26 | ds005841 | ERPs in VR vs screen | `.bdf` | 48 | VR (matches long-term project direction) |
| 27 | ds004621 | Nencki-Symfonia EEG/ERP | BrainVision | 42 | Multi-task ERP battery |
| 28 | ds006768 | Multiple Object Monitoring | `.edf` + **`.gz`** | 30 | **Compressed files** must be handled or rejected cleanly |
| 29 | ds006979 | Perceptual grouping in visual working memory | **mixed** `.edf`/`.set`/BrainVision (+ `.DS_Store`) | 53 | **Mixed formats within one dataset**, junk files |

## D. SSVEP / visual BCI

| # | ID | Dataset | Format | Subj | Edge case |
|---|---|---|---|---|---|
| 30 | ds004745 | 8-channel SSVEP with artifact trials | `.set` | 6 | Few channels; artifact labels |
| 31 | ds005028 | P300 flashing paradigms, online typing | `.edf` + `.dat` | 11 | BCI2000 `.dat` sidecars |
| 32 | MNE `ssvep` | MNE SSVEP sample dataset (`mne.datasets.ssvep`) | BrainVision | 2 | Ships with MNE: fast CI fixture |
| 33 | Zenodo 18873228 | Wang 2021 combined SSVEP | **Neuroscan `.cnt`** | 8 | Real `.cnt` (16/32-bit ambiguity) |
| 34 | Tsinghua BETA | BETA SSVEP benchmark (bci.med.tsinghua.edu.cn) | `.mat` | 70 | Epoched MATLAB arrays: "not a raw format" path |

## E. Format coverage beyond OpenNeuro (vendor-native files)

| # | Source | Dataset | Format | Why |
|---|---|---|---|---|
| 35 | MNE `testing` data | MNE testing dataset | **EGI `.mff`, Neuroscan `.cnt`, Curry, GDF, ANT `.cnt`, EDF, BDF, BrainVision, EEGLAB, FIF** | Every Tier-2 reader is tested here (blueprint section 4) |
| 36 | MNE `sample` | MNE sample (auditory/visual) | **`.fif`** (MEG+EEG) | Visual-stimulus FIF; mixed sensor types |
| 37 | BNCI Horizon / BCI Comp IV 2a | BCI Competition IV 2a | **`.gdf`** | GDF with event table; motor imagery (BCI motivation) |
| 38 | PhysioNet eegmmidb | EEG Motor Movement/Imagery (BCI2000) | **EDF+** | Annotation-channel events. Also on OpenNeuro as ds004362 (`.set`): **same data, two formats**, which is a cross-format consistency test |
| 39 | Zenodo 14860368 | Consumer-grade EEG + eye-tracking (113 subj) | **`.xdf`** (+ CSV) | **Real LSL XDF**: multi-stream, clock sync |
| 40 | OSF 3jk45 | THINGS-EEG2 raw (10 subj) | raw vendor files (confirm on OSF) | Very large RSVP; the second large object dataset |

## Format coverage summary

| Format | Datasets |
|---|---|
| BrainVision | 8, 9, 10, 11, 16, 22, 23, 24, 27, 29, 32, 35 |
| BioSemi BDF | 6, 7, 12, 13, 14, 20, 26, 35 |
| EDF / EDF+ | 17, 18, 28, 29, 31, 35, 38 |
| EEGLAB `.set`(+`.fdt`) | 1–5, 15, 19, 21, 25, 29, 30, 35, 38 (ds004362) |
| XDF | 39 |
| GDF | 35, 37 |
| Neuroscan / ANT `.cnt`, Curry | 33, 35 |
| EGI `.mff` | 35 (real-study EGI `.mff` still needed; see below) |
| FIF | 35, 36 |
| MAT / CSV | 34, 39 |

**Gaps to close later:** I found no public EGI `.mff` visual study with confirmed raw `.mff`
files, and no OpenBCI CSV visual ERP study. Those formats are covered only by MNE testing data
until a suitable dataset is found.

## Sources
- OpenNeuro GraphQL API (`https://openneuro.org/crn/graphql`), queried 2026-10-30 for all 466 public EEG datasets
- ERP CORE: https://erpinfo.org/erp-core, https://doi.org/10.18115/D5JW4R
- THINGS-EEG: https://www.nature.com/articles/s41597-021-01102-7
- BETA SSVEP: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7324867/
- BCI Competition IV: https://bbci.de/competition/iv/, BNCI Horizon: https://www.bnci-horizon-2020.eu/database/data-sets
- Wang 2021 SSVEP (Neuroscan .cnt): https://zenodo.org/records/18873228
- PhysioNet eegmmidb: https://www.physionet.org/content/eegmmidb/1.0.0/
- Consumer-grade EEG + eye tracking (XDF): https://zenodo.org/records/14860368
- THINGS-EEG2: https://osf.io/3jk45
