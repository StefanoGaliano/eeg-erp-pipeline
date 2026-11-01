"""Pydantic v2 models for the per-dataset YAML config (blueprint section 8).

Every model forbids unknown keys, so a typo in a config is an error rather than a silently
ignored setting.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ERP-phase sections. Imported into this module because config/reference.py looks every
# model of the JSON schema up here by name.
from eegunify.config.erp_sections import (
    ArtifactsSection,
    AsrSpec,
    BadChannelsSpec,
    ClusterSpec,
    ComponentsSection,
    Contrast,
    EogCorrelationSpec,
    EpochSet,
    EpochsSection,
    ErpSection,
    FilterSpec,
    GroupSection,
    IcaSpec,
    IcLabelSpec,
    LineNoiseSpec,
    PreprocSection,
    RejectSpec,
    ResponseRule,
)

# The ERP models not referenced below are only re-exported (see the comment above).
__all__ = ["AsrSpec", "BadChannelsSpec", "ClusterSpec", "EogCorrelationSpec", "EpochSet"]
__all__ += ["FilterSpec", "IcLabelSpec", "IcaSpec", "LineNoiseSpec", "RejectSpec", "ResponseRule"]

FormatName = Literal[
    "auto",
    "brainvision",
    "bdf",
    "edf",
    "eeglab",
    "xdf",
    "gdf",
    "ant",
    "neuroscan",
    "curry",
    "egi",
    "csv",
]
ChannelType = Literal["eeg", "eog", "emg", "ecg", "misc", "stim", "resp", "temperature"]

_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


class _Strict(BaseModel):
    # Attribute docstrings become field descriptions (docs/config_reference.md).
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class Anonymize(_Strict):
    """Shift the measurement date back for anonymization (MNE-BIDS ``anonymize``)."""

    daysback: int = Field(ge=0)
    """Days to subtract from the recording date (same for every subject)."""


class DatasetSection(_Strict):
    """What the dataset is and where it lives (paths relative to the project root)."""

    name: str
    """Dataset name, written to ``dataset_description.json``."""
    bids_root: str = "rawdata"
    """Output folder for the canonical BIDS dataset."""
    source_root: str
    """Folder holding the original recordings (never modified)."""
    task: str = Field(pattern=r"^[A-Za-z0-9]+$")
    """BIDS task label (letters and digits only)."""
    authors: list[str] = Field(default_factory=list)
    """Dataset authors for ``dataset_description.json``."""
    license: str | None = None
    """Data license, e.g. ``CC0`` or ``CC-BY-4.0``."""
    anonymize: Anonymize | None = None
    """Shift measurement dates back; ``null`` keeps them."""


class RecordingsSection(_Strict):
    """Which files make up the dataset."""

    pattern: str
    """Path of one subject's recording relative to ``source_root``, with ``{subject}``."""
    subjects: list[str] = Field(min_length=1)
    """Subject labels (alphanumeric, without ``sub-``)."""
    session: str | None = None
    """BIDS session label for the output, if any."""
    run: str | None = None
    """BIDS run label for the output, if any."""

    @field_validator("pattern")
    @classmethod
    def _needs_subject(cls, v: str) -> str:
        if "{subject}" not in v:
            raise ValueError("pattern must contain the '{subject}' placeholder")
        return v

    @field_validator("subjects")
    @classmethod
    def _alnum_subjects(cls, v: list[str]) -> list[str]:
        for s in v:
            if not re.fullmatch(r"[A-Za-z0-9]+", s):
                raise ValueError(f"subject label {s!r} must be alphanumeric (BIDS)")
        return v


class CsvHints(_Strict):
    """How to read a delimited text recording (OpenBCI GUI exports, generic CSV).

    Everything is optional: OpenBCI files declare their sampling rate in a ``%Sample Rate``
    header line and name channels ``EXG Channel N``; generic files are recognised by a time
    column (``time``/``timestamp``) and a marker column (``marker``/``trigger``/``event``).
    """

    sfreq: float | None = Field(default=None, gt=0)
    """Sampling rate in Hz (default: from an OpenBCI header or the time column)."""
    channel_columns: list[str] | None = None
    """Columns holding EEG/EOG data (default: OpenBCI ``EXG Channel N`` columns, else every
    numeric column except time, marker and sample-index columns)."""
    channel_names: list[str] | None = None
    """Names for those columns, in order (e.g. the 10-20 position of each OpenBCI pin)."""
    unit: Literal["V", "mV", "uV"] = "uV"
    """Unit of the values in the file."""
    delimiter: str | None = None
    """Column separator (default: sniffed from the first lines)."""
    comment: str = "%"
    """Lines starting with this are header comments."""
    time_column: str | None = None
    """Column with time in seconds (default: ``time``/``timestamp`` if present)."""
    marker_column: str | None = None
    """Column with event codes; non-zero values become events (default: auto)."""

    @model_validator(mode="after")
    def _names_match(self) -> CsvHints:
        if (
            self.channel_names
            and self.channel_columns
            and len(self.channel_names) != len(self.channel_columns)
        ):
            raise ValueError("csv.channel_names and csv.channel_columns differ in length")
        return self


