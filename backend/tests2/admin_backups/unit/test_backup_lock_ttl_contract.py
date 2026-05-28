"""
Contract test per il TTL del lock backup manuale.

Cluster bug coperto (commit b52aa308): pre-fix `MANUAL_LOCK_TTL_SEC`
era 7200s (2h). Se il worker Celery crashava (OOM, kill -9, broker
disconnect) il lock restava orfano fino alla scadenza naturale.
Post-fix: TTL ridotto a 300s + heartbeat 120s dal task → blast
radius worker morto da 2h a 5min.

I test boundary-DB (acquire/refresh/release atomic) sono coperti
da `tests2/scheduler/unit/test_concurrency_manager_*` per gli
scheduler_locks. Qui blindiamo solo i parametri TTL/heartbeat e
la presenza del thread heartbeat nel task, che sono il punto di
regressione piu' probabile.
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestManualBackupLockTtlPolicy:
    """`MANUAL_LOCK_TTL_SEC` deve restare breve, heartbeat < TTL."""

    def test_ttl_at_most_600_seconds(self):
        """Regressione contro reintroduzione di TTL lungo (es. 7200s
        della versione pre-fix). 600s e' il massimo accettabile per
        un sistema con heartbeat 120s."""
        from app.core.admin_backups.service import MANUAL_LOCK_TTL_SEC
        assert MANUAL_LOCK_TTL_SEC <= 600, (
            f"regressione: MANUAL_LOCK_TTL_SEC={MANUAL_LOCK_TTL_SEC}s "
            f"troppo lungo. Su worker morto il lock resta orfano per "
            f"~{MANUAL_LOCK_TTL_SEC}s prima dello sblocco."
        )

    def test_ttl_at_least_60_seconds(self):
        """TTL troppo breve causerebbe takeover spurio durante blip
        DB transient. 60s e' la soglia minima ragionevole."""
        from app.core.admin_backups.service import MANUAL_LOCK_TTL_SEC
        assert MANUAL_LOCK_TTL_SEC >= 60

    def test_heartbeat_strictly_less_than_ttl(self):
        """Invariante per liveness: il refresh deve scattare almeno
        DUE volte per TTL window, cosi' un singolo refresh fallito
        non causa scadenza."""
        from app.core.admin_backups.service import (
            MANUAL_LOCK_HEARTBEAT_SEC,
            MANUAL_LOCK_TTL_SEC,
        )
        assert MANUAL_LOCK_HEARTBEAT_SEC < MANUAL_LOCK_TTL_SEC, (
            f"regressione: heartbeat={MANUAL_LOCK_HEARTBEAT_SEC}s "
            f">= TTL={MANUAL_LOCK_TTL_SEC}s — il refresh non scatta "
            f"in tempo per impedire la scadenza"
        )
        assert MANUAL_LOCK_HEARTBEAT_SEC <= MANUAL_LOCK_TTL_SEC / 2, (
            "heartbeat troppo vicino al TTL: un singolo refresh "
            "fallito (network blip) puo' causare scadenza spuria. "
            "Tieni heartbeat <= TTL/2."
        )


class TestRefreshLockOwnerGuard:
    """`refresh_manual_backup_lock` deve rinnovare SOLO se il caller
    e' ancora owner — un thread heartbeat zombi non deve riprendersi
    un lock di cui ha perso la proprieta'."""

    def test_refresh_signature_has_owner_token_kwarg(self):
        """Architectural guard: l'API obbliga il caller a fornire il
        proprio token (no refresh "anonimo")."""
        from app.core.admin_backups.backup_run_store import (
            refresh_manual_backup_lock,
        )
        sig = inspect.signature(refresh_manual_backup_lock)
        assert "owner_token" in sig.parameters
        assert "ttl_seconds" in sig.parameters
        # owner_token e' kwarg-only (keyword-only)
        assert sig.parameters["owner_token"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_refresh_sql_filters_by_owner_token(self):
        """AST-check: il WHERE deve includere `owner_token = %s` per
        impedire UPDATE cross-owner."""
        from app.core.admin_backups import backup_run_store
        src = inspect.getsource(backup_run_store.refresh_manual_backup_lock)
        assert "owner_token = %s" in src, (
            "regressione: refresh_manual_backup_lock non filtra per "
            "owner_token nel WHERE — un heartbeat zombi potrebbe "
            "rinnovare un lock di un altro owner"
        )


class TestBackupTaskHeartbeatIntegration:
    """`run_manual_backup_task` deve avviare il thread heartbeat
    prima del backup e fermarlo nel finally."""

    def test_task_imports_heartbeat_helper(self):
        """Architectural: il task deve includere la funzione
        `_start_lock_heartbeat`."""
        from workers.tasks import admin_backup_task
        src = inspect.getsource(admin_backup_task)
        assert "_start_lock_heartbeat" in src, (
            "regressione: thread heartbeat rimosso dal task — il "
            "lock non viene piu' rinnovato durante l'esecuzione, "
            "scade dopo TTL e altri worker possono race-acquisire."
        )

    def test_task_starts_heartbeat_before_backup(self):
        """Sequenza: heartbeat start PRIMA di `backup_db.main()`,
        stop event PRIMA del release nel finally."""
        from workers.tasks import admin_backup_task
        src = inspect.getsource(admin_backup_task.run_manual_backup_task)
        # Ordine: start_heartbeat ... backup_db.main() ... stop ... release
        i_start = src.find("_start_lock_heartbeat(")
        i_backup = src.find("backup_db.main(")
        i_stop = src.find("stop_heartbeat.set()")
        i_release = src.find("release_manual_backup_lock(")
        assert i_start != -1 and i_backup != -1 and i_stop != -1 and i_release != -1
        assert i_start < i_backup, (
            "regressione ordine: heartbeat deve partire PRIMA del backup"
        )
        assert i_stop < i_release, (
            "regressione ordine: stop heartbeat deve precedere "
            "release_lock per evitare race release/refresh"
        )
