"""Contract — engine cache key include la cred-version (Backend master Part II #8).

Editare la password deve invalidare l'engine cached (chiave diversa), senza
esporre la password in chiaro nella chiave (che finisce nei log di eviction).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.query_execution.query_engines import DefaultEngineFactory


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _conn(password):
    return SimpleNamespace(
        type="postgres", host="h", port=5432, database="db", username="u", password=password,
    )


def test_key_changes_when_password_changes():
    f = DefaultEngineFactory()
    assert f._connection_key(_conn("old")) != f._connection_key(_conn("new"))


def test_key_stable_for_same_password():
    f = DefaultEngineFactory()
    assert f._connection_key(_conn("p")) == f._connection_key(_conn("p"))


def test_key_does_not_leak_password():
    f = DefaultEngineFactory()
    assert "supersecret" not in f._connection_key(_conn("supersecret"))