class FormatHints(_Strict):
    """Format-specific settings; each applies to one reader only."""

    neuroscan_data_format: Literal["auto", "int16", "int32"] = "auto"
    """Neuroscan ``.cnt`` sample width; ``auto`` keeps the width with plausible amplitudes."""
    bdf_status_mask: int = 0xFF
    """Bits of the BioSemi Status channel (or ``STATUS/<n>`` markers) that carry triggers."""
    xdf_eeg_stream: dict[str, str] = Field(default_factory=lambda: {"type": "EEG"})
    """Which XDF stream is the EEG, by ``type`` and/or ``name``."""
    xdf_marker_stream: dict[str, str] = Field(default_factory=lambda: {"type": "Markers"})
    """Which XDF stream holds the markers (optional; missing means no events)."""
    csv: CsvHints = Field(default_factory=CsvHints)
    """CSV / OpenBCI settings."""


class FormatSection(_Strict):
    """How the recordings are read."""

    name: FormatName = "auto"
    """Reader to use; ``auto`` detects it from the extension (and magic bytes for ``.cnt``)."""
    hints: FormatHints = Field(default_factory=FormatHints)
    """Format-specific settings."""


class UnitsSection(_Strict):
    """Amplitude units and the plausibility check."""

    declared: Literal["auto", "V", "mV", "uV"] = "auto"
    """``auto`` trusts the header. Otherwise the unit the values are really in when the header
    is wrong; data are rescaled and the factor logged. ``eegunify inspect`` suggests it."""
    expected_eeg_range_uV: tuple[float, float] = (0.5, 500.0)
    """Plausible median robust SD of EEG channels, in µV (unit check and QA)."""

    @model_validator(mode="after")
    def _ordered(self) -> UnitsSection:
        lo, hi = self.expected_eeg_range_uV
        if not 0 < lo < hi:
            raise ValueError("expected_eeg_range_uV must be [low, high] with 0 < low < high")
        return self


class ReferenceSection(_Strict):
    """Reference: ``online``/``ground`` describe the recording (Phase 1 never re-references);
    ``scheme``/``channels`` are the offline reference the ERP preproc stage applies."""

    online: str = "n/a"
    """Recorded (online) reference, e.g. ``Cz``, ``linked mastoids``; BioSemi defaults to
    ``CMS/DRL (reference-free)``. Written to ``EEGReference``; never changes the data."""
    ground: str | None = None
    """Ground electrode, if known."""
    scheme: Literal["average", "channels", "rest", "none"] = "average"
    """Offline re-reference applied by the ERP preproc stage (not by ``convert``): ``average``
    of good EEG channels, the mean of ``channels``, ``rest``, or ``none`` (refused for
    reference-free recordings such as BioSemi)."""
    channels: list[str] = Field(default_factory=list)
    """Reference channels for ``scheme: channels``, e.g. ``[P9, P10]``."""

    @model_validator(mode="after")
    def _channels_needed(self) -> ReferenceSection:
        if self.scheme == "channels" and not self.channels:
            raise ValueError("reference.scheme 'channels' requires reference.channels")
        if self.scheme != "channels" and self.channels:
            raise ValueError("reference.channels is only used with reference.scheme 'channels'")
        return self


class ChannelsSection(_Strict):
    """Channel names, types and bad channels (applied after automatic normalization)."""

    rename: dict[str, str] = Field(default_factory=dict)
    """Explicit renames, keyed by original or normalized name."""
    types: dict[str, ChannelType] = Field(default_factory=dict)
    """Channel types that override the automatic inference (e.g. ``EXG3: eog``)."""
    drop: list[str] = Field(default_factory=list)
    """Channels to leave out of the output."""
    bads: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    """Bad channels: a list for all subjects, or ``{"sub-01": [...]}`` per subject."""
    aliases_extra: dict[str, str] = Field(default_factory=dict)
    """Extra name aliases on top of the old 10-20 ones (``T3`` -> ``T7``, ...)."""


