"""Un input «Utente che esegue» collegato a qualcosa che non conta: si BLOCCA.

REGOLA UNICA (filtro «Utente che esegue» ignorato, correzione 11/11/2026)
-----------------------------------------------------------------------
Raccolta: ``docs/audits/identita-binding-ignorato-raccolta-2026-11-11.md``.
Un input con valore iniettato dal server (``runtime_input_kinds``, oggi
``current_user``) esiste per restringere i dati a quelli di chi esegue. Ogni
suo binding deve avere effetto sull'esecuzione; un binding senza effetto e'
un filtro ignorato IN SILENZIO (tutte le righe di tutti gli istituti) e qui
diventa un errore tipizzato ``runtime_identity_unbound``.

La verifica si fa DOPO la costruzione, con lo stesso codice
dell'esecuzione, su una prova del canvas (:func:`identity_binding_problems`):

1. ogni binding scrive un segnaposto UNICO nel suo bersaglio con la stessa
   scrittura della risoluzione (``binding_write._apply_scalar_binding``, segno
   ``value_literal`` compreso); i nodi sostituiti da un file e gli input
   parametro escono come nella risoluzione;
2. il bersaglio deve stare nel flusso eseguito
   (``canvas_preview_scope.flow_node_ids``: l'Output e i suoi antenati con
   le JOIN nei due versi, oppure il passo dell'anteprima e i suoi antenati).
   Fuori dal flusso: per l'Output si blocca (il nodo non conta); per
   l'anteprima di un passo il binding non riguarda quel passo e non si
   controlla;
3. Estrai: la query si costruisce con ``QueryGenerator`` sul canvas
   dell'esecutore (``scoped_canvas``); il segnaposto deve stare in un
   parametro legato (``:p_n`` presente nel SQL). Se la costruzione stessa
   fallisce, l'esecuzione fallisce con lo stesso errore: nessun dato esce, e
   la difesa nell'esecutore (``estrai.executor``, filtri ``value_literal``)
   resta in piedi.

Chi la chiama (test di enumerazione
``tests2/eseguibili/unit/test_identity_binding_effect.py``):

- ``runtime.resolve_runtime_inputs``: l'UNICA funzione che scrive il valore
  dell'utente nel canvas (Eseguibile: avvio e stima di Arricchisci; prova;
  anteprima, Valida con input, valori distinti; ricalcolo isolato a monte);
- ``upstream_plan.plan_for_version``: avvio e stima di ogni Eseguibile, prima
  di ogni file temporaneo;
- Valida (``projects.canvas_validation``: problema di severita' errore,
  canvas non valido), anteprima della query (``projects.query_preview``:
  errore visibile) e pubblicazione degli Eseguibili.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.eseguibili.binding_write import _apply_scalar_binding
from app.core.estrai.query_filters import LITERAL_IN_OR_GROUP_MESSAGE, LITERAL_OPERATOR_MESSAGE
from app.core.estrai.query_generator import QueryGenerator
from app.core.estrai.query_plan import estrai_result_node
from app.core.projects.canvas_models import CanvasState
from app.core.projects.canvas_preview_scope import FINAL_NODE_TYPES, flow_node_ids, scoped_canvas
from app.shared import error_codes
from app.shared.execution.runtime_bound_fields import strip_runtime_bound_fields
from app.shared.execution.runtime_identity import KIND_LABEL, RuntimeIdentityError
from app.shared.execution.runtime_input_kinds import is_server_injected, node_value, project_input_role

_SENTINEL = "⁣plenora-identita:"
_FILTER_PATH = re.compile(r"^filters\.(\d+)\.value$")
_HAVING_PATH = re.compile(r"^having_conditions\.(\d+)\.value$")

#: Tipi dei nodi il cui binding si verifica nella query di Estrai.
ESTRAI_QUERY_NODE_TYPES = frozenset({"source", "result"})


@dataclass(frozen=True)
class IdentityBindingProblem:
    """Un binding di un input di tipo server senza effetto."""

    input_key: str
    input_node_id: str
    target_node_id: str
    target_field: str | None
    message: str


def _sentinel(index: int) -> str:
    return f"{_SENTINEL}{index}⁣"


def _label(definition: Any) -> str:
    return str(getattr(definition, "label", None) or getattr(definition, "key", "") or KIND_LABEL)


def _table_name(node: Mapping[str, Any] | None) -> str:
    if not node:
        return "?"
    return str(node_value(node, "alias") or node_value(node, "table_name") or node.get("id") or "?")


def _target_text(node: Mapping[str, Any] | None, field: str | None) -> tuple[str, bool]:
    """``(dove, e' un filtro)`` per il messaggio: ``tabella.colonna`` per un
    filtro di Estrai, ``nodo.campo`` per gli altri bersagli."""
    match = _FILTER_PATH.fullmatch(str(field or ""))
    if node is not None and match:
        filters = node_value(node, "filters") or []
        index = int(match.group(1))
        column = ""
        if isinstance(filters, list) and index < len(filters) and isinstance(filters[index], Mapping):
            column = str(filters[index].get("column") or "")
        return f"{_table_name(node)}.{column or f'filtro {index + 1}'}", True
    name = str(node_value(node, "label") or node_value(node, "name") or (node or {}).get("id") or "?")
    return f"{name}.{field}" if field else name, False


def _message(label: str, where: str, is_filter: bool, reason: str) -> str:
    if is_filter:
        head = f"Il filtro «{KIND_LABEL}» (input «{label}») collegato a {where} non entra nella query"
    else:
        head = f"L'input «{label}» ({KIND_LABEL}) collegato a {where} non ha effetto"
    return (
        f"{head}: {reason}. Il filtro per utente verrebbe ignorato (tutte le righe, di tutti), "
        "quindi l'esecuzione si blocca. Correggi il collegamento e salva il progetto."
    )


def _sql_reason(node: Mapping[str, Any], field: str | None, tables_used: set[str]) -> str:
    if _HAVING_PATH.fullmatch(str(field or "")):
        return "la condizione HAVING non entra nella query (GROUP BY o HAVING spenti, condizione incompleta)"
    if node.get("type") == "source" and node.get("id") not in tables_used:
        return "la tabella non entra nella query (non e' collegata all'Output ne' a una tabella della query)"
    match = _FILTER_PATH.fullmatch(str(field or ""))
    if match:
        filters = node_value(node, "filters") or []
        index = int(match.group(1))
        item = filters[index] if isinstance(filters, list) and index < len(filters) else None
        if not isinstance(item, Mapping) or not item.get("column") or not item.get("operator"):
            return "il filtro non ha colonna o operatore e la query lo salta"
    return "la query costruita non lega il valore"


def identity_binding_problems(
    canvas: Mapping[str, Any],
    manifest: Any,
    *,
    node_id: Any = None,
    values: Mapping[str, Any] | None = None,
) -> list[IdentityBindingProblem]:
    """I binding degli input di tipo server di ``manifest`` che non hanno
    effetto sull'esecuzione di ``canvas`` (regola del modulo).

    ``node_id``: anteprima di un passo (None = l'Output). ``values``: i
    valori dell'avvio, per sapere quali nodi un file sostituisce."""
    inputs = {item.key: item for item in getattr(manifest, "inputs", None) or []}
    server_keys = {key for key, item in inputs.items() if is_server_injected(item.kind)}
    targets = [b for b in getattr(manifest, "bindings", None) or [] if b.input_key in server_keys]
    if not targets:
        return []

    probe = copy.deepcopy(dict(canvas))
    strip_runtime_bound_fields(probe)
    nodes = [node for node in probe.get("nodes") or [] if isinstance(node, dict)]
    by_id = {str(node.get("id")): node for node in nodes if node.get("id")}
    original = {key: copy.deepcopy(node) for key, node in by_id.items()}
    input_node_of = {
        str(node_value(node, "input_key") or ""): str(node.get("id"))
        for node in nodes if node.get("type") == "project_input"
    }

    # Nodi sostituiti da un file dato all'avvio (come la risoluzione).
    values = values or {}
    replaced: dict[str, str] = {}
    for binding in getattr(manifest, "bindings", None) or []:
        definition = inputs.get(binding.input_key)
        if binding.mode != "data_source" or definition is None or definition.kind != "file":
            continue
        raw = values.get(binding.input_key)
        if raw is None or (isinstance(raw, list) and not raw):
            continue
        replaced[str(binding.target_node_id)] = _label(definition)
        node = by_id.get(str(binding.target_node_id))
        if node is not None:
            node["type"] = "file_source"


    problems: list[IdentityBindingProblem] = []
    pending: list[tuple[int, Any]] = []

    def problem(binding: Any, reason: str) -> None:
        node = original.get(str(binding.target_node_id))
        where, is_filter = _target_text(node, binding.target_field)
        problems.append(IdentityBindingProblem(
            input_key=binding.input_key,
            input_node_id=input_node_of.get(binding.input_key, ""),
            target_node_id=str(binding.target_node_id),
            target_field=binding.target_field,
            message=_message(_label(inputs[binding.input_key]), where, is_filter, reason),
        ))

    for index, binding in enumerate(targets):
        node = by_id.get(str(binding.target_node_id))
        if node is None:
            problem(binding, "il nodo non esiste piu' nel canvas")
            continue
        if binding.mode == "data_source":
            # Sorgente «Utente che esegue»: un nodo Valori statici di una riga.
            node["type"] = "staticValues"
            pending.append((index, binding))
            continue
        if str(binding.target_node_id) in replaced:
            problem(binding, f"il nodo e' sostituito dal file dell'input «{replaced[str(binding.target_node_id)]}»")
            continue
        try:
            _apply_scalar_binding(node, str(binding.target_field), _sentinel(index), literal=True)
        except (ValueError, TypeError, IndexError) as exc:
            problem(binding, f"il campo collegato non si scrive ({exc})")
            continue
        pending.append((index, binding))

    # Gli input parametro e connessione escono dal canvas (come la risoluzione).
    dropped = {
        str(node.get("id")) for node in nodes
        if project_input_role(node) in {"parameter", "connection"}
    }
    probe["nodes"] = [node for node in nodes if str(node.get("id")) not in dropped]
    probe["edges"] = [
        edge for edge in probe.get("edges") or []
        if isinstance(edge, Mapping)
        and str(edge.get("source")) not in dropped and str(edge.get("target")) not in dropped
    ]

    if node_id is None and not any(node.get("type") in FINAL_NODE_TYPES for node in probe["nodes"]):
        # Senza Output il canvas resta com'e' (``run_canvas``): ogni nodo
        # e' eseguito, o l'esecutore lo rifiuta con il suo messaggio.
        flow = set(by_id)
    else:
        flow = {str(item) for item in flow_node_ids(probe, node_id)}
    in_flow: list[tuple[int, Any]] = []
    for index, binding in pending:
        if str(binding.target_node_id) in flow:
            in_flow.append((index, binding))
        elif node_id is None:
            what = "la sorgente" if binding.mode == "data_source" else (
                "la tabella" if by_id[str(binding.target_node_id)].get("type") == "source" else "il passo"
            )
            problem(binding, f"{what} non arriva all'Output (ramo scollegato)")
        # Anteprima di un passo: un bersaglio fuori dal passo non lo riguarda.

    if str(getattr(manifest, "project_type", "")) == "estrai":
        query_bound = [
            (index, binding) for index, binding in in_flow
            if binding.mode == "node_field"
            and by_id[str(binding.target_node_id)].get("type") in ESTRAI_QUERY_NODE_TYPES
        ]
        if query_bound:
            _check_estrai_query(probe, query_bound, node_id, report=problem)
    return problems


def _check_estrai_query(
    probe: Mapping[str, Any], bound: list[tuple[int, Any]], node_id: Any, *, report: Any,
) -> None:
    """La query costruita come dall'esecutore (``scoped_canvas`` +
    ``QueryGenerator``) deve legare ogni segnaposto in un parametro del SQL."""
    try:
        state = scoped_canvas(CanvasState(**probe), node_id, with_result=True)
        result = estrai_result_node(state.nodes)
        if result is None:
            return
        generator = QueryGenerator()
        info = generator.generate_sql(state.nodes, state.edges, result.id)
        tables_used = {str(node.id) for node in generator.query_tables(state.nodes, state.edges, result.id)}
    except (ValueError, TypeError, KeyError) as exc:
        if _is_literal_refusal(exc):
            # Rifiuto del valore impostato dal server (gruppo «oppure»,
            # operatore che allarga): lo si dice qui, prima dell'avvio.
            for _index, binding in bound:
                report(binding, str(exc).rstrip("."))
        # Altrimenti la costruzione fallisce anche nell'esecuzione con lo
        # stesso errore: nessun dato esce.
        return
    sql = str(info.get("sql") or "")
    params = info.get("params") or {}
    by_id = {str(node.get("id")): node for node in probe.get("nodes") or [] if isinstance(node, Mapping)}
    for index, binding in bound:
        marker = _sentinel(index)
        if any(
            re.search(rf":{re.escape(str(name))}\b", sql) and _contains(value, marker)
            for name, value in params.items()
        ):
            continue
        report(binding, _sql_reason(by_id[str(binding.target_node_id)], binding.target_field, tables_used))


def _is_literal_refusal(exc: BaseException) -> bool:
    text = str(exc)
    return text == LITERAL_IN_OR_GROUP_MESSAGE or text.startswith(LITERAL_OPERATOR_MESSAGE.split("{", 1)[0])


def _contains(value: Any, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, (list, tuple)):
        return any(_contains(item, marker) for item in value)
    return False


def assert_identity_bindings_effective(
    canvas: Mapping[str, Any],
    manifest: Any,
    *,
    node_id: Any = None,
    values: Mapping[str, Any] | None = None,
) -> None:
    """Blocco fail-closed: ``runtime_identity_unbound`` con i motivi."""
    problems = identity_binding_problems(canvas, manifest, node_id=node_id, values=values)
    if problems:
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_UNBOUND, " ".join(item.message for item in problems),
        )


__all__ = [
    "ESTRAI_QUERY_NODE_TYPES",
    "IdentityBindingProblem",
    "assert_identity_bindings_effective",
    "identity_binding_problems",
]
