"""
tests/config/unit/test_config_tracking.py
=========================================
Unit test per la persistenza tracking config su SQLite:
_get_app_db, _get_setting, _set_setting, properties TRACKING_*,
save_tracking_config, get_tracking_config.
"""

import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings_with_db(tmp_path):
    """Settings con DATA_DIR su tmp_path — DB SQLite reale in memoria."""
    with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}, clear=False):
        s = Settings()
        yield s


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "config.db"


# =============================================================================
# _get_app_db
# =============================================================================

class TestGetAppDb:

    def test_creates_db_and_table(self, settings_with_db, tmp_path):
        conn = settings_with_db._get_app_db()
        assert (tmp_path / "config.db").exists()
        # Verifica che la tabella esista
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_returns_connection_with_row_factory(self, settings_with_db):
        conn = settings_with_db._get_app_db()
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_wal_mode_enabled(self, settings_with_db):
        conn = settings_with_db._get_app_db()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        conn.close()

    def test_idempotent_table_creation(self, settings_with_db):
        """Chiamare _get_app_db due volte non causa errori."""
        conn1 = settings_with_db._get_app_db()
        conn1.close()
        conn2 = settings_with_db._get_app_db()
        conn2.close()


# =============================================================================
# _set_setting / _get_setting
# =============================================================================

class TestSetGetSetting:

    def test_roundtrip(self, settings_with_db):
        settings_with_db._set_setting("test_key", "test_value")
        assert settings_with_db._get_setting("test_key") == "test_value"

    def test_get_missing_returns_default(self, settings_with_db):
        assert settings_with_db._get_setting("nonexistent", "fallback") == "fallback"

    def test_get_missing_returns_empty_default(self, settings_with_db):
        assert settings_with_db._get_setting("nonexistent") == ""

    def test_set_overwrites(self, settings_with_db):
        settings_with_db._set_setting("k", "v1")
        settings_with_db._set_setting("k", "v2")
        assert settings_with_db._get_setting("k") == "v2"

    def test_env_takes_priority_over_db(self, settings_with_db):
        settings_with_db._set_setting("MY_KEY", "db_value")
        with patch.dict(os.environ, {"MY_KEY": "env_value"}):
            assert settings_with_db._get_setting("MY_KEY") == "env_value"

    def test_get_setting_db_error_returns_default(self, settings_with_db):
        with patch.object(settings_with_db, "_get_app_db", side_effect=Exception("db broken")):
            result = settings_with_db._get_setting("some_key", "safe")
            assert result == "safe"

    def test_set_setting_db_error_no_raise(self, settings_with_db):
        """_set_setting cattura le eccezioni senza propagarle."""
        with patch.object(settings_with_db, "_get_app_db", side_effect=Exception("db broken")):
            # Non deve sollevare eccezioni
            settings_with_db._set_setting("key", "val")


# =============================================================================
# TRACKING_API_KEY / TRACKING_ORG_ID / TRACKING_ORG_NAME properties
# =============================================================================

class TestTrackingProperties:

    def test_tracking_api_key_from_db(self, settings_with_db):
        settings_with_db._set_setting("TRACKING_API_KEY", "sk-123")
        assert settings_with_db.TRACKING_API_KEY == "sk-123"

    def test_tracking_org_id_from_db(self, settings_with_db):
        settings_with_db._set_setting("TRACKING_ORG_ID", "org-456")
        assert settings_with_db.TRACKING_ORG_ID == "org-456"

    def test_tracking_org_name_from_db(self, settings_with_db):
        settings_with_db._set_setting("TRACKING_ORG_NAME", "My Org")
        assert settings_with_db.TRACKING_ORG_NAME == "My Org"

    def test_tracking_api_key_empty_default(self, settings_with_db):
        assert settings_with_db.TRACKING_API_KEY == ""

    def test_tracking_api_key_from_env(self, settings_with_db):
        with patch.dict(os.environ, {"TRACKING_API_KEY": "env-key"}):
            assert settings_with_db.TRACKING_API_KEY == "env-key"

    def test_tracking_org_id_from_env(self, settings_with_db):
        with patch.dict(os.environ, {"TRACKING_ORG_ID": "env-org"}):
            assert settings_with_db.TRACKING_ORG_ID == "env-org"

    def test_tracking_org_name_from_env(self, settings_with_db):
        with patch.dict(os.environ, {"TRACKING_ORG_NAME": "Env Org"}):
            assert settings_with_db.TRACKING_ORG_NAME == "Env Org"

    def test_env_priority_over_db_for_tracking(self, settings_with_db):
        settings_with_db._set_setting("TRACKING_API_KEY", "db-key")
        with patch.dict(os.environ, {"TRACKING_API_KEY": "env-key"}):
            assert settings_with_db.TRACKING_API_KEY == "env-key"


