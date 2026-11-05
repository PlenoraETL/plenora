"""«Utente che esegue» COME SORGENTE in Manipola: una riga, una colonna.

Configurazione reale dell'utente (regola: username "E" + 4 cifre -> istituto
"IS_" + le prime 3 cifre; molti utenti sullo stesso istituto):

    [Utente che esegue, sorgente, colonna `utente`]
        -> [Formula: istituto = 'IS_' + utente.str.slice(1, 4)]
        -> [Join interno (sinistra) istituto = istituto (destra)] <- [dati]
        -> [Output]

- la stessa funzione server inietta lo username (mai dal client, blocco
  senza utente); il nodo sorgente diventa Valori statici di una riga;
- E1301 ed E1302 vedono le stesse righe (solo IS_130), E1121 solo IS_112;
- previsione delle colonne (POST /schemas sul canvas dell'editor) =
  colonne dell'esecuzione;
- anteprima e Valida usano il nome utente di prova; Estrai e Arricchisci non
  accettano la sorgente.
"""

from __future__ import annotations

import copy
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili.manifest import compile_manifest
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import apply_identity_inputs
from app.core.projects.canvas_models import CanvasState
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.shared.execution.runtime_identity import (
    RuntimeIdentity,
    RuntimeIdentityError,
    assert_identity_resolved,
)
from app.shared.execution.runtime_input_kinds import project_input_role
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

POS = {"x": 0, "y": 0}
FORMULA = "'IS_' + utente.str.slice(1, 4)"
DATI = pd.DataFrame({
    "istituto": ["IS_112", "IS_130", "IS_130", "IS_999"],
    "pratica": [1, 2, 3, 4],
})


def _canvas(**identity_extra) -> dict:
    return {"nodes": [
        {"id": "utente", "type": "project_input", "position": POS, "input_key": "utente", "label": "Utente",
         "input_kind": "current_user", "as_source": True, "source_column": "utente", **identity_extra},
        {"id": "f", "type": "transform", "position": POS, "transform_op": "formula",
         "config": {"new_column": "istituto", "formula": FORMULA}},
        {"id": "dati", "type": "file_source", "position": {"x": 0, "y": 200}, "file_source_id": "fs-dati",
         "columns": [{"name": "istituto", "type": "str", "visible": True},
                     {"name": "pratica", "type": "int", "visible": True}]},
        {"id": "j", "type": "transform", "position": POS, "transform_op": "join",
         "config": {"left_keys": ["istituto"], "right_keys": ["istituto"], "how": "inner"}},
        {"id": "r", "type": "result", "position": POS, "format": "csv", "fileName": "out"},
    ], "edges": [
        {"id": "e1", "source": "utente", "target": "f"},
        {"id": "e2", "source": "f", "target": "j", "targetHandle": "input-0"},
        {"id": "e3", "source": "dati", "target": "j", "targetHandle": "input-1"},
        {"id": "e4", "source": "j", "target": "r"},
    ]}


def _manifest(canvas: dict, project_type: str = "manipola"):
    return compile_manifest(source_project_id="p", project_type=project_type, canvas=canvas,
                            default_connection_id="conn")


def _resolved(username: str, canvas: dict | None = None) -> dict:
    canvas = canvas or _canvas()
    resolved, cleanup, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest(canvas), values={}, group_id="g",
        identity=RuntimeIdentity.authenticated(build_current_user(username=username)))
    assert cleanup == []
    return resolved


def _service(canvas: CanvasState, captured: dict):
    return SimpleNamespace(
        get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(group_id="g", project_type="manipola"),
        get_project_output_contract=lambda _: None,
        update_project_output_contract=lambda _, value: captured.update(contract=value))


