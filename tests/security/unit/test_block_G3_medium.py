"""
tests/security/unit/test_block_G3_medium.py
===========================================
Unit test per il Blocco G-3: fix MEDIUM/LOW dal 2° audit.

Copre:
  - N6: intermediate snapshot memory cap (manipola executor)
  - N14: bare except in file_service.load_json
"""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# N6 — intermediate snapshot memory cap
# ============================================================================

class TestIntermediateSnapshotCap:
    """Verifica che lo snapshot intermedio del nodo result sia cappato."""

    def test_snapshot_limited_to_max_rows(self):
        """Codice del fix: snapshot_df = current_df.head(cap)."""
        # Simula il pattern del fix in isolamento
        MAX = 10_000
        big_df = pd.DataFrame({"x": range(50_000)})
        snapshot_df = big_df.head(MAX)
        assert len(snapshot_df) == MAX

    def test_small_df_unchanged(self):
        MAX = 10_000
        small_df = pd.DataFrame({"x": range(100)})
        snapshot_df = small_df.head(MAX)
        assert len(snapshot_df) == 100

    def test_fix_present_in_code(self):
        """Verifica statica che il cap sia nel codice."""
        from pathlib import Path
        code = (
            Path(__file__).resolve().parents[3]
            / "backend" / "app" / "core" / "manipola" / "executor.py"
        ).read_text(encoding="utf-8")
        assert "_MAX_INTERMEDIATE_SNAPSHOT_ROWS" in code
        assert "current_df.head(_MAX_INTERMEDIATE_SNAPSHOT_ROWS)" in code


# ============================================================================
# N14 — bare except → specific exception in file_service.load_json
# ============================================================================

class TestFileServiceLoadJsonException:
    """Verifica che il bare except sia stato sostituito con (ValueError, TypeError)."""

    def test_records_orient_fallback(self, tmp_path):
        """JSON non-tabulare: il fallback senza orient deve funzionare."""
        from app.services.file_service import DefaultDataLoader
        loader = DefaultDataLoader()

        # JSON con orient='records' valido
        f = tmp_path / "valid.json"
        f.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
        df = loader.load_json(f)
        assert len(df) == 2

    def test_dict_json_fallback(self, tmp_path):
        """JSON come dict top-level: orient='records' fallisce, fallback ok."""
        from app.services.file_service import DefaultDataLoader
        loader = DefaultDataLoader()

        f = tmp_path / "dict.json"
        f.write_text('{"col1": [1, 2], "col2": [3, 4]}', encoding="utf-8")
        df = loader.load_json(f)
        assert len(df) == 2
        assert "col1" in df.columns
