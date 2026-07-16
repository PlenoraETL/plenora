"""Risoluzione tipizzata degli input di una run eseguibile."""

from __future__ import annotations

import copy
import io
import re
from datetime import date, datetime
from typing import Any, Mapping

from app.core.files.service import file_source_service

from . import db as eseguibili_db
from .models import ExecutableInputDefinition, ExecutableManifest


def _coerce_scalar(definition: ExecutableInputDefinition, value: Any) -> Any:
    kind = definition.kind
    if value is None or value == "":
        if definition.required and definition.default_value is None:
            raise ValueError(f"Input obbligatorio mancante: {definition.label}")
        value = definition.default_value
    if value is None:
        return None

    try:
        if kind == "text" or kind == "select":
            coerced: Any = str(value)
        elif kind == "integer":
            if isinstance(value, bool):
                raise ValueError
            coerced = int(value)
        elif kind == "number":
            if isinstance(value, bool):
                raise ValueError
            coerced = float(value)
        elif kind == "boolean":
            if isinstance(value, bool):
                coerced = value
            elif str(value).strip().lower() in {"true", "1", "yes", "si", "sì"}:
                coerced = True
            elif str(value).strip().lower() in {"false", "0", "no"}:
                coerced = False
            else:
                raise ValueError
        elif kind == "date":
            coerced = date.fromisoformat(str(value)).isoformat()
        elif kind == "datetime":
            coerced = datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
        else:
            coerced = value
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Valore non valido per '{definition.label}' ({kind})"
        ) from exc

    validation = definition.validation
    if isinstance(coerced, str):
        if validation.min_length is not None and len(coerced) < validation.min_length:
            raise ValueError(f"'{definition.label}' è troppo corto")
        if validation.max_length is not None and len(coerced) > validation.max_length:
            raise ValueError(f"'{definition.label}' è troppo lungo")
        if validation.pattern and re.fullmatch(validation.pattern, coerced) is None:
            raise ValueError(f"'{definition.label}' non rispetta il formato richiesto")
    if isinstance(coerced, (int, float)) and not isinstance(coerced, bool):
        if validation.minimum is not None and coerced < validation.minimum:
            raise ValueError(f"'{definition.label}' è sotto il minimo consentito")
        if validation.maximum is not None and coerced > validation.maximum:
            raise ValueError(f"'{definition.label}' supera il massimo consentito")
    if kind == "select" and coerced not in validation.options:
        raise ValueError(f"Opzione non valida per '{definition.label}'")
    return coerced


