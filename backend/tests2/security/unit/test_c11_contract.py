"""
Contract test C11 — OOM protection + cross-join refuse + Pydantic tight.

Copre:
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# A. ESTRAI_MAX_ROWS cap


def test_execute_raw_raises_when_rows_exceed_cap(monkeypatch):
    """Il loop fetchmany deve raise ValueError se il cumulativo supera
    ESTRAI_MAX_ROWS. Previene OOM su query mal filtrate."""
    from app.core.query_execution.query_executor import QueryExecutor

    # Abbassa il cap per il test
    monkeypatch.setenv("ESTRAI_MAX_ROWS", "100")

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


# D. query_generator refuse CROSS JOIN implicito


def test_query_generator_raises_on_orphan_join_node():
    """Un nodo tabella senza alcun edge di JOIN → ValueError esplicito.
    Pre-C11 emetteva silent CROSS JOIN (rischio cartesian product)."""
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.projects.canvas_models import Node

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
    # (espone il path del Previous behavior CROSS JOIN fallback).
    with pytest.raises(ValueError, match="non ha alcun edge"):
        gen._genera_join(
            node=orphan_node,
            edges=[],          # nessun edge → pre-C11 emetteva silent CROSS JOIN
            all_nodes=[orphan_node],
            processed_ids=set(),
        )


# E. Canvas validator — orphan non-source nodes


def test_canvas_put_rejects_orphan_transform_node():
    """Un nodo transform senza edge incoming → 400 al PUT /canvas."""
    from app.core.projects.router import update_project_canvas, CanvasWithPlugins
    from app.core.projects.canvas_models import Node, Edge
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
    from app.core.projects.canvas_models import Node, Edge

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = {"id": "p1", "group_id": "g1"}
    fake_svc.update_canvas.return_value = True
    fake_svc.update_project_output_contract.return_value = None

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


# RB.4.A (2026-04-27): cross-tenant ref validation in update_project_canvas
#
# FIXME(BUG.md #8): un canvas con file_source_id / source_project_id non visibili
# al gruppo del progetto poteva essere salvato senza errore (passava i
# vecchi check orphan + group_access). L'errore arrivava solo a runtime via
# `_assert_file_source_group_access` con messaggio opaco.
# Post-RB.4.A: il save rifiuta esplicitamente con 400 e messaggio che
# elenca i ref non validi.


class _RB4User:
    role = "admin"
    user_id = "u1"
    username = "u"
    group_id = "g1"


def _rb4_make_svc(owner_group_id: str = "g1"):
    """Service mock minimo per i test RB.4.A."""
    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = {
        "id": "p1",
        "group_id": owner_group_id,
    }
    fake_svc.update_canvas.return_value = True
    fake_svc.update_project_output_contract.return_value = None
    return fake_svc


def test_canvas_put_rejects_file_source_not_visible_to_owner_group():
    """Canvas con `file_source_id` di altro gruppo (riferimento stantio
    da progetto duplicato/condiviso): 400 con messaggio operativo che
    nomina l'id non visibile."""
    from fastapi import HTTPException

    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(
                id="fs",
                type="file_source",
                position={"x": 0, "y": 0},
                file_source_id="file_extra_excel_UDPN_BAD",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="fs", target="res")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    # visible_resource_ids ritorna SOLO i fs visibili a g1; UDPN_BAD NON e' nel set.
    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe_for_g1"}  # NB: UDPN_BAD assente
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        with pytest.raises(HTTPException) as excinfo:
            update_project_canvas("p1", body, _RB4User())

    assert excinfo.value.status_code == 400
    detail = excinfo.value.detail
    assert "non visibili" in detail
    assert "file_extra_excel_UDPN_BAD" in detail
    # Il save NON deve essere stato chiamato (fail-closed, no side-effect)
    fake_svc.update_canvas.assert_not_called()


