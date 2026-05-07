"""
Contract test BE-GO-READY-BOOTSTRAP-ENGINE-010-OBSERVABILITY-POOL-HEADROOM-PUBLIC.

Lock the new public helper `app.db.pg_connection.get_pool_headroom()` +
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.observability, pytest.mark.contract]


# PoolHeadroom dataclass


def test_pool_headroom_is_frozen_dataclass():
    from app.db.pg_connection import PoolHeadroom

    h = PoolHeadroom(max=12, in_use=3, free=4)
    with pytest.raises(Exception):
        h.in_use = 5  # type: ignore[misc]


def test_pool_headroom_fields_are_int():
    from app.db.pg_connection import PoolHeadroom

    h = PoolHeadroom(max=12, in_use=3, free=4)
    assert h.max == 12
    assert h.in_use == 3
    assert h.free == 4


def test_pool_headroom_saturated_when_in_use_at_max():
    from app.db.pg_connection import PoolHeadroom

    assert PoolHeadroom(max=3, in_use=3, free=0).saturated is True
    assert PoolHeadroom(max=3, in_use=4, free=0).saturated is True


def test_pool_headroom_not_saturated_when_in_use_below_max():
    from app.db.pg_connection import PoolHeadroom

    assert PoolHeadroom(max=12, in_use=11, free=1).saturated is False
    assert PoolHeadroom(max=12, in_use=0, free=12).saturated is False


# get_pool_headroom() — read pool internals


def test_get_pool_headroom_reads_pool_internals():
    """Riconciliato dietro il helper public: in_use = len(_used),
    free = len(_pool), max = _POOL_MAX."""
    fake_pool = type("P", (), {"_used": {"a": 1, "b": 2}, "_pool": [object(), object(), object()]})()
    with (
        patch("app.db.pg_connection._get_pool", return_value=fake_pool),
        patch("app.db.pg_connection._POOL_MAX", 12),
    ):
        from app.db.pg_connection import get_pool_headroom

        headroom = get_pool_headroom()

    assert headroom.max == 12
    assert headroom.in_use == 2
    assert headroom.free == 3
    assert headroom.saturated is False


def test_get_pool_headroom_reports_saturated_when_used_at_max():
    fake_pool = type("P", (), {"_used": {"a": 1, "b": 2, "c": 3}, "_pool": []})()
    with (
        patch("app.db.pg_connection._get_pool", return_value=fake_pool),
        patch("app.db.pg_connection._POOL_MAX", 3),
    ):
        from app.db.pg_connection import get_pool_headroom

        headroom = get_pool_headroom()

    assert headroom.in_use == 3
    assert headroom.max == 3
    assert headroom.free == 0
    assert headroom.saturated is True


def test_get_pool_headroom_handles_missing_attrs_gracefully():
    """Se psycopg2 in futuro cambia gli attribute name, `getattr` con
    default vuoto evita un AttributeError. Lo helper ritorna 0/0 e il
    consumer fail-OPEN nel caller."""
    fake_pool = type("P", (), {})()  # no _used, no _pool
    with (
        patch("app.db.pg_connection._get_pool", return_value=fake_pool),
        patch("app.db.pg_connection._POOL_MAX", 12),
    ):
        from app.db.pg_connection import get_pool_headroom

        headroom = get_pool_headroom()

    assert headroom.in_use == 0
    assert headroom.free == 0
    assert headroom.max == 12


def test_get_pool_headroom_propagates_pool_init_failure():
    """Se `_get_pool()` solleva (DB unreachable, config invalida), il
    helper deve propagare. Il consumer in `health.check_pool_headroom`
    cattura e mappa a fail-OPEN."""
    def _raise():
        raise RuntimeError("DB pool init failed")

    with patch("app.db.pg_connection._get_pool", _raise):
        from app.db.pg_connection import get_pool_headroom

        with pytest.raises(RuntimeError, match="DB pool init failed"):
            get_pool_headroom()


# Public surface: importable + module-level


def test_get_pool_headroom_is_module_level_public():
    """Il helper deve essere importabile come simbolo public."""
    import app.db.pg_connection as pg

    assert hasattr(pg, "get_pool_headroom")
    assert hasattr(pg, "PoolHeadroom")
    # No leading underscore on the public surface.
    assert not pg.get_pool_headroom.__name__.startswith("_")
    assert not pg.PoolHeadroom.__name__.startswith("_")
