"""
Stima prima di avviare un eseguibile versionato (Fase 5 T2, BA2).

Contratto:
- POST /eseguibili/{id}/preflight usa gli stessi input dell'avvio, verifica gli
  upload come l'avvio, NON li consuma e non esegue niente; per Arricchisci
  restituisce la stima dei nodi API, per tutti l'impronta della preparazione;
  i file temporanei letti per la stima si eliminano sempre.
- POST /eseguibili/{id}/runs con `preparation_fingerprint` diversa da quella
  attuale risponde 409 preparation_changed, non avvia e non consuma gli upload.
- L'impronta dipende da versione, input pubblici, gruppo e connettori; non dai
  campi interni (`_upload_group_id`).
"""

from unittest.mock import patch

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import preflight as eseguibili_preflight
from tests2.eseguibili.unit.conftest import build_client
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]


def _manifest(project_type: str) -> dict:
    return {
        "schema_version": 2,
        "project_type": project_type,
        "source_project_id": "project-1",
        "inputs": [
            {"key": "dati", "label": "Dati", "kind": "file", "required": True, "order": 1},
        ],
        "bindings": [{"input_key": "dati", "target_node_id": "src", "mode": "data_source"}],
    }


def _rows(project_type: str = "arricchisci"):
    manifest = _manifest(project_type)
    canvas = {
        "nodes": [
            {"id": "src", "type": "file_source", "data": {}},
            {"id": "api1", "type": "api_enrich", "data": {"api_id": "api-1"}},
        ],
        "edges": [],
    }
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1",
        eseguibile_id="exe-1",
        version_number=1,
        schema_version=2,
        project_type=project_type,
        source_project_id="project-1",
        source_hash="hash",
        canvas=canvas,
        manifest=manifest,
        status="published",
        created_by="u-1",
        created_at="2026-11-05T00:00:00+00:00",
    )
    executable = eseguibili_db.EseguibileRow(
        id="exe-1",
        name="Executable",
        description="",
        canvas=canvas,
        expected_columns=[],
        default_output_format="xlsx",
        group_id="grp-1",
        created_by="u-1",
        created_at="2026-11-05T00:00:00+00:00",
        updated_at="2026-11-05T00:00:00+00:00",
        project_type=project_type,
        active_version_id="version-1",
        manifest=manifest,
    )
    return executable, version


UPLOAD = {"eseguibile_id": "exe-1", "user_id": "u-1", "input_key": "dati", "group_id": "grp-1"}
INPUTS = {"dati": {"file_id": "up-1", "column_mapping": []}}
ESTIMATE = [{"node_id": "api1", "api_calls": 3, "input_rows": 3, "valid": True}]


def _patched(project_type="arricchisci", upload=UPLOAD):
    executable, version = _rows(project_type)
    return [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._load_upload_meta", return_value=upload),
        patch(
            "app.core.eseguibili.preflight.api_connection_service.get_api_connection",
            return_value={"id": "api-1", "api_type": "enrichment", "token": "segreto"},
        ),
    ]


class _Ctx:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        return [p.start() for p in self.patches]

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


def test_preflight_arricchisci_estimates_with_run_inputs_and_keeps_uploads():
    client = build_client(build_current_user())
    with _Ctx(_patched()), \
         patch("app.core.eseguibili.preflight.resolve_runtime_inputs", return_value=({"nodes": [], "edges": []}, ["tmp-1"], {})) as resolve, \
         patch("app.core.eseguibili.preflight.estimate_canvas", return_value=ESTIMATE) as estimate, \
         patch("app.core.eseguibili.service.file_source_service.delete_file_source") as delete_tmp, \
         patch("app.core.eseguibili.router._delete_upload_meta") as consume:
        response = client.post("/api/eseguibili/exe-1/preflight", json={"version_id": "version-1", "inputs": INPUTS})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["nodes"] == ESTIMATE
    assert isinstance(body["preparation_fingerprint"], str) and len(body["preparation_fingerprint"]) == 64
    # Stessi input dell'avvio, con l'upload verificato.
    assert resolve.call_args.kwargs["values"]["dati"]["file_id"] == "up-1"
    estimate.assert_called_once()
    delete_tmp.assert_called_once_with("tmp-1")
    consume.assert_not_called()


