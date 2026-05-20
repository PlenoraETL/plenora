"""Canvas synchronization helpers backed by output_contract."""

from __future__ import annotations

import re
from typing import Any

from app.core.output_contract.service import node_columns_from_contract
from app.core.projects.canvas_models import Column


SCALAR_COLUMN_CONFIG_KEYS = frozenset({
    "aggregate_column",
    "by_column",
    "column",
    "geom_column",
    "geometry_column",
    "group_by",
    "group_column",
    "id_column",
    "index_col",
    "input_geom",
    "lat_column",
    "left_geom",
    "left_geom_column",
    "left_on",
    "lon_column",
    "mask_geom",
    "old_name",
    "order_column",
    "partition_column",
    "pivot_col",
    "point_geom_column",
    "polygon_geom_column",
    "right_geom",
    "right_geom_column",
    "right_on",
    "source_column",
    "stratify_column",
    "target_column",
    "value_col",
    "wkt_column",
})

LIST_COLUMN_CONFIG_KEYS = frozenset({
    "column_order",
    "columns",
    "compare_columns",
    "group_by",
    "group_by_columns",
    "id_columns",
    "join_keys",
    "left_keys",
    "right_keys",
    "subset",
    "value_columns",
})

DICT_COLUMN_REFERENCE_LISTS = {
    "aggregations": ("column", "column_id"),
    "aggs": ("column", "column_id"),
    "maskings": ("column", "column_id"),
    "renames": ("old_name", "old_name_id"),
}

_REMOVED_COLUMN = object()


def sync_canvas_with_output_contracts(
    *,
    canvas: Any,
    project_id: str,
    project_service: Any,
) -> bool:
    """Normalize project_source columns and formula refs on a canvas."""

    changed, column_migrations_by_node = _sync_project_source_columns(
        canvas,
        project_service,
    )
    changed = _sync_transform_expression_configs(
        canvas=canvas,
        project_id=project_id,
        project_service=project_service,
        column_migrations_by_node=column_migrations_by_node,
    ) or changed
    changed = _sync_api_enrich_column_configs(
        canvas=canvas,
        project_id=project_id,
        project_service=project_service,
        column_migrations_by_node=column_migrations_by_node,
    ) or changed
    return changed


def _sync_project_source_columns(
    canvas: Any,
    project_service: Any,
) -> tuple[bool, dict[str, dict[str, dict[str, Any] | None]]]:
    changed = False
    column_migrations_by_node: dict[str, dict[str, dict[str, Any] | None]] = {}
    for node in getattr(canvas, "nodes", []) or []:
        if getattr(node, "type", None) != "project_source":
            continue
        source_project_id = _node_field(node, "source_project_id")
        if not source_project_id:
            continue
        output_contract = project_service.get_project_output_contract(source_project_id)
        if not isinstance(output_contract, dict):
            continue
        previous_columns = [_as_dict(c) for c in (getattr(node, "columns", None) or [])]
        next_columns = node_columns_from_contract(
            output_contract,
            previous_node_columns=previous_columns,
        )
        column_migrations_by_node[node.id] = _build_column_migrations(
            previous_columns,
            next_columns,
        )
        if _column_fingerprint(previous_columns) == _column_fingerprint(next_columns):
            continue
        node.columns = [Column(**c) for c in next_columns]
        if isinstance(getattr(node, "data", None), dict):
            node.data["columns"] = next_columns
        changed = True
    return changed, column_migrations_by_node


