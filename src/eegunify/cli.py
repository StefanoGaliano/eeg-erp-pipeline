"""Command-line interface (``eegunify``)."""

import typer
from rich.console import Console

from eegunify import __version__

app = typer.Typer(
    name="eegunify",
    help="Convert raw EEG recordings (any vendor format) into canonical BIDS-EEG.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def version() -> None:
    """Print the eegunify, MNE and MNE-BIDS versions."""
    import mne
    import mne_bids

    console.print(f"eegunify {__version__}")
    console.print(f"mne      {mne.__version__}")
    console.print(f"mne-bids {mne_bids.__version__}")


@app.callback()
def main() -> None:
    """eegunify: one canonical format for every raw EEG recording."""