# =============================================================================
# save_tracking_config
# =============================================================================

class TestSaveTrackingConfig:

    def test_save_returns_true(self, settings_with_db):
        result = settings_with_db.save_tracking_config("key-1", "org-1", "Org Name")
        assert result is True

    def test_save_persists_values(self, settings_with_db):
        settings_with_db.save_tracking_config("key-abc", "org-xyz", "Acme Corp")
        assert settings_with_db.TRACKING_API_KEY == "key-abc"
        assert settings_with_db.TRACKING_ORG_ID == "org-xyz"
        assert settings_with_db.TRACKING_ORG_NAME == "Acme Corp"

    def test_save_with_defaults(self, settings_with_db):
        settings_with_db.save_tracking_config("key-only")
        assert settings_with_db.TRACKING_API_KEY == "key-only"
        assert settings_with_db.TRACKING_ORG_ID == ""
        assert settings_with_db.TRACKING_ORG_NAME == ""

    def test_save_db_error_returns_false(self, settings_with_db):
        with patch.object(settings_with_db, "_set_setting", side_effect=Exception("fail")):
            result = settings_with_db.save_tracking_config("key", "org", "name")
            assert result is False

    def test_save_overwrites_previous(self, settings_with_db):
        settings_with_db.save_tracking_config("old-key", "old-org", "Old")
        settings_with_db.save_tracking_config("new-key", "new-org", "New")
        assert settings_with_db.TRACKING_API_KEY == "new-key"
        assert settings_with_db.TRACKING_ORG_NAME == "New"


# =============================================================================
# get_tracking_config
# =============================================================================

class TestGetTrackingConfig:

    def test_structure(self, settings_with_db):
        cfg = settings_with_db.get_tracking_config()
        assert "api_key" in cfg
        assert "org_id" in cfg
        assert "org_name" in cfg
        assert "service_url" in cfg
        assert "enabled" in cfg

    def test_reflects_saved_values(self, settings_with_db):
        settings_with_db.save_tracking_config("k1", "o1", "N1")
        cfg = settings_with_db.get_tracking_config()
        assert cfg["api_key"] == "k1"
        assert cfg["org_id"] == "o1"
        assert cfg["org_name"] == "N1"

    def test_service_url_default(self, settings_with_db):
        cfg = settings_with_db.get_tracking_config()
        assert cfg["service_url"] == "http://localhost:8001/api/v1"

    def test_enabled_default_true(self, settings_with_db):
        cfg = settings_with_db.get_tracking_config()
        assert cfg["enabled"] is True

    def test_enabled_from_env(self, settings_with_db):
        with patch.dict(os.environ, {"TRACKING_ENABLED": "false"}):
            cfg = settings_with_db.get_tracking_config()
            assert cfg["enabled"] is False

    def test_service_url_from_env(self, settings_with_db):
        with patch.dict(os.environ, {"TRACKING_SERVICE_URL": "https://custom.url/api"}):
            cfg = settings_with_db.get_tracking_config()
            assert cfg["service_url"] == "https://custom.url/api"
