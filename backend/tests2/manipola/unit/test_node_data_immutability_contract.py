"""
Contract test — anti-regression refactor D3.

Pre-fix: il canvas executor `ManipolaExecutor._applica_trasformazioni`
mutava in-place l'istanza Pydantic `Node` ricevuta dal caller con
`node.data["inputColumns"] = input_columns` (executor.py:715-718).
Il `Node` e' logicamente immutable nel design del canvas (input
read-only del run), quindi la mutation contaminava il caller, era
difficile da testare e bloccava la migrazione Go-like (in Go la
struct sarebbe passata by-value/by-pointer-const).

Post-fix: usa `node.model_copy(update={...})` per produrre un clone
arricchito, rebindando la variabile locale `node` cosi' i metodi
downstream vedono `inputColumns` mentre l'istanza esterna resta
intatta.

Questo test verifica l'invariant osservabile: dopo l'esecuzione di
`_applica_trasformazioni`, il `node.data` dell'istanza Pydantic
passata via `canvas.nodes` NON deve essere stato mutato.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.projects.canvas_models import (
    CanvasState,
    Edge,
    Node,
    Position,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _make_static_node(node_id: str, fields: list) -> Node:
    """Crea un staticValues node con `data` non vuoto per testare
    la preservazione dei campi pre-esistenti."""
    return Node(
        id=node_id,
        type="staticValues",
        position=Position(x=0, y=0),
        fields=fields,
        data={"sentinel_static": "do_not_mutate"},
    )


def _make_transform_node(node_id: str, data: dict) -> Node:
    """Crea un transform node con `data` arbitrario; il test
    verifica che questa esatta dict resti invariata."""
    return Node(
        id=node_id,
        type="transform",
        position=Position(x=100, y=0),
        transform_op="sort",
        data=data,
    )


def _make_edge(edge_id: str, source: str, target: str) -> Edge:
    return Edge(id=edge_id, source=source, target=target)


def test_applica_trasformazioni_does_not_mutate_node_data():
    """REGRESSION D3: la mutation in-place `node.data["inputColumns"] = ...`
    e' stata sostituita da un model_copy. Verifica che `node.data`
    dell'istanza originale (quella in `canvas.nodes`) NON contenga
    'inputColumns' dopo l'esecuzione, e che i campi pre-esistenti
    siano integri."""

    original_data = {"foo": "bar", "nested": {"k": "v"}}

    source_node = _make_static_node(
        "src",
        fields=[{"name": "id", "type": "int", "value": "1"}],
    )
    transform_node = _make_transform_node("tx", data=original_data)

    canvas = CanvasState(
        nodes=[source_node, transform_node],
        edges=[_make_edge("e1", "src", "tx")],
    )

    # dataframes pre-popolati con il source (skippiamo _applica_static_values)
    dataframes = {"src": pd.DataFrame({"id": [1, 2, 3]})}

    # Mock project_service: non viene chiamato lungo questo path perche'
    # passiamo `project_id=None`, cosi' schema_service resta None e il
    # ramo di calcolo inputColumns NON entra. Ma serve un secondo test
    # piu' specifico (sotto) che attivi quel ramo.
    executor = ManipolaExecutor(project_service=MagicMock())

    # Patch _esegui_trasformazione: ritorna l'input invariato. Cosi'
    # il test si concentra sull'invariant di immutabilita', non sulla
    # correttezza della trasformazione sort.
    with patch.object(
        ManipolaExecutor,
        "_esegui_trasformazione",
        return_value=dataframes["src"],
    ):
        executor._applica_trasformazioni(
            canvas=canvas,
            dataframes=dataframes,
            run_id=None,
            project_id=None,  # disattiva il ramo schema_service
        )

    # ANTI-REGRESSION CORE: l'istanza Pydantic originale e' intatta.
    assert transform_node.data == original_data, (
        f"node.data mutato in-place. atteso={original_data}, "
        f"trovato={transform_node.data}"
    )
    # `inputColumns` non deve apparire (con project_id=None non viene
    # neppure calcolato, ma il check resta valido anche dopo).
    assert "inputColumns" not in (transform_node.data or {}), (
        "node.data ha ricevuto 'inputColumns' via mutation in-place"
    )


def test_applica_trasformazioni_inputcolumns_path_does_not_mutate_node_data():
    """REGRESSION D3 — variante che ATTIVA il ramo di calcolo
    `inputColumns` (project_id valorizzato + SchemaInferenceService
    mockato). Verifica che anche in questo ramo l'istanza Pydantic
    originale NON sia mutata. Era il path bacato pre-fix."""

    original_data = {"foo": "bar"}

    source_node = _make_static_node(
        "src",
        fields=[{"name": "id", "type": "int", "value": "1"}],
    )
    transform_node = _make_transform_node("tx", data=original_data)

    canvas = CanvasState(
        nodes=[source_node, transform_node],
        edges=[_make_edge("e1", "src", "tx")],
    )
    dataframes = {"src": pd.DataFrame({"id": [1, 2, 3]})}

    executor = ManipolaExecutor(project_service=MagicMock())

    # Mock SchemaInferenceService: l'import lazy dentro
    # _applica_trasformazioni risolve la classe via module path;
    # patchiamo il modulo origine.
    fake_col = MagicMock()
    fake_col.to_dict.return_value = {
        "id": "c1", "name": "id", "type": "int",
    }

    fake_schema_service = MagicMock()
    fake_schema_service._schema_cache = {}
    fake_schema_service._group_edges_by_target.return_value = {}
    fake_schema_service._infer_node_schema.return_value = [fake_col]

    with patch(
        "app.core.manipola.executor.SchemaInferenceService",
        return_value=fake_schema_service,
    ), patch.object(
        ManipolaExecutor,
        "_esegui_trasformazione",
        return_value=dataframes["src"],
    ):
        executor._applica_trasformazioni(
            canvas=canvas,
            dataframes=dataframes,
            run_id=None,
            project_id="proj-test",  # attiva il ramo schema_service
        )

    # ANTI-REGRESSION CORE: anche col ramo inputColumns attivo,
    # l'istanza Pydantic originale resta intatta. Pre-fix questo
    # check FALLIVA: transform_node.data sarebbe stato
    # {"foo": "bar", "inputColumns": [...]}.
    assert transform_node.data == original_data, (
        f"node.data mutato in-place dal ramo schema_service. "
        f"atteso={original_data}, trovato={transform_node.data}"
    )
    assert "inputColumns" not in (transform_node.data or {}), (
        "node.data esterno contiene 'inputColumns': il refactor D3 "
        "model_copy non sta funzionando — la mutation e' tornata."
    )