def _sync_transform_expression_configs(
    *,
    canvas: Any,
    project_id: str,
    project_service: Any,
    column_migrations_by_node: dict[str, dict[str, dict[str, Any] | None]] | None = None,
) -> bool:
    column_migrations_by_node = column_migrations_by_node or {}
    nodes_by_id = {n.id: n for n in getattr(canvas, "nodes", []) or []}
    edges = getattr(canvas, "edges", []) or []
    edges_by_target: dict[str, list[Any]] = {}
    for edge in edges:
        edges_by_target.setdefault(edge.target, []).append(edge)

    try:
        previous_contract = project_service.get_project_output_contract(project_id)
    except Exception:
        previous_contract = None
    schema_context = _build_schema_context(canvas, project_service)

    changed = False
    for node in getattr(canvas, "nodes", []) or []:
        if getattr(node, "type", None) != "transform":
            continue
        config = _node_field(node, "config") or {}
        if not isinstance(config, dict):
            continue

        incoming_columns: list[dict[str, Any]] = []
        incoming_migrations: dict[str, dict[str, Any] | None] = {}
        for edge in edges_by_target.get(node.id, []) or []:
            source_node = nodes_by_id.get(edge.source)
            if not source_node:
                continue
            incoming_columns.extend(
                _node_output_columns(
                    source_node,
                    schema_context=schema_context,
                    project_id=project_id,
                )
            )
            _merge_column_migrations(
                incoming_migrations,
                column_migrations_by_node.get(source_node.id) or {},
            )

        next_config = _sync_expression_config(
            config,
            incoming_columns,
            column_migrations=incoming_migrations,
            extra_legacy_names=_contract_legacy_name_map(
                previous_contract,
                incoming_columns,
            ),
        )
        if next_config is not config:
            node.config = next_config
            if isinstance(getattr(node, "data", None), dict):
                node.data["config"] = next_config
            changed = True

        _merge_column_migrations(
            column_migrations_by_node.setdefault(node.id, {}),
            _filter_column_migrations_for_output(
                incoming_migrations,
                _node_output_columns(
                    node,
                    schema_context=schema_context,
                    project_id=project_id,
                ),
            ),
        )
    return changed


def _sync_api_enrich_column_configs(
    *,
    canvas: Any,
    project_id: str,
    project_service: Any,
    column_migrations_by_node: dict[str, dict[str, dict[str, Any] | None]] | None = None,
) -> bool:
    """Rewrite api_enrich input column mappings after upstream contract changes."""

    column_migrations_by_node = column_migrations_by_node or {}
    nodes_by_id = {n.id: n for n in getattr(canvas, "nodes", []) or []}
    edges = getattr(canvas, "edges", []) or []
    edges_by_target: dict[str, list[Any]] = {}
    for edge in edges:
        edges_by_target.setdefault(edge.target, []).append(edge)

    schema_context = _build_schema_context(canvas, project_service)
    changed = False
    for node in getattr(canvas, "nodes", []) or []:
        if getattr(node, "type", None) != "api_enrich":
            continue
        param_mapping = _node_field(node, "param_mapping") or {}
        if not isinstance(param_mapping, dict):
            continue

        incoming_columns: list[dict[str, Any]] = []
        incoming_migrations: dict[str, dict[str, Any] | None] = {}
        for edge in edges_by_target.get(node.id, []) or []:
            source_node = nodes_by_id.get(edge.source)
            if not source_node:
                continue
            incoming_columns.extend(
                _node_output_columns(
                    source_node,
                    schema_context=schema_context,
                    project_id=project_id,
                )
            )
            _merge_column_migrations(
                incoming_migrations,
                column_migrations_by_node.get(source_node.id) or {},
            )

        next_mapping = _sync_param_mapping(
            param_mapping,
            incoming_columns,
            column_migrations=incoming_migrations,
        )
        if next_mapping is param_mapping:
            continue
        node.param_mapping = next_mapping
        if isinstance(getattr(node, "data", None), dict):
            node.data["param_mapping"] = next_mapping
        changed = True
    return changed


