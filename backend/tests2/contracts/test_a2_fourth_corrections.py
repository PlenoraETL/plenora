"""G1/G2: DataFrame reale e lettura DB opzionale."""
import pandas as pd
import psycopg
import pytest

from app.core.dataframes.schema import resolve_column_type
from app.core.projects import schema_execution_sources
from tests2.contracts.test_a2_third_corrections import publish, scenario


@pytest.mark.parametrize("case", ["autoflatten", "normale", "colonna_vuota", "colonna_nulla"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("generator", [False, True])
def test_numeric_publishers(monkeypatch, case, empty, generator):
    frame, canvas, api = scenario(case, empty, generator)
    expected = {"x": "number", "gen": "integer", "id": "integer", "items[*].k": "number"}
    for name, kind in expected.items():
        frame[name] = pd.Series([3] * len(frame), dtype="float64" if kind == "number" else "int64")
    api["output_mapping"] = [{"path": name, "column": name, "type": "date"} for name in frame.columns]
    worker = publish(monkeypatch, "worker", frame, canvas, api)
    assert worker["columns"] == publish(monkeypatch, "enrich", frame, canvas, api)["columns"]
    types = {c["physical_name"]: c["type"]["logical"] for c in worker["columns"]}
    assert {name: types[name] for name in expected} == expected


@pytest.mark.parametrize("dtype", [object, "str"])
def test_declared_type_cannot_convert_text(monkeypatch, dtype):
    _, canvas, api = scenario("normale")
    frame = pd.DataFrame({name: pd.Series(["testo incompatibile"], dtype=dtype) for name in ["giorno", "n"]})
    api["output_mapping"] = [{"path": "giorno", "column": "giorno", "type": "date"},
                             {"path": "n", "column": "n", "type": "int"}]
    worker = publish(monkeypatch, "worker", frame, canvas, api)
    assert worker["columns"] == publish(monkeypatch, "enrich", frame, canvas, api)["columns"]
    assert [c["type"]["logical"] for c in worker["columns"]] == ["string", "string"]


def test_null_declaration_and_recognition():
    assert resolve_column_type(series=pd.Series([None], dtype=object), declared="int") == "int"
    assert resolve_column_type(series=pd.Series(["2026-11-04"]), declared=None) == "date"


@pytest.mark.parametrize("error", [psycopg.Error, RuntimeError, OSError, ValueError])
def test_optional_db_failure_preserves_issues(monkeypatch, error):
    from app.core.projects import canvas_validation as module
    from app.core.arricchisci.node_validator import ArricchisciNodeValidator
    _, canvas, _ = scenario("colonna_vuota_nodo")
    monkeypatch.setattr(module, "validator_for", lambda kind: ArricchisciNodeValidator() if kind == "api_enrich" else None)
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: {})
    expected = module.validate_canvas(canvas, project_id="p", project_type="arricchisci")
    def fail(_):
        raise error("DB non disponibile")
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", fail)
    logs = []
    from types import SimpleNamespace
    monkeypatch.setattr(module, "get_logger", lambda _: SimpleNamespace(warning=lambda *a, **kw: logs.append(a)))
    actual = module.validate_canvas(canvas, project_id="p", project_type="arricchisci")
    assert [i.model_dump() for i in actual.issues] == [i.model_dump() for i in expected.issues if i.code != "ARRICCHISCI_EMPTY_MAPPING_DESTINATION"]
    assert logs


def test_api_facade_forwards_real_frame_finalizer(monkeypatch):
    from app.core.arricchisci import canvas_orchestrator
    from app.core.arricchisci.api_connector import ApiConnectionService
    frame = pd.DataFrame({"x": [1.5]})
    received = []
    def orchestrate(svc, **kwargs):
        return kwargs["finalizer"](frame, "result", "csv", "p")
    monkeypatch.setattr(canvas_orchestrator, "execute_canvas_enrichment", orchestrate)
    def finalize(actual, *args):
        received.append(actual)
        return {"success": True}
    assert ApiConnectionService().execute_enrichment("p", {}, finalizer=finalize)["success"]
    assert received[0] is frame


def test_object_numeric_declaration_is_not_overruled():
    assert resolve_column_type(series=pd.Series([1], dtype=object), declared="int") == "int"
