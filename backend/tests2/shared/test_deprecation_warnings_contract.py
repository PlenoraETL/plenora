"""Contract test R-21/R-22/R-23 — DeprecationWarning emit-once per alias legacy.

Verifica che i 4 alias deprecated del codebase emettano sia un Python
``DeprecationWarning`` standard sia un ``logger.warning`` la prima volta
che vengono invocati, e che il warning sia cached (emit-once per processo).

Alias coperti:
  R-21: ``URBY_ALLOW_LOOPBACK_OUTBOUND`` env alias (api_utils._warn_urby_loopback_alias_once)
  R-23: ``api_utils.get_oauth2_token`` wrapper legacy
  R-23: ``api_utils.get_oauth2_client_credentials_token`` wrapper legacy
  R-22: ``cloud_backends.get_object_storage`` wrapper legacy

Pattern dei test:
  - cache_clear sul wrapper per rendere il primo emit osservabile.
  - ``pytest.warns(DeprecationWarning)`` per intercettare l'emit Python.
  - asserzione su contenuto messaggio (verbatim "[DEPRECATED]" + nome alias).
  - re-invocazione: nessun warning ulteriore (cache LRU emit-once).

Nota su pytest filterwarnings:
  ``backend/tests2/pytest.ini`` ha ``ignore::DeprecationWarning`` come
  default per non rompere il run su test esistenti che invocano i
  wrapper legacy. ``pytest.warns`` bypassa il filter (catch context),
  quindi i test contract sotto vedono il warning anche con il filter.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ============================================================
# R-21 — URBY_ALLOW_LOOPBACK_OUTBOUND env alias
# ============================================================


def test_urby_loopback_alias_emits_deprecation_warning():
    """R-21: l'alias env legacy emette DeprecationWarning + logger.warning."""
    from app.shared import api_utils

    api_utils._warn_urby_loopback_alias_once.cache_clear()

    with pytest.warns(DeprecationWarning) as record:
        api_utils._warn_urby_loopback_alias_once()

    assert len(record) == 1
    msg = str(record[0].message)
    assert "URBY_ALLOW_LOOPBACK_OUTBOUND" in msg
    assert "[DEPRECATED]" in msg
    assert "PLENORA_ALLOW_LOOPBACK_OUTBOUND" in msg, (
        "Il messaggio deve indicare il nome canonical da usare al posto."
    )
    assert "2026-Q4" in msg, (
        "Il messaggio deve includere il removal target documentato."
    )


def test_urby_loopback_alias_emit_is_cached_once_per_process():
    """R-21: lru_cache(maxsize=1) garantisce emit-once per processo."""
    from app.shared import api_utils

    api_utils._warn_urby_loopback_alias_once.cache_clear()

    # Prima invocazione: emit.
    with pytest.warns(DeprecationWarning):
        api_utils._warn_urby_loopback_alias_once()

    # Seconda invocazione: NESSUN emit (cache hit).
    # pytest.warns con record=True permette di asserire 0 warning.
    import warnings as _w
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        api_utils._warn_urby_loopback_alias_once()
    matches = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(matches) == 0, (
        f"Cache LRU rotta: emit-once violato. Caught: {len(matches)} warning."
    )


# ============================================================
# R-23 — get_oauth2_token wrapper legacy
# ============================================================


def test_get_oauth2_token_emits_deprecation_warning():
    """R-23: get_oauth2_token emette DeprecationWarning quando invocato.

    Il wrapper chiama get_cached_oauth2_token sotto. Mockiamo quel
    callee per evitare hit di rete reale.
    """
    from app.shared import api_utils

    api_utils._warn_get_oauth2_token_deprecated_once.cache_clear()

    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-1"):
        with pytest.warns(DeprecationWarning) as record:
            result = api_utils.get_oauth2_token({"token_url": "https://x"})

    assert result == "tok-1"
    assert len(record) == 1
    msg = str(record[0].message)
    assert "get_oauth2_token" in msg
    assert "get_cached_oauth2_token" in msg, (
        "Il messaggio deve indicare la migration target."
    )
    assert "2026-Q4" in msg


def test_get_oauth2_token_emit_is_cached_once():
    """R-23: emit-once per processo via lru_cache."""
    from app.shared import api_utils
    import warnings as _w

    api_utils._warn_get_oauth2_token_deprecated_once.cache_clear()

    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-1"):
        # Prima invocazione: emit.
        with pytest.warns(DeprecationWarning):
            api_utils.get_oauth2_token({"token_url": "https://x"})

        # Seconda invocazione: cache hit, NESSUN emit.
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            api_utils.get_oauth2_token({"token_url": "https://x"})
        matches = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "get_oauth2_token" in str(w.message)
        ]
        assert len(matches) == 0, (
            f"emit-once violato. Caught: {len(matches)}"
        )


# ============================================================
# R-23 — get_oauth2_client_credentials_token wrapper legacy
# ============================================================


