"""
Contract test per il fix del pool exhaustion sulla shared HTTP session.

Bug coperto (plenora-bug-connections P1):
  `pool_block=False` sull'HTTPAdapter condiviso permetteva la creazione
  di connessioni TCP oltre `pool_maxsize` (50) sotto carico concorrente,
  potendo saturare i file descriptor del processo o triggerare rate-
  limiting lato server (ArcGIS in particolare).

Post-fix: `pool_block=True` blocca i thread che eccedono il pool in
attesa di uno slot libero. Cap effettivo sulle connessioni TCP
simultanee verso un singolo host = `pool_maxsize`.
"""

from __future__ import annotations

import pytest

from app.infrastructure.http import session as http_session


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture(autouse=True)
def _reset_shared_session(monkeypatch):
    """Reset il singleton per garantire isolamento tra test."""
    monkeypatch.setattr(http_session, "_shared_session", None)
    yield
    monkeypatch.setattr(http_session, "_shared_session", None)


class TestSharedSessionPoolBlock:
    def test_adapter_has_pool_block_true(self):
        sess = http_session.get_shared_session()
        adapter = sess.get_adapter("https://example.com")
        # `pool_block` e' parametro di `urllib3.PoolManager` accessibile
        # via `_pool_block` sull'HTTPAdapter di requests.
        assert adapter._pool_block is True, (
            "HTTPAdapter deve avere pool_block=True per evitare "
            "connessioni TCP unbounded oltre pool_maxsize."
        )

    def test_adapter_has_pool_maxsize_cap(self):
        """Sanity: il cap del pool e' settato (50 nel default)."""
        sess = http_session.get_shared_session()
        adapter = sess.get_adapter("https://example.com")
        assert adapter._pool_maxsize == 50

    def test_shared_session_is_singleton(self):
        s1 = http_session.get_shared_session()
        s2 = http_session.get_shared_session()
        assert s1 is s2