class MontageSection(_Strict):
    """Electrode positions."""

    source: Literal["standard", "file", "embedded", "none"] = "standard"
    """``standard`` layout, a position ``file``, positions ``embedded`` in the recording, or
    ``none`` (also drops embedded positions)."""
    standard_name: str = "standard_1005"
    """Any MNE built-in montage, e.g. ``biosemi64``, ``GSN-HydroCel-128``."""
    file: str | None = None
    """Position file (``.elc``, ``.sfp``, ``.bvct``, ...) relative to the recording's folder;
    may contain ``{subject}``."""
    on_missing: Literal["raise", "warn", "ignore"] = "warn"
    """What to do with EEG channels that get no position."""

    @model_validator(mode="after")
    def _file_needed(self) -> MontageSection:
        if self.source == "file" and not self.file:
            raise ValueError("montage.source 'file' requires montage.file")
        return self


class EventsSection(_Strict):
    """Event extraction and code -> condition mapping."""

    source: Literal["auto", "stim_channel", "annotations", "bids_events"] = "auto"
    """``auto``: stim channel, else file markers, else the source's BIDS ``*_events.tsv``.
    ``bids_events`` forces the sidecar (for files without triggers)."""
    stim_channel: str | None = None
    """Trigger channel name (default: the first stim channel)."""
    mapping: dict[str, str] = Field(default_factory=dict)
    """Code (``"5"``), inclusive range (``"1-40"``) or marker label -> trial type
    (``stimulus/face``). Ranges must not overlap."""
    unmapped: Literal["keep_as_code", "drop", "error"] = "keep_as_code"
    """What to do with codes missing from ``mapping``."""
    ignore_codes: list[int] = Field(default_factory=lambda: [0])
    """Codes that are never events."""
    min_duration_samples: int = Field(default=1, ge=1)
    """Debounce for stim channels: shorter pulses are ignored."""
    expected_counts: dict[str, int] = Field(default_factory=dict)
    """Trial type -> expected count per subject (QA ``EVENTS_EXPECTED_COUNT``)."""
    count_tolerance: float = Field(default=0.05, ge=0, le=1)
    """Allowed relative deviation from ``expected_counts``."""
    onset_shift_ms: dict[str, float] = Field(default_factory=dict)
    """Known trigger-to-stimulus delays, by trial-type glob (e.g. ``{"stimulus/*": 26}`` for an
    LCD monitor that shows the image 26 ms after the trigger). Responses are usually not
    shifted. Applied to onsets and samples and recorded in provenance."""

    @field_validator("mapping", mode="before")
    @classmethod
    def _keys_to_str(cls, v: Any) -> Any:
        # YAML parses `1: face` with an int key; accept it.
        if isinstance(v, dict):
            return {str(k): val for k, val in v.items()}
        return v

    @field_validator("mapping")
    @classmethod
    def _no_overlapping_ranges(cls, v: dict[str, str]) -> dict[str, str]:
        spans: list[tuple[int, int, str]] = []
        for key in v:
            m = _RANGE_RE.match(key)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                if lo > hi:
                    raise ValueError(f"range {key!r} has low > high")
                spans.append((lo, hi, key))
            elif key.strip().isdigit():
                n = int(key)
                spans.append((n, n, key))
        spans.sort()
        for (_, hi1, k1), (lo2, _, k2) in pairwise(spans):
            if lo2 <= hi1:
                raise ValueError(f"event mapping keys {k1!r} and {k2!r} overlap")
        return v


class AlignOn(_Strict):
    """Which EEG events and log rows are aligned, and how."""

    eeg_trial_types: list[str] = Field(default_factory=lambda: ["*"])
    """Glob patterns of EEG trial types that correspond to log rows (e.g. ``stimulus/*``)."""
    beh_filter: str | None = None
    """pandas ``query`` selecting the log rows to align (e.g. ``"block != 'practice'"``)."""
    beh_time_column: str | None = None
    """Stimulus time in the log, in seconds (auto: PsychoPy ``*.started``, Presentation
    ``Time``). Without one, rows are paired in order and the counts must match."""


