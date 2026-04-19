"""Contract test N13 — risoluzione alias -> nome originale in dest_column.

Scenario reale (pre-fix):
  MANIPOLA rinomina una colonna via alias (es. "id" -> "id2" nel canvas Result).
  Il DataFrame output ha "id2" come nome colonna. Il nodo CARICA scrive su
  una tabella DB che ha la colonna col NOME ORIGINALE "id", non l'alias.

  - Auto-map: mapper.auto_map produce `dest_column = "id2"` (copia il nome df).
    Senza fix, la scrittura fallisce con 'colonna "id2" inesistente'.
  - Mapping esplicito: se l'utente configura mapping nel canvas e mette
    per sbaglio `dest_column = "id2"`, stesso errore.

Fix (app/core/carica/executor.py::_resolve_aliases_in_mappings):
  Tabella `alias -> name` costruita da `node_data["availableColumns"]`.
  Applicata a ENTRAMBI i path (auto + esplicito) prima della validazione.

Questa suite congela:
  1. Comportamento base (alias presente -> dest_column riscritto)
  2. No-op quando non ci sono alias / availableColumns vuoto
  3. Gap 2 chiuso: il fix si applica anche al path mapping esplicito
  4. Parziale: alcune colonne con alias, altre no -> solo le prime sostituite
  5. Sentinel AST: entrambi i path nell'executor chiamano il helper
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

import pytest

from app.core.carica.executor import _resolve_aliases_in_mappings
from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _make_mapping(source: str, dest: str) -> ColumnMapping:
    """Helper: ColumnMapping minimale per i test."""
    return ColumnMapping(source_column=source, dest_column=dest)


# =============================================================================
# 1. Behavior base — alias presente in availableColumns viene risolto
# =============================================================================


def test_dest_column_rewritten_when_alias_present():
    """Caso nominale: availableColumns dice id2 e' alias di id, il mapping
    ha dest_column='id2', dopo il fix dest_column='id'."""
    mappings = [_make_mapping("id2", "id2")]
    available_cols = [{"name": "id", "alias": "id2", "type": "int"}]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"
    assert mappings[0].source_column == "id2", "source_column NON deve cambiare"


def test_multiple_aliases_all_resolved():
    """Piu' alias -> tutti riscritti."""
    mappings = [
        _make_mapping("id2", "id2"),
        _make_mapping("nome_alias", "nome_alias"),
    ]
    available_cols = [
        {"name": "id", "alias": "id2", "type": "int"},
        {"name": "nome", "alias": "nome_alias", "type": "str"},
    ]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"
    assert mappings[1].dest_column == "nome"


# =============================================================================
# 2. No-op cases
# =============================================================================


def test_unchanged_when_no_aliases_in_availableColumns():
    """availableColumns senza alias -> dest_column invariato."""
    mappings = [_make_mapping("id", "id"), _make_mapping("nome", "nome")]
    available_cols = [
        {"name": "id", "type": "int"},
        {"name": "nome", "type": "str"},
    ]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"
    assert mappings[1].dest_column == "nome"


def test_unchanged_when_availableColumns_empty():
    mappings = [_make_mapping("id", "id")]
    _resolve_aliases_in_mappings(mappings, [])
    assert mappings[0].dest_column == "id"


def test_unchanged_when_availableColumns_is_none():
    mappings = [_make_mapping("id", "id")]
    _resolve_aliases_in_mappings(mappings, None)
    assert mappings[0].dest_column == "id"


def test_unchanged_when_dest_column_already_original():
    """Idempotenza: dest_column gia' corretto (nome originale) -> no cambio."""
    mappings = [_make_mapping("id2", "id")]  # user gia' scritto 'id'
    available_cols = [{"name": "id", "alias": "id2", "type": "int"}]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"


