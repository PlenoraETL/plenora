"""
File temporanei degli input runtime (Fase 5, correzione dopo la conferma di
T2+T4, punto 5).

Regola: ogni file temporaneo materializzato da ``resolve_runtime_inputs`` si
elimina su QUALUNQUE uscita con errore (un solo try nella funzione che li
crea; ``execute_versioned`` li elimina se fallisce dopo averli ricevuti);
``_trusted_run_inputs`` rifiuta un input file obbligatorio senza file
(assente, null, lista vuota) prima di materializzare qualunque file.

Il test enumera le uscite con errore con DUE input file (il primo valido e
gia' materializzato) e verifica, direttamente e da /preflight e /runs, che
nessun file temporaneo resti.
"""

from itertools import count
from unittest.mock import patch

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.manifest import ExecutableManifest
from app.core.eseguibili.runtime import resolve_runtime_inputs
from tests2.eseguibili.unit.conftest import build_client
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]


def _manifest_dict(*, second_multiple: bool = False, second_target: str = "src2", second_required: bool = True) -> dict:
    return {
        "schema_version": 2,
        "project_type": "arricchisci",
        "source_project_id": "project-1",
        "inputs": [
            {"key": "primo", "label": "Primo", "kind": "file", "required": True, "order": 1},
            {
                "key": "secondo", "label": "Secondo", "kind": "file", "required": second_required,
                "order": 2, "multiple": second_multiple,
            },
        ],
        "bindings": [
            {"input_key": "primo", "target_node_id": "src1", "mode": "data_source"},
            {"input_key": "secondo", "target_node_id": second_target, "mode": "data_source"},
        ],
    }


CANVAS = {
    "nodes": [
        {"id": "src1", "type": "file_source", "data": {}},
        {"id": "src2", "type": "file_source", "data": {}},
    ],
    "edges": [],
}
FILE = {"file_id": "up-1", "column_mapping": []}


class _TempFiles:
    """Registro dei file temporanei creati ed eliminati."""

    def __init__(self):
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._ids = count(1)

    def materialize(self, **_kwargs):
        temp_id = f"tmp-{next(self._ids)}"
        self.created.append(temp_id)
        return temp_id

    def delete(self, file_id):
        self.deleted.append(file_id)

    @property
    def remaining(self) -> list[str]:
        return [item for item in self.created if item not in self.deleted]


def _patch_temp_files(files: _TempFiles):
    return [
        patch("app.core.eseguibili.runtime._materialize_file_input", side_effect=files.materialize),
        patch("app.core.eseguibili.runtime.file_source_service.delete_file_source", side_effect=files.delete),
    ]


class _Ctx:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        return [p.start() for p in self.patches]

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


# Ogni uscita con errore dopo che il PRIMO file e' gia' materializzato
# (caso -> (manifest, valori, file creati prima dell'errore)).
FAILURES = {
    "secondo mancante": (dict(), {"primo": FILE}, 1),
    "secondo null": (dict(), {"primo": FILE, "secondo": None}, 1),
    "secondo lista vuota (multi-file)": (dict(second_multiple=True), {"primo": FILE, "secondo": []}, 1),
    "secondo lista non di file (multi-file)": (dict(second_multiple=True), {"primo": FILE, "secondo": ["x"]}, 1),
    "secondo payload non valido": (dict(), {"primo": FILE, "secondo": "non-un-file"}, 1),
    "binding verso un nodo inesistente": (dict(second_target="manca"), {"primo": FILE, "secondo": FILE}, 2),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_resolve_runtime_inputs_removes_every_temp_file_on_any_error(case):
    manifest_kwargs, values, expected_created = FAILURES[case]
    manifest = ExecutableManifest.model_validate(_manifest_dict(**manifest_kwargs))
    files = _TempFiles()
    with _Ctx(_patch_temp_files(files)):
        with pytest.raises(ValueError):
            resolve_runtime_inputs(
                eseguibile_id="exe-1", canvas=CANVAS, manifest=manifest, values=values, group_id="grp-1",
            )
    assert len(files.created) == expected_created, case
    assert files.remaining == [], case


def test_resolve_runtime_inputs_removes_temp_files_when_materializing_the_second_fails():
    manifest = ExecutableManifest.model_validate(_manifest_dict())
    files = _TempFiles()
    calls = count()

    def materialize(**kwargs):
        if next(calls) == 1:
            raise RuntimeError("storage giu")
        return files.materialize(**kwargs)

    with _Ctx([
        patch("app.core.eseguibili.runtime._materialize_file_input", side_effect=materialize),
        patch("app.core.eseguibili.runtime.file_source_service.delete_file_source", side_effect=files.delete),
    ]):
        with pytest.raises(RuntimeError):
            resolve_runtime_inputs(
                eseguibile_id="exe-1", canvas=CANVAS, manifest=manifest,
                values={"primo": FILE, "secondo": FILE}, group_id="grp-1",
            )
    assert files.created == ["tmp-1"]
    assert files.remaining == []


def test_resolve_runtime_inputs_success_hands_the_files_to_the_caller():
    manifest = ExecutableManifest.model_validate(_manifest_dict())
    files = _TempFiles()
    with _Ctx(_patch_temp_files(files)):
        _canvas, cleanup_ids, _summary = resolve_runtime_inputs(
            eseguibile_id="exe-1", canvas=CANVAS, manifest=manifest,
            values={"primo": FILE, "secondo": FILE}, group_id="grp-1",
        )
    assert cleanup_ids == ["tmp-1", "tmp-2"]
    assert files.deleted == []


# ------------------------------------------------------------- endpoint ---

def _rows(manifest: dict):
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1", eseguibile_id="exe-1", version_number=1, schema_version=2,
        project_type="arricchisci", source_project_id="project-1", source_hash="hash",
        canvas=CANVAS, manifest=manifest, status="published", created_by="u-1",
        created_at="2026-11-05T00:00:00+00:00",
    )
    executable = eseguibili_db.EseguibileRow(
        id="exe-1", name="Executable", description="", canvas=CANVAS, expected_columns=[],
        default_output_format="xlsx", group_id="grp-1", created_by="u-1",
        created_at="2026-11-05T00:00:00+00:00", updated_at="2026-11-05T00:00:00+00:00",
        project_type="arricchisci", active_version_id="version-1", manifest=manifest,
    )
    return executable, version


