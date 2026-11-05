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
