# The canonical contract

What every later stage of the pipeline (filtering, epoching, ERPs, decoding) can rely on, no
matter which file format a recording came from. eegunify writes nothing that breaks these
rules: violations are refused by `eegunify.canonical.validate` (blueprint section 5).

## On disk (`rawdata/`)

**BIDS-EEG with a BrainVision payload**:
- `.vhdr` + `.vmrk` + `.eeg`
- float32, **µV**

| File | Guarantees |
|---|---|
| `*_eeg.vhdr/.eeg` | Continuous data exactly as recorded: no filtering, re-referencing, resampling or cropping (XDF is the only exception: it is resampled onto a uniform grid if its clock drifted more than 0.1 %, and this is recorded). |
| `*_events.tsv` | **The authoritative event list.** `onset`/`sample` count from the first sample (`sample` is 0-based); `value` is the **original trigger code**; `trial_type` is the condition (`stimulus/face`); optional `beh_*` columns come from the behavioural log. Known monitor delays (`events.onset_shift_ms`) are already applied. |
| `*_channels.tsv` | 10-05 names (`Fp1`, `PO8`); `original_name` as in the source file; `type` for every channel; `status=bad` for bad channels (kept, not removed); units `µV` for voltage channels. |
| `*_eeg.json` | `EEGReference` (the online reference as recorded; BioSemi: `CMS/DRL (reference-free)`); hardware and task metadata from the source when it was BIDS; an `eegunify` block with source files and their sha256. |
| `*_electrodes.tsv` | Positions when known (standard layout, position file, or embedded). |

The full provenance is in `derivatives/eegunify-qa/sub-*/*_provenance.json`. It records the
source files with sha256, all package versions, the git commit, the resolved config, every
channel rename, the event method and any unit correction. The QA report sits next to it.

## In memory (`eegunify.read_recording`)

- `mne.io.Raw`, data in **Volts** (MNE convention), preloaded; no stim channels left.
- `raw.annotations`: one per event, `description = trial_type`, `extras = {"code": original}`.
- `raw.info["bads"]` set; montage set when positions are known; `raw.info["line_freq"]` set
  from the source or detected.

## Reading canonical data back

```python
import mne_bids

raw = mne_bids.read_raw_bids(
    mne_bids.BIDSPath(subject="015", session="N170", task="N170", datatype="eeg", root="rawdata")
)
epochs = mne.Epochs(raw, *mne.events_from_annotations(raw), tmin=-0.2, tmax=0.8)
faces = epochs["stimulus/face"]
```

- **When a trial type spans several codes, MNE-BIDS labels each event `trial_type/value`**,
  e.g. `stimulus/face/12`. Select with MNE's `/`-tag matching (`epochs["stimulus/face"]`),
  never with exact equality. See `code/scripts/erp_preview.py`.
- **MATLAB:** `matlab/load_canonical.m` gives an EEGLAB `EEG` with events from `events.tsv`:
  `type` = trial_type, `code` = original value, 1-based latency. Or run
  `eegunify export --to eeglab` for `.set` files.
- **Reference and filtering are left to you.** BioSemi data in particular must be re-referenced
  before any analysis.
