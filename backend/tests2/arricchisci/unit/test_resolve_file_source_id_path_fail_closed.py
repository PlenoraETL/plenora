"""Regression guards per `_resolve_file_source_id_path` fail-closed su
errori tecnici (DB lookup + scrittura FS cache).

Batch 3 Fix-A (audit `arricchisci/api_connector` §7.1 + checkpoint
blocco Fix-A + Fix-D).

Pre-fix: catch broad `Exception` outer -> `logger.warning + return
None`. Conflato: errori DB (pool exhausted, conn drop), errori FS
(filesystem read-only, permission denied), file_source inesistente
(`row_meta is None`), blob vuoto (`row_full["data"] is None`) -
tutti diventavano `None`. I caller (`_resolve_file_source_path`,
`_build_rest_file_multipart` ramo extra_input) producevano messaggi
"file_source non trovato / non risolvibile" indistinguibili tra
errore tecnico e ramo dati legittimo.

Post-fix:
- `psycopg2.Error` su lookup metadata / lookup blob -> raise
  `FileSourceResolutionError(cause="lookup", ...)` con chain `from e`.
- `OSError` su mkdir / write_bytes / os.replace -> raise
  `FileSourceResolutionError(cause="materialization", ...)` con chain.
- `row_meta is None` / blob vuoto -> `return None` INVARIATO (ramo
  dati legittimo).
- outer broad `except Exception` eliminato.
- cleanup finally unlink tmp_path invariato (best-effort con
  `logger.debug`, nessuna regressione).

Il boundary `_execute_rest_file` ramo input resolve catturera'
`FileSourceResolutionError` in Fix-D (commit successivo) e la
traduce in response `{success:False, error:"Risoluzione input
fallita: ..."}`. Fino a Fix-D merged, l'eccezione fluisce al ramo
outer `except Exception` del boundary (ancora broad pre-Fix-D) con
messaggio meno specifico -- funzionalmente equivalente a un task
fallito, solo meno descrittivo.
"""

from unittest.mock import patch

import psycopg2
import pytest

import app.config as config_mod
import app.db.db_service as db_service_mod
import app.shared.sharing as sharing_mod
from app.shared.exceptions import FileSourceResolutionError


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.regression,
]


def _bypass_security(monkeypatch, api_service, tmp_path):
    """Allinea il pre-check security per lasciar entrare l'esecuzione
    nel corpo di `_resolve_file_source_id_path`:

    - `_current_group_id` settato (altrimenti
      `_assert_file_source_group_access` solleva subito).
    - `is_resource_visible` mockato a True (cross-tenant check ok).
    - `settings.get_file_source_dir` ritorna una dir sotto `tmp_path`
      per-test, isolata.
    """
    api_service._current_group_id = "grp-test"
    monkeypatch.setattr(sharing_mod, "is_resource_visible", lambda *a, **kw: True)
    monkeypatch.setattr(
        config_mod,
        "settings",
        type(
            "S",
            (),
            {"get_file_source_dir": lambda self, fs_id: tmp_path / fs_id},
        )(),
    )


def test_returns_none_on_missing_metadata(api_service, monkeypatch, tmp_path):
    """Regression guard (vincolo 7 del checkpoint): file_source
    inesistente in DB -> return None. E' ramo dati legittimo, non
    errore. Se Fix-A trasformasse questo caso in eccezione, il caller
    `_build_rest_file_multipart` ramo extra_input `required=False`
    inizierebbe a fallire su condizioni normali ("extra file
    opzionale assente"), cambiando comportamento silenziosamente
    rispetto al pre-fix.
    """
    _bypass_security(monkeypatch, api_service, tmp_path)
    monkeypatch.setattr(db_service_mod, "get_file_source", lambda *a, **kw: None)

    result = api_service._resolve_file_source_id_path("fs-absent")

    assert result is None


def test_returns_none_on_empty_blob(api_service, monkeypatch, tmp_path):
    """Regression guard (vincolo 7): metadata ok ma blob NULL ->
    return None. file_source listato in DB ma `data` non popolata
    (race di upload, bug di save) e' ramo dati legittimo, non errore
    tecnico. La seconda query DB deve comunque essere invocata per
    arrivare a questo ramo.
    """
    _bypass_security(monkeypatch, api_service, tmp_path)

    calls = {"n": 0}

    def _get(fs_id, include_data=False):
        calls["n"] += 1
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        return {"data": None}

    monkeypatch.setattr(db_service_mod, "get_file_source", _get)

    result = api_service._resolve_file_source_id_path("fs-empty")

    assert result is None
    assert calls["n"] == 2, "seconda query (blob) deve essere invocata"