def _execute(canvas_dict: dict, monkeypatch) -> pd.DataFrame:
    """Esecuzione Manipola reale (ManipolaExecutor) del canvas risolto."""
    from app.core import artifacts
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    from app.core.manipola import executor as module

    canvas = CanvasState(**canvas_dict)
    captured: dict = {}

    def persist(**kwargs):
        captured["frame"] = kwargs["df"].copy()
        captured["bytes"] = save_dataframe_to_bytes(kwargs["df"], "csv")
        return SimpleNamespace(output_filename="out.csv", result_id="id", output_columns=list(kwargs["df"].columns))

    def load_with_metadata(self, ref, **kwargs):
        assert ref.file_source_id == "fs-dati"
        return SimpleNamespace(data=DATI.copy(), metadata=None)

    monkeypatch.setattr(module, "save_pipeline_result_outcome", persist)
    monkeypatch.setattr(module, "append_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(artifacts.SourceLoader, "load_with_metadata", load_with_metadata)
    result = module.ManipolaExecutor(project_service=_service(canvas, captured)).execute(
        "exe-1", canvas, execution_context={"group_id": "g"}, intermediate_mode_override="none")
    assert result["success"], result
    return captured["frame"]


# ---------------------------------------------------------------- contratto


def test_the_source_is_a_data_source_binding_of_the_node_itself():
    manifest = _manifest(_canvas())
    (definition,) = manifest.inputs
    assert (definition.kind, definition.required, definition.default_value) == ("current_user", True, None)
    assert [(b.mode, b.target_node_id, b.target_field) for b in manifest.bindings] == [("data_source", "utente", None)]
    assert project_input_role(_canvas()["nodes"][0]) == "source"


@pytest.mark.parametrize("project_type", ["estrai", "arricchisci"])
def test_the_source_is_manipola_only(project_type):
    with pytest.raises(ValueError, match="come sorgente"):
        _manifest(_canvas(), project_type)


def test_source_rejects_parameter_bindings_and_unsafe_column_names():
    with pytest.raises(ValueError, match="sorgente"):
        _manifest(_canvas(bindings=[{"target_node_id": "f", "target_field": "config.formula"}]))
    with pytest.raises(ValueError, match="Nome colonna"):
        _manifest(_canvas(source_column="nome utente"))


def test_resolution_turns_the_node_into_one_static_row():
    resolved = _resolved("E1121")
    node = next(n for n in resolved["nodes"] if n["id"] == "utente")
    assert node["type"] == "staticValues"
    assert node["fields"] == [{"name": "utente", "value": "E1121", "field_type": "string"}]
    assert {e["id"] for e in resolved["edges"]} == {"e1", "e2", "e3", "e4"}
    assert_identity_resolved(resolved)


def test_same_server_rule_client_value_rejected_and_no_user_blocks():
    canvas = _canvas()
    with pytest.raises(RuntimeIdentityError) as err:
        executable_runtime.resolve_runtime_inputs(
            eseguibile_id="exe-1", canvas=canvas, manifest=_manifest(canvas), values={"utente": "E1301"},
            group_id="g", identity=RuntimeIdentity.authenticated(build_current_user(username="E1121")))
    assert err.value.code == error_codes.RUNTIME_IDENTITY_CLIENT_VALUE
    with pytest.raises(RuntimeIdentityError) as err:
        executable_runtime.resolve_runtime_inputs(
            eseguibile_id="exe-1", canvas=canvas, manifest=_manifest(canvas), values={},
            group_id="g", identity=RuntimeIdentity.unattended())
    assert err.value.code == error_codes.RUNTIME_IDENTITY_REQUIRED
    # Schedule/catene: il nodo non risolto arriva agli executor e blocca.
    with pytest.raises(RuntimeIdentityError):
        assert_identity_resolved(canvas)


def test_template_variables_in_a_username_never_change_the_filter():
    with pytest.raises(RuntimeIdentityError, match="nome utente"):
        _resolved("{{TODAY}}")


# ---------------------------------------------------------------- end-to-end


def test_each_user_sees_only_their_institute(monkeypatch):
    frames = {user: _execute(_resolved(user), monkeypatch) for user in ("E1121", "E1301", "E1302", "E1999")}
    # Colonne del Join (suffissi _L/_R del passo): utente_L, istituto, pratica_R.
    assert list(frames["E1301"].columns) == ["utente_L", "istituto", "pratica_R"]
    rows = {user: frame[["istituto", "pratica_R"]].values.tolist() for user, frame in frames.items()}
    assert rows["E1121"] == [["IS_112", 1]]
    assert rows["E1301"] == rows["E1302"] == [["IS_130", 2], ["IS_130", 3]]
    assert rows["E1999"] == []
    assert set(frames["E1302"]["utente_L"]) == {"E1302"}


def test_schemas_predicted_in_the_editor_match_the_execution(monkeypatch):
    from app.core.manipola.canvas_schema_runtime import build_columns_with_types
    from app.core.projects import router
    from app.core.projects.schema_inference import SchemaInferenceService

    editor_canvas = CanvasState(**_canvas(preview_username="E1121"))
    captured: dict = {}
    service = _service(editor_canvas, captured)
    monkeypatch.setattr(router, "_project_service", service)
    monkeypatch.setattr(router, "_schema_service", SchemaInferenceService(service))
    monkeypatch.setattr(router, "assert_group_access", lambda *args: None)
    monkeypatch.setattr(router, "_prepare_canvas_for_owner", lambda _, canvas, *args, **kwargs: canvas)
    schemas = router.infer_project_schemas(
        "p", router.SchemaInferenceRequest(canvas=editor_canvas.model_dump(exclude_unset=True)),
        CurrentUser(user_id="u", username="u", role="admin", group_id="g"))["schemas"]
    assert [(c["name"], c["type"]) for c in schemas["utente"]] == [("utente", "str")]
    executed = _execute(_resolved("E1301"), monkeypatch)
    assert [c["name"] for c in schemas["r"]] == list(executed.columns)
    executed_types = {c["name"]: c["type"] for c in build_columns_with_types(executed, {})}
    assert {c["name"]: c["type"] for c in schemas["r"]} == executed_types
    # Stessi id di colonna prima e dopo la risoluzione (sorgente = Valori statici).
    resolved_schemas = SchemaInferenceService(None).infer_all_schemas("p", CanvasState(**_resolved("E1301")))
    resolved_ids = [getattr(c, "id", None) or c["id"] for c in resolved_schemas["utente"]]
    assert resolved_ids == [c["id"] for c in schemas["utente"]]


def test_preview_and_validate_use_the_test_username():
    from app.core.projects.canvas_validation import validate_canvas

    contract = PipelineExecutionContract(project_id="p", project_type="manipola", group_id="g",
                                         canvas=_canvas(preview_username="E1121"),
                                         runtime_context={"default_connection_id": "conn"})
    previewed = apply_identity_inputs(contract, RuntimeIdentity.preview_of(contract.canvas))
    node = next(n for n in previewed.canvas["nodes"] if n["id"] == "utente")
    assert node["fields"][0]["value"] == "E1121"
    with pytest.raises(RuntimeIdentityError):
        apply_identity_inputs(contract.model_copy(update={"canvas": _canvas()}),
                              RuntimeIdentity.preview_of(_canvas()))
    report = validate_canvas(CanvasState(**_canvas()), project_id="p", project_type="manipola")
    codes = {issue.code for issue in report.issues}
    assert "runtime_inputs.invalid" not in codes and "SOURCE_ISOLATED" not in codes


def test_csv_result_has_the_institute_rows_only(monkeypatch):
    frame = _execute(_resolved("E1121"), monkeypatch)
    text = frame.to_csv(index=False)
    assert "IS_112" in text and "IS_130" not in text and "IS_999" not in text
    assert BytesIO(text.encode()).getvalue()
    assert copy.deepcopy(frame).shape[0] == 1
