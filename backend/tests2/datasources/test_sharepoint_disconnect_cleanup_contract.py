"""
Contract test per la chiusura esplicita della sessione HTTP nel
`SharePointDataSource.disconnect`.

Bug coperto (plenora-bug-connections P1):
  Pre-fix `disconnect()` faceva solo `self._ctx = None`. La
  `requests.Session` interna di `Office365-REST-Python-Client` restava
  aperta finche' il GC non collezionava l'oggetto (non deterministico),
  accumulando connessioni TCP verso SharePoint sotto carico concorrente.

Post-fix: cleanup esplicito di `ctx._session.close()` prima del
dereference. Best-effort: errori del close vengono loggati ma non
propagati per non rompere la disconnect.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.datasources.interface import BackendConfig
from app.core.datasources.sharepoint import SharePointDataSource


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _make_ds_with_fake_ctx(session_mock):
    cfg = BackendConfig(
        id="ds-sp",
        type="sharepoint",
        root_path="",
        extra={
            "site_url": "https://contoso.sharepoint.com/sites/x",
            "username": "u",
            "password": "p",
        },
    )
    ds = SharePointDataSource(cfg)
    # Inject fake ctx con session esposta come `_session` (convenzione
    # Office365-REST-Python-Client).
    ds._ctx = SimpleNamespace(_session=session_mock)
    return ds


class TestSharePointDisconnect:
    def test_disconnect_closes_internal_session(self):
        sess = MagicMock()
        ds = _make_ds_with_fake_ctx(sess)
        ds.disconnect()
        sess.close.assert_called_once()
        assert ds._ctx is None

    def test_disconnect_supports_session_attribute_fallback(self):
        """Alcune versioni della libreria espongono `session` invece di
        `_session`. Cleanup tenta entrambi.
        """
        sess = MagicMock()
        cfg = BackendConfig(
            id="ds-sp",
            type="sharepoint",
            root_path="",
            extra={
                "site_url": "https://x.sharepoint.com/sites/y",
                "username": "u",
                "password": "p",
            },
        )
        ds = SharePointDataSource(cfg)
        ds._ctx = SimpleNamespace(session=sess)
        ds.disconnect()
        sess.close.assert_called_once()
        assert ds._ctx is None

    def test_disconnect_swallows_close_failure(self):
        sess = MagicMock()
        sess.close.side_effect = RuntimeError("connection already dead")
        ds = _make_ds_with_fake_ctx(sess)
        # Deve completare senza sollevare: cleanup e' best-effort.
        ds.disconnect()
        assert ds._ctx is None

    def test_disconnect_handles_ctx_already_none(self):
        cfg = BackendConfig(
            id="ds-sp",
            type="sharepoint",
            root_path="",
            extra={
                "site_url": "https://x.sharepoint.com",
                "username": "u",
                "password": "p",
            },
        )
        ds = SharePointDataSource(cfg)
        # Nessuna connect chiamata prima.
        assert ds._ctx is None
        ds.disconnect()  # no-op idempotent
        assert ds._ctx is None
