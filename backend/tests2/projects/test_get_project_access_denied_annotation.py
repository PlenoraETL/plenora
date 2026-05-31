"""
Contract test RB.4.B (2026-04-27) — get_project annotation cross-tenant.

BUG.md #8 layer B:
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.projects.router import get_project
from app.core.projects.canvas_models import CanvasState, Edge, Node


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


class _User:
    role = "admin"
    user_id = "u1"
    username = "u"
    group_id = "g1"


class _Super:
    role = "superadmin"
    user_id = "u_super"
    username = "super"
    group_id = "g1"


def _make_metadata(group_id: str = "g1"):
    """ProjectMetadata-like mock con i campi minimi consumati."""
    md = MagicMock()
    md.id = "p1"
    md.group_id = group_id
    md.model_dump.return_value = {"id": "p1", "group_id": group_id}
    return md


def _make_dirty_file_source_canvas() -> CanvasState:
    """Canvas con un file_source_id di altro gruppo (riferimento stantio)."""
    return CanvasState(
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


def _make_clean_canvas() -> CanvasState:
    """Canvas con file_source visibile al gruppo del progetto."""
    return CanvasState(
        nodes=[
            Node(
                id="fs",
                type="file_source",
                position={"x": 0, "y": 0},
                file_source_id="file_safe_for_g1",
            ),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="fs", target="res")],
    )


def test_get_project_annotates_inaccessible_file_source_node():
    """Canvas sporco con file_source di altro gruppo -> response 200 con
    nodo `fs` marcato `access_denied=True` e `inaccessible_resource_type`
    = 'file_source'. Il nodo NON viene rimosso."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_file_source_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            # UDPN_BAD non e' nel set -> ref non visibile
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = get_project("p1", _User())

    nodes = resp["canvas"]["nodes"]
    fs_node = next(n for n in nodes if n["id"] == "fs")
    assert fs_node["data"]["access_denied"] is True
    assert fs_node["data"]["inaccessible_resource_type"] == "file_source"
    assert "file_extra_excel_UDPN_BAD" in fs_node["data"]["access_denied_reason"]
    # Il nodo non e' stato rimosso
    assert len(nodes) == 2


def test_get_project_annotates_inaccessible_project_source_node():
    """Stesso pattern per source_project_id cross-tenant.

    RB.4.A.16: projects non e' shareable -> il check passa per
    `_visible_project_ids_strict` invece di `visible_resource_ids`.
    """
    metadata = _make_metadata(group_id="g1")
    canvas = CanvasState(
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

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        return set()

    def _fake_strict(project_ids, owner_group_id, is_superadmin):
        visible = {"project_safe_for_g1"}
        return {pid for pid in (project_ids or ()) if pid in visible}

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ), patch(
        "app.core.projects.router._visible_project_ids_strict",
        side_effect=_fake_strict,
    ):
        resp = get_project("p1", _User())

    nodes = resp["canvas"]["nodes"]
    ps_node = next(n for n in nodes if n["id"] == "ps")
    assert ps_node["data"]["access_denied"] is True
    assert ps_node["data"]["inaccessible_resource_type"] == "project_source"
    assert "project_other_group" in ps_node["data"]["access_denied_reason"]


def test_get_project_does_not_annotate_clean_canvas():
    """Regression: canvas con tutti i ref visibili -> nessun nodo viene
    marcato con access_denied. Response identica al Previous behavior (a meno di
    `data` field potenzialmente popolato da `model_dump()` con default)."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_clean_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = get_project("p1", _User())

    for n in resp["canvas"]["nodes"]:
        # access_denied non deve essere presente o, se presente, deve essere falsy
        assert not (n.get("data") or {}).get("access_denied")


def test_get_project_does_not_annotate_for_superadmin():
    """Superadmin vede TUTTO: nessuna annotazione anche se il canvas ha
    riferimenti che, per un user non-super, sarebbero invisibili.
    Inoltre il path superadmin NON deve nemmeno chiamare visible_resource_ids
    (no DB hit gratuito)."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_file_source_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", visible_spy
    ):
        resp = get_project("p1", _Super())

    fs_node = next(n for n in resp["canvas"]["nodes"] if n["id"] == "fs")
    assert not (fs_node.get("data") or {}).get("access_denied")
    visible_spy.assert_not_called()