def _endpoint_patches(manifest: dict, files: _TempFiles):
    executable, version = _rows(manifest)

    def upload_meta(file_id):
        key = {"up-1": "primo", "up-2": "secondo"}.get(file_id)
        return {"eseguibile_id": "exe-1", "user_id": "u-1", "input_key": key, "group_id": "grp-1"}

    return [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._load_upload_meta", side_effect=upload_meta),
        patch("app.core.eseguibili.router._delete_upload_meta"),
        patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"),
        patch("app.core.eseguibili.router._save_task_owner"),
        patch("app.core.eseguibili.router._output_store.delete_task_owner"),
        patch("app.core.eseguibili.preflight.estimate_canvas", return_value=[]),
        *_patch_temp_files(files),
    ]


SECOND = {"file_id": "up-2", "column_mapping": []}
ENDPOINT_FAILURES = {
    # Rifiutati prima di materializzare (nessun file creato).
    "secondo mancante": (dict(), {"primo": FILE}, 0),
    "secondo null": (dict(), {"primo": FILE, "secondo": None}, 0),
    "secondo lista vuota (multi-file)": (dict(second_multiple=True), {"primo": FILE, "secondo": []}, 0),
    # Superano la verifica degli upload e falliscono preparando il secondo.
    "binding verso un nodo inesistente": (dict(second_target="manca"), {"primo": FILE, "secondo": SECOND}, 2),
}


@pytest.mark.parametrize("endpoint", ["preflight", "runs"])
@pytest.mark.parametrize("case", sorted(ENDPOINT_FAILURES))
def test_endpoints_leave_no_temp_file_on_input_errors(endpoint, case):
    manifest_kwargs, inputs, expected_created = ENDPOINT_FAILURES[case]
    manifest = _manifest_dict(**manifest_kwargs)
    files = _TempFiles()
    client = build_client(build_current_user())
    with _Ctx(_endpoint_patches(manifest, files)):
        response = client.post(f"/api/eseguibili/exe-1/{endpoint}", json={"version_id": "version-1", "inputs": inputs})
    assert response.status_code == 400, response.text
    assert len(files.created) == expected_created, (endpoint, case)
    assert files.remaining == [], (endpoint, case)


def test_execute_versioned_removes_temp_files_when_it_fails_after_resolving():
    manifest = _manifest_dict()
    executable, version = _rows(manifest)
    files = _TempFiles()
    with _Ctx([
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch(
            "app.core.eseguibili.service.resolve_runtime_connection_id",
            side_effect=ValueError("connessione runtime non valida"),
        ),
        *_patch_temp_files(files),
    ]):
        with pytest.raises(ValueError):
            eseguibili_service.execute_versioned(
                eseguibile=executable,
                raw_inputs={"primo": FILE, "secondo": SECOND},
                user_id="u-1",
                version_id="version-1",
                task_id="task-1",
            )
    assert files.created == ["tmp-1", "tmp-2"]
    assert files.remaining == []


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("empty", ["assente", "null", "lista vuota"])
def test_optional_file_input_without_file_is_accepted(multiple, empty):
    """Regola unica: un input file FACOLTATIVO senza file (assente, null o
    lista vuota) e' "nessun file" sia nella verifica degli upload sia nella
    risoluzione (rilettura T2+T4, punto 4)."""
    manifest = _manifest_dict(second_multiple=multiple, second_required=False)
    inputs = {"primo": FILE}
    if empty == "null":
        inputs["secondo"] = None
    elif empty == "lista vuota":
        inputs["secondo"] = []
    files = _TempFiles()
    client = build_client(build_current_user())
    with _Ctx(_endpoint_patches(manifest, files)):
        response = client.post("/api/eseguibili/exe-1/preflight", json={"version_id": "version-1", "inputs": inputs})
    assert response.status_code == 200, response.text
    assert files.remaining == []
