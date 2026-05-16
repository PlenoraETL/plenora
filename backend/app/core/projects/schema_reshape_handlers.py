"""Reshape transform schema handlers for schema inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.core.projects.types import ColumnMetadataRows

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


class ReshapeSchemaHandlers:
    """Mixin for reshape and config-explicit structure schema handlers."""

    def _schema_melt(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """melt: wide -> long."""
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        id_cols = [
            c for c in self._normalize_str_list(config.get("id_columns"))
            if c in by_name
        ]
        raw_value = config.get("value_columns")
        if raw_value is None or raw_value == "" or raw_value == []:
            value_cols_names = [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]
        else:
            normalized = [
                c for c in self._normalize_str_list(raw_value)
                if c in by_name
            ]
            value_cols_names = normalized or [
                c.name for c in input_schema if c.name not in set(id_cols)
            ]

        var_name = config.get("var_name") or "variable"
        value_name = config.get("value_name") or "value"

        types = {by_name[c].type for c in value_cols_names if c in by_name}
        if len(types) == 1:
            val_type = next(iter(types))
        else:
            val_type = "str"

        result: List[ColumnSchema] = []
        for c in id_cols:
            col = by_name[c]
            result.append(self._column_schema_cls(
                name=c,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
        result.append(self._column_schema_cls(
            name=var_name,
            type="str",
            source_node=node_id,
            is_calculated=True,
        ))
        result.append(self._column_schema_cls(
            name=value_name,
            type=val_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result

    def _schema_flatten_json(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """flatten_json: input + N columns declared via output_columns."""
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return result

        existing = {c.name for c in result}
        column = config.get("column") or ""
        prefix = config.get("prefix") or (f"{column}_" if column else "")
        for entry in output_cols:
            name = entry["name"]
            full_name = f"{prefix}{name}" if prefix else name
            if not full_name or full_name in existing:
                continue
            result.append(self._column_schema_cls(
                name=full_name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(full_name)
        return result

    def _schema_transpose(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """transpose: schema declared via output_columns."""
        if not input_schemas:
            return []
        output_cols = self._normalize_output_columns(config.get("output_columns"))
        if not output_cols:
            return self._copy_schema(input_schemas[0], node_id)

        input_by_name = {c.name: c for c in input_schemas[0]}
        result: List[ColumnSchema] = []
        seen: set = set()

        id_column = config.get("id_column") or ""
        if id_column and id_column in input_by_name:
            col = input_by_name[id_column]
            result.append(self._column_schema_cls(
                name=id_column,
                type=col.type,
                source_node=node_id,
                is_calculated=False,
            ))
            seen.add(id_column)

        for entry in output_cols:
            name = entry["name"]
            if not name or name in seen:
                continue
            result.append(self._column_schema_cls(
                name=name,
                type=entry["type"],
                source_node=node_id,
                is_calculated=True,
            ))
            seen.add(name)
        return result

    def _normalize_output_columns(
        self,
        raw,
    ) -> ColumnMetadataRows:
        """Normalize output_columns to List[{name, type}]."""
        if raw is None:
            return []
        if isinstance(raw, str):
            s = raw.strip()
            return [{"name": s, "type": "str"}] if s else []
        if not isinstance(raw, list):
            return []

        out: ColumnMetadataRows = []
        for entry in raw:
            if isinstance(entry, str):
                s = entry.strip()
                if s:
                    out.append({"name": s, "type": "str"})
            elif isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                type_ = str(entry.get("type") or "").strip() or "str"
                out.append({"name": name, "type": type_})
        return out