def test_get_oauth2_cc_token_emits_deprecation_warning():
    """R-23: simmetrico per il client_credentials wrapper."""
    from app.shared import api_utils

    api_utils._warn_get_oauth2_cc_token_deprecated_once.cache_clear()

    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-cc"):
        with pytest.warns(DeprecationWarning) as record:
            result = api_utils.get_oauth2_client_credentials_token({"token_url": "https://x"})

    assert result == "tok-cc"
    assert len(record) == 1
    msg = str(record[0].message)
    assert "get_oauth2_client_credentials_token" in msg
    assert "get_cached_oauth2_token" in msg
    assert "2026-Q4" in msg


def test_get_oauth2_cc_token_emit_is_cached_once():
    """R-23: emit-once anche per il client_credentials wrapper."""
    from app.shared import api_utils
    import warnings as _w

    api_utils._warn_get_oauth2_cc_token_deprecated_once.cache_clear()

    with patch.object(api_utils, "get_cached_oauth2_token", return_value="tok-cc"):
        with pytest.warns(DeprecationWarning):
            api_utils.get_oauth2_client_credentials_token({"token_url": "https://x"})

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            api_utils.get_oauth2_client_credentials_token({"token_url": "https://x"})
        matches = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "client_credentials" in str(w.message)
        ]
        assert len(matches) == 0


# ============================================================
# R-22 — cloud_backends.get_object_storage alias legacy
# ============================================================


def test_get_object_storage_emits_deprecation_warning():
    """R-22: get_object_storage emette DeprecationWarning + logger.warning.

    Il wrapper chiama get_runtime_object_storage. Mockiamo quel callee
    per evitare hit di capability check reale (che richiede env vars).
    """
    from app.shared import cloud_backends

    cloud_backends._warn_get_object_storage_deprecated_once.cache_clear()

    fake_storage = object()
    with patch.object(cloud_backends, "get_runtime_object_storage", return_value=fake_storage):
        with pytest.warns(DeprecationWarning) as record:
            result = cloud_backends.get_object_storage()

    assert result is fake_storage
    assert len(record) == 1
    msg = str(record[0].message)
    assert "get_object_storage" in msg
    assert "get_runtime_object_storage" in msg or "get_backup_object_storage" in msg, (
        "Il messaggio deve indicare i target espliciti runtime/backup."
    )
    assert "2026-Q4" in msg


def test_get_object_storage_emit_is_cached_once():
    """R-22: emit-once per processo."""
    from app.shared import cloud_backends
    import warnings as _w

    cloud_backends._warn_get_object_storage_deprecated_once.cache_clear()

    fake_storage = object()
    with patch.object(cloud_backends, "get_runtime_object_storage", return_value=fake_storage):
        with pytest.warns(DeprecationWarning):
            cloud_backends.get_object_storage()

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            cloud_backends.get_object_storage()
        matches = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "get_object_storage" in str(w.message)
        ]
        assert len(matches) == 0


# ============================================================
# Cross-cutting: gli emit-once non si contaminano fra di loro
# ============================================================


def test_each_alias_has_its_own_cache():
    """I 4 helper emit-once devono avere cache LRU indipendenti: il
    cache_clear di uno non resetta gli altri (e viceversa)."""
    from app.shared import api_utils, cloud_backends

    # Reset tutti.
    api_utils._warn_urby_loopback_alias_once.cache_clear()
    api_utils._warn_get_oauth2_token_deprecated_once.cache_clear()
    api_utils._warn_get_oauth2_cc_token_deprecated_once.cache_clear()
    cloud_backends._warn_get_object_storage_deprecated_once.cache_clear()

    # Trigger solo URBY loopback.
    with pytest.warns(DeprecationWarning):
        api_utils._warn_urby_loopback_alias_once()

    # Gli altri devono ancora emettere al primo invocazione.
    with patch.object(api_utils, "get_cached_oauth2_token", return_value="t"):
        with pytest.warns(DeprecationWarning) as record:
            api_utils.get_oauth2_token({"token_url": "https://x"})
        assert "get_oauth2_token" in str(record[0].message)


# ============================================================
# Stack-trace: il warning indica il caller, non il wrapper interno
# ============================================================


def test_get_oauth2_token_stacklevel_points_to_caller():
    """R-23: stacklevel=3 fa puntare il warning al chiamante esterno
    di get_oauth2_token, non al helper interno _warn_*_deprecated_once.

    Stacklevel calibration:
      caller -> get_oauth2_token -> _warn_helper -> warnings.warn
                  level=1            level=2          level=3
    Quindi stacklevel=3 dal warnings.warn = caller. (level=2 sarebbe
    get_oauth2_token, level=1 sarebbe _warn_helper.)
    """
    from app.shared import api_utils

    api_utils._warn_get_oauth2_token_deprecated_once.cache_clear()

    with patch.object(api_utils, "get_cached_oauth2_token", return_value="t"):
        with pytest.warns(DeprecationWarning) as record:
            api_utils.get_oauth2_token({"token_url": "https://x"})

    # Il warning deve avere filename/lineno coerente con questo test
    # (il caller esterno), non con api_utils.py.
    # NB: in alcuni runner pytest istromenta lo stack; verifichiamo
    # almeno che il filename non sia api_utils.py interno.
    w = record[0]
    if w.filename:
        assert "api_utils.py" not in w.filename, (
            f"stacklevel sbagliato: warning punta a {w.filename}:{w.lineno}, "
            "deve puntare al caller esterno."
        )
