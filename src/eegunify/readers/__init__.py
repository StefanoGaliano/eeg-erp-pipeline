"""Format readers. Importing this package registers every built-in reader."""

from eegunify.readers import (
    ant,
    bdf,
    brainvision,
    csv_generic,
    curry,
    edf,
    eeglab,
    egi,
    gdf,
    neuroscan,
    xdf,
)
from eegunify.readers.base import REGISTRY, Reader, get_reader

__all__ = [
    "REGISTRY",
    "Reader",
    "ant",
    "bdf",
    "brainvision",
    "csv_generic",
    "curry",
    "edf",
    "eeglab",
    "egi",
    "gdf",
    "get_reader",
    "neuroscan",
    "xdf",
]
