"""
Contratto porte resource_access (Epic C roadmap, guardia C5).

Due livelli:

1. Guardia AST: ``auth/resource_access.py`` non importa a runtime
   ``app.core.projects.service`` ne' ``app.core.files.db`` e non costruisce
   ``ProjectService`` — legge attraverso i port di ``auth.ports``
   (adapter nei domini proprietari, binding al composition root).
2. Contratto comportamentale con reader fake iniettati: la semantica
   400/404/403 resta fail-closed e identica al percorso storico.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RESOURCE_ACCESS = _BACKEND_ROOT / "app" / "core" / "auth" / "resource_access.py"

_FORBIDDEN_RUNTIME_IMPORTS = ("app.core.projects.service", "app.core.files.db")


def _module_tree() -> ast.Module:
    return ast.parse(_RESOURCE_ACCESS.read_text(encoding="utf-8"))


def _is_type_checking_if(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    )


def test_resource_access_has_no_runtime_import_of_project_service_or_files_db() -> None:
    """C5: vietati gli import runtime dei moduli concreti. Ammesso solo il
    blocco TYPE_CHECKING (annotazioni) — il fallback lazy importa gli
    ADAPTER dei domini proprietari, mai service/db direttamente."""
    # ast.walk attraversa anche i body degli If TYPE_CHECKING: gli import
    # li' dentro sono solo annotazioni, quindi vanno esclusi (per riga).
    tree = _module_tree()
    type_checking_lines: set[int] = set()
    for node in tree.body:
        if _is_type_checking_if(node):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
    real_violations = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.lineno not in type_checking_lines
        and any(
            node.module == forbidden or node.module.startswith(forbidden + ".")
            for forbidden in _FORBIDDEN_RUNTIME_IMPORTS
        )
    ]
    assert real_violations == [], (
        "auth/resource_access.py deve leggere project/file attraverso i port "
        f"di auth.ports, non importare i moduli concreti: {real_violations}"
    )


def test_resource_access_does_not_construct_project_service() -> None:
    calls = [
        node.lineno
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "ProjectService")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "ProjectService")
        )
    ]
    assert calls == [], (
        f"auth/resource_access.py non deve istanziare ProjectService (righe {calls}): "
        "la lettura metadata passa dal port ProjectMetadataReader"
    )


# ---------------------------------------------------------------------------
# Contratto comportamentale con reader fake (DI via bind_resource_readers)
# ---------------------------------------------------------------------------


class _FakeMeta:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.id = "p-1"
        self.created_by = "u-1"


class _FakeProjectReader:
    def __init__(self, meta) -> None:
        self._meta = meta

    def get_project_metadata(self, project_id: str):
        return self._meta


class _FakeFileReader:
    def __init__(self, row) -> None:
        self._row = row

    def get_file_source(self, file_source_id: str):
        return self._row


class _User:
    def __init__(self, group_id: str, role: str = "editor") -> None:
        self.group_id = group_id
        self.role = role
        self.user_id = "u-1"
        self.username = "u"
        self.is_superadmin = role == "superadmin"


@pytest.fixture()
def resource_access():
    from app.core.auth import resource_access as mod

    mod.reset_resource_readers()
    yield mod
    mod.reset_resource_readers()


def test_assert_project_access_400_on_empty_id(resource_access) -> None:
    with pytest.raises(HTTPException) as exc:
        resource_access.assert_project_access("", _User("g1"))
    assert exc.value.status_code == 400


def test_assert_project_access_404_when_reader_returns_none(resource_access) -> None:
    resource_access.bind_resource_readers(project_metadata_reader=_FakeProjectReader(None))
    with pytest.raises(HTTPException) as exc:
        resource_access.assert_project_access("p-1", _User("g1"))
    assert exc.value.status_code == 404


def test_assert_project_access_403_cross_group_fail_closed(resource_access) -> None:
    resource_access.bind_resource_readers(
        project_metadata_reader=_FakeProjectReader(_FakeMeta(group_id="other-group"))
    )
    with pytest.raises(HTTPException) as exc:
        resource_access.assert_project_access("p-1", _User("g1"))
    assert exc.value.status_code == 403


def test_assert_project_access_allows_same_group(resource_access) -> None:
    meta = _FakeMeta(group_id="g1")
    resource_access.bind_resource_readers(project_metadata_reader=_FakeProjectReader(meta))
    assert resource_access.assert_project_access("p-1", _User("g1")) is meta


def test_assert_file_source_access_404_when_reader_returns_none(resource_access) -> None:
    resource_access.bind_resource_readers(file_source_reader=_FakeFileReader(None))
    with pytest.raises(HTTPException) as exc:
        resource_access.assert_file_source_access("fs-1", _User("g1"))
    assert exc.value.status_code == 404


def test_assert_file_source_access_403_cross_group_not_shared(
    resource_access, monkeypatch
) -> None:
    # file_sources e' shareable: il check di visibilita' condivisa interroga
    # lo share store (Postgres). Qui lo forziamo a "non condiviso" per
    # restare unit: il contratto sotto test e' il 403 fail-closed.
    monkeypatch.setattr(
        "app.core.auth.ownership.is_resource_visible", lambda *a, **kw: False
    )
    row = {"id": "fs-1", "group_id": "other-group", "created_by": "u-2"}
    resource_access.bind_resource_readers(file_source_reader=_FakeFileReader(row))
    with pytest.raises(HTTPException) as exc:
        resource_access.assert_file_source_access("fs-1", _User("g1"))
    assert exc.value.status_code == 403