def test_raises_on_metadata_lookup_db_error(api_service, monkeypatch, tmp_path):
    """`psycopg2.Error` sul primo lookup (metadata, `include_data=False`)
    non viene piu' degradato a `None`: raise `FileSourceResolutionError
    (cause="lookup", ...)` con `original_error` identita' preservata
    e `__cause__` settato via `raise ... from e`.
    """
    _bypass_security(monkeypatch, api_service, tmp_path)
    original = psycopg2.OperationalError("pool exhausted")

    def _boom(fs_id, include_data=False):
        raise original

    monkeypatch.setattr(db_service_mod, "get_file_source", _boom)

    with pytest.raises(FileSourceResolutionError) as exc_info:
        api_service._resolve_file_source_id_path("fs-1")

    err = exc_info.value
    assert err.file_source_id == "fs-1"
    assert err.cause == "lookup"
    assert "DB" in err.reason
    assert err.original_error is original
    assert err.__cause__ is original


def test_raises_on_blob_lookup_db_error(api_service, monkeypatch, tmp_path):
    """`psycopg2.Error` sul secondo lookup (blob, `include_data=True`)
    dopo metadata ok -> stesso fail-closed del test precedente.
    Lock separato perche' il codice fa due query DB distinte
    (`include_data=False` / `include_data=True`) e entrambe devono
    essere protette dalla stessa classe narrow.
    """
    _bypass_security(monkeypatch, api_service, tmp_path)
    original = psycopg2.OperationalError("connection lost")

    def _get(fs_id, include_data=False):
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        raise original

    monkeypatch.setattr(db_service_mod, "get_file_source", _get)

    with pytest.raises(FileSourceResolutionError) as exc_info:
        api_service._resolve_file_source_id_path("fs-1")

    err = exc_info.value
    assert err.file_source_id == "fs-1"
    assert err.cause == "lookup"
    assert err.original_error is original
    assert err.__cause__ is original


def test_raises_on_filesystem_materialization_error(
    api_service, monkeypatch, tmp_path
):
    """`OSError` su scrittura FS (mkdir su filesystem read-only,
    permission denied, disk full) -> raise `FileSourceResolutionError
    (cause="materialization", ...)` con chain preservata. La
    classe `PermissionError` e' una sottoclasse di `OSError` e va
    quindi catturata dalla stessa clausola.
    """
    _bypass_security(monkeypatch, api_service, tmp_path)

    def _get(fs_id, include_data=False):
        if not include_data:
            return {"original_filename": "input.xlsx", "metadata": {}}
        return {"data": b"payload"}

    monkeypatch.setattr(db_service_mod, "get_file_source", _get)

    original = PermissionError("filesystem read-only")

    def _boom_mkdir(self, *args, **kwargs):
        raise original

    # Patcha mkdir a livello della classe Path per intercettare la
    # chiamata `source_dir.mkdir(...)` del path cache-miss. Pytest
    # revert automatico del monkeypatch a teardown.
    monkeypatch.setattr(type(tmp_path), "mkdir", _boom_mkdir)

    with pytest.raises(FileSourceResolutionError) as exc_info:
        api_service._resolve_file_source_id_path("fs-1")

    err = exc_info.value
    assert err.file_source_id == "fs-1"
    assert err.cause == "materialization"
    assert "filesystem" in err.reason.lower()
    assert err.original_error is original
    assert err.__cause__ is original


def test_fails_closed_without_security_context_still_short_circuits(api_service):
    """Regression guard del pre-check: il security check
    `_assert_file_source_group_access` resta FUORI dal try (come
    pre-fix), quindi `_current_group_id=None` continua a produrre
    `ValueError("Contesto di sicurezza non determinabile")` PRIMA
    di qualsiasi lookup. Fix-A non deve aver spostato il check
    dentro un try che lo wrapperebbe in `FileSourceResolutionError`.
    """
    api_service._current_group_id = None

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._resolve_file_source_id_path("fs-1")
