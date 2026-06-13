"""Sessione 3 contract test: 5 P1 backend security/logging.

Bug coperti:
1. admin_runtime_config P1 — DATABASE_URL/CELERY_BROKER_URL non redatti.
2. admin_system_status P1 — WorkerDetail.hostname/pid leak.
3. admin_system_status P1 — GET /admin/system-status senza @audit_action.
4. auth/auth.py P1 — /change-password senza rate limit.
5. token_blacklist P1 — fail-open silenzioso a WARNING (deve essere ERROR + tag SECURITY).
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from tests2.shared._ast_identifiers import all_identifiers, call_has_keyword, uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


# Path 1: URL redaction

def test_database_url_redacted_with_credentials():
    """DATABASE_URL con credenziali embedded → redaction strict
    (netloc completo mascherato, scheme + db name preservati)."""
    from app.core.admin_runtime_config.service import _serialize_value

    result = _serialize_value(
        "DATABASE_URL",
        "postgresql://plenora:supersecret@db.internal:5432/plenora",
    )
    assert "supersecret" not in str(result), "password leak"
    assert "db.internal" not in str(result), "host leak"
    assert "5432" not in str(result), "port leak"
    # Lo scheme + db name devono restare per forensics
    assert str(result).startswith("postgresql://")
    assert "/plenora" in str(result)


def test_database_url_redacted_without_credentials():
    """DATABASE_URL senza user:pwd@ → redaction strict comunque
    (host:port interno e' info sensibile)."""
    from app.core.admin_runtime_config.service import _serialize_value

    result = _serialize_value(
        "DATABASE_URL",
        "postgresql://internal-pg:5432/plenora",
    )
    assert "internal-pg" not in str(result), (
        "host interno esposto pre-fix: _redact_url no-op su URL senza @"
    )
    assert "5432" not in str(result), "port interno esposto"
    assert str(result).startswith("postgresql://")


def test_celery_broker_url_redacted():
    """CELERY_BROKER_URL con eventuale auth password → redaction."""
    from app.core.admin_runtime_config.service import _serialize_value

    result = _serialize_value(
        "CELERY_BROKER_URL",
        "redis://:redis-password@redis.internal:6379/0",
    )
    assert "redis-password" not in str(result)
    assert "redis.internal" not in str(result)
    assert str(result).startswith("redis://")
    assert "/0" in str(result)  # DB number preserved


def test_celery_result_backend_redacted():
    """CELERY_RESULT_BACKEND idem (stessa famiglia)."""
    from app.core.admin_runtime_config.service import _serialize_value

    result = _serialize_value(
        "CELERY_RESULT_BACKEND",
        "redis://redis.internal:6379/1",
    )
    assert "redis.internal" not in str(result)


def test_url_redact_keys_contains_known_targets():
    """Sanity: il modulo espone _URL_REDACT_KEYS con almeno DATABASE_URL,
    CELERY_BROKER_URL, CELERY_RESULT_BACKEND."""
    from app.core.admin_runtime_config.service import _URL_REDACT_KEYS

    expected = {"DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"}
    assert expected.issubset(set(_URL_REDACT_KEYS))


# Path 2 (storico): WorkerDetail.hostname/pid exclude — rimosso in Plenora 2.0
# Fase 5. Senza Celery non c'e' piu' topologia worker (hostname/pid) da
# esporre nel payload admin: il blocco workers e' ora derivato da PostgreSQL
# (solo il conteggio task in esecuzione), quindi il vettore di leak non esiste.


# Path 3: audit_action su system-status

def test_get_system_status_has_audit_action_decorator():
    """`@audit_action(action="admin.system_status.read")` deve essere
    presente sul router GET. Pre-fix mancava — accesso non lascia
    traccia forensica."""
    import inspect as _inspect
    from app.core.admin_system_status import router as ss_router

    src = _inspect.getsource(ss_router)
    # Anti-regression: il decorator deve essere presente con action canonica
    assert call_has_keyword(src, "audit_action", "action"), "audit_action decorator missing"
    # RAW: string literal — valore dell'action, non un identificatore
    assert "admin.system_status.read" in src, (
        "audit_action action name non canonico — atteso "
        "'admin.system_status.read'"
    )


# Path 4: change-password rate limit

def test_change_password_calls_rate_limit_check():
    """change-password deve invocare _check_rate_limit + _record_failed_login
    su credenziali errate. Pre-fix: nessun limit → brute-force su old_password."""
    from app.core.auth import auth as auth_module

    src = inspect.getsource(auth_module.change_password)

    assert "_check_rate_limit" in all_identifiers(textwrap.dedent(src)), (
        "change_password deve chiamare _check_rate_limit(client_ip) "
        "come fa /login (stesso threat model: brute-force)"
    )
    assert "_record_failed_login" in all_identifiers(textwrap.dedent(src)), (
        "change_password deve registrare i failed attempt per "
        "trigger del rate limit progressive"
    )


# Path 5: token_blacklist log livello security

def test_token_blacklist_read_fail_logs_at_error_level():
    """Quando Redis va giu', `is_jti_blacklisted` deve loggare a
    ERROR (non WARNING) con tag [SECURITY] per visibilita' operativa.
    Pre-fix: log a WARNING, missato nel rumore generale."""
    from app.core.security import token_blacklist

    src = inspect.getsource(token_blacklist.is_jti_blacklisted)

    # logger.error invece di logger.warning per il fail path
    assert uses_attribute(textwrap.dedent(src), "logger", "error"), (
        "is_jti_blacklisted deve loggare il fail di Redis read a "
        "livello ERROR (era WARNING pre-fix)"
    )
    # RAW: string literal — tag nel messaggio di log, non un identificatore
    assert "[SECURITY]" in src, (
        "Il messaggio deve avere tag [SECURITY] per visibilita' "
        "in alerting/monitoring"
    )