def test_canvas_put_rejects_source_project_not_visible_to_owner_group():
    """Stesso pattern per `source_project_id` cross-tenant.

    RB.4.A.16: projects non e' shareable (no project_shares table) ->
    il check passa per `_visible_project_ids_strict` invece di
    `visible_resource_ids`. Mocchiamo l'helper a livello di modulo.
    """
    from fastapi import HTTPException

    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(
                id="ps",
                type="project_source",
                position={"x": 0, "y": 0},
                source_project_id="project_other_group",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="ps", target="res")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        return set()

    def _fake_strict(project_ids, owner_group_id, is_superadmin):
        # Solo "project_safe_for_g1" e' visibile a g1; OTHER non lo e'
        return {pid for pid in (project_ids or ()) if pid == "project_safe_for_g1"}

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ), patch(
        "app.core.projects.router._visible_project_ids_strict",
        side_effect=_fake_strict,
    ):
        with pytest.raises(HTTPException) as excinfo:
            update_project_canvas("p1", body, _RB4User())

    assert excinfo.value.status_code == 400
    assert "project_source: project_other_group" in excinfo.value.detail
    fake_svc.update_canvas.assert_not_called()


def test_canvas_put_accepts_when_all_refs_visible_to_owner_group():
    """Regression: canvas con file_source_id e source_project_id tutti
    visibili al gruppo del progetto -> 200, save invocato regolarmente."""
    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(
                id="fs",
                type="file_source",
                position={"x": 0, "y": 0},
                file_source_id="file_safe",
            ),
            Node(
                id="ps",
                type="project_source",
                position={"x": 100, "y": 0},
                source_project_id="project_safe",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[
            Edge(id="e1", source="fs", target="res"),
            Edge(id="e2", source="ps", target="res"),
        ],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe", "file_other_safe"}
        return set()

    def _fake_strict(project_ids, owner_group_id, is_superadmin):
        visible = {"project_safe"}
        return {pid for pid in (project_ids or ()) if pid in visible}

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ), patch(
        "app.core.projects.router._visible_project_ids_strict",
        side_effect=_fake_strict,
    ):
        resp = update_project_canvas("p1", body, _RB4User())

    assert resp.get("status") == "ok"
    fake_svc.update_canvas.assert_called_once()


def test_canvas_put_skips_visibility_check_when_no_cross_tenant_refs():
    """Canvas senza nodi file_source / project_source: nessun chiamata a
    visible_resource_ids (no overhead, no DB hit). Verifica la fast-path."""
    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="src", target="res")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")
    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", visible_spy
    ):
        resp = update_project_canvas("p1", body, _RB4User())

    assert resp.get("status") == "ok"
    visible_spy.assert_not_called()  # fast-path: skip se refs vuoti


def test_canvas_put_superadmin_can_save_any_ref():
    """superadmin bypassa il check (visible_resource_ids ritorna TUTTI gli
    id quando is_superadmin=True). Coerente con la policy globale."""
    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(
                id="fs",
                type="file_source",
                position={"x": 0, "y": 0},
                file_source_id="file_anywhere",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="fs", target="res")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        # is_superadmin=True nel chiamante -> qualsiasi id presente
        if is_superadmin and resource_table == "file_sources":
            return {"file_anywhere"}
        return set()

    class _Super:
        role = "superadmin"
        user_id = "u_super"
        username = "super"
        group_id = "g1"

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = update_project_canvas("p1", body, _Super())

    assert resp.get("status") == "ok"
    fake_svc.update_canvas.assert_called_once()


# RB.7.A (2026-04-27): estensione cross-tenant validation a ArcGIS connections
#
# FIXME(BUG.md #7): post rebrand 2026-04-18 (urby-* -> plenora-*) alcuni canvas
# storici contenevano `arcgis_connection_id` / `imdf_arcgis_connection_id`
# che ora puntavano a connection rinominate o non visibili al gruppo.
# Il save passava in silenzio, il successivo `listFolders` rispondeva 404
# con messaggio opaco.
# Post-RB.7.A: il save rifiuta esplicitamente con 400 e detail che elenca
# `arcgis_connection: <ids>` non validi.


