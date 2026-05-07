"""
Integration test — update_project_canvas_key_atomic vs. concorrenza reale.

Scenario: due writer concorrenti sullo stesso progetto.
"""

from __future__ import annotations

import json
import threading
import time
from typing import List

import pytest


pytestmark = [pytest.mark.integration]


def _get_canvas_raw(project_id: str) -> dict:
    """Legge il canvas direttamente dal DB e lo ritorna come dict."""
    import app.db.config_db_service as cfg_db
    row = cfg_db.get_project(project_id)
    assert row is not None, f"Progetto {project_id} non trovato"
    canvas = row.get("canvas")
    if isinstance(canvas, str):
        canvas = json.loads(canvas)
    return canvas or {}


def _create_test_project(group_id: str) -> str:
    import app.db.config_db_service as cfg_db
    from app.services.project_service import ProjectService

    svc = ProjectService()
    # Lazy: creiamo direttamente via DB per evitare dipendenze sull'API
    project_id = f"atomic-test-{int(time.time() * 1000)}"
    cfg_db.create_project(
        project_id=project_id,
        group_id=group_id,
        name="atomic concurrent test",
        project_type="estrai",
        connection_id=None,
        created_by=None,
    )
    return project_id


def test_concurrent_writes_preserve_both_keys(integration_user_seed):
    """Due thread scrivono chiavi canvas diverse in parallelo.
    Post-fix: entrambe le chiavi sono presenti nel canvas finale.
    """
    import app.db.config_db_service as cfg_db

    group_id = integration_user_seed["group_id"]
    project_id = _create_test_project(group_id)

    # Setup: canvas iniziale con nodes/edges preesistenti
    cfg_db.update_project(project_id, canvas={
        "nodes": [{"id": "n1", "type": "source"}],
        "edges": [],
    })

    # Due writer paralleli: uno aggiorna columns_metadata, l'altro
    # aggiorna un'altra chiave (es. una "custom_key" di test)
    writer_a_value = [{"name": f"col_{i}", "type": "str"} for i in range(50)]
    writer_b_value = {"marker": "B-was-here"}

    start_barrier = threading.Barrier(2)
    errors: List[Exception] = []

    def writer_a():
        try:
            start_barrier.wait()
            for _ in range(20):
                cfg_db.update_project_canvas_key_atomic(
                    project_id, "columns_metadata", writer_a_value
                )
        except Exception as e:
            errors.append(e)

    def writer_b():
        try:
            start_barrier.wait()
            for _ in range(20):
                cfg_db.update_project_canvas_key_atomic(
                    project_id, "custom_marker", writer_b_value
                )
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer_a)
    t2 = threading.Thread(target=writer_b)
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)

    try:
        assert not errors, f"Writer concorrenti hanno sollevato: {errors}"
        assert not t1.is_alive() and not t2.is_alive(), (
            "Un writer e' rimasto bloccato — deadlock?"
        )

        # ASSERT CRUCIALE: entrambe le key sono nel canvas finale.
        # Senza il lock atomico, B avrebbe sovrascritto columns_metadata
        # (o viceversa) perche' il read-modify-write non-lockato produce
        # lost update.
        final = _get_canvas_raw(project_id)
        assert final.get("columns_metadata") == writer_a_value, (
            f"columns_metadata di Writer A perso — lost update.\n"
            f"Canvas finale: {list(final.keys())}"
        )
        assert final.get("custom_marker") == writer_b_value, (
            f"custom_marker di Writer B perso — lost update.\n"
            f"Canvas finale: {list(final.keys())}"
        )
        # Anche i campi pre-esistenti (nodes) devono restare
        assert final.get("nodes") == [{"id": "n1", "type": "source"}], (
            f"nodes cancellati durante il merge atomico: {final.get('nodes')!r}"
        )
    finally:
        cfg_db.delete_project(project_id)


def test_concurrent_same_key_writes_last_wins_no_corruption(integration_user_seed):
    """N thread scrivono la STESSA key con valori diversi. Nessuno
    deve crashare; il valore finale deve essere uno di quelli scritti
    (ultimo a committare), mai un ibrido o corrotto."""
    import app.db.config_db_service as cfg_db

    group_id = integration_user_seed["group_id"]
    project_id = _create_test_project(group_id)

    cfg_db.update_project(project_id, canvas={"nodes": [], "edges": []})

    N_THREADS = 8
    WRITES_PER_THREAD = 10
    valid_values = set()

    def writer(thread_id: int):
        for i in range(WRITES_PER_THREAD):
            val = [{"name": f"t{thread_id}_col_{i}", "type": "str"}]
            valid_values.add(json.dumps(val, sort_keys=True))
            cfg_db.update_project_canvas_key_atomic(
                project_id, "columns_metadata", val
            )

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(not t.is_alive() for t in threads), "Deadlock rilevato"

    try:
        final = _get_canvas_raw(project_id)
        final_cm = final.get("columns_metadata")
        assert final_cm is not None, "columns_metadata mancante"
        # Il valore finale deve essere IDENTICO a uno di quelli scritti:
        # no merge parziale, no corrotto. Serializziamo per confronto
        # stabile (ordine chiavi).
        final_serialized = json.dumps(final_cm, sort_keys=True)
        assert final_serialized in valid_values, (
            f"columns_metadata finale NON corrisponde a nessuno dei valori "
            f"scritti: {final_cm!r}. Suggerisce corruption/merge parziale."
        )
    finally:
        cfg_db.delete_project(project_id)


def test_atomic_update_on_missing_project_returns_false(integration_user_seed):
    """Smoke: progetto inesistente → False, non solleva."""
    import app.db.config_db_service as cfg_db
    _ = integration_user_seed  # assicura DB up
    result = cfg_db.update_project_canvas_key_atomic(
        "projet-ghost-never-existed", "columns_metadata", []
    )
    assert result is False
