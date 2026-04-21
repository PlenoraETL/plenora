"""Contract test A1 — validate_storage_config (fail-fast boot check).

Copre la validazione di coerenza config per la storage policy introdotta
da A1 (orchestrate/phase_1_stabilization/a1_storage_policy.md §5). NON
testa il dispatch (commit B/C) ne' la raggiungibilita' del provider:
qui si blinda solo che il boot fallisca loud se la combinazione di
STORAGE_POLICY, CLOUD_PROVIDER, bucket e soglie non e' consistente.
"""

from __future__ import annotations

import logging

import pytest

from app.shared.cloud_backends import (
    StorageConfigError,
    validate_storage_config,
)


class _WarningCapturer(logging.Handler):
    """Handler locale per il test: caplog non raccoglie i log del namespace
    `app.*` perche' `app.utils.logger.get_logger("app")` setta propagate=False
    al primo import del logger applicativo."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clean_storage_env(monkeypatch):
    """Pulisce tutte le env coinvolte prima di ogni test: evita leak tra
    case e rende esplicito cosa il test sta dichiarando."""
    for name in (
        "STORAGE_POLICY",
        "CLOUD_PROVIDER",
        "BACKUP_BUCKET",
        "BACKUP_S3_BUCKET",
        "RESULT_S3_THRESHOLD_BYTES",
        "FILE_SOURCE_S3_THRESHOLD_BYTES",
        "RESULT_DB_HARD_CAP_BYTES",
        "LOG_ARCHIVE_ENABLED",
        "LOG_ARCHIVE_BUCKET",
        "LOG_ARCHIVE_KEY_PREFIX",
        "LOG_ARCHIVE_BATCH_ROWS",
        "AUDIT_RETENTION_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# =============================================================================
# 1. STORAGE_POLICY enum
# =============================================================================


def test_default_hybrid_requires_bucket(monkeypatch):
    """Senza env: default = hybrid, richiede bucket -> fallisce."""
    with pytest.raises(StorageConfigError, match="STORAGE_POLICY=hybrid"):
        validate_storage_config()


def test_invalid_policy_fails(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "ram")
    with pytest.raises(StorageConfigError, match="non valido"):
        validate_storage_config()


def test_policy_is_case_insensitive(monkeypatch):
    """DB / Db / db devono essere accettati."""
    monkeypatch.setenv("STORAGE_POLICY", "DB")
    validate_storage_config()  # no raise


# =============================================================================
# 2. Policy = db
# =============================================================================


def test_db_policy_no_bucket_passes(monkeypatch):
    """db mode non richiede bucket ne' provider cloud."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    validate_storage_config()


def test_db_policy_ignores_bucket_but_warns(monkeypatch):
    """bucket + provider cloud con policy=db -> warning non-blocking."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "some-bucket")

    mod_logger = logging.getLogger("app.shared.cloud_backends")
    capturer = _WarningCapturer()
    mod_logger.addHandler(capturer)
    try:
        validate_storage_config()
    finally:
        mod_logger.removeHandler(capturer)

    assert any("cut-paste" in m for m in capturer.messages)


def test_db_policy_rejects_negative_hard_cap(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("RESULT_DB_HARD_CAP_BYTES", "-1")
    with pytest.raises(StorageConfigError, match="RESULT_DB_HARD_CAP_BYTES"):
        validate_storage_config()


def test_db_policy_rejects_non_int_hard_cap(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("RESULT_DB_HARD_CAP_BYTES", "abc")
    with pytest.raises(StorageConfigError, match="non e' un intero"):
        validate_storage_config()


# =============================================================================
# 3. Policy = object_storage
# =============================================================================


def test_object_storage_requires_bucket(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    with pytest.raises(StorageConfigError, match="bucket"):
        validate_storage_config()


def test_object_storage_accepts_legacy_bucket_env(monkeypatch):
    """Boot passa se solo BACKUP_S3_BUCKET (legacy) e' settato: coerente
    col comportamento di get_object_storage che accetta entrambi."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "legacy-name")
    validate_storage_config()


