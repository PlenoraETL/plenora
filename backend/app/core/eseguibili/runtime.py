"""Risoluzione tipizzata degli input di una run eseguibile."""

from __future__ import annotations

import copy
import io
import math
import re
from datetime import date, datetime
from typing import Any, Mapping

from app.core.dataframes import geodata as dataframe_geodata
from app.core.dataframes import tabular as dataframe_tabular
from app.core.files.service import file_source_service
from app.shared.contracts.payloads import JsonObject

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
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            coerced = int(value)
        elif kind == "number":
            if isinstance(value, bool):
                raise ValueError
            coerced = float(value)
            if not math.isfinite(coerced):
                raise ValueError
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


def _set_existing_path(
    container: dict[str, Any],
    path: str,
    value: Any,
    *,
    allow_missing_leaf: bool = False,
) -> None:
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
        if leaf not in current and not allow_missing_leaf:
            raise ValueError(f"Campo binding inesistente: {path}")
        current[leaf] = value
    else:
        raise ValueError(f"Campo binding non scrivibile: {path}")


def _apply_scalar_binding(node: dict[str, Any], path: str, value: Any) -> None:
    data = node.get("data")
    applied = False
    if isinstance(data, dict) and path.split(".", 1)[0] in data:
        _set_existing_path(data, path, value, allow_missing_leaf=True)
        applied = True
    if path.split(".", 1)[0] in node:
        _set_existing_path(node, path, value, allow_missing_leaf=True)
        applied = True
    if not applied and path.split(".", 1)[0] == "config":
        container = data if isinstance(data, dict) else node
        container["config"] = {}
        _set_existing_path(container, path, value, allow_missing_leaf=True)
        applied = True
    if not applied:
        raise ValueError(f"Nodo {node.get('id')} senza campo binding {path}")


def _materialize_file_input(
    *,
    eseguibile_id: str,
    definition: ExecutableInputDefinition,
    payload: Mapping[str, Any] | list[Mapping[str, Any]],
    group_id: str,
) -> str:
    payloads = payload if isinstance(payload, list) else [payload]
    frames = []
    spatial_flags: list[bool] = []
    for index, item_payload in enumerate(payloads, start=1):
        file_id = str(item_payload.get("file_id") or "")
        if not file_id:
            raise ValueError(f"File {index} mancante per '{definition.label}'")
        df, gdf = file_source_service.load_dataframe(
            file_id,
            expected_group_id=str(
                item_payload.get("_upload_group_id") or group_id
            ),
        )
        if df is None or df.empty:
            raise ValueError(
                f"Il file {index} di '{definition.label}' è vuoto o illeggibile"
            )

        rename_map: dict[str, str] = {}
        raw_mapping = item_payload.get("column_mapping") or []
        if not isinstance(raw_mapping, list):
            raise ValueError(
                f"Mapping colonne non valido per '{definition.label}'"
            )
        for item in raw_mapping:
            if not isinstance(item, Mapping):
                continue
            chain_col = str(item.get("chain_column") or "")
            file_col = str(item.get("file_column") or "")
            if chain_col and file_col and file_col in df.columns:
                rename_map[file_col] = chain_col
        frame = gdf if gdf is not None else df
        if rename_map:
            geometry = getattr(frame, "geometry", None)
            geometry_name = getattr(geometry, "name", None)
            if geometry_name in rename_map and hasattr(frame, "rename_geometry"):
                frame = frame.rename_geometry(rename_map.pop(geometry_name))
            if rename_map:
                frame = frame.rename(columns=rename_map)

        missing = [
            column.name
            for column in definition.expected_columns
            if column.required and column.name not in frame.columns
        ]
        if missing:
            raise ValueError(
                f"Colonne obbligatorie non mappate nel file {index} per "
                f"'{definition.label}': " + ", ".join(missing)
            )
        frames.append(frame)
        spatial_flags.append(gdf is not None)

    if len(frames) == 1:
        df = frames[0]
    else:
        if any(spatial_flags) and not all(spatial_flags):
            raise ValueError(
                f"'{definition.label}' non puo' unire file geografici e tabellari"
            )
        if all(spatial_flags):
            geometry_name = frames[0].geometry.name
            reference_crs = frames[0].crs
            aligned = []
            for frame in frames:
                if frame.geometry.name != geometry_name:
                    raise ValueError(
                        f"I file di '{definition.label}' usano colonne geometria diverse"
                    )
                if frame.crs != reference_crs:
                    if reference_crs is None or frame.crs is None:
                        raise ValueError(
                            f"I file di '{definition.label}' hanno CRS incompatibili"
                        )
                    frame = frame.to_crs(reference_crs)
                aligned.append(frame)
            combined = dataframe_tabular.concat_dataframes(
                aligned,
                ignore_index=True,
            )
            df = dataframe_geodata.geodataframe_from_geometry(
                combined,
                geometry=geometry_name,
                crs=reference_crs,
            )
        else:
            df = dataframe_tabular.concat_dataframes(frames, ignore_index=True)

    name = f"__exec_input_{eseguibile_id}_{definition.key}"
    if all(spatial_flags):
        from app.core.dataframes.geospatial_io import geospatial_dataframe_to_bytes

        content = geospatial_dataframe_to_bytes(
            df,
            "geojson",
            df.geometry.name,
            str(df.crs or ""),
        )
        buffer = io.BytesIO(content)
        filename = f"{name}.geojson"
    else:
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False, encoding="utf-8")
        buffer.seek(0)
        filename = f"{name}.csv"
    created = file_source_service.create_from_upload(
        file_content=buffer,
        filename=filename,
        name=name,
        description=f"Input {definition.key} per eseguibile {eseguibile_id}",
        group_id=group_id,
    )
    temp_id = created.id if hasattr(created, "id") else created["id"]
    try:
        eseguibili_db.mark_temp_file_source_ttl(temp_id)
    except Exception as exc:
        file_source_service.delete_file_source(temp_id)
        raise RuntimeError(
            "Impossibile impostare la scadenza dell'input temporaneo"
        ) from exc
    return str(temp_id)