def test_preflight_non_arricchisci_has_no_estimate_but_a_fingerprint():
    client = build_client(build_current_user())
    with _Ctx(_patched("estrai")), \
         patch("app.core.eseguibili.preflight.estimate_canvas") as estimate:
        response = client.post("/api/eseguibili/exe-1/preflight", json={"inputs": INPUTS})

    assert response.status_code == 200, response.text
    assert response.json()["nodes"] == []
    assert response.json()["preparation_fingerprint"]
    estimate.assert_not_called()


def test_preflight_rejects_upload_of_another_user():
    client = build_client(build_current_user())
    with _Ctx(_patched(upload={**UPLOAD, "user_id": "u-2"})), \
         patch("app.core.eseguibili.preflight.estimate_canvas") as estimate:
        response = client.post("/api/eseguibili/exe-1/preflight", json={"inputs": INPUTS})

    assert response.status_code == 403
    estimate.assert_not_called()


def test_run_with_changed_preparation_returns_409_and_keeps_uploads():
    client = build_client(build_current_user())
    with _Ctx(_patched()), \
         patch("app.core.eseguibili.router.eseguibili_service.execute_versioned") as execute, \
         patch("app.core.eseguibili.router._delete_upload_meta") as consume:
        response = client.post(
            "/api/eseguibili/exe-1/runs",
            json={"version_id": "version-1", "inputs": INPUTS, "preparation_fingerprint": "vecchia"},
        )

    assert response.status_code == 409
    assert "preparation_changed" in response.text
    execute.assert_not_called()
    consume.assert_not_called()


def test_run_with_current_fingerprint_starts():
    client = build_client(build_current_user())
    executable, version = _rows()
    fingerprint = eseguibili_preflight.preparation_fingerprint(
        eseguibile_id="exe-1",
        version_id="version-1",
        group_id="grp-1",
        raw_inputs={"dati": {"file_id": "up-1", "column_mapping": [], "_upload_group_id": "grp-1"}},
        connections={"api-1": {"id": "api-1", "api_type": "enrichment", "token": "segreto"}},
        identity_values={},
    )

    class _Result:
        def to_api_dict(self):
            return {"async": True, "task_id": "task-1"}

    with _Ctx(_patched()), \
         patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"), \
         patch("app.core.eseguibili.router._save_task_owner"), \
         patch("app.core.eseguibili.router.eseguibili_service.execute_versioned", return_value=_Result()) as execute, \
         patch("app.core.eseguibili.router._delete_upload_meta") as consume:
        response = client.post(
            "/api/eseguibili/exe-1/runs",
            json={"version_id": "version-1", "inputs": INPUTS, "preparation_fingerprint": fingerprint},
        )

    assert response.status_code == 200, response.text
    execute.assert_called_once()
    consume.assert_called_once_with("up-1")


def test_fingerprint_depends_on_public_inputs_version_and_connections_only():
    base = dict(
        eseguibile_id="exe-1",
        version_id="v1",
        group_id="grp-1",
        raw_inputs={"dati": {"file_id": "up-1", "_upload_group_id": "grp-1"}},
        connections={"api-1": {"token": "a"}},
        identity_values={},
    )
    fp = eseguibili_preflight.preparation_fingerprint(**base)
    assert fp == eseguibili_preflight.preparation_fingerprint(**{**base, "raw_inputs": {"dati": {"file_id": "up-1", "_upload_group_id": "altro"}}})
    assert fp != eseguibili_preflight.preparation_fingerprint(**{**base, "raw_inputs": {"dati": {"file_id": "up-2"}}})
    assert fp != eseguibili_preflight.preparation_fingerprint(**{**base, "version_id": "v2"})
    assert fp != eseguibili_preflight.preparation_fingerprint(**{**base, "connections": {"api-1": {"token": "b"}}})
    # Utente che esegue: due utenti = due impronte.
    assert fp != eseguibili_preflight.preparation_fingerprint(**{**base, "identity_values": {"utente": "mario"}})
    mario = eseguibili_preflight.preparation_fingerprint(**{**base, "identity_values": {"utente": "mario"}})
    assert mario != eseguibili_preflight.preparation_fingerprint(**{**base, "identity_values": {"utente": "anna"}})
