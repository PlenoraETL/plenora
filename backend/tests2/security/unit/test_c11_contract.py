"""Contract test C11 — OOM protection + cross-join refuse + Pydantic tight.

Copre:
A. ESTRAI_MAX_ROWS cap: verifica che il loop fetchmany raise se supera.
D. query_generator refuse CROSS JOIN implicito (pre-fix era silent).
E. Canvas validator reject orphan non-source nodes.
F. Pydantic CanvasWithPlugins.nodes/edges tipizzati (List[Node]/List[Edge]).

Non coperti:
B. Streaming CSV/Parquet — l'end-to-end richiede connection reale.
   Test smoke: chiamare _execute_streaming_to_file con mock del
   cursor per verificare che fetchmany loop termini correttamente.
C. Intermediate snapshot cap — coperto implicitamente dal path in
   execute_query (complesso mockarlo in unit; scope integration se serve).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# A. ESTRAI_MAX_ROWS cap
# =============================================================================


def test_execute_raw_raises_when_rows_exceed_cap(monkeypatch):
    """Il loop fetchmany deve raise ValueError se il cumulativo supera
    ESTRAI_MAX_ROWS. Previene OOM su query mal filtrate."""
    from app.db.query_executor import QueryExecutor
    import app.config

    # Abbassa il cap per il test
    monkeypatch.setattr(
        app.config.settings, "ESTRAI_MAX_ROWS", 100, raising=False
    )

    # Fake cursor che ritorna 10k righe per chunk (> 100 cap)
    fake_chunk = [(1, "a")] * 10_000
    calls = {"n": 0}

    def fake_fetchmany(size):
        calls["n"] += 1
        if calls["n"] > 1:
            return []  # safety: stop dopo un chunk se non abbiamo raise
        return fake_chunk

    fake_result = MagicMock()
    fake_result.fetchmany.side_effect = fake_fetchmany
    fake_result.cursor.description = [("id",), ("val",)]

    fake_conn_ctx = MagicMock()
    fake_conn_ctx.__enter__.return_value.execute.return_value = fake_result
    fake_engine = MagicMock()
    fake_engine.connect.return_value = fake_conn_ctx

    mock_factory = MagicMock()
    mock_factory.create_engine.return_value = fake_engine

    executor = QueryExecutor(engine_factory=mock_factory, logger=MagicMock())
    connection = MagicMock()
    connection.type = "postgres"

    with pytest.raises(ValueError, match="piu' di 100"):
        executor.execute_raw(connection, "SELECT ...", None)


# =============================================================================
# D. query_generator refuse CROSS JOIN implicito
# =============================================================================


def test_query_generator_raises_on_orphan_join_node():
    """Un nodo tabella senza alcun edge di JOIN → ValueError esplicito.
    Pre-C11 emetteva silent CROSS JOIN (rischio cartesian product)."""
    from app.core.estrai.query_generator import QueryGenerator
    from app.shared.models import Node

    gen = QueryGenerator(db_type="postgres")
    orphan_node = Node(
        id="src-orphan",
        type="source",
        position={"x": 0, "y": 0},
        table_name="ORPHAN_TABLE",
        columns=[],
        filters=[],
    )

    # Invoca direttamente la funzione che genera JOIN per un singolo nodo
    # (espone il path del pre-fix CROSS JOIN fallback).
    with pytest.raises(ValueError, match="non ha alcun edge"):
        gen._genera_join(
            node=orphan_node,
            edges=[],          # nessun edge → pre-C11 emetteva silent CROSS JOIN
            all_nodes=[orphan_node],
            processed_ids=set(),
        )


# =============================================================================
# E. Canvas validator — orphan non-source nodes
# =============================================================================


def test_canvas_put_rejects_orphan_transform_node():
    """Un nodo transform senza edge incoming → 400 al PUT /canvas."""
    from app.core.projects.router import update_project_canvas, CanvasWithPlugins
    from app.shared.models import Node, Edge
    from fastapi import HTTPException

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = {"id": "p1", "group_id": "g1"}

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(id="orphan", type="transform", position={"x": 200, "y": 0}),
            Node(id="res", type="result", position={"x": 400, "y": 0}, columns=[]),
        ],
        edges=[
            Edge(id="e1", source="src", target="res"),  # orphan NON e' target
        ],
    )

    class _User:
        role = "admin"; user_id = "u1"; username = "u"; group_id = "g1"

    with patch("app.core.projects.router._project_service", fake_svc):
        with pytest.raises(HTTPException) as excinfo:
            update_project_canvas("p1", body, _User())
    assert excinfo.value.status_code == 400
    assert "orfani" in excinfo.value.detail
    assert "orphan" in excinfo.value.detail


def test_canvas_put_accepts_source_nodes_without_incoming_edges():
    """Nodi source-like (source, file_source, project_source, etc.) non
    hanno mai edge incoming — non devono triggerare il reject."""
    from app.core.projects.router import update_project_canvas, CanvasWithPlugins
    from app.shared.models import Node, Edge

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = {"id": "p1", "group_id": "g1"}
    fake_svc.update_canvas.return_value = True
    fake_svc.update_project_columns_metadata.return_value = None

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[
            Edge(id="e1", source="src", target="res"),
        ],
    )

    class _User:
        role = "admin"; user_id = "u1"; username = "u"; group_id = "g1"

    with patch("app.core.projects.router._project_service", fake_svc):
        resp = update_project_canvas("p1", body, _User())
    assert resp.get("status") == "ok"


# =============================================================================
# F. Pydantic tight contract
# =============================================================================


def test_canvas_with_plugins_rejects_non_list_nodes():
    """Pre-C11 nodes: Any accettava qualsiasi cosa. Post-C11 List[Node]."""
    from app.core.projects.router import CanvasWithPlugins

    with pytest.raises(ValidationError):
        CanvasWithPlugins(nodes="not a list", edges=[])


def test_canvas_with_plugins_rejects_malformed_node_dict():
    """Dict senza i campi obbligatori di Node (id, type, position) → 422."""
    from app.core.projects.router import CanvasWithPlugins

    with pytest.raises(ValidationError):
        CanvasWithPlugins(
            nodes=[{"foo": "bar"}],  # niente id/type/position
            edges=[],
        )


def test_edge_accepts_reactflow_handle_fields():
    """sourceHandle/targetHandle sono campi opzionali (forward-compat
    con ReactFlow che a volte li invia senza essere a noi utili)."""
    from app.shared.models import Edge

    # Non deve sollevare — campi dichiarati come Optional
    edge = Edge(
        id="e1", source="n1", target="n2",
        sourceHandle="out-1", targetHandle="in-1",
    )
    assert edge.sourceHandle == "out-1"
    assert edge.targetHandle == "in-1"


def test_edge_rejects_empty_id_source_target():
    """id/source/target sono obbligatori e non-vuoti (min_length=1)."""
    from app.shared.models import Edge

    with pytest.raises(ValidationError):
        Edge(id="", source="n1", target="n2")
    with pytest.raises(ValidationError):
        Edge(id="e1", source="", target="n2")
    with pytest.raises(ValidationError):
        Edge(id="e1", source="n1", target="")


# =============================================================================
# C11-nit: CROSS JOIN esplicito
# =============================================================================


def test_edge_accepts_explicit_cross_join_type():
    """C11-nit: il messaggio di errore in query_generator suggerisce
    join_type='CROSS' per CROSS JOIN intenzionale. Il model DEVE
    accettare questo valore, altrimenti il contract e' rotto
    (messaggio promette una configurazione impossibile da costruire)."""
    from app.shared.models import Edge

    edge = Edge(id="e1", source="n1", target="n2", join_type="cross")
    assert edge.join_type == "cross"


def test_query_generator_emits_cross_join_without_on_clause():
    """Se l'utente configura edge.join_type='cross', il generator
    deve emettere `CROSS JOIN table [alias]` SENZA clausola ON
    (SQL standard rifiuta `CROSS JOIN t ON ...`)."""
    from app.core.estrai.query_generator import QueryGenerator
    from app.shared.models import Node, Edge

    gen = QueryGenerator(db_type="postgres")
    other_node = Node(
        id="src-a", type="source", position={"x": 0, "y": 0},
        table_name="TABLE_A", alias="a", columns=[], filters=[],
    )
    cross_node = Node(
        id="src-b", type="source", position={"x": 200, "y": 0},
        table_name="TABLE_B", alias="b", columns=[], filters=[],
    )
    edge = Edge(
        id="e1", source="src-a", target="src-b",
        join_type="cross",
    )

    sql = gen._genera_join(
        node=cross_node,
        edges=[edge],
        all_nodes=[other_node, cross_node],
        processed_ids={"src-a"},
    )
    # Deve contenere CROSS JOIN ma NON " ON "
    assert "CROSS JOIN" in sql.upper(), f"Expected CROSS JOIN, got: {sql}"
    assert " ON " not in sql.upper(), (
        f"CROSS JOIN non deve avere ON clause (SQL invalid). SQL: {sql}"
    )
