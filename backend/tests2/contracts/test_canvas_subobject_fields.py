"""I campi che il frontend salva per un sotto-oggetto del canvas esistono nel modello.

Fase 5, T5+T6, correzione dopo la conferma. REGOLA: per ogni modello Pydantic
del canvas, ogni campo scritto dal serializer frontend per quel sotto-oggetto
e' dichiarato, oppure il round-trip (PUT -> modello -> GET) lo conserva
(``extra="allow"``). Prima ``AggregationConfig`` scartava ``alias_source``
(``extra="ignore"``) e l'alias scritto dall'utente tornava generato al reload.

Il contratto dei campi e' ``canvas_subobject_fields.json``, condiviso con il
frontend (tests/shared/canvasSubobjectFields.contract.test.jsx verifica che i
pannelli scrivano solo quei campi). Enumerazione: ogni lista di sotto-modelli
del Node con ``extra`` diverso da ``allow`` deve stare nel contratto.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.core.projects.canvas_models import CanvasState, Node

CONTRACT = {k: v for k, v in json.loads(
    (Path(__file__).with_name("canvas_subobject_fields.json")).read_text(encoding="utf-8")).items()
    if not k.startswith("_")}

SAMPLE_VALUES = {
    "column": "pop", "function": "SUM", "alias": "sum_pop", "alias_source": "user",
    "direction": "DESC", "operator": ">", "value": "3", "group": 1,
}


def _item_model(field_name: str) -> type[BaseModel] | None:
    """Modello degli elementi se il campo e' una lista di sotto-modelli."""
    def find(annotation, in_list=False):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation if in_list else None
        is_list = typing.get_origin(annotation) in (list, typing.List)
        for arg in typing.get_args(annotation):
            found = find(arg, in_list or is_list)
            if found is not None:
                return found
        return None
    return find(Node.model_fields[field_name].annotation)


def _submodel_lists() -> dict[str, type[BaseModel]]:
    out = {}
    for name in Node.model_fields:
        model = _item_model(name)
        if model is not None:
            out[name] = model
    return out


def test_every_closed_submodel_list_is_in_the_contract():
    closed = {name for name, model in _submodel_lists().items()
              if model.model_config.get("extra") != "allow"}
    assert closed, "enumerazione vuota: il test non legge piu' il modello"
    assert closed <= set(CONTRACT), sorted(closed - set(CONTRACT))


@pytest.mark.parametrize("field", sorted(CONTRACT))
def test_frontend_fields_are_declared_or_preserved(field):
    model = _submodel_lists()[field]
    keys = CONTRACT[field]
    declared = set(model.model_fields)
    if model.model_config.get("extra") != "allow":
        assert set(keys) <= declared, sorted(set(keys) - declared)
    item = {k: SAMPLE_VALUES[k] for k in keys}
    node = {"id": "n1", "type": "result", "position": {"x": 0, "y": 0}, field: [item]}
    state = CanvasState.model_validate({"nodes": [node], "edges": []})
    saved = state.model_dump(exclude_none=True)["nodes"][0][field][0]
    reloaded = CanvasState.model_validate({"nodes": [{**node, field: [saved]}], "edges": []})
    again = reloaded.model_dump(exclude_none=True)["nodes"][0][field][0]
    assert {k: saved.get(k) for k in keys} == item
    assert again == saved


def test_user_alias_survives_reload():
    agg = {"column": "pop", "function": "SUM", "alias": "sum_pop", "alias_source": "user"}
    node = {"id": "n1", "type": "result", "position": {"x": 0, "y": 0}, "aggregations": [agg]}
    dumped = Node.model_validate(node).model_dump()["aggregations"][0]
    assert dumped["alias_source"] == "user"