class BehaviorSection(_Strict):
    """Behavioural log merged into ``events.tsv``."""

    source: Literal["psychopy", "presentation", "eprime", "none"] = "none"
    """Log format, or ``none``."""
    file: str | None = None
    """Path relative to ``dataset.source_root``; may contain ``{subject}`` and glob ``*``."""
    align_on: AlignOn = Field(default_factory=AlignOn)
    """Alignment settings."""
    columns: list[str] = Field(default_factory=list)
    """Log columns appended to ``events.tsv`` (as ``beh_<column>``)."""
    max_drift_ms: float = Field(default=20.0, gt=0)
    """Largest EEG-vs-log timing residual accepted for a match."""

    @model_validator(mode="after")
    def _file_needed(self) -> BehaviorSection:
        if self.source != "none" and not self.file:
            raise ValueError("behavior.file is required when behavior.source is set")
        return self


class Config(_Strict):
    """A complete, validated per-dataset configuration."""

    schema_version: Literal[1] = 1
    """Config format version."""
    dataset: DatasetSection
    """Name, paths, BIDS task, authors, license."""
    recordings: RecordingsSection
    """Which files and subjects."""
    format: FormatSection = Field(default_factory=FormatSection)
    """Reader selection and format-specific hints."""
    units: UnitsSection = Field(default_factory=UnitsSection)
    """Unit correction and the plausibility range."""
    reference: ReferenceSection = Field(default_factory=ReferenceSection)
    """Online reference and ground as recorded."""
    channels: ChannelsSection = Field(default_factory=ChannelsSection)
    """Renames, types, drops and bad channels."""
    montage: MontageSection = Field(default_factory=MontageSection)
    """Electrode positions: standard layout, position file, embedded, or none."""
    events: EventsSection = Field(default_factory=EventsSection)
    """Event extraction, mapping, expected counts, onset shifts."""
    behavior: BehaviorSection = Field(default_factory=BehaviorSection)
    """Behavioural log alignment."""
    preproc: PreprocSection = Field(default_factory=PreprocSection)
    """ERP phases: continuous-data cleaning (unused by ``convert``)."""
    artifacts: ArtifactsSection = Field(default_factory=ArtifactsSection)
    """ERP phases: ICA / ASR artifact correction."""
    epochs: EpochsSection = Field(default_factory=EpochsSection)
    """ERP phases: epoch sets and rejection."""
    erp: ErpSection = Field(default_factory=ErpSection)
    """ERP phases: averaging, measurement and figures."""
    conditions: dict[str, list[str]] = Field(default_factory=dict)
    """ERP phases: condition name -> trial-type prefixes pooled into it
    (``face: [stimulus/face]``)."""
    contrasts: dict[str, Contrast] = Field(default_factory=dict)
    """ERP phases: difference waves by name (``face_minus_car: {plus: face, minus: car}``)."""
    components: ComponentsSection = Field(default_factory=ComponentsSection)
    """ERP phases: components to measure, from the component library."""
    group: GroupSection = Field(default_factory=GroupSection)
    """ERP phases: group statistics and comparison with published values."""
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Per-subject patches: ``{"sub-01": {"channels": {...}}}``."""

    @field_validator("overrides")
    @classmethod
    def _override_keys(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        allowed = {
            "format",
            "units",
            "reference",
            "channels",
            "montage",
            "events",
            "behavior",
            "preproc",
            "artifacts",
            "epochs",
            "components",
        }
        for sub, patch in v.items():
            if not sub.startswith("sub-"):
                raise ValueError(f"override key {sub!r} must look like 'sub-<label>'")
            bad = set(patch) - allowed
            if bad:
                raise ValueError(f"override {sub!r} touches unsupported sections {sorted(bad)}")
        return v

    @model_validator(mode="after")
    def _contrasts_use_conditions(self) -> Config:
        for name, c in self.contrasts.items():
            for side in (c.plus, c.minus):
                if side not in self.conditions:
                    raise ValueError(
                        f"contrast {name!r} uses unknown condition {side!r}; "
                        f"defined: {sorted(self.conditions)}"
                    )
        if self.group.cluster.enabled and self.group.cluster.contrast not in self.contrasts:
            raise ValueError("group.cluster.contrast must name one of 'contrasts'")
        return self

    def bads_for(self, subject: str) -> list[str]:
        """Bad channels for one subject (``bads`` may be a list or a per-subject dict)."""
        bads = self.channels.bads
        if isinstance(bads, list):
            return list(bads)
        return list(bads.get(f"sub-{subject}", []))
