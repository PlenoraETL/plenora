"""Contract test BE-CONFIG-001E — DEDUP direct env reads su chiavi gia' in DEFAULTS.

Coperti:

1. `INTERMEDIATE_ARTIFACT_PREFIX` letto via `settings.get(...)` in
   `intermediate/artifact_writer.py` (era duplicato `os.getenv`).
2. Le 3 chiavi `LOG_ARCHIVE_KEY_PREFIX` / `LOG_ARCHIVE_BATCH_ROWS` /
   `AUDIT_RETENTION_DAYS` restano con direct `os.getenv` nel
   boot-time validator `validate_storage_config` per fail-fast su env
   malformata (vedi commento + ADR-0021 allowlist). Il runtime path
   continua a leggerle via property `settings.LOG_ARCHIVE_*` /
   `settings.AUDIT_RETENTION_DAYS`.
3. La migrazione `INTERMEDIATE_ARTIFACT_PREFIX` rispetta il default
   `"intermediate"` e l'override env via `_converti_tipo_env`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# ---------------------------------------------------------------------------
# Default present + tipato per la chiave migrata (INTERMEDIATE_ARTIFACT_PREFIX)
# ---------------------------------------------------------------------------

def test_intermediate_artifact_prefix_default_present_and_typed():
    assert "INTERMEDIATE_ARTIFACT_PREFIX" in Settings.DEFAULTS
    val = Settings.DEFAULTS["INTERMEDIATE_ARTIFACT_PREFIX"]
    assert isinstance(val, str)
    assert val == "intermediate"


def test_intermediate_artifact_prefix_env_override(monkeypatch):
    monkeypatch.setenv("INTERMEDIATE_ARTIFACT_PREFIX", "custom-x/y")
    assert settings.get("INTERMEDIATE_ARTIFACT_PREFIX") == "custom-x/y"


# ---------------------------------------------------------------------------
# Source-bound: artifact_writer ora legge via settings.get
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_artifact_writer_reads_intermediate_prefix_via_settings():
    src = _read(_BACKEND_APP / "core" / "intermediate" / "artifact_writer.py")
    assert "_settings.get(\"INTERMEDIATE_ARTIFACT_PREFIX\")" in src
    assert "os.getenv(\"INTERMEDIATE_ARTIFACT_PREFIX" not in src


def test_artifact_writer_does_not_import_os_anymore():
    """`os` import era usato solo per `os.getenv`; rimosso post-001E."""
    src = _read(_BACKEND_APP / "core" / "intermediate" / "artifact_writer.py")
    # Non deve esserci `import os` come modulo top-level
    for line in src.splitlines():
        stripped = line.strip()
        if stripped == "import os" or stripped.startswith("import os "):
            pytest.fail(
                f"`import os` ancora presente in artifact_writer.py: {line!r}"
            )


# ---------------------------------------------------------------------------
# Validator boot-time: i 3 cloud_backends keys restano direct os.getenv
# (intentional, ADR-0021 allowlist). Verifica che NON siano stati
# silenziosamente migrati a settings.get.
# ---------------------------------------------------------------------------

def test_validate_storage_config_keeps_os_getenv_for_log_archive_key_prefix():
    src = _read(_BACKEND_APP / "shared" / "cloud_backends.py")
    # Il validator deve continuare a leggere il raw env per fail-fast
    assert "os.getenv(\"LOG_ARCHIVE_KEY_PREFIX\")" in src


def test_validate_storage_config_keeps_os_getenv_for_audit_retention_days():
    src = _read(_BACKEND_APP / "shared" / "cloud_backends.py")
    assert "os.getenv(\"AUDIT_RETENTION_DAYS\")" in src


def test_validate_storage_config_check_positive_int_uses_os_getenv():
    """Il helper `_check_positive_int` legge direttamente l'env per
    distinguere unset vs malformed. Sostituirlo con settings.get
    romperebbe il fail-fast su typo (es. LOG_ARCHIVE_BATCH_ROWS=abc)."""
    src = _read(_BACKEND_APP / "shared" / "cloud_backends.py")
    assert "raw = os.getenv(name)" in src


def test_validator_documents_intentional_direct_read():
    """Il commento BE-CONFIG-001E + ADR-0021 deve esserci per
    documentare la scelta deliberata."""
    src = _read(_BACKEND_APP / "shared" / "cloud_backends.py")
    assert "BE-CONFIG-001E + ADR-0021" in src


# ---------------------------------------------------------------------------
# Runtime path: le 3 chiavi log archive sono lette via property in
# beat_scheduler. Verifica la parita' tra Settings.DEFAULTS e property.
# ---------------------------------------------------------------------------

def test_log_archive_runtime_path_via_settings_properties():
    """Beat scheduler usa `settings.LOG_ARCHIVE_*` / `settings.AUDIT_RETENTION_DAYS`
    properties (gia' migrate). Lo verifichiamo via accesso attribute
    su una istanza fresca."""
    assert isinstance(settings.LOG_ARCHIVE_KEY_PREFIX, str)
    assert settings.LOG_ARCHIVE_KEY_PREFIX.endswith("/")
    assert isinstance(settings.LOG_ARCHIVE_BATCH_ROWS, int)
    assert settings.LOG_ARCHIVE_BATCH_ROWS > 0
    assert isinstance(settings.AUDIT_RETENTION_DAYS, int)
    assert settings.AUDIT_RETENTION_DAYS >= 0


def test_log_archive_property_honors_env_override(monkeypatch):
    """Override env per il runtime path (non per il boot validator) via
    property — coerente con BE-CONFIG-001A/B/C/D pattern."""
    monkeypatch.setenv("LOG_ARCHIVE_KEY_PREFIX", "custom-archive/")
    monkeypatch.setenv("LOG_ARCHIVE_BATCH_ROWS", "5000")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "180")
    assert settings.LOG_ARCHIVE_KEY_PREFIX == "custom-archive/"
    assert settings.LOG_ARCHIVE_BATCH_ROWS == 5000
    assert settings.AUDIT_RETENTION_DAYS == 180
