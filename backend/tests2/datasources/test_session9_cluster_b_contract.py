"""Sessione 9 Cluster B contract test: datasources cleanup + logger leak.

Bug coperti:
1. connections P1 — datasources/service.py:180-188 logger.error con
   raw `{e}` → poteva leakare credenziali del provider.
2. connections P1 — datasources/service.py test_connection senza
   cleanup esplicito del backend (handle FTP/SFTP/SharePoint orfane).
3. connections P1 — datasources/sharepoint.py auth failure: ctx
   parziale orfano con session HTTP aperta.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from tests2.shared._ast_identifiers import calls_method


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_datasources_service_test_connection_uses_type_name_pattern():
    """`test_connection` in datasources/service.py NON deve loggare
    `f"{e}"` raw — solo `type(e).__name__`."""
    from app.core.datasources import service

    src = inspect.getsource(service.DataSourceService.test_connection)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: il pattern raw f"{e}" non deve apparire come
    # interpolation nei logger call. Il commento "{e}" pre-fix nel
    # docstring e' OK (cattura solo righe non-commento).
    # RAW: f"{e}" è un pattern f-string/expression, non un identificatore
    assert 'f"{e}"' not in src_no_comments
    # RAW: type(e).__name__ è un'espressione composta (call + attributo su risultato), non un semplice identificatore
    assert "type(e).__name__" in src_no_comments


def test_datasources_service_test_connection_calls_disconnect():
    """`test_connection` deve chiamare `backend.disconnect()` nel finally
    per evitare handle orfane verso FTP/SFTP/SharePoint."""
    from app.core.datasources import service

    src = textwrap.dedent(inspect.getsource(service.DataSourceService.test_connection))
    # RAW: "finally:" è un keyword sintattico Python, non un identificatore
    assert "finally:" in src
    assert calls_method(src, "disconnect"), "test_connection deve chiamare .disconnect()"
    # RAW: hasattr(backend, "disconnect") — "disconnect" è un string-literal arg a hasattr, non un identificatore
    assert 'hasattr(backend, "disconnect")' in src or "hasattr(backend, 'disconnect')" in src


def test_sharepoint_auth_failure_resets_ctx_to_none():
    """`SharePointDataSource.connect` su exception deve resettare
    `self._ctx = None` per safe disconnect() successiva."""
    from app.core.datasources import sharepoint

    src = inspect.getsource(sharepoint)
    # Trova il `connect` method
    idx = src.find("def connect(")
    assert idx >= 0
    # Il blocco except del connect deve includere `self._ctx = None`
    # prima del raise DataSourceConnectionError.
    connect_block = src[idx:idx + 3000]
    except_idx = connect_block.find("except Exception as e:")
    assert except_idx >= 0
    except_block = connect_block[except_idx:except_idx + 1500]
    # RAW: "self._ctx = None" è un'istruzione di assegnamento, non un identificatore
    assert "self._ctx = None" in except_block, (
        "SharePoint connect except deve resettare self._ctx=None "
        "per defense-in-depth contro ctx parziale orfano"
    )
