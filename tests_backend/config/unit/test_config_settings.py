"""
tests/config/unit/test_config_settings.py
=========================================
Unit test per la classe Settings: defaults, env override,
conversione tipi, computed properties, directory helpers, validate_startup.
"""

import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Import sotto test
# ---------------------------------------------------------------------------
from app.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_settings(**env_overrides) -> Settings:
    """Crea un Settings con env pulito (solo i valori in env_overrides)."""
    with patch.dict(os.environ, env_overrides, clear=False):
        return Settings()


# =============================================================================
# _converti_tipo_env
# =============================================================================

class TestConvertiTipoEnv:
    """Verifica la coercizione dei valori da stringa env al tipo del default."""

    def test_bool_true_variants(self):
        s = Settings()
        for val in ("true", "True", "1", "yes"):
            assert s._converti_tipo_env("TRACKING_ENABLED", val) is True

    def test_bool_false_variants(self):
        s = Settings()
        for val in ("false", "0", "no", "anything"):
            assert s._converti_tipo_env("TRACKING_ENABLED", val) is False

    def test_int_conversion(self):
        s = Settings()
        assert s._converti_tipo_env("DB_CONNECT_TIMEOUT", "42") == 42
        assert isinstance(s._converti_tipo_env("DB_CONNECT_TIMEOUT", "42"), int)

    def test_int_conversion_invalid_raises(self):
        s = Settings()
        with pytest.raises(ValueError):
            s._converti_tipo_env("DB_CONNECT_TIMEOUT", "not_a_number")

    def test_float_conversion(self):
        s = Settings()
        assert s._converti_tipo_env("TRACKING_TIMEOUT", "12.5") == 12.5
        assert isinstance(s._converti_tipo_env("TRACKING_TIMEOUT", "12.5"), float)

    def test_float_conversion_invalid_raises(self):
        s = Settings()
        with pytest.raises(ValueError):
            s._converti_tipo_env("TRACKING_TIMEOUT", "abc")

    def test_str_passthrough(self):
        s = Settings()
        result = s._converti_tipo_env("CELERY_BROKER_URL", "redis://localhost")
        assert result == "redis://localhost"

    def test_unknown_key_returns_str(self):
        """Chiave non in DEFAULTS -> nessun default_value -> str passthrough."""
        s = Settings()
        assert s._converti_tipo_env("UNKNOWN_KEY_XYZ", "hello") == "hello"


# =============================================================================
# get() — priorita' env > DEFAULTS > default param
# =============================================================================

class TestGet:

    def test_default_from_class(self):
        s = Settings()
        assert s.get("DB_CONNECT_TIMEOUT") == 10

    def test_env_overrides_default(self):
        with patch.dict(os.environ, {"DB_CONNECT_TIMEOUT": "99"}):
            s = Settings()
            assert s.get("DB_CONNECT_TIMEOUT") == 99

    def test_fallback_to_default_param(self):
        s = Settings()
        assert s.get("NON_EXISTENT_KEY", "fallback") == "fallback"

    def test_fallback_none_when_missing(self):
        s = Settings()
        assert s.get("NON_EXISTENT_KEY") is None

    def test_env_bool_override(self):
        with patch.dict(os.environ, {"TRACKING_ENABLED": "false"}):
            s = Settings()
            assert s.get("TRACKING_ENABLED") is False

    def test_env_float_override(self):
        with patch.dict(os.environ, {"TRACKING_TIMEOUT": "5.5"}):
            s = Settings()
            val = s.get("TRACKING_TIMEOUT")
            assert val == 5.5


# =============================================================================
# __getattr__
# =============================================================================

class TestGetattr:

    def test_attribute_access_resolves_default(self):
        s = Settings()
        assert s.DB_QUERY_TIMEOUT == 30

    def test_attribute_access_with_env(self):
        with patch.dict(os.environ, {"API_TIMEOUT": "60"}):
            s = Settings()
            assert s.API_TIMEOUT == 60

    def test_private_attr_raises(self):
        s = Settings()
        with pytest.raises(AttributeError):
            _ = s._nonexistent_private

    def test_missing_key_returns_none(self):
        s = Settings()
        assert s.TOTALLY_RANDOM_KEY is None


# =============================================================================
# set()
# =============================================================================

class TestSet:

    def test_set_puts_in_env(self):
        s = Settings()
        with patch.dict(os.environ, {}, clear=False):
            s.set("MY_CUSTOM_VAR", "hello")
            assert os.environ["MY_CUSTOM_VAR"] == "hello"


# =============================================================================
# Computed directory properties
# =============================================================================