def test_call_is_idempotent():
    """Doppia applicazione non cambia nulla."""
    mappings = [_make_mapping("id2", "id2")]
    available_cols = [{"name": "id", "alias": "id2", "type": "int"}]

    _resolve_aliases_in_mappings(mappings, available_cols)
    first = mappings[0].dest_column
    _resolve_aliases_in_mappings(mappings, available_cols)
    second = mappings[0].dest_column

    assert first == second == "id"


# =============================================================================
# 3. Mixed cases — alcune colonne con alias, altre no
# =============================================================================


def test_mixed_aliased_and_non_aliased_columns():
    """Colonna con alias riscritta, colonna senza alias invariata."""
    mappings = [
        _make_mapping("id2", "id2"),        # con alias
        _make_mapping("valore", "valore"),  # senza alias
    ]
    available_cols = [
        {"name": "id", "alias": "id2", "type": "int"},
        {"name": "valore", "type": "real"},   # no alias field
    ]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"
    assert mappings[1].dest_column == "valore"


# =============================================================================
# 4. Robustezza input malformato (best-effort)
# =============================================================================


def test_availableColumns_with_malformed_entries_is_tolerated():
    """availableColumns con entry non-dict o senza name/alias -> skip."""
    mappings = [_make_mapping("id2", "id2")]
    available_cols = [
        "not a dict",                                     # non-dict
        {"alias": "orphan_alias"},                        # manca name
        {"name": "lone_name"},                            # manca alias
        {"name": "id", "alias": "id2", "type": "int"},   # valida
    ]

    _resolve_aliases_in_mappings(mappings, available_cols)

    assert mappings[0].dest_column == "id"


def test_empty_mappings_no_error():
    available_cols = [{"name": "id", "alias": "id2", "type": "int"}]
    _resolve_aliases_in_mappings([], available_cols)  # no-op, no exception


# =============================================================================
# 5. Sentinel: Gap 2 — entrambi i path chiamano il helper
# =============================================================================
# Se qualcuno sposta il fix solo dentro `if not config.column_mappings:`
# riaprendo Gap 2, questo test cattura la regressione.


def test_execute_calls_resolve_helper_outside_auto_map_branch():
    """La chiamata a `_resolve_aliases_in_mappings` deve stare FUORI dal blocco
    `if not config.column_mappings` — altrimenti il path esplicito resta scoperto.
    """
    from app.core.carica import executor as exec_mod

    src = inspect.getsource(exec_mod.CaricaExecutor.execute)
    tree = ast.parse(inspect.getsource(exec_mod))

    # Trova il nodo AsyncFunctionDef/FunctionDef `execute`
    execute_fn = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute":
            for c in ast.walk(node):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) \
                        and c.func.id == "_resolve_aliases_in_mappings":
                    execute_fn = node
                    resolve_call = c
                    break
            if execute_fn:
                break

    assert execute_fn is not None, (
        "execute() non chiama _resolve_aliases_in_mappings. Gap 2 riaperto: "
        "il fix N13 non si applica al path mapping esplicito."
    )

    # Verifica che la chiamata NON sia dentro un `if not config.column_mappings`.
    # Approccio: cammina l'AST di execute, traccia gli `If` ancestor della call.
    # Se tra gli antenati c'e' un `If` con test `not config.column_mappings`,
    # il fix e' confinato al path auto-map.
    parents: list = []

    def _walk(node, parent_chain):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_resolve_aliases_in_mappings":
            parents.append(list(parent_chain))
            return
        for child in ast.iter_child_nodes(node):
            _walk(child, parent_chain + [node])

    _walk(execute_fn, [])

    assert parents, "Chiamata helper non localizzata — sentinel rotto."
    for chain in parents:
        for ancestor in chain:
            if isinstance(ancestor, ast.If):
                # L'if NON deve essere `not config.column_mappings`
                test = ancestor.test
                is_not_column_mappings = (
                    isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Attribute)
                    and test.operand.attr == "column_mappings"
                )
                assert not is_not_column_mappings, (
                    "_resolve_aliases_in_mappings e' dentro `if not config.column_mappings`: "
                    "Gap 2 riaperto, il path mapping esplicito non e' coperto."
                )
