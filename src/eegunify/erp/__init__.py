"""ERP phases (docs/BLUEPRINT_PHASES.md): stages from canonical rawdata to group statistics.

Stages (``preproc -> ica -> epochs -> erp -> group``) are independent, write their outputs to
``derivatives/eegunify-<stage>/`` and are cached by a per-stage key (``hashing.py``); the
``runner`` decides what to rerun. Component windows and sites are data
(``components/*.yaml``).
"""
