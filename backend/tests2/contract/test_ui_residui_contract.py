"""Residui del collaudo dal vivo (09/11/2026, docs/audits/ui-collaudo-live-2026-11-07.md).

Una regola per voce, ogni test fallisce senza la correzione:

- L4b: un campo obbligatorio vuoto si dice col suo nome in Valida
  (``node_validator.required_param_issues``), mai «colonna 'None'»;
- L4c: anche il testo dell'attesa in coda viene da ``PROGRESS_MESSAGES``
  (prima «Pipeline in coda...» scritto a mano in ``task_service``);
- M2: un passo senza ingressi ferma esecuzione e anteprima di Manipola con il
  messaggio di Valida e col passo nell'esito (non piu' «edge mismatch?»);
- M4: con l'Output senza ingressi Valida da' un avviso solo, non uno per passo;
- M5: l'operazione di ArcGIS passa dalla sua etichetta, nessun testo di
  avanzamento porta il dettaglio grezzo di un errore (il motivo e' nell'esito).
"""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pandas as pd
import pytest

from app.core.projects.canvas_models import CanvasState
from app.shared.execution.progress_messages import PROGRESS_MESSAGES, VALUE_LABELS, progress_text

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND = Path(__file__).resolve().parents[2]


def _canvas(nodes, edges):
    return CanvasState.model_validate({
        "nodes": [{"id": i, "type": t, "position": {"x": 0, "y": 0}, **extra} for i, t, *rest in nodes for extra in [rest[0] if rest else {}]],
        "edges": [{"id": f"{s}>{t}", "source": s, "target": t} for s, t in edges],
    })


# ── L4b ───────────────────────────────────────────────────────────────────

def _validate_filter(config):
    from app.core.manipola.node_validator import ManipolaNodeValidator
    from app.shared.execution.schema_types import InferredSchema

    node = CanvasState.model_validate({"nodes": [{"id": "f", "type": "transform", "position": {"x": 0, "y": 0},
                                                  "transform_op": "filter", "config": config}], "edges": []}).nodes[0]
    return ManipolaNodeValidator().validate_node(node, upstream=InferredSchema(columns=[{"name": "stato", "type": "str"}]))


@pytest.mark.parametrize("config", [
    {},                                                                   # Filtra appena aggiunto
    {"column": "", "operator": "=="},                                     # campo svuotato
    {"conditions": [{"column": "stato", "operator": "==", "value": "a"}, {"column": "", "operator": "=="}]},
])
def test_an_empty_required_field_is_said_with_its_name(config):
    issues = _validate_filter(config)
    assert [(i.code, i.severity) for i in issues] == [("TRANSFORM_PARAM_REQUIRED", "error")]
    assert issues[0].message == "Compila «Colonna»: il campo è obbligatorio."
    assert "None" not in issues[0].message and "Filter" not in issues[0].message


def test_a_configured_filter_is_not_flagged():
    assert _validate_filter({"conditions": [{"column": "stato", "operator": "==", "value": "a"}]}) == []


def test_every_required_parameter_has_a_label_for_the_message():
    # La regola vale per ogni operazione: il messaggio usa l'etichetta del registry.
    from app.core.manipola.plugin_executor import get_executor

    unlabeled = [f"{op}.{p['id']}" for op, spec in get_executor().registry.items()
                 for p in spec.get("parameters") or [] if p.get("required") and not p.get("label")]
    assert unlabeled == []


# ── L4c ───────────────────────────────────────────────────────────────────

def test_the_queued_text_comes_from_the_table():
    source = (BACKEND / "app/core/orchestrator/task_service.py").read_text(encoding="utf-8")
    assert "queued_message" not in source
    assert 'message=progress_text("queued")' in source
    assert progress_text("queued") == "In coda..."


def _string_constants(path):
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


STATUS_TEXT = re.compile(r"\b(in coda|in corso|completat[oa]|avvio)\b.*\.\.\.$", re.IGNORECASE)


@pytest.mark.parametrize("path", [
    "app/core/orchestrator/task_service.py", "workers/tasks/pipeline_task.py", "workers/tasks/carica_task.py",
    "workers/tasks/enrichment_task.py", "app/core/carica/dispatch.py", "app/core/arricchisci/enrichment_service.py",
])
def test_no_status_text_written_by_hand(path):
    # Testi di stato («... in coda...», «Avvio ...») scritti fuori dalla tabella.
    written = [text for text in _string_constants(path) if STATUS_TEXT.search(text.strip())]
    assert written == []


def test_the_status_probe_recognises_the_old_text():
    assert STATUS_TEXT.search("Pipeline in coda...")
    assert STATUS_TEXT.search("Task CARICA in coda...")


# ── M2 ────────────────────────────────────────────────────────────────────

