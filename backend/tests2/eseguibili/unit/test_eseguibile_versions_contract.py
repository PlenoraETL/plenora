from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import service as eseguibili_service


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _version(*, version_number: int = 2) -> eseguibili_db.EseguibileVersionRow:
    return eseguibili_db.EseguibileVersionRow(
        id=f"exec-1-v{version_number}",
        eseguibile_id="exec-1",
        version_number=version_number,
        schema_version=2,
        project_type="manipola",
        source_project_id="project-1",
        source_hash="hash",
        canvas={"nodes": [{"id": "out", "type": "result"}], "edges": []},
        manifest={
            "schema_version": 2,
            "project_type": "manipola",
            "source_project_id": "project-1",
            "inputs": [
                {
                    "key": "file",
                    "label": "File",
                    "kind": "file",
                    "expected_columns": [{"name": "id", "required": True}],
                }
            ],
            "bindings": [],
        },
        status="published",
        created_by="user-1",
        created_at="2026-07-16T10:00:00+00:00",
    )


def _executable() -> eseguibili_db.EseguibileRow:
    return eseguibili_db.EseguibileRow(
        id="exec-1",
        name="Executable",
        description="",
        canvas={},
        expected_columns=[],
        default_output_format="xlsx",
        group_id="group-1",
        created_by="user-1",
        created_at="2026-07-16T09:00:00+00:00",
        updated_at="2026-07-16T09:00:00+00:00",
        source_canvas_id="project-1",
        project_type="manipola",
        active_version_id="exec-1-v1",
    )


def test_activate_version_synchronizes_legacy_projection(monkeypatch) -> None:
    version = _version()
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        eseguibili_db,
        "get_eseguibile_version",
        lambda *_args, **_kwargs: version,
    )
    monkeypatch.setattr(
        eseguibili_db,
        "_execute",
        lambda sql, params=(): calls.append((sql, params)),
    )

    eseguibili_db.activate_eseguibile_version("exec-1", version.id)

    assert len(calls) == 1
    _sql, params = calls[0]
    assert params[0] == version.id
    assert params[1] == "manipola"
    assert json.loads(params[2]) == version.canvas
    assert json.loads(params[3]) == [{"name": "id", "required": True}]


def test_republish_compiles_and_activates_new_snapshot(monkeypatch) -> None:
    executable = _executable()
    canvas = {
        "nodes": [
            {
                "id": "input",
                "type": "project_input",
                "input_key": "file",
                "label": "File",
                "input_kind": "file",
            },
            {"id": "out", "type": "result"},
        ],
        "edges": [{"id": "e1", "source": "input", "target": "out"}],
    }
    monkeypatch.setattr(
        eseguibili_service.project_access_context,
        "load_project_publish_context",
        lambda _project_id: SimpleNamespace(
            project_id="project-1",
            group_id="group-1",
            project_type="manipola",
            canvas=canvas,
            default_connection_id=None,
        ),
    )
    # Versione attiva: stesso input «file» (nessun input tolto o cambiato).
    monkeypatch.setattr(
        eseguibili_db,
        "get_eseguibile_version",
        lambda *_a, **_k: _version(version_number=1),
    )
    created = _version()
    create_calls: list[dict] = []
    activated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        eseguibili_db,
        "create_eseguibile_version",
        lambda **kwargs: create_calls.append(kwargs) or created,
    )
    monkeypatch.setattr(
        eseguibili_db,
        "activate_eseguibile_version",
        lambda executable_id, version_id: activated.append(
            (executable_id, version_id)
        ),
    )
    monkeypatch.setattr(
        eseguibili_db,
        "get_eseguibile",
        lambda _executable_id: executable,
    )

    result = eseguibili_service.publish_new_version(
        eseguibile=executable,
        created_by="user-1",
    )

    assert result is executable
    assert create_calls[0]["manifest"]["inputs"][0]["key"] == "file"
    assert activated == [("exec-1", created.id)]


def test_republish_rejects_project_type_change(monkeypatch) -> None:
    monkeypatch.setattr(
        eseguibili_service.project_access_context,
        "load_project_publish_context",
        lambda _project_id: SimpleNamespace(
            project_id="project-1",
            group_id="group-1",
            project_type="estrai",
            canvas={"nodes": []},
            default_connection_id="conn-1",
        ),
    )

    with pytest.raises(ValueError, match="tipo del progetto sorgente"):
        eseguibili_service.publish_new_version(
            eseguibile=_executable(),
            created_by="user-1",
        )