def test_get_project_does_not_persist_canvas_during_get():
    """Sicurezza: GET e' read-only. Il canvas in DB non deve essere
    modificato/sovrascritto come side-effect dell'annotazione. Verifica
    che `update_canvas` / `save_*` non siano stati chiamati."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_file_source_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        get_project("p1", _User())

    # Nessuna scrittura sul canvas durante il GET
    fake_svc.update_canvas.assert_not_called()
    # Difensivo: niente chiamate generiche di tipo save_*
    for method_name in ("save_canvas", "save_project", "update_project_canvas"):
        method = getattr(fake_svc, method_name, None)
        if method is not None and isinstance(method, MagicMock):
            method.assert_not_called()


def test_get_project_skips_visibility_check_when_no_cross_tenant_refs():
    """Fast-path: canvas con SOLO nodi non-source-cross-tenant (es. solo
    `source` ESTRAI + `result`) non triggera chiamate a visible_resource_ids."""
    metadata = _make_metadata(group_id="g1")
    canvas = CanvasState(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(id="res", type="result", position={"x": 200, "y": 0}, columns=[]),
        ],
        edges=[Edge(id="e1", source="src", target="res")],
    )

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", visible_spy
    ):
        resp = get_project("p1", _User())

    assert resp["canvas"] is not None
    visible_spy.assert_not_called()


# RB.7.B (2026-04-27): annotation cross-tenant per ArcGIS connections
#
# FIXME(BUG.md #7): post rebrand 2026-04-18 alcuni canvas storici contenevano
# `arcgis_connection_id` / `imdf_arcgis_connection_id` ora rinominati o
# non visibili. RB.7.A previene i save futuri; RB.7.B annota i canvas
# gia' persistiti per disabilitare run/preview lato UI prima del 404 a
# runtime di `listFolders` / publish.


def _make_dirty_carica_arcgis_canvas() -> CanvasState:
    """Canvas con un nodo carica_arcgis che riferisce una connection
    ArcGIS di altro gruppo (es. id pre-rebrand urby_*)."""
    return CanvasState(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(
                id="carica_ag",
                type="carica_arcgis",
                position={"x": 200, "y": 0},
                arcgis_connection_id="arcgis_Arcgis_Online_Urby_OLD",
            ),
        ],
        edges=[Edge(id="e1", source="src", target="carica_ag")],
    )


def test_get_project_annotates_inaccessible_arcgis_connection_on_carica_arcgis_node():
    """Canvas con carica_arcgis che punta a connection di altro gruppo
    -> response 200 con nodo marcato access_denied=true e
    inaccessible_resource_type='arcgis_connection'."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_carica_arcgis_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            return {"arcgis_Arcgis_Online_Plenora_NEW"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = get_project("p1", _User())

    nodes = resp["canvas"]["nodes"]
    ag_node = next(n for n in nodes if n["id"] == "carica_ag")
    assert ag_node["data"]["access_denied"] is True
    assert ag_node["data"]["inaccessible_resource_type"] == "arcgis_connection"
    assert "arcgis_Arcgis_Online_Urby_OLD" in ag_node["data"]["access_denied_reason"]
    assert "Connessione ArcGIS" in ag_node["data"]["access_denied_reason"]
    # Nodo non rimosso
    assert len(nodes) == 2


def test_get_project_does_not_annotate_arcgis_connection_when_visible():
    """Regression: canvas con arcgis_connection visibile al gruppo del
    progetto -> nessuna annotazione."""
    metadata = _make_metadata(group_id="g1")
    canvas = CanvasState(
        nodes=[
            Node(id="src", type="source", position={"x": 0, "y": 0}, columns=[]),
            Node(
                id="carica_ag",
                type="carica_arcgis",
                position={"x": 200, "y": 0},
                arcgis_connection_id="arcgis_safe_for_g1",
            ),
        ],
        edges=[Edge(id="e1", source="src", target="carica_ag")],
    )

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            return {"arcgis_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = get_project("p1", _User())

    for n in resp["canvas"]["nodes"]:
        assert not (n.get("data") or {}).get("access_denied")


def test_get_project_does_not_annotate_arcgis_connection_for_superadmin():
    """Superadmin -> no annotation, no DB hit (visible_resource_ids non
    chiamato per il path arcgis_connection)."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_carica_arcgis_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", visible_spy
    ):
        resp = get_project("p1", _Super())

    ag_node = next(n for n in resp["canvas"]["nodes"] if n["id"] == "carica_ag")
    assert not (ag_node.get("data") or {}).get("access_denied")
    visible_spy.assert_not_called()


def test_get_project_does_not_persist_canvas_during_arcgis_annotation():
    """Sicurezza: GET resta read-only anche per il ramo arcgis_connection.
    Nessuna scrittura sul canvas durante l'annotazione."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_dirty_carica_arcgis_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_output_contract.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "arcgis_connections":
            return {"arcgis_Arcgis_Online_Plenora_IMDF_NEW"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.core.auth.visibility.visible_resource_ids", side_effect=_fake_visible
    ):
        get_project("p1", _User())

    fake_svc.update_canvas.assert_not_called()
    for method_name in ("save_canvas", "save_project", "update_project_canvas"):
        method = getattr(fake_svc, method_name, None)
        if method is not None and isinstance(method, MagicMock):
            method.assert_not_called()