def _cleanup_runtime_files(file_ids: list[str]) -> None:
    for file_id in file_ids:
        try:
            file_source_service.delete_file_source(file_id)
        except Exception:
            pass


def resolve_runtime_inputs(
    *,
    eseguibile_id: str,
    canvas: Mapping[str, Any],
    manifest: ExecutableManifest,
    values: Mapping[str, Any],
    group_id: str,
) -> tuple[JsonObject, list[str], JsonObject]:
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
                if not isinstance(raw_value, list) or not raw_value:
                    raise ValueError(
                        f"Payload multi-file non valido per '{definition.label}'"
                    )
                if not all(isinstance(item, Mapping) for item in raw_value):
                    raise ValueError(
                        f"Payload multi-file non valido per '{definition.label}'"
                    )
            elif not isinstance(raw_value, Mapping):
                raise ValueError(f"Payload file non valido per '{definition.label}'")
            try:
                temp_id = _materialize_file_input(
                    eseguibile_id=eseguibile_id,
                    definition=definition,
                    payload=raw_value,
                    group_id=group_id,
                )
            except Exception:
                _cleanup_runtime_files(cleanup_ids)
                raise
            cleanup_ids.append(temp_id)
            try:
                for binding in bindings:
                    if binding.mode != "data_source":
                        continue
                    node = nodes_by_id.get(binding.target_node_id)
                    if node is None:
                        raise ValueError(f"Nodo input non trovato: {binding.target_node_id}")
                    node["type"] = "file_source"
                    node["file_source_id"] = temp_id
                    node.setdefault("data", {})["file_source_id"] = temp_id
            except Exception:
                _cleanup_runtime_files(cleanup_ids)
                raise
            summary[definition.key] = {
                "kind": "file",
                "file_source_id": temp_id,
                "files_count": len(raw_value) if isinstance(raw_value, list) else 1,
            }
            continue

        scalar = coerced_scalars[definition.key]
        try:
            for binding in bindings:
                if binding.mode != "node_field" or not binding.target_field:
                    continue
                node = nodes_by_id.get(binding.target_node_id)
                if node is None:
                    raise ValueError(f"Nodo binding non trovato: {binding.target_node_id}")
                _apply_scalar_binding(node, binding.target_field, scalar)
        except Exception:
            _cleanup_runtime_files(cleanup_ids)
            raise
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