def test_object_storage_rejects_provider_none(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    with pytest.raises(StorageConfigError, match="CLOUD_PROVIDER=none"):
        validate_storage_config()


# =============================================================================
# 4. Policy = hybrid
# =============================================================================


def test_hybrid_passes_with_bucket(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    validate_storage_config()


def test_hybrid_rejects_zero_threshold(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", "0")
    with pytest.raises(StorageConfigError, match="RESULT_S3_THRESHOLD_BYTES"):
        validate_storage_config()


def test_hybrid_rejects_non_int_threshold(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "big")
    with pytest.raises(StorageConfigError, match="FILE_SOURCE_S3_THRESHOLD_BYTES"):
        validate_storage_config()


def test_hybrid_hard_cap_guard_applies(monkeypatch):
    """Il hard cap DB e' validato anche in hybrid (guarda il ramo DB)."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("RESULT_DB_HARD_CAP_BYTES", "0")
    with pytest.raises(StorageConfigError, match="RESULT_DB_HARD_CAP_BYTES"):
        validate_storage_config()


# =============================================================================
# 5. CLOUD_PROVIDER enum
# =============================================================================


def test_unknown_provider_fails(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "oracle")
    with pytest.raises(StorageConfigError, match="sconosciuto"):
        validate_storage_config()


@pytest.mark.parametrize("provider", ["minio", "azure", "gcp"])
def test_known_but_unimplemented_provider_fails(monkeypatch, provider):
    """minio/azure/gcp sono roadmap ma non implementati: devono failare
    al boot anche in policy=db, per evitare che qualcuno li scriva
    aspettandosi che funzionino."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", provider)
    with pytest.raises(StorageConfigError, match="non ancora implementato"):
        validate_storage_config()


# =============================================================================
# 6. LOG_ARCHIVE_* fail-fast (O-ARC Batch 2, ADR-0014 §A1-§A5)
# =============================================================================


def test_log_archive_disabled_skips_all_checks(monkeypatch):
    """Con ENABLED=false il ramo non scatta: tutte le LOG_ARCHIVE_* /
    AUDIT_RETENTION_DAYS invalide sono ignorate (pattern §A4 preparazione
    pre-flip: env settate nel .env prima del flip non devono rompere boot)."""
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "false")
    monkeypatch.setenv("LOG_ARCHIVE_KEY_PREFIX", "no-trailing-slash")
    monkeypatch.setenv("LOG_ARCHIVE_BATCH_ROWS", "-1")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "not-an-int")
    validate_storage_config()  # no raise


def test_log_archive_enabled_rejects_policy_db(monkeypatch):
    monkeypatch.setenv("STORAGE_POLICY", "db")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    with pytest.raises(StorageConfigError, match="STORAGE_POLICY=db"):
        validate_storage_config()


def test_log_archive_enabled_rejects_provider_none(monkeypatch):
    """ENABLED=true con CLOUD_PROVIDER=none: boot fallisce. Il check A1
    precedente copre gia' il caso policy=object_storage/hybrid+provider=none;
    la regola LOG_ARCHIVE (regola 2) e' difensiva — il test documenta
    l'invariante combinata senza pinnare il layer che raise."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "none")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "archive-b")
    with pytest.raises(StorageConfigError, match="CLOUD_PROVIDER=none"):
        validate_storage_config()


def test_log_archive_enabled_requires_bucket(monkeypatch):
    """LOG_ARCHIVE_BUCKET unset con ENABLED=true -> fail (intenzionalmente
    fuori da Settings.DEFAULTS, required-if-enabled per design §A3)."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    with pytest.raises(StorageConfigError, match="LOG_ARCHIVE_BUCKET"):
        validate_storage_config()


def test_log_archive_enabled_rejects_bucket_collides_with_backup(monkeypatch):
    """Bucket archive == BACKUP_BUCKET: IAM boundary richiede distinzione
    (O-ARC §5.4, ADR-0014 §A3)."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "shared-bucket")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "shared-bucket")
    with pytest.raises(StorageConfigError, match="coincide con"):
        validate_storage_config()


def test_log_archive_enabled_rejects_invalid_key_prefix(monkeypatch):
    """KEY_PREFIX settato ma senza '/' finale: fail (coerente con
    log_archive._build_object_key)."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "archive-b")
    monkeypatch.setenv("LOG_ARCHIVE_KEY_PREFIX", "logs-archive")
    with pytest.raises(StorageConfigError, match="LOG_ARCHIVE_KEY_PREFIX"):
        validate_storage_config()


def test_log_archive_enabled_rejects_non_positive_batch_rows(monkeypatch):
    """BATCH_ROWS=0 -> fail (riusa _check_positive_int)."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "archive-b")
    monkeypatch.setenv("LOG_ARCHIVE_BATCH_ROWS", "0")
    with pytest.raises(StorageConfigError, match="LOG_ARCHIVE_BATCH_ROWS"):
        validate_storage_config()


def test_log_archive_enabled_rejects_audit_retention_non_int(monkeypatch):
    """AUDIT_RETENTION_DAYS non parseabile come int -> fail."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "archive-b")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "forever")
    with pytest.raises(StorageConfigError, match="AUDIT_RETENTION_DAYS"):
        validate_storage_config()


def test_log_archive_enabled_rejects_audit_retention_negative(monkeypatch):
    """AUDIT_RETENTION_DAYS < 0 -> fail; 0 resta ammesso = retention
    disabilitata (simmetrico a SCHEDULE_LOGS_RETENTION_DAYS)."""
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("LOG_ARCHIVE_BUCKET", "archive-b")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "-1")
    with pytest.raises(StorageConfigError, match="AUDIT_RETENTION_DAYS"):
        validate_storage_config()


@pytest.mark.parametrize("truthy_value", ["true", "1", "yes", "TRUE", "Yes"])
def test_log_archive_enabled_truthy_variants(monkeypatch, truthy_value):
    """Parsing ENABLED: `.strip().lower()` accetta true/1/yes case-insensitive.
    Forza un errore noto (bucket mancante) per verificare che il ramo
    scatta per ciascuna variante."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("BACKUP_BUCKET", "b")
    monkeypatch.setenv("LOG_ARCHIVE_ENABLED", truthy_value)
    with pytest.raises(StorageConfigError, match="LOG_ARCHIVE_BUCKET"):
        validate_storage_config()
