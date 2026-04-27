"""Contract test RB.4.B (2026-04-27) — get_project annotation cross-tenant.

BUG.md #8 layer B:
    Canvas storici esistenti (creati prima di RB.4.A) possono contenere
    `file_source_id` o `source_project_id` di gruppi non visibili
    all'utente che apre il progetto. Il save futuro e' bloccato da RB.4.A,
    ma i canvas gia' persistiti restano "sporchi".

Fix RB.4.B:
    `GET /projects/{id}` ritorna il canvas as-is dal DB (no mutation),
    ma annota i nodi `file_source` / `project_source` con riferimenti
    non visibili al gruppo del progetto:
        node.data.access_denied = True
        node.data.access_denied_reason = "..."
        node.data.inaccessible_resource_type = "file_source" | "project_source"

    Il frontend (RB.4.C) usa il flag per disabilitare run/preview e
    mostrare un warning. Backend resta autorita': RB.4.A blocca save,
    runtime fail-closed (`_assert_file_source_group_access`) resta intatto.

Test scope:
    - canvas con ref non visibile -> response annotata, NO save
    - canvas pulito -> nessuna annotazione (regression)
    - superadmin -> nessuna annotazione (vede tutto)
    - canvas persistito intatto durante GET (no update_canvas chiamato)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.projects.router import get_project
from app.shared.models import CanvasState, Edge, Node


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
    fake_svc.get_project_columns_metadata.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            # UDPN_BAD non e' nel set -> ref non visibile
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", side_effect=_fake_visible
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
    """Stesso pattern per source_project_id cross-tenant."""
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
    fake_svc.get_project_columns_metadata.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "projects":
            return {"project_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", side_effect=_fake_visible
    ):
        resp = get_project("p1", _User())

    nodes = resp["canvas"]["nodes"]
    ps_node = next(n for n in nodes if n["id"] == "ps")
    assert ps_node["data"]["access_denied"] is True
    assert ps_node["data"]["inaccessible_resource_type"] == "project_source"
    assert "project_other_group" in ps_node["data"]["access_denied_reason"]


def test_get_project_does_not_annotate_clean_canvas():
    """Regression: canvas con tutti i ref visibili -> nessun nodo viene
    marcato con access_denied. Response identica al pre-fix (a meno di
    `data` field potenzialmente popolato da `model_dump()` con default)."""
    metadata = _make_metadata(group_id="g1")
    canvas = _make_clean_canvas()

    fake_svc = MagicMock()
    fake_svc.get_project_metadata.return_value = metadata
    fake_svc.get_project_canvas.return_value = canvas
    fake_svc.get_project_columns_metadata.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", side_effect=_fake_visible
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
    fake_svc.get_project_columns_metadata.return_value = []

    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", visible_spy
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
    fake_svc.get_project_columns_metadata.return_value = []

    def _fake_visible(resource_table, group_id, is_superadmin=False):
        if resource_table == "file_sources":
            return {"file_safe_for_g1"}
        return set()

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", side_effect=_fake_visible
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
    fake_svc.get_project_columns_metadata.return_value = []

    visible_spy = MagicMock(return_value=set())

    with patch("app.core.projects.router._project_service", fake_svc), patch(
        "app.shared.sharing.visible_resource_ids", visible_spy
    ):
        resp = get_project("p1", _User())

    assert resp["canvas"] is not None
    visible_spy.assert_not_called()