def _sync_param_mapping(
    mapping: dict[str, Any],
    columns: list[dict[str, Any]],
    *,
    column_migrations: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    changed = False
    out = dict(mapping)
    by_name = _columns_by_current_name(columns)
    legacy_names = _legacy_name_map(columns)
    legacy_names.update(_legacy_name_map_from_migrations(column_migrations))

    for api_param, raw_value in mapping.items():
        if not isinstance(raw_value, str) or not raw_value:
            continue
        next_value = _sync_param_mapping_value(
            raw_value,
            by_name,
            legacy_names,
            column_migrations,
        )
        if next_value != raw_value:
            out[api_param] = next_value
            changed = True
    return out if changed else mapping


def _sync_param_mapping_value(
    value: str,
    columns_by_name: dict[str, dict[str, Any]],
    legacy_names: dict[str, str],
    column_migrations: dict[str, dict[str, Any] | None],
) -> str:
    if _is_removed_column_reference(value, column_migrations):
        return ""

    current = _resolve_column_name(
        value,
        columns_by_name,
        legacy_names,
        column_migrations,
    )
    if current and current.get("name"):
        return str(current["name"])

    next_value = value
    for old_name, current_name in legacy_names.items():
        next_value = next_value.replace(f"{{{old_name}}}", f"{{{current_name}}}")
    for removed_name in _removed_name_tokens_from_migrations(column_migrations):
        if f"{{{removed_name}}}" in next_value:
            return ""
    return next_value


def _node_output_columns(
    node: Any,
    *,
    schema_context: tuple[Any, dict[str, Any], dict[str, list[Any]]] | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    columns = [_as_dict(c) for c in (getattr(node, "columns", None) or [])]
    if columns or not schema_context or not project_id:
        return columns
    schema_service, nodes_by_id, edges_by_target = schema_context
    try:
        schema = schema_service._infer_node_schema(
            node=node,
            nodes_by_id=nodes_by_id,
            edges_by_target=edges_by_target,
            project_id=project_id,
        )
    except Exception:
        return []
    return [_as_dict(col) for col in schema]


def _sync_expression_config(
    config: dict[str, Any],
    columns: list[dict[str, Any]],
    *,
    column_migrations: dict[str, dict[str, Any] | None] | None = None,
    extra_legacy_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    out, changed = _sync_column_reference_config(
        config,
        columns,
        column_migrations=column_migrations,
        extra_legacy_names=extra_legacy_names,
    )
    by_id = {
        str(col.get("id") or col.get("column_id")): col
        for col in columns
        if col.get("id") or col.get("column_id")
    }

    for key, refs in list(out.items()):
        if not key.endswith("_column_refs") or not isinstance(refs, list):
            continue
        expr_key = key[:-12]
        expr = out.get(expr_key)
        if not isinstance(expr, str):
            continue
        next_expr = expr
        next_refs = []
        removed_ref_seen = False
        columns_by_name = _columns_by_current_name(columns)
        legacy_names = _legacy_name_map(columns)
        legacy_names.update(_legacy_name_map_from_migrations(column_migrations or {}))
        legacy_names.update(extra_legacy_names or {})
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            ref_name = str(ref.get("name") or "")
            if _is_removed_column_reference(
                ref_name,
                column_migrations or {},
                None,
                None,
                ref.get("id"),
            ):
                removed_ref_seen = True
                changed = True
                continue
            current = by_id.get(str(ref["id"])) if ref.get("id") else None
            if not current:
                current = _lookup_column_migration(column_migrations or {}, ref)
            if not current and ref_name:
                current = _resolve_column_name(
                    ref_name,
                    columns_by_name,
                    legacy_names,
                    column_migrations or {},
                )
            if not current or not current.get("name"):
                next_refs.append(ref)
                continue
            current_id = current.get("id") or current.get("column_id") or ref.get("id")
            next_expr = _rewrite_expression_token(
                next_expr,
                str(ref.get("name") or ""),
                str(current["name"]),
            )
            next_refs.append({"id": current_id, "name": current["name"]})
        if removed_ref_seen:
            next_expr = ""
            next_refs = []
        if next_expr != expr:
            out[expr_key] = next_expr
            changed = True
        if next_refs != refs:
            out[key] = next_refs
            changed = True

    for expr_key in ("formula",):
        expr = out.get(expr_key)
        if not isinstance(expr, str) or not expr:
            continue
        refs_key = f"{expr_key}_column_refs"
        if isinstance(out.get(refs_key), list) and out[refs_key]:
            continue
        next_expr = expr
        legacy_names = _legacy_name_map(columns)
        legacy_names.update(_legacy_name_map_from_migrations(column_migrations or {}))
        legacy_names.update(extra_legacy_names or {})
        for old_name, current_name in legacy_names.items():
            next_expr = _rewrite_expression_token(next_expr, old_name, current_name)
        if any(
            _expression_has_token(next_expr, removed_name)
            for removed_name in _removed_name_tokens_from_migrations(column_migrations or {})
        ):
            if out.get(expr_key) != "":
                out[expr_key] = ""
                changed = True
            if out.get(refs_key) != []:
                out[refs_key] = []
                changed = True
            continue
        refs = _collect_expression_refs(next_expr, columns)
        if next_expr != expr:
            out[expr_key] = next_expr
            changed = True
        if refs:
            out[refs_key] = refs
            changed = True

    return out if changed else config


def _sync_column_reference_config(
    config: dict[str, Any],
    columns: list[dict[str, Any]],
    *,
    column_migrations: dict[str, dict[str, Any] | None] | None = None,
    extra_legacy_names: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Rewrite non-expression column params after upstream alias changes."""

    changed = False
    out = dict(config)
    by_name = _columns_by_current_name(columns)
    legacy_names = _legacy_name_map(columns)
    legacy_names.update(_legacy_name_map_from_migrations(column_migrations or {}))
    legacy_names.update(extra_legacy_names or {})

    for key in SCALAR_COLUMN_CONFIG_KEYS:
        value = out.get(key)
        if not isinstance(value, str):
            continue
        id_key = f"{key}_column_id"
        if _is_removed_column_reference(
            value,
            column_migrations or {},
            None,
            None,
            out.get(id_key),
        ):
            if out.get(key) != "":
                out[key] = ""
                changed = True
            if id_key in out and out.get(id_key) is not None:
                out[id_key] = None
                changed = True
            continue
        current = _lookup_id_migration(column_migrations or {}, out.get(id_key))
        if not current:
            current = _resolve_column_name(
            value,
            by_name,
            legacy_names,
            column_migrations or {},
            )
        if not current:
            continue
        current_name = str(current.get("name") or value)
        current_id = current.get("id") or current.get("column_id")
        if current_name != value:
            out[key] = current_name
            changed = True
        if current_id and id_key in out and out.get(id_key) != current_id:
            out[id_key] = current_id
            changed = True

    for key in LIST_COLUMN_CONFIG_KEYS:
        value = out.get(key)
        if not isinstance(value, list):
            continue
        next_values: list[Any] = []
        next_ids: list[Any] = []
        resolved_any_id = False
        for item_index, item in enumerate(value):
            if not isinstance(item, str):
                next_values.append(item)
                next_ids.append(None)
                continue
            companion_ids = out.get(f"{key}_column_ids")
            if _is_removed_column_reference(
                item,
                column_migrations or {},
                companion_ids,
                item_index,
            ):
                changed = True
                continue
            companion_id = (
                companion_ids[item_index]
                if isinstance(companion_ids, list) and item_index < len(companion_ids)
                else None
            )
            current = _lookup_id_migration(column_migrations or {}, companion_id)
            if not current:
                current = _resolve_column_name(
                item,
                by_name,
                legacy_names,
                column_migrations or {},
                )
            if not current:
                next_values.append(item)
                next_ids.append(None)
                continue
            current_name = str(current.get("name") or item)
            current_id = current.get("id") or current.get("column_id")
            next_values.append(current_name)
            next_ids.append(current_id)
            resolved_any_id = resolved_any_id or bool(current_id)
        if next_values != value:
            out[key] = next_values
            changed = True
        ids_key = f"{key}_column_ids"
        if (
            ids_key in out
            and (resolved_any_id or next_values != value)
            and out.get(ids_key) != next_ids
        ):
            out[ids_key] = next_ids
            changed = True

    for key, (name_field, id_field) in DICT_COLUMN_REFERENCE_LISTS.items():
        out = _sync_dict_column_reference_list(
            out,
            key=key,
            name_field=name_field,
            id_field=id_field,
            columns_by_name=by_name,
            legacy_names=legacy_names,
            column_migrations=column_migrations or {},
        )

    return out, changed or out != config


def _sync_dict_column_reference_list(
    config: dict[str, Any],
    *,
    key: str,
    name_field: str,
    columns_by_name: dict[str, dict[str, Any]],
    legacy_names: dict[str, str],
    column_migrations: dict[str, dict[str, Any] | None],
    id_field: str = "column_id",
) -> dict[str, Any]:
    values = config.get(key)
    if not isinstance(values, list):
        return config
    changed = False
    next_values: list[Any] = []
    for item in values:
        if not isinstance(item, dict):
            next_values.append(item)
            continue
        value = item.get(name_field)
        if not isinstance(value, str):
            next_values.append(item)
            continue
        current = _lookup_id_migration(column_migrations, item.get(id_field))
        if not current:
            current = _resolve_column_name(
            value,
            columns_by_name,
            legacy_names,
            column_migrations,
            )
        if current is None and _is_removed_column_reference(
            value,
            column_migrations,
            None,
            None,
            item.get(id_field),
        ):
            changed = True
            continue
        if not current:
            next_values.append(item)
            continue
        current_name = str(current.get("name") or value)
        current_id = current.get("id") or current.get("column_id")
        next_item = dict(item)
        if current_name != value:
            next_item[name_field] = current_name
            changed = True
        if current_id and id_field in next_item and next_item.get(id_field) != current_id:
            next_item[id_field] = current_id
            changed = True
        next_values.append(next_item)
    if not changed:
        return config
    out = dict(config)
    out[key] = next_values
    return out


def _columns_by_current_name(columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for col in columns:
        name = col.get("name")
        if name:
            candidates.setdefault(str(name), []).append(col)
    return {
        name: values[0]
        for name, values in candidates.items()
        if len(values) == 1
    }


def _build_schema_context(
    canvas: Any,
    project_service: Any,
) -> tuple[Any, dict[str, Any], dict[str, list[Any]]] | None:
    try:
        from app.core.projects.schema_inference import SchemaInferenceService

        schema_service = SchemaInferenceService(project_service)
        nodes_by_id = {n.id: n for n in getattr(canvas, "nodes", []) or []}
        edges_by_target = schema_service._group_edges_by_target(
            getattr(canvas, "edges", []) or []
        )
        return schema_service, nodes_by_id, edges_by_target
    except Exception:
        return None


def _contract_legacy_name_map(
    contract: Any,
    columns: list[dict[str, Any]],
) -> dict[str, str]:
    if not isinstance(contract, dict):
        return {}
    previous_columns = [
        col for col in (contract.get("columns") or [])
        if isinstance(col, dict) and col.get("status", "active") == "active"
    ]
    current_by_id = {
        str(col.get("id") or col.get("column_id")): col
        for col in columns
        if col.get("id") or col.get("column_id")
    }
    current_by_source_candidates: dict[str, list[dict[str, Any]]] = {}
    for col in columns:
        source_name = col.get("source_name")
        if source_name:
            current_by_source_candidates.setdefault(str(source_name), []).append(col)
    current_by_source = {
        source_name: values[0]
        for source_name, values in current_by_source_candidates.items()
        if len(values) == 1
    }

    candidates: dict[str, set[str]] = {}
    for previous in previous_columns:
        current = None
        previous_id = previous.get("id")
        if previous_id:
            current = current_by_id.get(str(previous_id))
        if not current and previous.get("source_name"):
            current = current_by_source.get(str(previous.get("source_name")))
        if not current or not current.get("name"):
            continue
        current_name = str(current["name"])
        for key in ("physical_name", "display_name"):
            old_name = previous.get(key)
            if old_name and str(old_name) != current_name:
                candidates.setdefault(str(old_name), set()).add(current_name)
    return {
        old_name: next(iter(names))
        for old_name, names in candidates.items()
        if len(names) == 1
    }


def _resolve_column_name(
    name: str,
    columns_by_name: dict[str, dict[str, Any]],
    legacy_names: dict[str, str],
    column_migrations: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    migrated = column_migrations.get(f"name:{name}")
    if isinstance(migrated, dict):
        return migrated
    current_name = legacy_names.get(name, name)
    return columns_by_name.get(current_name)


def _build_column_migrations(
    previous_columns: list[dict[str, Any]],
    next_columns: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    migrations: dict[str, dict[str, Any] | None] = {}
    next_by_id = {
        str(col.get("id") or col.get("column_id")): col
        for col in next_columns
        if col.get("id") or col.get("column_id")
    }
    next_by_source_candidates: dict[str, list[dict[str, Any]]] = {}
    for col in next_columns:
        source_name = col.get("source_name")
        if source_name:
            next_by_source_candidates.setdefault(str(source_name), []).append(col)
    next_by_source = {
        name: values[0]
        for name, values in next_by_source_candidates.items()
        if len(values) == 1
    }

    matched_next_indexes: set[int] = set()
    for index, previous in enumerate(previous_columns):
        current = None
        previous_id = previous.get("id") or previous.get("column_id")
        if previous_id:
            current = next_by_id.get(str(previous_id))
        if not current and previous.get("source_name"):
            current = next_by_source.get(str(previous.get("source_name")))
        if not current and len(previous_columns) == len(next_columns) and index not in matched_next_indexes:
            current = next_columns[index]
        if not current:
            for key in _column_lookup_keys(previous):
                _put_unique_migration(migrations, key, _REMOVED_COLUMN)
            continue
        try:
            matched_next_indexes.add(next_columns.index(current))
        except ValueError:
            pass
        if not current.get("name"):
            continue
        if _same_column_reference(previous, current):
            continue
        for key in _column_lookup_keys(previous):
            _put_unique_migration(migrations, key, current)
    return migrations


def _same_column_reference(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_id = previous.get("id") or previous.get("column_id")
    current_id = current.get("id") or current.get("column_id")
    return (
        str(previous_id or "") == str(current_id or "")
        and str(previous.get("name") or "") == str(current.get("name") or "")
    )


def _column_lookup_keys(column: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    col_id = column.get("id") or column.get("column_id")
    if col_id:
        keys.append(f"id:{col_id}")
    for name_key in ("name", "source_name", "display_name"):
        name = column.get(name_key)
        if name:
            keys.append(f"name:{name}")
    return keys


def _put_unique_migration(
    migrations: dict[str, Any],
    key: str,
    current: Any,
) -> None:
    existing = migrations.get(key)
    if key in migrations and existing != current:
        migrations[key] = None
        return
    migrations[key] = current


def _is_removed_column_reference(
    name: str,
    migrations: dict[str, dict[str, Any] | None],
    ids: Any = None,
    index: int | None = None,
    explicit_id: Any = None,
) -> bool:
    candidate_id = explicit_id
    if candidate_id is None and isinstance(ids, list) and index is not None and index < len(ids):
        candidate_id = ids[index]
    if candidate_id:
        return migrations.get(f"id:{candidate_id}") is _REMOVED_COLUMN
    if name and f"name:{name}" in migrations and migrations.get(f"name:{name}") is _REMOVED_COLUMN:
        return True
    return False


def _lookup_id_migration(
    migrations: dict[str, dict[str, Any] | None],
    column_id: Any,
) -> dict[str, Any] | None:
    if not column_id:
        return None
    current = migrations.get(f"id:{column_id}")
    return current if isinstance(current, dict) else None


def _merge_column_migrations(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    for key, current in source.items():
        if current is _REMOVED_COLUMN:
            target[key] = _REMOVED_COLUMN
            continue
        if current is None:
            target[key] = None
            continue
        _put_unique_migration(target, key, current)


def _lookup_column_migration(
    migrations: dict[str, dict[str, Any] | None],
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    col_id = ref.get("id") or ref.get("column_id")
    if col_id:
        found = migrations.get(f"id:{col_id}")
        if isinstance(found, dict):
            return found
    name = ref.get("name")
    if name:
        found = migrations.get(f"name:{name}")
        if isinstance(found, dict):
            return found
    return None


def _filter_column_migrations_for_output(
    migrations: dict[str, dict[str, Any] | None],
    columns: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    if not migrations:
        return {}
    if not columns:
        return dict(migrations)

    output_ids = {
        str(col.get("id") or col.get("column_id"))
        for col in columns
        if col.get("id") or col.get("column_id")
    }
    output_names = {
        str(value)
        for col in columns
        for value in (col.get("name"), col.get("source_name"), col.get("display_name"))
        if value
    }

    out: dict[str, dict[str, Any] | None] = {}
    for key, current in migrations.items():
        if not isinstance(current, dict):
            if current is _REMOVED_COLUMN:
                out[key] = _REMOVED_COLUMN
            continue
        current_id = current.get("id") or current.get("column_id")
        current_name = current.get("name")
        if (current_id and str(current_id) in output_ids) or (
            current_name and str(current_name) in output_names
        ):
            out[key] = current
    return out


def _legacy_name_map_from_migrations(
    migrations: dict[str, dict[str, Any] | None],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, current in migrations.items():
        if not key.startswith("name:") or not isinstance(current, dict):
            continue
        current_name = current.get("name")
        old_name = key.removeprefix("name:")
        if current_name and old_name != str(current_name):
            out[old_name] = str(current_name)
    return out


def _removed_name_tokens_from_migrations(
    migrations: dict[str, dict[str, Any] | None],
) -> set[str]:
    return {
        key.removeprefix("name:")
        for key, current in migrations.items()
        if key.startswith("name:") and current is _REMOVED_COLUMN
    }


def _legacy_name_map(columns: list[dict[str, Any]]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for col in columns:
        current = col.get("name")
        if not current:
            continue
        for key in ("source_name", "display_name"):
            old = col.get(key)
            if old and str(old) != str(current):
                candidates.setdefault(str(old), set()).add(str(current))
    return {
        old: next(iter(values))
        for old, values in candidates.items()
        if len(values) == 1
    }


def _collect_expression_refs(
    expression: str,
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    token_columns: dict[str, set[str]] = {}
    for col in columns:
        col_id = col.get("id") or col.get("column_id")
        if not col_id:
            continue
        col_id = str(col_id)
        for name in (col.get("name"), col.get("source_name"), col.get("display_name")):
            if name:
                token_columns.setdefault(str(name), set()).add(col_id)

    ref_ids: set[str] = set()
    for token, col_ids in token_columns.items():
        if len(col_ids) != 1 or not _expression_has_token(expression, token):
            continue
        ref_ids.add(next(iter(col_ids)))
    refs = []
    for col in columns:
        col_id = col.get("id") or col.get("column_id")
        if col_id and str(col_id) in ref_ids and col.get("name"):
            refs.append({"id": col_id, "name": col.get("name")})
    return refs


def _rewrite_expression_token(expression: str, old_name: str, new_name: str) -> str:
    if not expression or not old_name or not new_name or old_name == new_name:
        return expression
    rewritten = expression
    rewritten = rewritten.replace(f"`{old_name}`", f"`{new_name}`")
    rewritten = rewritten.replace(f"['{old_name}']", f"['{new_name}']")
    rewritten = rewritten.replace(f'["{old_name}"]', f'["{new_name}"]')
    if old_name.isidentifier():
        rewritten = re.sub(rf"\b{re.escape(old_name)}\b", new_name, rewritten)
    return rewritten


def _expression_has_token(expression: str, token: str) -> bool:
    if not expression or not token:
        return False
    if f"`{token}`" in expression:
        return True
    if f"['{token}']" in expression:
        return True
    if f'["{token}"]' in expression:
        return True
    if token.isidentifier():
        return bool(re.search(rf"\b{re.escape(token)}\b", expression))
    return token in expression


def _column_fingerprint(columns: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            c.get("id") or c.get("column_id"),
            c.get("name"),
            c.get("source_name"),
            c.get("display_name"),
            c.get("type"),
            c.get("visible", True),
        )
        for c in columns
    ]


def _node_field(node: Any, key: str, default: Any = None) -> Any:
    value = getattr(node, key, None)
    if value is not None:
        return value
    data = getattr(node, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {
        "id": getattr(value, "id", None),
        "column_id": getattr(value, "column_id", None),
        "name": getattr(value, "name", None),
        "source_name": getattr(value, "source_name", None),
        "display_name": getattr(value, "display_name", None),
        "type": getattr(value, "type", None),
        "visible": getattr(value, "visible", True),
    }


__all__ = ["sync_canvas_with_output_contracts"]