def _set_existing_path(container: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: Any = container
    for part in parts[:-1]:
        if isinstance(current, list):
            index = int(part)
            if index >= len(current):
                raise ValueError(f"Indice binding fuori range: {path}")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise ValueError(f"Campo binding inesistente: {path}")
            current = current[part]
        else:
            raise ValueError(f"Campo binding non attraversabile: {path}")
    leaf = parts[-1]
    if isinstance(current, list):
        index = int(leaf)
        if index >= len(current):
            raise ValueError(f"Indice binding fuori range: {path}")
        current[index] = value
    elif isinstance(current, dict):
        if leaf not in current:
            raise ValueError(f"Campo binding inesistente: {path}")
        current[leaf] = value
    else:
        raise ValueError(f"Campo binding non scrivibile: {path}")


def _apply_scalar_binding(node: dict[str, Any], path: str, value: Any) -> None:
    data = node.get("data")
    applied = False
    if isinstance(data, dict) and path.split(".", 1)[0] in data:
        _set_existing_path(data, path, value)
        applied = True
    if path.split(".", 1)[0] in node:
        _set_existing_path(node, path, value)
        applied = True
    if not applied:
        raise ValueError(f"Nodo {node.get('id')} senza campo binding {path}")


def _materialize_file_input(
    *,
    eseguibile_id: str,
    definition: ExecutableInputDefinition,
    payload: Mapping[str, Any],
    group_id: str,
) -> str:
    file_id = str(payload.get("file_id") or "")
    if not file_id:
        raise ValueError(f"File mancante per '{definition.label}'")
    df, _gdf = file_source_service.load_dataframe(
        file_id,
        expected_group_id=str(payload.get("_upload_group_id") or group_id),
    )
    if df is None or df.empty:
        raise ValueError(f"Il file '{definition.label}' è vuoto o illeggibile")

    rename_map: dict[str, str] = {}
    raw_mapping = payload.get("column_mapping") or []
    if not isinstance(raw_mapping, list):
        raise ValueError(f"Mapping colonne non valido per '{definition.label}'")
    for item in raw_mapping:
        if not isinstance(item, Mapping):
            continue
        chain_col = str(item.get("chain_column") or "")
        file_col = str(item.get("file_column") or "")
        if chain_col and file_col and file_col in df.columns:
            rename_map[file_col] = chain_col
    if rename_map:
        df = df.rename(columns=rename_map)

    missing = [
        column.name
        for column in definition.expected_columns
        if column.required and column.name not in df.columns
    ]
    if missing:
        raise ValueError(
            f"Colonne obbligatorie non mappate per '{definition.label}': "
            + ", ".join(missing)
        )

    buffer = io.BytesIO()
    df.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    name = f"__exec_input_{eseguibile_id}_{definition.key}"
    created = file_source_service.create_from_upload(
        file_content=buffer,
        filename=f"{name}.csv",
        name=name,
        description=f"Input {definition.key} per eseguibile {eseguibile_id}",
        group_id=group_id,
    )
    temp_id = created.id if hasattr(created, "id") else created["id"]
    try:
        eseguibili_db.mark_temp_file_source_ttl(temp_id)
    except Exception:
        # Il TTL è una safety net; il task riceve comunque l'elenco cleanup.
        pass
    return str(temp_id)


def resolve_runtime_inputs(
    *,
    eseguibile_id: str,
    canvas: Mapping[str, Any],
    manifest: ExecutableManifest,
    values: Mapping[str, Any],
    group_id: str,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Valida gli input e restituisce canvas risolto, cleanup ids e audit."""

    expected_keys = {item.key for item in manifest.inputs}
    unknown = sorted(set(values) - expected_keys)
    if unknown:
        raise ValueError("Input non dichiarati: " + ", ".join(unknown))

    resolved_canvas = copy.deepcopy(dict(canvas))
    nodes = resolved_canvas.get("nodes") or []
    nodes_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    bindings_by_key: dict[str, list[Any]] = {}
    for binding in manifest.bindings:
        bindings_by_key.setdefault(binding.input_key, []).append(binding)

    cleanup_ids: list[str] = []
    summary: dict[str, Any] = {}
    coerced_scalars = {
        definition.key: _coerce_scalar(
            definition,
            values.get(definition.key, definition.default_value),
        )
        for definition in manifest.inputs
        if definition.kind != "file"
    }
    for definition in sorted(manifest.inputs, key=lambda item: item.order):
        raw_value = values.get(definition.key, definition.default_value)
        bindings = bindings_by_key.get(definition.key, [])
        if definition.kind == "file":
            if raw_value is None:
                if definition.required:
                    raise ValueError(f"Input obbligatorio mancante: {definition.label}")
                continue
            if definition.multiple:
                raise ValueError(
                    f"L'input multiplo '{definition.label}' non è ancora supportato"
                )
            if not isinstance(raw_value, Mapping):
                raise ValueError(f"Payload file non valido per '{definition.label}'")
            try:
                temp_id = _materialize_file_input(
                    eseguibile_id=eseguibile_id,
                    definition=definition,
                    payload=raw_value,
                    group_id=group_id,
                )
            except Exception:
                for created_id in cleanup_ids:
                    try:
                        file_source_service.delete_file_source(created_id)
                    except Exception:
                        pass
                raise
            cleanup_ids.append(temp_id)
            for binding in bindings:
                if binding.mode != "data_source":
                    continue
                node = nodes_by_id.get(binding.target_node_id)
                if node is None:
                    raise ValueError(f"Nodo input non trovato: {binding.target_node_id}")
                node["type"] = "file_source"
                node["file_source_id"] = temp_id
                node.setdefault("data", {})["file_source_id"] = temp_id
            summary[definition.key] = {
                "kind": "file",
                "file_source_id": temp_id,
            }
            continue

        scalar = coerced_scalars[definition.key]
        for binding in bindings:
            if binding.mode != "node_field" or not binding.target_field:
                continue
            node = nodes_by_id.get(binding.target_node_id)
            if node is None:
                raise ValueError(f"Nodo binding non trovato: {binding.target_node_id}")
            _apply_scalar_binding(node, binding.target_field, scalar)
        summary[definition.key] = {"kind": definition.kind, "provided": scalar is not None}

    # I project_input scalari e i loro archi sono configurazione di authoring,
    # non nodi del data-flow. Dopo l'applicazione dei binding non devono
    # raggiungere gli executor dei tre motori.
    scalar_node_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict)
        and node.get("type") == "project_input"
        and str((node.get("data") or {}).get("input_kind") or node.get("input_kind") or "file")
        != "file"
    }
    resolved_canvas["nodes"] = [
        node for node in nodes if str(node.get("id")) not in scalar_node_ids
    ]
    resolved_canvas["edges"] = [
        edge
        for edge in (resolved_canvas.get("edges") or [])
        if str(edge.get("source")) not in scalar_node_ids
        and str(edge.get("target")) not in scalar_node_ids
    ]

    return resolved_canvas, cleanup_ids, summary


__all__ = ["resolve_runtime_inputs"]
