"""Sessione 11 P1: source_read cleanup order.

Bug coperto:
- files/source_read.py:96-104 — gc.collect chiamato PRIMA di
  _cleanup_temp_path. Su worker memory-constrained con GeoDataFrame
  grandi (shp/gdb), il gc tentava di liberare memoria del GeoDataFrame
  PRIMA del cleanup FS → picco memoria spurious.
- Post-fix: cleanup FS PRIMA, gc.collect DOPO.
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_source_read_finally_block_cleanup_before_gc():
    """`load_dataframe` finally block: `_cleanup_temp_path` deve precedere
    `gc.collect()`. Anti-regression."""
    from app.core.files import source_read

    src = inspect.getsource(source_read.load_dataframe)
    # Strip commenti per evitare false positive su "Pre-fix: ... gc.collect()"
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    cleanup_idx = src_no_comments.find("_cleanup_temp_path(")
    gc_idx = src_no_comments.find("gc.collect()")

    assert cleanup_idx >= 0, "_cleanup_temp_path call non trovata"
    assert gc_idx >= 0, "gc.collect() call non trovata"
    assert cleanup_idx < gc_idx, (
        "_cleanup_temp_path deve precedere gc.collect() — pre-fix era "
        "l'opposto, picco memoria spurious su GeoDataFrame grandi."
    )
