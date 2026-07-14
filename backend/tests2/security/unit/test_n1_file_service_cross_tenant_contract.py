"""
Contract test N1 — FileSourceService fail-closed cross-tenant guard.

Scenario di sicurezza:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture
def svc():
    """FileSourceService con mock dei collaboratori per test isolato."""
    from app.core.files.service import FileSourceService
    s = FileSourceService.__new__(FileSourceService)
    s._logger = MagicMock()
    return s


def _file_source(group_id="A"):
    """Mock di FileSource con minimal fields usati dal guard."""
    return SimpleNamespace(
        id="f_test_1",
        group_id=group_id,
        name="test",
        format="csv",
        original_filename="test.csv",
        layer=None,
        sheet=None,
    )


# _assert_visible core


def test_assert_visible_passes_when_group_matches(svc):
    """Owner del file passa immediatamente (path hot)."""
    with patch.object(svc, "get_file_source", return_value=_file_source(group_id="A")):
        # Non solleva
        svc._assert_visible("f1", "A")


def test_assert_visible_raises_on_group_mismatch_without_share(svc):
    """Tenant B tenta accesso a file di tenant A senza share attivo."""
    with patch.object(svc, "get_file_source", return_value=_file_source(group_id="A")):
        with patch(
            "app.core.files.service.is_resource_visible", return_value=False
        ), patch(
            "app.core.files.service.is_shareable", return_value=True
        ):
            with pytest.raises(PermissionError, match="non accessibile al gruppo 'B'"):
                svc._assert_visible("f1", "B")


def test_assert_visible_allows_explicit_share(svc):
    """File condiviso con tenant B (via *_shares) → accesso OK."""
    with patch.object(svc, "get_file_source", return_value=_file_source(group_id="A")):
        with patch(
            "app.core.files.service.is_resource_visible", return_value=True
        ), patch(
            "app.core.files.service.is_shareable", return_value=True
        ):
            # Non solleva
            svc._assert_visible("f1", "B")


def test_assert_visible_fails_closed_on_empty_group_id(svc):
    """expected_group_id mancante/vuoto → PermissionError (fail-closed).
    Evita che un caller sbadato bypassi la validazione passando None."""
    with pytest.raises(PermissionError, match="expected_group_id mancante"):
        svc._assert_visible("f1", "")


def test_assert_visible_superadmin_bypass_with_sentinel(svc):
    """SUPERADMIN_BYPASS sentinel '*' → accesso OK + log info."""
    from app.core.files.service import FileSourceService
    with patch.object(svc, "get_file_source", return_value=_file_source(group_id="A")):
        svc._assert_visible("f1", FileSourceService.SUPERADMIN_BYPASS)
    # Log info emesso
    svc._logger.info.assert_called()
    log_msg = svc._logger.info.call_args[0][0]
    assert "superadmin bypass" in log_msg.lower()


def test_assert_visible_raises_valueerror_on_missing_file(svc):
    """file_id inesistente → ValueError (distinto da PermissionError
    per distinguere 404 da 403 nel router)."""
    with patch.object(svc, "get_file_source", return_value=None):
        with pytest.raises(ValueError, match="non trovato"):
            svc._assert_visible("nonexistent", "A")


def test_assert_visible_fails_closed_on_sharing_exception(svc):
    """Se is_resource_visible solleva (DB glitch, schema share
    corrotto), NON passiamo — FAIL-CLOSED per default."""
    with patch.object(svc, "get_file_source", return_value=_file_source(group_id="A")):
        with patch(
            "app.core.files.service.is_resource_visible",
            side_effect=RuntimeError("share table corrupt"),
        ), patch(
            "app.core.files.service.is_shareable", return_value=True
        ):
            with pytest.raises(PermissionError):
                svc._assert_visible("f1", "B")


# load_dataframe wiring


def test_load_dataframe_requires_expected_group_id(svc):
    """Il metodo pubblico DEVE chiamare _assert_visible. Se group
    mismatch, PermissionError prima ancora di leggere il blob."""
    fs = _file_source(group_id="A")
    with patch.object(svc, "get_file_source", return_value=fs):
        with patch(
            "app.core.files.service.is_resource_visible", return_value=False
        ), patch(
            "app.core.files.service.is_shareable", return_value=True
        ):
            with pytest.raises(PermissionError):
                svc.load_dataframe("f1", expected_group_id="B")


def test_load_dataframe_passes_when_group_matches_owner(svc):
    """Group match: _assert_visible passa, poi load_dataframe procede
    (ci fermiamo al blob lookup, sufficiente per confermare il check
    non blocca)."""
    fs = _file_source(group_id="A")
    with patch.object(svc, "get_file_source", return_value=fs), patch.object(
        svc,
        "_open_blob_spool",
        side_effect=FileNotFoundError("missing object"),
    ):
        with pytest.raises(ValueError, match="Nessun dato"):
            svc.load_dataframe("f1", expected_group_id="A")


# File ID entropy


def test_file_id_generator_produces_32_hex_uuid():
    """N1: file_id oggi contiene uuid_part = uuid.uuid4().hex (32 char).
    Previous behavior: [:8] = ~10^9 combinazioni → brute-force fattibile.
    Current contract: 2^128 → infeasible.
    """
    from app.core.files.service import DefaultTimeProvider
    tp = DefaultTimeProvider()
    uuid_part = tp.generate_uuid()
    assert len(uuid_part) == 32, (
        f"REGRESSIONE N1: uuid_part e' {len(uuid_part)} char "
        f"(atteso 32 hex). Brute-force di file_id diventa fattibile."
    )
    # Deve essere hex valido
    int(uuid_part, 16)  # non deve sollevare


def test_file_id_format_is_opaque_no_name_no_timestamp():
    """N1 follow-up (opzione B): il format oggi e' `file_{uuid}`,
    niente name ne' timestamp incorporati nell'ID. Evita metadata
    leakage via ID in log/URL trafilati (es. non si puo' dedurre
    dalle ID 'file_salary_data_20260215_...abcd' il nome sensibile
    e il giorno di upload)."""
    from app.core.files.service import FileIdGenerator

    sensitive_name = "sensitive_salary_data"
    sensitive_ts = "20260215_143022"
    uuid_part = "0123456789abcdef0123456789abcdef"

    file_id = FileIdGenerator.generate(sensitive_name, sensitive_ts, uuid_part)

    # Non deve contenere il nome
    assert sensitive_name not in file_id, (
        f"REGRESSIONE N1-B: filename '{sensitive_name}' ancora "
        f"presente nell'ID ({file_id}) → metadata leakage."
    )
    # Non deve contenere il timestamp
    assert sensitive_ts not in file_id, (
        f"REGRESSIONE N1-B: timestamp '{sensitive_ts}' ancora "
        f"presente nell'ID ({file_id}) → metadata leakage."
    )
    # Formato atteso: file_<32-hex>
    assert file_id.startswith("file_"), f"Prefix mancante: {file_id}"
    assert file_id == f"file_{uuid_part}", (
        f"Formato inatteso: {file_id}"
    )
