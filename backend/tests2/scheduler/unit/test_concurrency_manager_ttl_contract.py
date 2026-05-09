"""
Contract test per `app.core.scheduler.concurrency_manager.ConcurrencyManager` — TTL / expiration semantics.

Gap reale vs ``test_concurrency_manager_contract.py`` (17 test esistenti):
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pytest

from app.core.scheduler.concurrency_manager import ConcurrencyManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


class _FakeCursor:
    """Simula psycopg2 cursor con .rowcount per gli UPDATE."""

    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount


class _FakeDb:
    """In-memory store per la tabella ``scheduler_locks``.

    Riconosce solo i pattern SQL usati da ``concurrency_manager``:
    - CREATE TABLE IF NOT EXISTS (bootstrap no-op)
    - INSERT INTO scheduler_locks (...) VALUES (%s, %s, %s)
    - SELECT value, expires_at FROM scheduler_locks WHERE key = %s
    - DELETE FROM scheduler_locks WHERE key = %s
    - UPDATE scheduler_locks SET expires_at = %s WHERE key = %s
      (refresh_lock, usa %s al posto di ? perché è su pg_connection)
    """

    def __init__(self):
        self.rows: Dict[str, Dict[str, Optional[str]]] = {}
        self.executed_sql: List[Tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.executed_sql.append((sql, params))
        norm = " ".join(sql.split()).upper()

        if norm.startswith("CREATE TABLE"):
            return _FakeCursor()

        if "INSERT INTO SCHEDULER_LOCKS" in norm:
            key, value, expires_at = params
            if key in self.rows:
                raise RuntimeError("UNIQUE constraint violation (simulated)")
            self.rows[key] = {"value": value, "expires_at": expires_at}
            return _FakeCursor(rowcount=1)

        if "DELETE FROM SCHEDULER_LOCKS" in norm:
            # Supporta sia DELETE WHERE key = %s sia DELETE WHERE key LIKE ...
            if "LIKE" in norm:
                prefix = params[0].replace("%", "") if params else "scheduler:"
                removed = [k for k in self.rows if k.startswith(prefix)]
                for k in removed:
                    del self.rows[k]
                return _FakeCursor(rowcount=len(removed))
            key = params[0]
            removed = 1 if key in self.rows else 0
            self.rows.pop(key, None)
            return _FakeCursor(rowcount=removed)

        if "UPDATE SCHEDULER_LOCKS" in norm and "RETURNING" not in norm:
            # refresh_lock: UPDATE ... SET expires_at = %s WHERE key = %s
            new_expires, key = params
            if key in self.rows:
                self.rows[key]["expires_at"] = new_expires
                return _FakeCursor(rowcount=1)
            return _FakeCursor(rowcount=0)

        return _FakeCursor()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Optional[str]]]:
        self.executed_sql.append((sql, params))
        norm = " ".join(sql.split()).upper()
        if "SELECT" in norm and "SCHEDULER_LOCKS" in norm:
            key = params[0]
            entry = self.rows.get(key)
            if entry is None:
                return None
            return {
                "value": entry["value"],
                "expires_at": entry["expires_at"],
            }
        return None


@pytest.fixture
def mgr_with_real_helpers(monkeypatch):
    """ConcurrencyManager con helper VERI, ma il layer db mockato.

    Così ``_get`` e ``_set_nx`` girano tutto il loro codice reale (TTL
    parsing, cleanup, INSERT) — è esattamente ciò che voglio testare.
    """
    fake = _FakeDb()
    monkeypatch.setattr(
        "app.core.scheduler.concurrency_manager.db.execute", fake.execute
    )
    monkeypatch.setattr(
        "app.core.scheduler.concurrency_manager.db.fetchone", fake.fetchone
    )

    mgr = ConcurrencyManager()
    mgr._fake = fake  # side-channel per i test
    return mgr


def _count_deletes_on_key(fake: _FakeDb, key: str) -> int:
    """Conta i DELETE emessi per una chiave specifica."""
    count = 0
    for sql, params in fake.executed_sql:
        norm = " ".join(sql.split()).upper()
        if "DELETE FROM SCHEDULER_LOCKS" in norm and "LIKE" not in norm:
            if params and params[0] == key:
                count += 1
    return count


def _count_updates(fake: _FakeDb) -> int:
    """Conta gli UPDATE (refresh_lock) emessi."""
    count = 0
    for sql, _ in fake.executed_sql:
        norm = " ".join(sql.split()).upper()
        if "UPDATE SCHEDULER_LOCKS" in norm and "RETURNING" not in norm:
            count += 1
    return count


def test_get_deletes_and_returns_none_when_lock_expired(mgr_with_real_helpers):
    """Blinda §1.6 lock-expire: se ``expires_at`` è nel passato,
    ``_get`` DEVE:
      1. emettere un ``DELETE`` per la chiave (auto-cleanup)
      2. ritornare ``None`` come se il lock non esistesse

    Regressione: rimuovere il branch ``if datetime.now > exp`` o il
    ``DELETE`` successivo lascerebbe i lock appesi dopo SIGKILL finché
    un job esterno non li pulisce. Il contract TTL sarebbe decorativo.
    """
    fake = mgr_with_real_helpers._fake
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fake.rows["scheduler:lock:expired-sched"] = {
        "value": "running",
        "expires_at": past_iso,
    }

    result = mgr_with_real_helpers._get("scheduler:lock:expired-sched")

    assert result is None, "lock scaduto DEVE apparire come libero"
    assert _count_deletes_on_key(fake, "scheduler:lock:expired-sched") == 1, (
        "lock scaduto DEVE essere cancellato via DELETE (auto-cleanup)"
    )
    assert "scheduler:lock:expired-sched" not in fake.rows, (
        "row scaduta DEVE essere fisicamente rimossa dallo store"
    )


def test_get_preserves_lock_when_expires_at_is_malformed(mgr_with_real_helpers):
    """Blinda il contract fail-closed su corruzione dati: se
    ``expires_at`` non è parseable come ISO8601, il ``try/except`` di
    ``_get`` (lines 89-90) DEVE swalloware l'errore e ritornare
    comunque il value del lock — preservando il lock invece di
    crasharlo o rimuoverlo tacitamente.

    Scenario reale: downgrade schema, bug in write, corruzione manuale.
    Preferiamo un lock "stuck" visibile all'ispezione piuttosto che:
    - un crash che killa lo scheduler (se raise)
    - un lock che sparisce silent su parse error (se il catch
      cancellasse la row)

    Regressione: sostituire ``except Exception: pass`` con ``raise``
    propagherebbe l'errore al caller dello scheduler, rompendo il
    ciclo di polling.
    """
    fake = mgr_with_real_helpers._fake
    fake.rows["scheduler:lock:corrupt"] = {
        "value": "running",
        "expires_at": "NOT-A-VALID-ISO-DATETIME",
    }

    result = mgr_with_real_helpers._get("scheduler:lock:corrupt")

    assert result == "running", (
        "expires_at malformato DEVE far tornare il value comunque "
        f"(lock preservato), ottenuto {result!r}"
    )
    # La row NON deve essere cancellata dalla corruzione
    assert "scheduler:lock:corrupt" in fake.rows, (
        "expires_at malformato NON deve cancellare il lock silenziosamente"
    )
    assert _count_deletes_on_key(fake, "scheduler:lock:corrupt") == 0


def test_get_returns_value_when_expires_at_is_null(mgr_with_real_helpers):
    """Blinda il contract: un lock con ``expires_at=NULL`` è persistente
    (nessun TTL). ``_get`` DEVE saltare il check di scadenza e ritornare
    il value sempre.

    Use case: counter di coda (``scheduler:queue:*``) non ha TTL — vive
    finché non viene decrementato a 0. Se qualcuno introducesse un
    default TTL su ``expires_at=NULL``, i counter diverrebbero
    effimeri.
    """
    fake = mgr_with_real_helpers._fake
    fake.rows["scheduler:queue:sched-1"] = {"value": "5", "expires_at": None}

    result = mgr_with_real_helpers._get("scheduler:queue:sched-1")

    assert result == "5"
    # Nessun DELETE emesso per un lock NULL-expires
    assert _count_deletes_on_key(fake, "scheduler:queue:sched-1") == 0


def test_try_acquire_on_expired_lock_returns_acquired_and_reinstalls_lock(
    mgr_with_real_helpers,
):
    """Blinda la catena completa lock-expire → re-acquire:

    Scenario: worker A prende il lock, muore di SIGKILL senza release.
    Dopo ``LOCK_TTL_SECONDS``, worker B chiama ``try_acquire`` sullo
    stesso schedule. Il contract atteso:

      1. ``_set_nx`` invoca ``_get`` come side-effect
      2. ``_get`` vede expires_at passato → DELETE + ritorna None
      3. ``_set_nx`` ora procede con INSERT → True
      4. ``try_acquire`` ritorna "acquired" (NON "queued")

    Regressione: se qualcuno togliesse la linea ``self._get(key)`` di
    cleanup preventivo in ``_set_nx`` (line 96), la UNIQUE constraint
    bloccherebbe l'INSERT → ``_set_nx`` ritornerebbe False → il nuovo
    worker si accoderebbe dietro un lock morto. Effetto: il sistema
    "si ferma" fino a cleanup manuale dopo ogni crash.
    """
    fake = mgr_with_real_helpers._fake
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # Lock orfano di un worker morto
    fake.rows["scheduler:lock:orphan-sched"] = {
        "value": "running",
        "expires_at": past_iso,
    }

    result = mgr_with_real_helpers.try_acquire("orphan-sched")

    assert result == "acquired", (
        f"lock orfano scaduto DEVE essere sostituito da nuovo acquire, "
        f"ottenuto {result!r} (probabile regressione: _set_nx non "
        f"cleanuppa più i lock scaduti)"
    )
    # Il lock è stato rinstallato con value "running" + TTL nuovo (nel futuro)
    new_entry = fake.rows["scheduler:lock:orphan-sched"]
    assert new_entry["value"] == "running"
    # expires_at è nel futuro (il nuovo TTL)
    new_exp = datetime.fromisoformat(new_entry["expires_at"])
    assert new_exp > datetime.now(timezone.utc), (
        "nuovo lock DEVE avere expires_at nel futuro"
    )


def test_is_running_returns_false_when_lock_is_expired(mgr_with_real_helpers):
    """Blinda il contract pubblico: ``is_running`` DEVE ritornare False
    su lock scaduto, non solo sul lock mai acquisito.

    Il metodo delega a ``_get``, che cleanuppa. Ma vale la pena
    blindarlo a livello pubblico perché è parte dell'API esterna usata
    da scheduler_service e router — un caller che interroga
    ``is_running`` dopo un crash silent del worker DEVE vedere False,
    non True.

    Regressione: se qualcuno short-circuitasse ``is_running`` con
    ``return f"scheduler:lock:{id}" in self._data`` (bypassando il
    TTL check), il sistema vedrebbe lock vivi in eterno.
    """
    fake = mgr_with_real_helpers._fake
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    fake.rows["scheduler:lock:dead"] = {
        "value": "running",
        "expires_at": past_iso,
    }

    assert mgr_with_real_helpers.is_running("dead") is False
    assert "scheduler:lock:dead" not in fake.rows, (
        "is_running su lock scaduto DEVE innescare il cleanup (via _get)"
    )


def test_refresh_lock_on_expired_lock_returns_false_without_issuing_update(
    mgr_with_real_helpers,
):
    """Blinda il guard ``if self._get(lock_key) is None: return False``
    in ``refresh_lock`` (line 244).

    Contract: se il lock è scaduto (auto-cleanuppato da ``_get``),
    ``refresh_lock`` DEVE:
      - ritornare False
      - **NON eseguire l'UPDATE** (la riga è già sparita, un UPDATE
        rimbalzerebbe con rowcount=0 ma è comunque traffico sprecato e
        log-noise)

    Più importante: se qualcuno togliesse il guard e lasciasse solo
    l'UPDATE finale, il caller userebbe ``rowcount > 0`` come proxy di
    "lock vivo" — ma in un DB che dopo il cleanup fa rientrare un
    nuovo INSERT concorrente, un UPDATE "blind" potrebbe overwritare
    il TTL di un lock FRESCO acquisito nel frattempo da un altro
    worker. Effetto: due worker credono di avere il lock.

    Regressione: rimuovere il guard esplicito cambia la semantica
    race-safe del refresh.
    """
    fake = mgr_with_real_helpers._fake
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    fake.rows["scheduler:lock:dead-refresh"] = {
        "value": "running",
        "expires_at": past_iso,
    }

    updates_before = _count_updates(fake)

    result = mgr_with_real_helpers.refresh_lock("dead-refresh")

    assert result is False, "refresh su lock scaduto DEVE ritornare False"
    assert _count_updates(fake) == updates_before, (
        "refresh su lock scaduto NON deve emettere UPDATE: il guard "
        "_get() is None → return False deve short-circuitare prima."
    )
    # Il lock è stato rimosso da _get (auto-cleanup), non ri-creato da refresh
    assert "scheduler:lock:dead-refresh" not in fake.rows, (
        "il cleanup di _get DEVE aver rimosso la row; refresh NON deve "
        "reinstaurarla"
    )
