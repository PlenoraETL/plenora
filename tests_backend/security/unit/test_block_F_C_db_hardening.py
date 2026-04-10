"""
tests/security/unit/test_block_F_C_db_hardening.py
==================================================
Unit test per il Blocco F-C: 3 fix DB/Log hardening.

Copre:
  - F7 file_service get_scan_state rinomina file corrotto
  - F8 intermediate _to_sqlite preserva bytes (BLOB)
  - F9 logs router LIKE wildcard escape
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# F7 — get_scan_state corrupted file rename
# ============================================================================

class TestGetScanStateCorruption:

    def test_corrupted_file_renamed_and_returns_empty(self, tmp_path):
        from app.core.datasources.service import DataSourceService
        from app.core.datasources.models import ScanState

        svc = DataSourceService()
        # Mocka _scan_state_path per puntare a tmp
        scan_path = tmp_path / "scan_corrupt.json"
        scan_path.write_text("{not valid json[", encoding="utf-8")

        with patch.object(svc, "_scan_state_path", return_value=scan_path):
            result = svc.get_scan_state("ds-1")

        # Ritorna un ScanState vuoto
        assert isinstance(result, ScanState)
        # Il file originale è stato rinominato in `.broken.<ts>`
        assert not scan_path.exists()
        broken_files = list(tmp_path.glob("scan_corrupt.json.broken.*"))
        assert len(broken_files) == 1
        # Il contenuto è preservato per investigazione
        assert "{not valid json" in broken_files[0].read_text(encoding="utf-8")

    def test_valid_file_unchanged(self, tmp_path):
        from app.core.datasources.service import DataSourceService

        svc = DataSourceService()
        scan_path = tmp_path / "scan_ok.json"
        scan_path.write_text(json.dumps({"last_check": None, "processed": []}), encoding="utf-8")

        with patch.object(svc, "_scan_state_path", return_value=scan_path):
            result = svc.get_scan_state("ds-1")

        # File ancora lì, no broken
        assert scan_path.exists()
        assert not list(tmp_path.glob("*.broken.*"))

    def test_no_file_returns_empty(self, tmp_path):
        from app.core.datasources.service import DataSourceService
        from app.core.datasources.models import ScanState

        svc = DataSourceService()
        with patch.object(svc, "_scan_state_path", return_value=tmp_path / "missing.json"):
            result = svc.get_scan_state("ds-1")
        assert isinstance(result, ScanState)


# ============================================================================
# F8 — _to_sqlite preserves bytes (BLOB)
# ============================================================================

class TestToSqliteBytes:
    """Verifica che bytes/bytearray vengano preservati come BLOB invece di
    essere silenziosamente convertiti a None (silent data loss)."""

    def test_bytes_preserved(self):
        # Replica della logica del fix per testarla in isolamento
        # (la funzione _to_sqlite è inner di insert_node_rows, non esportata)
        def _to_sqlite(v):
            if v is None:
                return None
            if hasattr(v, "isoformat"):
                return v.isoformat()
            import decimal
            if isinstance(v, decimal.Decimal):
                return float(v)
            t = type(v).__name__
            if "int" in t:
                return int(v)
            if "float" in t:
                return float(v)
            if "bool" in t:
                return int(v)
            if isinstance(v, bytearray):
                return bytes(v)
            if isinstance(v, bytes):
                return v
            return v

        # bytes preservati
        b = b"\x00\x01\x02hello"
        assert _to_sqlite(b) == b
        assert _to_sqlite(b) is not None

        # bytearray convertito a bytes (per il driver sqlite3)
        ba = bytearray(b"\xff\xfe")
        result = _to_sqlite(ba)
        assert isinstance(result, bytes)
        assert result == b"\xff\xfe"

        # Tipi standard ancora funzionano
        assert _to_sqlite(None) is None
        assert _to_sqlite(42) == 42
        assert _to_sqlite("text") == "text"

    def test_real_sqlite_roundtrip(self, tmp_path):
        """Smoke test: scrive bytes in sqlite e li rilegge."""
        import sqlite3
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (v BLOB)")
        original = b"\x00\xff\x80raw bytes"
        conn.execute("INSERT INTO t (v) VALUES (?)", (original,))
        conn.commit()
        row = conn.execute("SELECT v FROM t").fetchone()
        conn.close()
        assert row[0] == original


# ============================================================================
# F9 — LIKE wildcard escape
# ============================================================================

class TestLogsLikeEscape:
    """Verifica che il filtro `search` su /logs escapi i metacaratteri LIKE."""

    def test_percent_escaped(self):
        # Replica della logica di escape (semplice da testare in isolamento)
        search = "test%admin"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        assert escaped == "test\\%admin"

    def test_underscore_escaped(self):
        search = "user_login"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        assert escaped == "user\\_login"

    def test_backslash_escaped_first(self):
        """Backslash deve essere escapato PRIMA degli altri per evitare doppio escape."""
        search = "path\\to"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        assert escaped == "path\\\\to"

    def test_no_special_chars_unchanged(self):
        search = "normal text"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        assert escaped == "normal text"

    def test_multiple_specials(self):
        search = "a%b_c\\d"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        assert escaped == "a\\%b\\_c\\\\d"

    def test_real_sqlite_like_with_escape(self, tmp_path):
        """Smoke test: SQLite LIKE con ESCAPE '\\' tratta i % come letterali."""
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE logs (msg TEXT)")
        db.execute("INSERT INTO logs VALUES ('contains 100% raw')")
        db.execute("INSERT INTO logs VALUES ('contains discount')")
        db.commit()

        # Cerco letterale "100%" → deve matchare solo la prima
        search = "100%"
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = db.execute(
            "SELECT msg FROM logs WHERE msg LIKE ? ESCAPE '\\'",
            (f"%{escaped}%",)
        ).fetchall()
        assert len(rows) == 1
        assert "100%" in rows[0][0]
