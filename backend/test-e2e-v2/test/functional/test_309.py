"""test_309 - ESTRAI cross-db smoke on Oracle.

Enable with ``PLENORA_E2E_CROSS_DB=1`` or
``PLENORA_E2E_CROSS_DB_DIALECTS=oracle``.
"""

from __future__ import annotations

from _cross_db import run_dialect


if __name__ == "__main__":
    run_dialect("oracle")

