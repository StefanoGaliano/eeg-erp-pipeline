"""Pydantic v2 models for the per-dataset YAML config (blueprint section 8).

Every model forbids unknown keys, so a typo in a config is an error rather than a silently
ignored setting.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    model_config = ConfigDict(extra="forbid")


class Anonymize(_Strict):
    daysback: int = Field(ge=0)


class DatasetSection(_Strict):
    name: str
    bids_root: str = "rawdata"
    source_root: str
    task: str = Field(pattern=r"^[A-Za-z0-9]+$")
    authors: list[str] = Field(default_factory=list)
    license: str | None = None
    anonymize: Anonymize | None = None


class RecordingsSection(_Strict):
    pattern: str
    subjects: list[str] = Field(min_length=1)
    session: str | None = None
    run: str | None = None

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
    channel_columns: list[str] | None = None
    """Columns holding EEG/EOG data (default: OpenBCI ``EXG Channel N`` columns, else every
    numeric column except time, marker and sample-index columns)."""
    channel_names: list[str] | None = None
    """Names for those columns, in order (e.g. the 10-20 position of each OpenBCI pin)."""
    unit: Literal["V", "mV", "uV"] = "uV"
    delimiter: str | None = None
    comment: str = "%"
    time_column: str | None = None
    marker_column: str | None = None

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
    neuroscan_data_format: Literal["auto", "int16", "int32"] = "auto"
    bdf_status_mask: int = 0xFF
    xdf_eeg_stream: dict[str, str] = Field(default_factory=lambda: {"type": "EEG"})
    xdf_marker_stream: dict[str, str] = Field(default_factory=lambda: {"type": "Markers"})
    csv: CsvHints = Field(default_factory=CsvHints)


class FormatSection(_Strict):
    name: FormatName = "auto"
    hints: FormatHints = Field(default_factory=FormatHints)


class UnitsSection(_Strict):
    #: ``auto`` trusts the file header. Otherwise: the unit the values *actually* are in, when
    #: the header is wrong. The data are rescaled by that factor (``uV`` -> x1e-6) and the
    #: correction is logged in provenance. ``eegunify inspect`` suggests the right value.
    declared: Literal["auto", "V", "mV", "uV"] = "auto"
    expected_eeg_range_uV: tuple[float, float] = (0.5, 500.0)

    @model_validator(mode="after")
    def _ordered(self) -> UnitsSection:
        lo, hi = self.expected_eeg_range_uV
        if not 0 < lo < hi:
            raise ValueError("expected_eeg_range_uV must be [low, high] with 0 < low < high")
        return self


class ReferenceSection(_Strict):
    online: str = "n/a"
    ground: str | None = None


class ChannelsSection(_Strict):
    rename: dict[str, str] = Field(default_factory=dict)
    types: dict[str, ChannelType] = Field(default_factory=dict)
    drop: list[str] = Field(default_factory=list)
    bads: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    aliases_extra: dict[str, str] = Field(default_factory=dict)


class MontageSection(_Strict):
    source: Literal["standard", "file", "embedded", "none"] = "standard"
    standard_name: str = "standard_1005"
    file: str | None = None
    on_missing: Literal["raise", "warn", "ignore"] = "warn"

    @model_validator(mode="after")
    def _file_needed(self) -> MontageSection:
        if self.source == "file" and not self.file:
            raise ValueError("montage.source 'file' requires montage.file")
        return self


class EventsSection(_Strict):
    #: ``bids_events``: the source's own BIDS ``*_events.tsv`` (for files without triggers).
    #: ``auto`` falls back to it when the recording itself has no events.
    source: Literal[
        "auto", "stim_channel", "annotations", "bids_events", "marker_stream", "marker_file"
    ] = "auto"
    stim_channel: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)
    unmapped: Literal["keep_as_code", "drop", "error"] = "keep_as_code"
    ignore_codes: list[int] = Field(default_factory=lambda: [0])
    min_duration_samples: int = Field(default=1, ge=1)
    expected_counts: dict[str, int] = Field(default_factory=dict)
    count_tolerance: float = Field(default=0.05, ge=0, le=1)

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
    eeg_trial_types: list[str] = Field(default_factory=lambda: ["*"])
    """Glob patterns of EEG trial types that correspond to log rows (e.g. ``stimulus/*``)."""
    beh_filter: str | None = None
    """pandas ``query`` selecting the log rows to align (e.g. ``"block != 'practice'"``)."""
    beh_time_column: str | None = None
    """Stimulus time in the log, in seconds (auto: PsychoPy ``*.started``, Presentation
    ``Time``). Without one, rows are paired in order and the counts must match."""


class BehaviorSection(_Strict):
    source: Literal["psychopy", "presentation", "eprime", "none"] = "none"
    file: str | None = None
    """Path relative to ``dataset.source_root``; may contain ``{subject}`` and glob ``*``."""
    align_on: AlignOn = Field(default_factory=AlignOn)
    columns: list[str] = Field(default_factory=list)
    """Log columns appended to ``events.tsv`` (as ``beh_<column>``)."""
    max_drift_ms: float = Field(default=20.0, gt=0)

    @model_validator(mode="after")
    def _file_needed(self) -> BehaviorSection:
        if self.source != "none" and not self.file:
            raise ValueError("behavior.file is required when behavior.source is set")
        return self


class Config(_Strict):
    """A complete, validated per-dataset configuration."""

    schema_version: Literal[1] = 1
    dataset: DatasetSection
    recordings: RecordingsSection
    format: FormatSection = Field(default_factory=FormatSection)
    units: UnitsSection = Field(default_factory=UnitsSection)
    reference: ReferenceSection = Field(default_factory=ReferenceSection)
    channels: ChannelsSection = Field(default_factory=ChannelsSection)
    montage: MontageSection = Field(default_factory=MontageSection)
    events: EventsSection = Field(default_factory=EventsSection)
    behavior: BehaviorSection = Field(default_factory=BehaviorSection)
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _override_keys(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        allowed = {"format", "units", "reference", "channels", "montage", "events", "behavior"}
        for sub, patch in v.items():
            if not sub.startswith("sub-"):
                raise ValueError(f"override key {sub!r} must look like 'sub-<label>'")
            bad = set(patch) - allowed
            if bad:
                raise ValueError(f"override {sub!r} touches unsupported sections {sorted(bad)}")
        return v

    def bads_for(self, subject: str) -> list[str]:
        """Bad channels for one subject (``bads`` may be a list or a per-subject dict)."""
        bads = self.channels.bads
        if isinstance(bads, list):
            return list(bads)
        return list(bads.get(f"sub-{subject}", []))
