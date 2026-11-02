# Supported formats

Every reader turns a file into the same canonical recording (see `BLUEPRINT.md` section 5). This
table says how each format is tested and what is specific to it.

**How each format is tested.**
- **Synthetic round trip:** a generated recording with known content is written in the format
  and must come back exactly (`tests/unit/test_roundtrip.py`).
- **Real file:** a vendor file from mne-testing-data, at a pinned commit (`pytest -m realdata`).
- **Cross-format:** the same recording exported in two formats must give identical data and
  events.
- **Real dataset:** converted in the validation run ([validation_results.md](validation_results.md)).

| Format | Extensions | Events from | Testing | Format-specific handling |
|---|---|---|---|---|
| BrainVision | `.vhdr` + `.vmrk` + `.eeg` | marker file | synthetic, real datasets | Header pointing at renamed files is repaired without editing the source; `STATUS/<n>` markers from converted BioSemi data are masked to 8 bits |
| BioSemi BDF | `.bdf` | `Status` channel (low 8 bits) | synthetic, real file, real datasets | Reference-free (CMS/DRL); `A1..B32` labels mapped to 10-20 for 64-channel caps |
| EDF / EDF+ | `.edf` | annotations or stim channel | synthetic, real datasets | `EEG Fp1-Ref` style labels cleaned; 16-bit quantisation |
| EEGLAB | `.set` (+ `.fdt`) | `EEG.event` | synthetic, real datasets (ERP CORE golden) | |
| LSL XDF | `.xdf` | marker stream | synthetic (hand-written XDF with clock offset and jitter) | LSL clock sync and dejitter; resampled if the clock drifts > 0.1 %; stream chosen by type/name |
| GDF | `.gdf` | event table / stim channel | real files (GDF 1.25, 2.20) | |
| ANT Neuro eego | `.cnt` (+ `.evt`) | `.evt` | real file, cross-format vs its BrainVision export | `.cnt` told apart from Neuroscan by its RIFF magic bytes; impedance-check markers dropped |
| Neuroscan | `.cnt` | embedded | real file | 16- vs 32-bit samples: when the header can't tell, both are read and the physiologically plausible one is kept (else `neuroscan_data_format` must be set) |
| Curry 7 / 8 | `.dap` / `.cdt` | embedded | real files, cross-format vs the BDF original | Code 50000 (trigger off, 2 ms after every trigger) ignored |
| EGI / Magstim | `.mff` (folder), `.raw` | annotations | real file; synthetic `.raw` | One stim channel per event type, so annotations are used (otherwise all but one type would be lost); `E1..E256` names kept. Segmented `.raw` holding a single segment (Net Station exports, e.g. Zenodo 5172962) is read as continuous; several segments (epoched data) are refused |
| CSV / OpenBCI | `.csv`, `.tsv`, `.txt` | marker column | synthetic (generic CSV, OpenBCI GUI export) | Sampling rate from config, OpenBCI `%Sample Rate` header, or the time column, recorded as a note; channel names from `format.hints.csv.channel_names` |
| BIDS sidecar events | `*_events.tsv` next to any of the above | the sidecar | real datasets (ds003645, ds006768) | Used automatically when the recording itself has no events |

**Behavioural logs** (`behavior:` config) are aligned to EEG events and appended to
`events.tsv` as `beh_<column>`:
- **PsychoPy** `.csv`: time from `*.started`.
- **Presentation** `.log`: `Time` in units of 0.1 ms.
- **E-Prime** E-DataAid tab export, UTF-16 or UTF-8.

With a time column, the clock offset and drift are estimated and each event takes the nearest
log row. A dropped trigger leaves that one trial unmatched instead of shifting all later trials.
Without a time column, rows are paired in order and the counts must match. QA check
`BEHAVIOR_ALIGNMENT` reports what was matched.

**Not supported:**
- Epoched-only data, such as the Tsinghua BETA `.mat` arrays.
- Raw E-Prime `.edat2` files (export to tab-delimited text first).
- Compressed files (`.gz`): decompress them first.
