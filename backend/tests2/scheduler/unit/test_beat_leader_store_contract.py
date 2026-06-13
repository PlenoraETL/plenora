"""Contratti dello store del lock leader del beat (Plenora 2.0, Fase 3).

Lane no-DB: si blinda la forma SQL (atomicita' acquire-or-refresh, release
filtrata per owner, filtro di scadenza) via check source-bound; la semantica
real-time e' coperta dai fake nei test di leader election.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.scheduler import beat_leader_store


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_acquire_is_single_atomic_upsert():
    src = inspect.getsource(beat_leader_store.acquire_or_refresh_beat_leader)
    assert "INSERT INTO beat_leader_locks" in src
    assert "ON CONFLICT (lock_name) DO UPDATE" in src
    # Acquisisce se scaduto OPPURE rinnova se sono owner — in un solo statement.
    assert "beat_leader_locks.expires_at <= now()" in src
    assert "beat_leader_locks.owner_token = EXCLUDED.owner_token" in src
    assert "RETURNING owner_token" in src
    assert "interval '1 second'" in src


def test_release_is_filtered_by_owner_token():
    src = inspect.getsource(beat_leader_store.release_beat_leader)
    assert "DELETE FROM beat_leader_locks" in src
    # Release safe: non cancella il lock di un altro owner.
    assert "owner_token = %s" in src


def test_current_leader_filters_expired():
    src = inspect.getsource(beat_leader_store.current_beat_leader)
    assert "expires_at > now()" in src


def test_lock_name_is_stable():
    assert beat_leader_store.BEAT_LEADER_LOCK_NAME == "beat_leader"


def test_module_has_no_redis_dependency():
    from pathlib import Path

    src = Path(beat_leader_store.__file__).read_text(encoding="utf-8").lower()
    # PostgreSQL-only: nessun client Redis importato (la parola "Redis" puo'
    # comparire nei commenti — e' il backend che stiamo rimpiazzando).
    assert "import redis" not in src
