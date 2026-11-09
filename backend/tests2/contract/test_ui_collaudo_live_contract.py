"""Collaudo dal vivo dopo il deploy di 42563405 (07/11/2026): L3, L4, L5.

Una regola per voce, ogni test fallisce senza la correzione:

- L3: si esegue solo cio' che arriva all'Output (``canvas_preview_scope``):
  regola su canvas tipizzati e dict, tutti gli ingressi delle esecuzioni e
  delle anteprime, Valida con i rami scollegati come avviso;
- L4: i testi di avanzamento vengono dalla tabella ``PROGRESS_MESSAGES``
  (nessuna stringa scritta a mano nelle chiamate), numeri formattati, niente
  id nei messaggi di Valida;
- L5: il salvataggio con un passo senza ingresso e' in
  ``tests2/projects/test_unsaved_canvas_schemas.py``; qui Valida.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.projects.canvas_models import CanvasState
from app.core.projects.canvas_preview_scope import (
    OFF_BRANCH_TYPES,
    run_canvas,
    scoped_canvas,
    unreached_node_ids,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

BACKEND = Path(__file__).resolve().parents[2]


def _canvas(nodes, edges):
    return CanvasState.model_validate({
        "nodes": [{"id": i, "type": t, "position": {"x": 0, "y": 0}} for i, t in nodes],
        "edges": [{"id": f"{s}>{t}", "source": s, "target": t} for s, t in edges],
    })


# Sorgente -> Filtra -> Output -> Carica; un ramo Sorgente2 -> Formula che non
# arriva all'Output; parametri, input runtime e note scollegati.
NODES = [
    ("src", "file_source"), ("flt", "transform"), ("out", "result"), ("load", "carica"),
    ("src2", "project_source"), ("calc", "transform"), ("calc2", "transform"),
    ("vals", "staticValues"), ("inp", "project_input"), ("note", "comment"),
]
EDGES = [("src", "flt"), ("flt", "out"), ("out", "load"), ("src2", "calc"), ("calc", "calc2")]


# ── L3: la regola ─────────────────────────────────────────────────────────

def test_run_scope_drops_only_the_branches_that_do_not_reach_the_output():
    canvas = _canvas(NODES, EDGES)
    assert unreached_node_ids(canvas) == {"src2", "calc", "calc2"}
    scoped = run_canvas(canvas)
    assert [n.id for n in scoped.nodes] == ["src", "flt", "out", "load", "vals", "inp", "note"]
    assert {(e.source, e.target) for e in scoped.edges} == {("src", "flt"), ("flt", "out"), ("out", "load")}
    # I nodi che non sono rami restano (parametri, input runtime, note).
    assert {"staticValues", "project_input", "comment"} == set(OFF_BRANCH_TYPES)


def test_run_scope_same_rule_on_the_dict_canvas_of_the_enrichment_worker():
    canvas = _canvas(NODES, EDGES).model_dump()
    scoped = run_canvas(canvas)
    assert isinstance(scoped, dict)
    assert {n["id"] for n in scoped["nodes"]} == {"src", "flt", "out", "load", "vals", "inp", "note"}
    assert canvas["nodes"] != scoped["nodes"]  # l'originale non cambia


def test_run_scope_a_branch_reaching_only_a_load_does_not_reach_a_final_node():
    # Un Carica parte solo se collegato all'Output (carica_dispatcher).
    canvas = _canvas(NODES + [("load2", "carica")], EDGES + [("calc2", "load2")])
    assert {"calc", "calc2", "load2"} <= unreached_node_ids(canvas)


def test_run_scope_without_output_keeps_the_canvas():
    canvas = _canvas([("src", "file_source"), ("flt", "transform")], [("src", "flt")])
    assert run_canvas(canvas) is canvas
    assert unreached_node_ids(canvas) == set()


def test_scoped_canvas_previews_a_step_or_the_output():
    canvas = _canvas(NODES, EDGES)
    assert {n.id for n in scoped_canvas(canvas, "calc").nodes} == {"src2", "calc"}
    assert "calc" not in {n.id for n in scoped_canvas(canvas, None).nodes}


# ── L3: ogni ingresso delle esecuzioni e delle anteprime passa dalla regola ─

class _Recorder:
    def __init__(self):
        self.canvas = None

    def execute(self, project_id, canvas, *args, **kwargs):
        self.canvas = canvas
        return {"success": True, "message": "", "rows_count": 1}

    def preview(self, project_id, canvas, limit, **kwargs):
        self.canvas = canvas
        return {"success": True}


@pytest.mark.parametrize("project_type", ["estrai", "manipola", "arricchisci"])
def test_every_execution_receives_only_what_reaches_the_output(project_type, monkeypatch):
    from app.core.orchestrator import service as service_mod

    svc = service_mod.OrchestratorService.__new__(service_mod.OrchestratorService)
    recorders = {name: _Recorder() for name in ("estrai", "manipola", "arricchisci")}
    svc.estrai_executor = recorders["estrai"]
    svc.manipola_executor = recorders["manipola"]
    svc.enrich_executor = recorders["arricchisci"]
    carica = MagicMock(return_value=None)
    monkeypatch.setattr(svc, "_execute_carica_if_present", carica)
    monkeypatch.setattr(service_mod, "assert_identity_resolved", lambda canvas: None)

    canvas = _canvas(NODES, EDGES)
    svc._execute_loaded_pipeline(
        project_id="p1", project_type=project_type, canvas=canvas,
        force_recalculate=False, skip_carica=False,
    )
    ran = recorders[project_type].canvas
    assert {n.id for n in ran.nodes} == {"src", "flt", "out", "load", "vals", "inp", "note"}
    # I caricamenti leggono il canvas intero (partono solo se collegati all'Output).
    assert carica.call_args.args[1] is canvas


@pytest.mark.parametrize("module", [
    "app/core/manipola/executor.py", "app/core/estrai/executor.py", "app/core/arricchisci/executor.py",
])
def test_every_executor_preview_goes_through_scoped_canvas(module):
    source = (BACKEND / module).read_text(encoding="utf-8")
    preview = source[source.index("    def preview("):]
    preview = preview[:preview.index("\n    def ", 10)]
    assert "scoped_canvas(canvas, node_id" in preview
    assert "preview_canvas(" not in source


def test_the_enrichment_worker_runs_only_what_reaches_the_output():
    source = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    assert "run_scope = run_canvas(canvas)" in source
    assert 'nodes = run_scope.get("nodes", [])' in source
    assert "CanvasGraph.from_canvas(run_scope)" in source
    assert "CanvasGraph.from_canvas(canvas)" not in source


# ── L3 + L5: Valida ───────────────────────────────────────────────────────

def _validate(canvas):
    from app.core.projects.canvas_validation import validate_canvas

    return validate_canvas(canvas)


def test_validate_branches_that_do_not_reach_the_output_are_warnings():
    # Nel ramo scollegato un passo senza ingresso (errore se arrivasse all'Output).
    canvas = _canvas(NODES + [("lone", "transform"), ("lone2", "transform")], EDGES + [("lone", "lone2")])
    report = _validate(canvas)
    by_node = {}
    for issue in report.issues:
        by_node.setdefault(issue.node_id, []).append(issue)
    for node_id in ("src2", "calc", "calc2", "lone", "lone2"):
        assert all(i.severity != "error" for i in by_node.get(node_id, [])), by_node.get(node_id)
        assert any(i.code == "structure.unreached" and i.severity == "warning" for i in by_node[node_id])
    assert any(i.code == "structure.orphan" and i.severity == "warning" for i in by_node["lone"])


def test_validate_a_step_without_input_that_reaches_the_output_blocks():
    # Sorgente eliminata (L5): il passo arriva all'Output ma non ha dati.
    canvas = _canvas([("flt", "transform"), ("out", "result")], [("flt", "out")])
    report = _validate(canvas)
    orphan = [i for i in report.issues if i.code == "structure.orphan"]
    assert orphan and orphan[0].severity == "error" and orphan[0].node_id == "flt"
    # L4: nessun id nel testo (il titolo lo mette il frontend dal node_id).
    assert "flt" not in orphan[0].message


def test_validate_chain_skips_project_sources_of_unreached_branches():
    from app.core.projects.canvas_validation import validate_project_chain

    main = _canvas(NODES, EDGES)
    main.nodes[4].data = {"source_project_id": "upstream"}  # src2, ramo scollegato
    loaded = []

    def load(project_id):
        loaded.append(project_id)
        return (main, "manipola") if project_id == "p1" else None

    validate_project_chain("p1", load)
    assert loaded == ["p1"]


# ── L4: testi di avanzamento ──────────────────────────────────────────────

PROGRESS_FUNCS = {"_update_progress", "update_task_progress", "report_task_progress", "progress", "committed_progress"}
PROGRESS_FILES = [
    "workers/tasks/pipeline_task.py", "workers/tasks/carica_task.py", "workers/tasks/enrichment_task.py",
    "app/core/carica/dispatch.py",
]


def _progress_calls(path):
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in PROGRESS_FUNCS:
                yield node


def _message_arg(call):
    args = [a for a in call.args if not (isinstance(a, ast.Name) and a.id == "self")]
    return args[1] if len(args) >= 2 else None


@pytest.mark.parametrize("path", PROGRESS_FILES)
def test_progress_messages_come_from_the_table(path):
    calls = list(_progress_calls(path))
    assert calls, path
    written = []
    for call in calls:
        message = _message_arg(call)
        if isinstance(message, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            written.append(f"{path}:{call.lineno} {ast.unparse(message)[:80]}")
    assert written == []


def test_enrichment_counter_comes_from_the_table():
    from app.core.arricchisci import enrichment_service

    source = inspect.getsource(enrichment_service.EnrichmentService._update_progress) \
        if hasattr(enrichment_service, "EnrichmentService") else inspect.getsource(enrichment_service)
    assert 'progress_text(\n                "enrich_calls"' in source or 'progress_text("enrich_calls"' in source


def test_progress_text_formats_numbers_like_the_frontend():
    from app.shared.execution.progress_messages import PROGRESS_MESSAGES, progress_text

    assert progress_text("load_read", rows=238044) == "Lette 238.044 righe: scrittura in corso..."
    assert progress_text("enrich_calls", api="Geocoder", processed=1200, total=4567, success=1100, cached=50, errors=50) \
        == "Geocoder: 1.200 di 4.567 righe (1.100 riuscite, 50 dalla cache, 50 errori)"
    # Nessun testo nomina pipeline, preview, task, output minuscolo: il test dei
    # termini del frontend legge questa tabella; qui una sonda.
    lowered = " ".join(PROGRESS_MESSAGES.values()).lower()
    for word in ("pipeline", "preview", "task", "run ", "dataframe", "datasource"):
        assert word not in lowered, word
    assert "output" not in " ".join(PROGRESS_MESSAGES.values()).replace("Output", "")


def test_the_terms_test_of_the_frontend_reads_the_table():
    terms = (BACKEND.parent / "frontend/tests/shared/terms.contract.test.js").read_text(encoding="utf-8")
    assert "progress_messages.py" in terms


def test_unused_rows_are_not_declared():
    from app.shared.execution.progress_messages import PROGRESS_MESSAGES

    sources = "".join(
        (BACKEND / p).read_text(encoding="utf-8")
        for p in PROGRESS_FILES + ["app/core/arricchisci/enrichment_service.py", "app/core/orchestrator/task_service.py"]
    )
    unused = [key for key in PROGRESS_MESSAGES if f'"{key}"' not in sources]
    assert unused == []


def test_simple_namespace_canvas_is_tolerated():
    # validate_canvas riceve anche oggetti semplici (test e chiamanti interni).
    canvas = SimpleNamespace(nodes=[], edges=[])
    assert [i.code for i in _validate(canvas).issues] == ["CANVAS_EMPTY"]
