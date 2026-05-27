"""
Regression guards per `_resolve_file_source_id_bytes` fail-closed su
errori tecnici di lookup DB/object storage.
"""


# P0 (2026-05-26): migrato a psycopg3 (psycopg2 retirato).
import psycopg
import pytest

import app.core.files.db as file_sources_db
# Patch del binding sul consumer (source_loading), non sull'origine:
# `source_loading` fa `from app.core.auth.visibility import is_resource_visible`
# all'import time, quindi monkeypatch dell'origine non lo intercetta.
import app.core.files.source_loading as sharing_mod
from app.core.arricchisci.execution_context import ArricchisciExecutionContext
from app.core.files.exceptions import FileSourceResolutionError


# BE-GO-READY-ORCHESTRATION-ENGINE-010: il fallback singleton
# `_current_group_id` per la catena `file_source` e' stato rimosso
# da `_extract_file_source_group_id`. Questi test ora propagano un
# ctx tipato come fa il codice di produzione
# (`_build_rest_file_multipart` linea 536 e
# `_resolve_file_source_path` linea 1144 → `ctx=ctx`).
_TEST_CTX = ArricchisciExecutionContext(
    project_id="p-test",
    group_id="grp-test",
    preview_limit=None,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.regression,
]


def _bypass_security(monkeypatch, api_service):
    """Allinea il pre-check security per lasciar entrare l'esecuzione
    nel corpo di `_resolve_file_source_id_bytes`:

    - `is_resource_visible` mockato a True (cross-tenant check ok).

    BE-GO-READY-ORCHESTRATION-ENGINE-010: i test ora passano `ctx`
    esplicito alle chiamate `_resolve_file_source_id_bytes(...,
    ctx=_TEST_CTX)` invece di settare `api_service._current_group_id`.
    Il fallback singleton e' stato rimosso dalla catena file_source.
    """
    monkeypatch.setattr(sharing_mod, "is_resource_visible", lambda *a, **kw: True)


def test_returns_none_on_missing_metadata(api_service, monkeypatch):
    """Regression guard (vincolo 7 del checkpoint): file_source
    inesistente in DB -> return None. E' ramo dati legittimo, non
    errore. Se the fail-closed resolver contract trasformasse questo caso in eccezione, il caller
    `_build_rest_file_multipart` ramo extra_input `required=False`
    inizierebbe a fallire su condizioni normali ("extra file
    opzionale assente"), cambiando comportamento silenziosamente
    rispetto al Previous behavior.
    """
    _bypass_security(monkeypatch, api_service)
    monkeypatch.setattr(file_sources_db, "get_file_source", lambda *a, **kw: None)

    result = api_service._resolve_file_source_id_bytes("fs-absent", ctx=_TEST_CTX)

    assert result is None


def test_returns_none_on_empty_blob(api_service, monkeypatch):
    """Regression guard (vincolo 7): metadata ok ma blob NULL ->
    return None. file_source listato in DB ma `data` non popolata
    (race di upload, bug di save) e' ramo dati legittimo, non errore
    tecnico. La seconda query DB deve comunque essere invocata per
    arrivare a questo ramo.
    """
    _bypass_security(monkeypatch, api_service)

    calls = {"n": 0}

    def _get(fs_id, include_data=False):
        calls["n"] += 1
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        return {"data": None}

    monkeypatch.setattr(file_sources_db, "get_file_source", _get)

    result = api_service._resolve_file_source_id_bytes("fs-empty", ctx=_TEST_CTX)

    assert result is None
    assert calls["n"] == 2, "seconda query (blob) deve essere invocata"


def test_raises_on_metadata_lookup_db_error(api_service, monkeypatch):
    """`psycopg2.Error` sul primo lookup (metadata, `include_data=False`)
    non viene piu' degradato a `None`: raise `FileSourceResolutionError
    (cause="lookup", ...)` con `original_error` identita' preservata
    e `__cause__` settato via `raise ... from e`.
    """
    _bypass_security(monkeypatch, api_service)
    original = psycopg.OperationalError("pool exhausted")

    def _boom(fs_id, include_data=False):
        raise original

    monkeypatch.setattr(file_sources_db, "get_file_source", _boom)

    with pytest.raises(FileSourceResolutionError) as exc_info:
        api_service._resolve_file_source_id_bytes("fs-1", ctx=_TEST_CTX)

    err = exc_info.value
    assert err.file_source_id == "fs-1"
    assert err.cause == "lookup"
    assert "DB" in err.reason
    assert err.original_error is original
    assert err.__cause__ is original


def test_raises_on_blob_lookup_db_error(api_service, monkeypatch):
    """`psycopg2.Error` sul secondo lookup (blob, `include_data=True`)
    dopo metadata ok -> stesso fail-closed del test precedente.
    Lock separato perche' il codice fa due query DB distinte
    (`include_data=False` / `include_data=True`) e entrambe devono
    essere protette dalla stessa classe narrow.
    """
    _bypass_security(monkeypatch, api_service)
    original = psycopg.OperationalError("connection lost")

    def _get(fs_id, include_data=False):
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        raise original

    monkeypatch.setattr(file_sources_db, "get_file_source", _get)

    with pytest.raises(FileSourceResolutionError) as exc_info:
        api_service._resolve_file_source_id_bytes("fs-1", ctx=_TEST_CTX)

    err = exc_info.value
    assert err.file_source_id == "fs-1"
    assert err.cause == "lookup"
    assert err.original_error is original
    assert err.__cause__ is original


def test_bytes_first_resolver_does_not_materialize_to_filesystem(
    api_service, monkeypatch, tmp_path
):
    """Post-Phase5: il resolver e' bytes-first e non crea cache locali."""
    _bypass_security(monkeypatch, api_service)

    def _get(fs_id, include_data=False):
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        return {"data": b"payload"}

    monkeypatch.setattr(file_sources_db, "get_file_source", _get)

    original = PermissionError("filesystem read-only")

    def _boom_mkdir(self, *args, **kwargs):
        raise original

    # Patcha mkdir a livello della classe Path per intercettare la
    # chiamata `source_dir.mkdir(...)` del path cache-miss. Pytest
    # revert automatico del monkeypatch a teardown.
    monkeypatch.setattr(type(tmp_path), "mkdir", _boom_mkdir)

    assert api_service._resolve_file_source_id_bytes("fs-1", ctx=_TEST_CTX) == (
        b"payload",
        "input.xlsx",
    )


def test_fails_closed_without_security_context_still_short_circuits(api_service):
    """Regression guard del pre-check: il security check
    `_assert_file_source_group_access` resta FUORI dal try (come
    Previous behavior), quindi `_current_group_id=None` continua a produrre
    `ValueError("Contesto di sicurezza non determinabile")` PRIMA
    di qualsiasi lookup. the fail-closed resolver contract non deve aver spostato il check
    dentro un try che lo wrapperebbe in `FileSourceResolutionError`.
    """
    api_service._current_group_id = None

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._resolve_file_source_id_bytes("fs-1")
