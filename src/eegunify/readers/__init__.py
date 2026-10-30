"""Format readers. Importing this package registers every built-in reader."""

from eegunify.readers import bdf, brainvision, edf, eeglab
from eegunify.readers.base import REGISTRY, Reader, get_reader

__all__ = ["REGISTRY", "Reader", "bdf", "brainvision", "edf", "eeglab", "get_reader"]
