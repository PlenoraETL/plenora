"""Sessione 11 P1: SharePoint disconnect multi-probe.

Bug coperto:
- datasources/sharepoint.py:168-180 — disconnect usa solo 2 probe
  internal (`_session`/`session`). Se la libreria refactora gli attr,
  leak silente della requests.Session.

Post-fix: 4 probe sequenziali + try `ctx.close()` API pubblica + log
warning esplicito se NESSUNA probe ha successo.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_sharepoint_disconnect_has_multiple_probes():
    """`disconnect()` deve includere almeno 4 probe sequenziali per la
    sessione HTTP + un tentativo di `ctx.close()` API pubblica."""
    from app.core.datasources import sharepoint

    src = inspect.getsource(sharepoint.SharePointDataSource.disconnect)

    # Le 4 probe canoniche
    for probe in [
        "_session",
        "session",
        "_client_context_session",
        "pending_request",
    ]:
        assert probe in src, (
            f"disconnect deve probare l'attributo `{probe}` per fallback "
            f"su versioni diverse della libreria Office365-REST-Python-Client"
        )

    # Tentativo API pubblica
    assert "ctx.close()" in src
    # Log warning quando nessuna probe ha successo
    assert "if not closed:" in src
    assert "nessuna delle 4 probe" in src or "probe" in src.lower()


def test_sharepoint_disconnect_closes_session_via_first_probe():
    """Functional: probe `_session` valida → la disconnect chiude correttamente."""
    from app.core.datasources import sharepoint

    instance = sharepoint.SharePointDataSource.__new__(sharepoint.SharePointDataSource)
    fake_session = MagicMock()
    fake_ctx = MagicMock()
    # Remove close() attr to force probe fallback
    del fake_ctx.close
    fake_ctx._session = fake_session
    instance._ctx = fake_ctx

    instance.disconnect()

    fake_session.close.assert_called_once()
    assert instance._ctx is None


def test_sharepoint_disconnect_uses_public_close_when_available():
    """Functional: se ctx.close() pubblica esiste, NON tocca le probe internal."""
    from app.core.datasources import sharepoint

    instance = sharepoint.SharePointDataSource.__new__(sharepoint.SharePointDataSource)
    fake_session_internal = MagicMock()
    fake_ctx = MagicMock()
    fake_ctx._session = fake_session_internal
    instance._ctx = fake_ctx

    instance.disconnect()

    # ctx.close() chiamato (preferred), NON il probe _session
    fake_ctx.close.assert_called_once()
    fake_session_internal.close.assert_not_called()


def test_sharepoint_disconnect_no_op_when_ctx_already_none():
    """disconnect() su `_ctx=None` deve essere no-op safe."""
    from app.core.datasources import sharepoint

    instance = sharepoint.SharePointDataSource.__new__(sharepoint.SharePointDataSource)
    instance._ctx = None

    # No exception
    instance.disconnect()
    assert instance._ctx is None