class TestDirectoryProperties:

    def test_data_dir_default(self):
        s = Settings()
        assert s.DATA_DIR == Path("./data")

    def test_data_dir_from_env(self):
        with patch.dict(os.environ, {"DATA_DIR": "/custom/path"}):
            s = Settings()
            assert s.DATA_DIR == Path("/custom/path")

    def test_logs_dir_equals_data_dir(self):
        s = Settings()
        assert s.LOGS_DIR == s.DATA_DIR

    def test_temp_dir_creates_directory(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            temp = s.TEMP_DIR
            assert temp == tmp_path / "temp"
            assert temp.exists()

    def test_connections_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.CONNECTIONS_DIR == tmp_path / "connections"

    def test_results_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.RESULTS_DIR == tmp_path / "results"

    def test_api_connections_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.API_CONNECTIONS_DIR == tmp_path / "api_connections"

    def test_uploads_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.UPLOADS_DIR == tmp_path / "uploads"

    def test_files_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.FILES_DIR == tmp_path / "files"

    def test_catalog_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.CATALOG_DIR == tmp_path / "catalog"

    def test_tasks_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.TASKS_DIR == tmp_path / "tasks"

    def test_scheduler_logs_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.SCHEDULER_LOGS_DIR == tmp_path / "scheduler_logs"

    def test_schedules_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.SCHEDULES_DIR == tmp_path / "schedules"

    def test_arcgis_connections_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            assert s.ARCGIS_CONNECTIONS_DIR == tmp_path / "arcgis_connections"


# =============================================================================
# _directory helper
# =============================================================================

class TestDirectoryHelper:

    def test_creates_when_create_true(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s._directory("mysubdir", create=True)
            assert d.exists()
            assert d == tmp_path / "mysubdir"

    def test_no_create_when_false(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s._directory("absent", create=False)
            assert not d.exists()

    def test_custom_base(self, tmp_path):
        s = Settings()
        custom = tmp_path / "custom_base"
        custom.mkdir()
        d = s._directory("sub", base=custom, create=True)
        assert d == custom / "sub"
        assert d.exists()


# =============================================================================
# Helper percorsi
# =============================================================================

class TestHelperPercorsi:

    def test_get_project_output_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s.get_project_output_dir("proj-456")
            assert d == tmp_path / "results" / "proj-456"

    def test_get_connection_file(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            f = s.get_connection_file("conn-1")
            assert f == tmp_path / "connections" / "conn-1.json"

    def test_get_api_connection_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s.get_api_connection_dir("api-1")
            assert d == tmp_path / "api_connections" / "api-1"

    def test_get_file_source_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s.get_file_source_dir("file-1")
            assert d == tmp_path / "files" / "file-1"

    def test_get_task_dir(self, tmp_path):
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            d = s.get_task_dir("task-1")
            assert d == tmp_path / "tasks" / "task-1"


# =============================================================================
# CORS_ORIGINS_LIST
# =============================================================================

class TestCorsOriginsList:

    def test_default_cors_origins(self):
        s = Settings()
        origins = s.CORS_ORIGINS_LIST
        assert isinstance(origins, list)
        assert "http://localhost:3000" in origins
        assert "http://localhost:5173" in origins

    def test_custom_cors_via_env(self):
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://a.com, https://b.com"}):
            s = Settings()
            origins = s.CORS_ORIGINS_LIST
            assert origins == ["https://a.com", "https://b.com"]

    def test_empty_cors(self):
        with patch.dict(os.environ, {"CORS_ORIGINS": ""}):
            s = Settings()
            assert s.CORS_ORIGINS_LIST == []


# =============================================================================
# DEFAULTS completeness
# =============================================================================

class TestDefaults:

    def test_defaults_contains_expected_keys(self):
        expected = [
            "DATA_DIR", "LOG_DB_FILE", "LOG_DB_MAX_ROWS",
            "CORS_ORIGINS", "DB_CONNECT_TIMEOUT", "DB_QUERY_TIMEOUT",
            "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
            "TRACKING_ENABLED", "TRACKING_SERVICE_URL",
            "API_TIMEOUT", "MAX_PREVIEW_ROWS",
            "SUPPORTED_DB_TYPES", "SUPPORTED_OUTPUT_FORMATS",
            "SUPPORTED_FILE_FORMATS", "SUPPORTED_PROJECT_TYPES",
        ]
        for k in expected:
            assert k in Settings.DEFAULTS, f"Chiave mancante: {k}"

    def test_supported_db_types_is_list(self):
        s = Settings()
        val = s.get("SUPPORTED_DB_TYPES")
        assert isinstance(val, list)
        assert "postgres" in val


# =============================================================================
# validate_startup
# =============================================================================

class TestValidateStartup:

    def test_validate_success(self, tmp_path):
        """validate_startup non solleva errori con DATA_DIR scrivibile."""
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            # Non dovrebbe sollevare eccezioni
            s.validate_startup()

    def test_validate_unwritable_dir_exits(self, tmp_path):
        """Se DATA_DIR non e' scrivibile, validate_startup fa SystemExit(1)."""
        fake_dir = tmp_path / "readonly_dir"
        fake_dir.mkdir()

        with patch.dict(os.environ, {"DATA_DIR": str(fake_dir)}):
            s = Settings()
            # Rendiamo il touch fallire
            with patch.object(Path, "touch", side_effect=PermissionError("denied")):
                with pytest.raises(SystemExit) as exc_info:
                    s.validate_startup()
                assert exc_info.value.code == 1

    def test_validate_creates_data_dir(self, tmp_path):
        new_dir = tmp_path / "brand_new"
        with patch.dict(os.environ, {"DATA_DIR": str(new_dir)}):
            s = Settings()
            s.validate_startup()
            assert new_dir.exists()

    def test_validate_cleans_temp(self, tmp_path):
        """validate_startup rimuove contenuti di temp/."""
        with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
            s = Settings()
            temp = tmp_path / "temp"
            temp.mkdir()
            (temp / "stale_file.txt").write_text("old")
            s.validate_startup()
            # Il file dovrebbe essere stato rimosso
            assert not (temp / "stale_file.txt").exists()
