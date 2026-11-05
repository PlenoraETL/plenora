"""
Perimetro confermato della bonifica orfani (audit frontend R10.4).

La pagina Object Storage mostra gli orfani del filtro di gruppo e manda i
loro project_id: la bonifica tocca solo quelli, e solo se sono davvero
orfani. Senza perimetro resta il comportamento globale.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _run(project_ids):
    from app.core.results.orphan_cleanup import cleanup_orphans

    deleted_results: list[str] = []
    deleted_runs: list[str] = []
    with patch(
        "app.core.results.orphan_cleanup.find_orphan_result_project_ids",
        return_value=["p-a", "p-b", "p-c"],
    ), patch(
        "app.core.results.orphan_cleanup.find_orphan_run_canvas_ids",
        return_value=["p-a", "p-c"],
    ), patch(
        "app.core.results.orphan_cleanup.delete_results_for_project",
        side_effect=lambda pid: deleted_results.append(pid) or 1,
    ), patch(
        "app.core.results.orphan_cleanup.delete_runs_for_canvas",
        side_effect=lambda cid: deleted_runs.append(cid) or 1,
    ):
        report = cleanup_orphans(dry_run=False, project_ids=project_ids)
    return report, deleted_results, deleted_runs


def test_scope_limits_deletion_to_confirmed_orphans():
    report, results, runs = _run(["p-a", "p-b", "p-non-orfano"])
    assert results == ["p-a", "p-b"]
    assert runs == ["p-a"]
    assert report["orphan_results"]["project_ids"] == ["p-a", "p-b"]


def test_empty_scope_deletes_nothing():
    report, results, runs = _run([])
    assert results == [] and runs == []
    assert report["status"] == "clean"


def test_no_scope_keeps_global_behaviour():
    _, results, runs = _run(None)
    assert results == ["p-a", "p-b", "p-c"]
    assert runs == ["p-a", "p-c"]


def test_catalog_marks_orphans_by_id_not_by_name():
    from app.core.admin_object_storage.service import StorageProjectGroup

    group = StorageProjectGroup(
        project_id="p-1", project_name="Progetto eliminato", group_id="g", results=[],
    )
    assert group.orphan is False  # il nome non basta: decide il server


def test_catalog_lists_every_orphan_the_global_cleanup_would_touch(monkeypatch):
    """Regola: il catalogo mostra TUTTI gli orfani della bonifica globale
    (risultati ed esecuzioni intermedie, stessa fonte find_orphan_runs), cosi'
    la bonifica con gli id mostrati pulisce entrambi."""
    from datetime import datetime
    from types import SimpleNamespace

    from app.core.admin_object_storage import service

    monkeypatch.setattr(service.projects_db, "list_projects", lambda group_id=None: [
        SimpleNamespace(group_id="g1", id="p-live", name="Vivo", project_type="manipola"),
    ])
    monkeypatch.setattr(service.results_db, "list_admin_result_rows", lambda group_id=None: [
        SimpleNamespace(id="r1", project_id="p-res", group_id="g1", storage_key="k1", filename="a.csv",
                        format="csv", size_bytes=3, rows_count=1, created_at=datetime(2026, 1, 1)),
    ])
    monkeypatch.setattr(service, "find_orphan_runs", lambda: [
        ("p-res", "g1", 2),       # orfano con risultati e run
        ("p-runs-only", "g1", 4),  # orfano con sole esecuzioni intermedie
        ("p-other", "g2", 1),      # altro gruppo
    ])

    catalog = service.list_project_results()
    by_id = {p.project_id: p for p in catalog.projects}
    assert by_id["p-runs-only"].orphan is True
    assert by_id["p-runs-only"].results == []
    assert by_id["p-runs-only"].intermediate_runs == 4
    assert by_id["p-res"].intermediate_runs == 2 and len(by_id["p-res"].results) == 1
    assert {p.project_id for p in catalog.projects if p.orphan} == {"p-res", "p-runs-only", "p-other"}

    scoped = service.list_project_results(group_id="g1")
    assert {p.project_id for p in scoped.projects if p.orphan} == {"p-res", "p-runs-only"}

    # La bonifica con gli id mostrati tocca risultati E run di quegli id.
    with patch(
        "app.core.results.orphan_cleanup.find_orphan_result_project_ids", return_value=["p-res"],
    ), patch(
        "app.core.results.orphan_cleanup.find_orphan_run_canvas_ids",
        return_value=["p-res", "p-runs-only", "p-other"],
    ), patch(
        "app.core.results.orphan_cleanup.delete_results_for_project", return_value=1,
    ), patch(
        "app.core.results.orphan_cleanup.delete_runs_for_canvas", return_value=1,
    ):
        from app.core.results.orphan_cleanup import cleanup_orphans
        report = cleanup_orphans(project_ids=[p.project_id for p in scoped.projects if p.orphan])
    assert sorted(report["orphan_runs"]["canvas_ids"]) == ["p-res", "p-runs-only"]
    assert report["orphan_results"]["project_ids"] == ["p-res"]


def test_catalog_survives_unavailable_intermediate_db(monkeypatch):
    """Rilettura T1+T3 (R1): se il DB delle esecuzioni intermedie non risponde,
    il catalogo dei risultati resta (prima del T1 non ne dipendeva) e lo dice."""
    from datetime import datetime
    from types import SimpleNamespace

    from app.core.admin_object_storage import service

    monkeypatch.setattr(service.projects_db, "list_projects", lambda group_id=None: [])
    monkeypatch.setattr(service.results_db, "list_admin_result_rows", lambda group_id=None: [
        SimpleNamespace(id="r1", project_id="p-res", group_id="g1", storage_key="k1", filename="a.csv",
                        format="csv", size_bytes=3, rows_count=1, created_at=datetime(2026, 1, 1)),
    ])

    def broken():
        raise RuntimeError("pool del DB intermedio non disponibile")

    monkeypatch.setattr(service, "find_orphan_runs", broken)
    catalog = service.list_project_results()
    assert [p.project_id for p in catalog.projects] == ["p-res"]
    assert catalog.intermediate_runs_unavailable is True