def test_a_step_without_inputs_stops_with_the_message_of_valida():
    from app.core.manipola.executor import ManipolaExecutor
    from app.core.projects.canvas_access import ORPHAN_STEP_MESSAGE
    from app.core.projects.canvas_validation import validate_canvas
    from app.shared.execution.failed_node import failed_node_fields

    # Sorgente eliminata: «Solo attivi» non ha ingressi, «Unione» la segue, poi l'Output.
    canvas = _canvas([("flt", "transform", {"transform_op": "filter", "config": {"column": "a"}}),
                      ("next", "transform", {"transform_op": "filter", "config": {"column": "a"}}),
                      ("out", "result")], [("flt", "next"), ("next", "out")])
    with pytest.raises(ValueError) as caught:
        ManipolaExecutor(project_service=None)._applica_trasformazioni(canvas, {"x": pd.DataFrame({"a": [1]})})
    assert str(caught.value) == ORPHAN_STEP_MESSAGE
    assert failed_node_fields(caught.value) == {"error_node_id": "flt", "error_node_type": "transform"}
    orphan = [i for i in validate_canvas(canvas).issues if i.code == "structure.orphan"]
    assert [(i.node_id, i.message) for i in orphan] == [("flt", ORPHAN_STEP_MESSAGE)]


def test_the_technical_message_is_only_for_a_real_state_mismatch():
    source = (BACKEND / "app/core/manipola/executor.py").read_text(encoding="utf-8")
    assert "senza input, skip" not in source
    assert "raise mark_failed_node(ValueError(ORPHAN_STEP_MESSAGE), node.id, node.type)" in source


# ── M4 ────────────────────────────────────────────────────────────────────

def test_an_unconnected_output_gives_one_warning():
    from app.core.projects.canvas_validation import OUTPUT_UNCONNECTED_MESSAGE, validate_canvas

    canvas = _canvas([("src", "file_source"), ("a", "transform"), ("b", "transform"), ("c", "transform"), ("out", "result")],
                     [("src", "a"), ("a", "b"), ("b", "c")])
    issues = validate_canvas(canvas).issues
    assert [i.code for i in issues if i.code == "structure.unreached"] == []
    unconnected = [i for i in issues if i.code == "structure.output_unconnected"]
    assert [(i.node_id, i.severity, i.message) for i in unconnected] == [("out", "warning", OUTPUT_UNCONNECTED_MESSAGE)]


def test_a_connected_output_keeps_the_warning_per_unreached_step():
    from app.core.projects.canvas_validation import validate_canvas

    canvas = _canvas([("src", "file_source"), ("a", "transform"), ("out", "result"), ("src2", "file_source"), ("z", "transform")],
                     [("src", "a"), ("a", "out"), ("src2", "z")])
    codes = [(i.node_id, i.code) for i in validate_canvas(canvas).issues if i.code.startswith("structure.")]
    assert ("z", "structure.unreached") in codes and ("src2", "structure.unreached") in codes
    assert not any(code == "structure.output_unconnected" for _, code in codes)


# ── M5 ────────────────────────────────────────────────────────────────────

def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_no_progress_text_carries_the_raw_detail_of_an_error():
    assert [key for key, template in PROGRESS_MESSAGES.items() if "detail" in _placeholders(template)] == []


ARCGIS_OPERATIONS = ("add", "append", "update", "upsert", "delete", "create_service")


def test_every_arcgis_operation_has_a_label():
    # Le voci del pannello (CaricanArcGISNode) e la normalizzazione di dispatch.
    panel = (BACKEND.parent / "frontend/src/components/nodes/CaricanArcGISNode.jsx").read_text(encoding="utf-8")
    assert set(re.findall(r"\{ value: '(\w+)', label:", panel)) <= set(ARCGIS_OPERATIONS)
    assert set(VALUE_LABELS["operation"]) == set(ARCGIS_OPERATIONS)
    for op in ARCGIS_OPERATIONS:
        for key in ("load_arcgis", "load_arcgis_features"):
            text = progress_text(key, operation=op)
            assert op not in text, text
    assert "scrittura" in progress_text("load_arcgis", operation="sconosciuta")


def test_every_named_value_of_the_table_goes_through_its_label():
    # Un segnaposto con un'etichetta e' usato nella tabella (nessuna riga morta).
    used = set().union(*(_placeholders(t) for t in PROGRESS_MESSAGES.values()))
    assert set(VALUE_LABELS) <= used


def test_the_failure_of_an_enrichment_carries_its_reason_in_the_outcome():
    # Il testo «failed» non porta piu' il dettaglio: il motivo deve stare
    # nell'esito, dove la finestra lo legge (taskFailureMessage).
    source = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    block = source[source.index('progress_text("failed")'):]
    block = block[:block.index("finally:")]
    assert '"message": error_message' in block
