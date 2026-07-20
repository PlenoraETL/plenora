"""Compiler del contratto pubblico degli eseguibili.

Il canvas resta l'authoring model. Il manifest è invece l'unico contratto
accettato dal runtime: contiene input e binding espliciti, versionati e
indipendenti dalle euristiche del wizard legacy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.shared.contracts.payloads import JsonObject

from .binding_capabilities import canonical_binding_path
from .models import (
    ExecutableInputBinding,
    ExecutableInputDefinition,
    ExecutableInputValidation,
    ExecutableManifest,
    ExpectedColumn,
    ProjectType,
)
from .scanner import extract_expected_columns


_SAFE_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_INDEX = re.compile(r"^[0-9]+$")
_BLOCKED_FIELD_PARTS = frozenset(
    {
        "auth",
        "connection_id",
        "api_id",
        "file_source_id",
        "source_project_id",
        "password",
        "secret",
        "token",
        "key_value",
    }
)


def _node_data(node: Mapping[str, Any]) -> JsonObject:
    data = node.get("data")
    merged = dict(node)
    if isinstance(data, Mapping):
        merged.update(data)
    return merged


def _as_expected_columns(value: Any) -> list[ExpectedColumn]:
    if not isinstance(value, list):
        return []
    return [ExpectedColumn.model_validate(item) for item in value]


def _validate_target_field(path: str) -> str:
    """Valida la sintassi del percorso e blocca i campi sensibili.

    La capability semantica viene verificata in seguito sul nodo concreto.
    """

    parts = path.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError(f"Campo binding non valido: {path!r}")
    for part in parts:
        if not (_SAFE_SEGMENT.fullmatch(part) or _SAFE_INDEX.fullmatch(part)):
            raise ValueError(f"Campo binding non valido: {path!r}")
        if part.lower() in _BLOCKED_FIELD_PARTS:
            raise ValueError(f"Campo binding protetto: {path!r}")

    # L'autorizzazione semantica dipende dal nodo destinazione ed e' applicata
    # da ``canonical_binding_path`` dopo la compilazione dei riferimenti.
    return path


def _compile_project_input(
    node: Mapping[str, Any],
    *,
    order: int,
    inferred_target_id: str | None = None,
) -> tuple[ExecutableInputDefinition, list[ExecutableInputBinding]]:
    data = _node_data(node)
    node_id = str(node.get("id") or "").strip()
    if not node_id:
        raise ValueError("Nodo project_input senza id")

    key = str(data.get("input_key") or "").strip()
    label = str(data.get("label") or key).strip()
    kind = str(data.get("input_kind") or "file").strip().lower()
    raw_validation = data.get("validation") or {}
    if not isinstance(raw_validation, Mapping):
        raise ValueError(f"Validazione input {key or node_id} non valida")

    definition = ExecutableInputDefinition(
        key=key,
        label=label,
        kind=kind,
        description=str(data.get("description") or ""),
        required=bool(data.get("required", True)),
        default_value=data.get("default_value"),
        multiple=bool(data.get("multiple", False)),
        expected_columns=_as_expected_columns(data.get("expected_columns")),
        validation=ExecutableInputValidation.model_validate(dict(raw_validation)),
        order=int(data.get("order", order)),
    )

    bindings: list[ExecutableInputBinding] = []
    if definition.kind == "file":
        bindings.append(
            ExecutableInputBinding(
                input_key=definition.key,
                target_node_id=node_id,
                mode="data_source",
            )
        )

    raw_bindings = data.get("bindings") or []
    if not isinstance(raw_bindings, list):
        raise ValueError(f"Binding input {definition.key} non validi")
    for raw in raw_bindings:
        if not isinstance(raw, Mapping):
            raise ValueError(f"Binding input {definition.key} non valido")
        bindings.append(
            ExecutableInputBinding(
                input_key=definition.key,
                target_node_id=str(
                    raw.get("target_node_id") or inferred_target_id or ""
                ),
                mode="node_field",
                target_field=_validate_target_field(
                    str(raw.get("target_field") or "")
                ),
            )
        )

    if definition.kind != "file" and not bindings:
        raise ValueError(
            f"L'input scalare '{definition.label}' non è collegato a nessun campo"
        )
    return definition, bindings


def compile_manifest(
    *,
    source_project_id: str,
    project_type: ProjectType,
    canvas: Mapping[str, Any],
    default_connection_id: str | None = None,
) -> ExecutableManifest:
    """Compila e valida il manifest v2 da un canvas persistito."""

    nodes = canvas.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Il progetto non ha un canvas valido")

    nodes_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, Mapping) and node.get("id")
    }
    node_ids = set(nodes_by_id)
    inputs: list[ExecutableInputDefinition] = []
    bindings: list[ExecutableInputBinding] = []

    project_inputs = [
        node
        for node in nodes
        if isinstance(node, Mapping) and node.get("type") == "project_input"
    ]
    edges = canvas.get("edges") or []
    outgoing_targets: dict[str, list[str]] = {}
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, Mapping):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            outgoing_targets.setdefault(source, []).append(target)
    for order, node in enumerate(project_inputs):
        node_id = str(node.get("id") or "")
        targets = list(dict.fromkeys(outgoing_targets.get(node_id, [])))
        data = _node_data(node)
        if str(data.get("input_kind") or "file") != "file" and len(targets) > 1:
            raise ValueError(
                f"L'input scalare '{data.get('label') or node_id}' deve essere "
                "collegato a un solo nodo destinazione"
            )
        definition, node_bindings = _compile_project_input(
            node,
            order=order,
            inferred_target_id=targets[0] if targets else None,
        )
        inputs.append(definition)
        bindings.extend(node_bindings)

    for binding in bindings:
        if binding.target_node_id not in node_ids:
            raise ValueError(
                f"Binding input '{binding.input_key}' verso nodo inesistente "
                f"'{binding.target_node_id}'"
            )
        if binding.mode == "node_field" and binding.target_field:
            binding.target_field = canonical_binding_path(
                nodes_by_id[binding.target_node_id],
                binding.target_field,
            )

    claimed_targets: dict[tuple[str, str], str] = {}
    for binding in bindings:
        if binding.mode != "node_field" or not binding.target_field:
            continue
        target = (binding.target_node_id, binding.target_field)
        previous_input = claimed_targets.get(target)
        if previous_input is not None:
            raise ValueError(
                "Parametro runtime collegato a piu' input: "
                f"{binding.target_node_id}.{binding.target_field} "
                f"({previous_input}, {binding.input_key})"
            )
        claimed_targets[target] = binding.input_key

    # Adapter di pubblicazione per gli eseguibili già supportati: un canvas
    # Arricchisci senza project_input conserva il contratto single-file.
    if not project_inputs and project_type == "arricchisci":
        source_nodes = [
            node
            for node in nodes
            if isinstance(node, Mapping)
            and node.get("type") in ("file_source", "project_source", "staticValues")
        ]
        if len(source_nodes) != 1:
            raise ValueError(
                "Un Arricchisci legacy deve avere una sola sorgente oppure "
                "dichiarare nodi Input progetto espliciti"
            )
        expected = [
            ExpectedColumn.model_validate(item)
            for item in extract_expected_columns(dict(canvas))
        ]
        inputs.append(
            ExecutableInputDefinition(
                key="input_file",
                label="File di input",
                kind="file",
                expected_columns=expected,
            )
        )
        bindings.append(
            ExecutableInputBinding(
                input_key="input_file",
                target_node_id=str(source_nodes[0].get("id")),
                mode="data_source",
            )
        )

    if project_type == "estrai" and not default_connection_id:
        raise ValueError("Il progetto Estrai non ha una connessione predefinita")

    return ExecutableManifest(
        project_type=project_type,
        source_project_id=source_project_id,
        inputs=inputs,
        bindings=bindings,
        default_connection_id=default_connection_id,
    )


__all__ = ["compile_manifest"]
