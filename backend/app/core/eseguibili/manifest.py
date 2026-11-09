"""Compiler del contratto pubblico degli eseguibili.

Il canvas resta l'authoring model. Il manifest è invece l'unico contratto
accettato dal runtime: contiene input e binding espliciti, versionati e
indipendenti dalle euristiche del wizard legacy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.shared.contracts.payloads import JsonObject
from app.shared.execution.runtime_identity import identity_source_column, identity_transform
from app.shared.execution.runtime_input_kinds import kind_spec, project_input_role

from .binding_capabilities import canonical_binding_path
from .identity_transform import assert_valid_transform
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
    spec = kind_spec(kind)
    if spec is None:
        raise ValueError(f"Tipo di input non valido: {kind!r}")
    # Tipo impostato dal server («Utente che esegue»): niente valore
    # predefinito, vincoli o facoltativita' dal canvas (resti di un tipo
    # precedente compresi); lo username di prova non entra mai nel manifest.
    server = spec.server_injected
    # Trasformazione del nome utente (solo tipo server): stesso cancello della
    # Formula, rifiutata qui con il motivo (pubblicazione, Valida, esecuzione).
    transform = identity_transform(node) if server else None
    if transform is not None:
        assert_valid_transform(transform, label=label or key or node_id)

    definition = ExecutableInputDefinition(
        key=key,
        label=label,
        kind=kind,
        description=str(data.get("description") or ""),
        required=True if server else bool(data.get("required", True)),
        default_value=None if server else data.get("default_value"),
        multiple=bool(data.get("multiple", False)),
        expected_columns=_as_expected_columns(data.get("expected_columns")),
        validation=ExecutableInputValidation.model_validate(
            {} if server else dict(raw_validation)
        ),
        connection_set_id=(
            str(data.get("connection_set_id") or "").strip() or None
        ),
        order=int(data.get("order", order)),
        transform=transform,
    )

    # Ruolo nel canvas: regola unica (runtime_input_kinds.project_input_role).
    # Una sorgente (file, «Utente che esegue» come tabella di una riga)
    # sostituisce il nodo stesso; un parametro scrive campi di altri nodi.
    role = project_input_role(node)
    bindings: list[ExecutableInputBinding] = []
    if role == "source":
        if spec.value != "file":
            column = identity_source_column(node)
            if not _SAFE_SEGMENT.fullmatch(column) or len(column) > 80:
                raise ValueError(
                    f"Nome colonna non valido per l'input '{definition.label}': {column!r}"
                )
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
    if role == "source" and spec.value != "file" and raw_bindings:
        raise ValueError(
            f"L'input '{definition.label}' e' una sorgente: non si collega a "
            "parametri di altri nodi"
        )
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

    if spec.value == "scalar" and not bindings:
        raise ValueError(
            f"L'input scalare '{definition.label}' non è collegato a nessun campo"
        )
    if spec.value == "connection" and bindings:
        raise ValueError(
            f"L'input connessione '{definition.label}' è un parametro di progetto "
            "e non può essere collegato a un nodo"
        )
    return definition, bindings


@dataclass(frozen=True)
class BindingProblem:
    """Binding che l'esecuzione corrente non puo' rispettare: il bersaglio
    manca nel canvas o non e' piu' tra quelli letti dall'esecuzione
    (``binding_capabilities.NODE_TARGET_FAMILIES``)."""

    input_key: str
    target_node_id: str
    target_field: str | None
    motivo: str


def canvas_nodes_by_id(canvas: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    nodes = canvas.get("nodes") if isinstance(canvas, Mapping) else None
    if not isinstance(nodes, list):
        return {}
    return {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, Mapping) and node.get("id")
    }


def check_binding(
    binding: ExecutableInputBinding,
    nodes_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, BindingProblem | None]:
    """REGOLA UNICA dei binding contro le capability correnti: la usa la
    pubblicazione (problema -> pubblicazione rifiutata) e l'elenco/dettaglio
    degli Eseguibili gia' pubblicati (problema -> ``binding_warnings``).
    Ritorna il campo canonico del bersaglio oppure il problema."""

    def problem(motivo: str) -> tuple[None, BindingProblem]:
        return None, BindingProblem(
            input_key=binding.input_key,
            target_node_id=binding.target_node_id,
            target_field=binding.target_field,
            motivo=motivo,
        )

    node = nodes_by_id.get(binding.target_node_id)
    if node is None:
        return problem(
            f"Binding input '{binding.input_key}' verso nodo inesistente "
            f"'{binding.target_node_id}'"
        )
    if binding.mode != "node_field" or not binding.target_field:
        return binding.target_field, None
    try:
        return canonical_binding_path(node, binding.target_field), None
    except ValueError as exc:
        return problem(str(exc))


def binding_problems(
    bindings: Iterable[ExecutableInputBinding],
    canvas: Mapping[str, Any],
) -> list[BindingProblem]:
    """Binding di un manifest pubblicato che l'esecuzione non legge piu'
    (stessa regola della pubblicazione, :func:`check_binding`)."""
    nodes_by_id = canvas_nodes_by_id(canvas)
    return [
        found
        for found in (check_binding(binding, nodes_by_id)[1] for binding in bindings)
        if found is not None
    ]


def compile_project_inputs(
    canvas: Mapping[str, Any],
    project_type: ProjectType,
) -> tuple[list[ExecutableInputDefinition], list[ExecutableInputBinding]]:
    """Input e binding dei nodi «Input progetto» del canvas: la parte della
    pubblicazione che li legge (``compile_manifest``), riusata dall'anteprima
    della ripubblicazione (``service.source_status``, N1) per verificare i
    binding con la stessa regola (``check_binding``) senza fermarsi al primo.
    I binding non sono ancora verificati contro il canvas."""
    nodes = canvas.get("nodes") or []
    if not isinstance(nodes, list):
        nodes = []
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
        if project_input_role(node) != "source" and len(targets) > 1:
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
        spec = kind_spec(definition.kind)
        if spec is not None and project_type not in spec.modes:
            if spec.value == "connection":
                raise ValueError(
                    "Gli input Connection Set sono supportati solo nei progetti Estrai"
                )
            raise ValueError(
                f"Il tipo di input '{definition.kind}' non e' disponibile nei "
                f"progetti {project_type}"
            )
        if (
            spec is not None
            and spec.value != "file"
            and project_input_role(node) == "source"
            and project_type not in spec.source_modes
        ):
            raise ValueError(
                f"L'input '{definition.label}' come sorgente e' disponibile solo "
                f"nei progetti {', '.join(spec.source_modes)}"
            )
        bindings.extend(node_bindings)

    return inputs, bindings


def compile_manifest(
    *,
    source_project_id: str,
    project_type: ProjectType,
    canvas: Mapping[str, Any],
    default_connection_id: str | None = None,
    for_project: bool = False,
) -> ExecutableManifest:
    """Compila e valida il manifest v2 da un canvas persistito."""

    nodes = canvas.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Il progetto non ha un canvas valido")

    nodes_by_id = canvas_nodes_by_id(canvas)
    inputs, bindings = compile_project_inputs(canvas, project_type)
    project_inputs = [
        node
        for node in nodes
        if isinstance(node, Mapping) and node.get("type") == "project_input"
    ]

    if sum(item.kind == "connection" for item in inputs) > 1:
        raise ValueError(
            "Un progetto Estrai può dichiarare un solo input Connection Set"
        )

    for binding in bindings:
        canonical, problem = check_binding(binding, nodes_by_id)
        if problem is not None:
            raise ValueError(problem.motivo)
        binding.target_field = canonical

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
    if not project_inputs and project_type == "arricchisci" and not for_project:
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

    if project_type == "estrai" and not default_connection_id and not for_project:
        raise ValueError("Il progetto Estrai non ha una connessione predefinita")

    return ExecutableManifest(
        project_type=project_type,
        source_project_id=source_project_id,
        inputs=inputs,
        bindings=bindings,
        default_connection_id=default_connection_id,
    )


__all__ = [
    "BindingProblem",
    "binding_problems",
    "canvas_nodes_by_id",
    "check_binding",
    "compile_manifest",
    "compile_project_inputs",
]