@pytest.mark.parametrize(
    ("node_type", "field_name", "bad_id"),
    [
        (
            "carica_arcgis",
            "arcgis_connection_id",
            "arcgis_Arcgis_Online_Urby_OLD",
        ),
    ],
)
def test_canvas_put_rejects_arcgis_connection_not_visible_to_owner_group(
    node_type, field_name, bad_id,
):
    """ArcGIS connection refs non visibili al gruppo owner -> 400."""
    from fastapi import HTTPException

    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(
                id="arcgis_ref",
                type=node_type,
                position={"x": 200, "y": 0},
                **{field_name: bad_id},
            ),
        ],
        edges=[Edge(id="e1", source="src", target="arcgis_ref")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            # OLD non e' nel set -> ref non visibile
            return {"arcgis_Arcgis_Online_Plenora_NEW"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        with pytest.raises(HTTPException) as excinfo:
            update_project_canvas("p1", body, _RB4User())

    assert excinfo.value.status_code == 400
    assert f"arcgis_connection: {bad_id}" in excinfo.value.detail
    fake_svc.update_canvas.assert_not_called()


def test_canvas_put_accepts_when_arcgis_connection_is_visible():
    """Regression: canvas con arcgis_connection visibile al gruppo del
    progetto -> 200, save invocato regolarmente."""
    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(
                id="carica_ag",
                type="carica_arcgis",
                position={"x": 200, "y": 0},
                arcgis_connection_id="arcgis_safe",
            ),
        ],
        edges=[
            Edge(id="e1", source="src", target="carica_ag"),
        ],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            return {"arcgis_safe"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = update_project_canvas("p1", body, _RB4User())

    assert resp.get("status") == "ok"
    fake_svc.update_canvas.assert_called_once()


def test_canvas_put_collects_arcgis_connection_id_from_node_data_dict():
    """Compat: il frontend puo' salvare arcgis_connection_id sia come
    field top-level del Node sia dentro `data` (`node.data['arcgis_connection_id']`).
    `_collect_canvas_resource_refs` deve catturare entrambi i path."""
    from fastapi import HTTPException

    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(
                id="carica_ag",
                type="carica_arcgis",
                position={"x": 200, "y": 0},
                # Solo dentro data, NON come field top-level
                data={"arcgis_connection_id": "arcgis_inside_data_BAD"},
            ),
        ],
        edges=[Edge(id="e1", source="src", target="carica_ag")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            return set()  # nessun id visibile
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        with pytest.raises(HTTPException) as excinfo:
            update_project_canvas("p1", body, _RB4User())

    assert excinfo.value.status_code == 400
    assert "arcgis_inside_data_BAD" in excinfo.value.detail


# RB.4.A.16 (2026-04-27): _visible_project_ids_strict
#
# FIXME(BUG.md #16): `visible_resource_ids("projects",...)` solleva ValueError
# perche' projects non e' nel whitelist `_SHAREABLE` (no project_shares
# table). Previous behavior: PUT canvas con un project_source restituiva 500
# "Tabella 'projects' non e' shareable". Current contract: helper ownership-only
# `_visible_project_ids_strict` che fa una sola query batch
# `WHERE id IN (...) AND group_id = ?`.


def test_visible_project_ids_strict_returns_empty_set_for_empty_input():
    """Fast-path: input vuoto -> set() senza DB hit."""
    from app.core.projects.router import _visible_project_ids_strict

    fetchall_spy = MagicMock()
    with patch("app.db.pg_connection.fetchall", fetchall_spy):
        assert _visible_project_ids_strict([], "g1", is_superadmin=False) == set()
        assert _visible_project_ids_strict(None, "g1", is_superadmin=False) == set()
        assert (
            _visible_project_ids_strict(set(), "g1", is_superadmin=False) == set()
        )

    fetchall_spy.assert_not_called()


def test_visible_project_ids_strict_superadmin_returns_input_no_query():
    """superadmin -> tutti gli id passati sono visibili, nessun DB hit."""
    from app.core.projects.router import _visible_project_ids_strict

    fetchall_spy = MagicMock()
    ids = {"p_a", "p_b", "p_c"}
    with patch("app.db.pg_connection.fetchall", fetchall_spy):
        result = _visible_project_ids_strict(ids, "g_super", is_superadmin=True)

    assert result == ids
    fetchall_spy.assert_not_called()


def test_visible_project_ids_strict_filters_by_owner_group():
    """Non-superadmin: query batch filtra per group_id e ritorna SOLO
    gli id di proprieta' del gruppo del progetto."""
    from app.core.projects.router import _visible_project_ids_strict

    # Mock fetchall: ritorna SOLO p_a (p_b e' di altro gruppo, p_c non esiste)
    def _fake_fetchall(sql, params):
        # Verifica IN clause + group_id binding (params = ids... + group_id)
        assert "IN (" in sql.upper().replace("  ", " ")
        assert "GROUP_ID" in sql.upper()
        assert params[-1] == "g1"
        return [{"id": "p_a"}]

    with patch("app.core.projects.db.fetchall", side_effect=_fake_fetchall):
        result = _visible_project_ids_strict(
            ["p_a", "p_b", "p_c"], "g1", is_superadmin=False,
        )

    assert result == {"p_a"}


def test_visible_project_ids_strict_returns_empty_when_owner_group_missing():
    """Difensivo: senza owner_group_id non possiamo filtrare -> set()
    invece di esporre tutti i progetti dei gruppi."""
    from app.core.projects.router import _visible_project_ids_strict

    fetchall_spy = MagicMock()
    with patch("app.db.pg_connection.fetchall", fetchall_spy):
        result = _visible_project_ids_strict(
            {"p_a"}, owner_group_id=None, is_superadmin=False,
        )

    assert result == set()
    fetchall_spy.assert_not_called()


def test_canvas_put_with_project_source_does_not_raise_value_error_anymore():
    """Anti-regression #16: Previous behavior `update_project_canvas` con un
    project_source ref triggherava `visible_resource_ids("projects",...)`
    -> ValueError("Tabella 'projects' non e' shareable") -> 500 generico.
    Current contract: la chiamata passa per `_visible_project_ids_strict`
    (no ValueError). Il test verifica che il path raggiunge 400 (cross-tenant)
    o 200 (ref valido) ma MAI un'eccezione non-HTTPException."""
    from fastapi import HTTPException

    from app.core.projects.router import CanvasWithPlugins, update_project_canvas
    from app.core.projects.canvas_models import Edge, Node

    body = CanvasWithPlugins(
        nodes=[
            Node(
                id="ps",
                type="project_source",
                position={"x": 0, "y": 0},
                source_project_id="some_project",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="ps", target="res")],
    )

    fake_svc = _rb4_make_svc(owner_group_id="g1")

    # Mock fetchall: simula DB che ritorna some_project come visibile a g1
    def _fake_fetchall(sql, params):
        return [{"id": "some_project"}]

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.projects.db.fetchall", side_effect=_fake_fetchall
    ):
        # Non deve sollevare ValueError. Puo' sollevare HTTPException
        # (404 o altre validazioni a valle), ma non un errore generico.
        try:
            resp = update_project_canvas("p1", body, _RB4User())
            assert resp.get("status") == "ok"
        except HTTPException:
            # Accettato: significa che il path e' stato valutato
            # correttamente (no crash interno).
            pass
        except ValueError as e:  # pragma: no cover - regression
            pytest.fail(f"Regression #16: ValueError leaked: {e}")


# F. Pydantic tight contract


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
    from app.core.projects.canvas_models import Edge

    # Non deve sollevare — campi dichiarati come Optional
    edge = Edge(
        id="e1", source="n1", target="n2",
        sourceHandle="out-1", targetHandle="in-1",
    )
    assert edge.sourceHandle == "out-1"
    assert edge.targetHandle == "in-1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": "", "source": "n1", "target": "n2"},
        {"id": "e1", "source": "", "target": "n2"},
        {"id": "e1", "source": "n1", "target": ""},
    ],
)
def test_edge_rejects_empty_id_source_target(kwargs):
    """id/source/target sono obbligatori e non-vuoti (min_length=1)."""
    from app.core.projects.canvas_models import Edge

    with pytest.raises(ValidationError):
        Edge(**kwargs)


# C11-nit: CROSS JOIN esplicito


def test_edge_accepts_explicit_cross_join_type():
    """C11-nit: il messaggio di errore in query_generator suggerisce
    join_type='CROSS' per CROSS JOIN intenzionale. Il model DEVE
    accettare questo valore, altrimenti il contract e' rotto
    (messaggio promette una configurazione impossibile da costruire)."""
    from app.core.projects.canvas_models import Edge

    edge = Edge(id="e1", source="n1", target="n2", join_type="cross")
    assert edge.join_type == "cross"


def test_query_generator_emits_cross_join_without_on_clause():
    """Se l'utente configura edge.join_type='cross', il generator
    deve emettere `CROSS JOIN table [alias]` SENZA clausola ON
    (SQL standard rifiuta `CROSS JOIN t ON ...`)."""
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.projects.canvas_models import Node, Edge

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
