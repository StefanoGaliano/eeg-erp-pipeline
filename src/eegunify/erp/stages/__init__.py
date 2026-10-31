"""Built-in stages. They register themselves when imported; ``base.registered_stages()``
imports them on first use (importing them here would be circular: they use the helpers that
use ``base``)."""
