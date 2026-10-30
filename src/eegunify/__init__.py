"""eegunify: convert any raw EEG recording into one canonical BIDS-EEG format."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eegunify")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"


def __getattr__(name: str) -> object:
    # Lazy public API so ``import eegunify`` stays fast (MNE import is heavy).
    if name in ("read_recording", "convert"):
        from eegunify import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module 'eegunify' has no attribute {name!r}")


__all__ = ["__version__", "convert", "read_recording"]
