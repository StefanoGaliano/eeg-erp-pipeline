"""eegunify: convert any raw EEG recording into one canonical BIDS-EEG format."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eegunify")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
