"""Reshape transform schema handlers for schema inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.core.projects.types import ColumnMetadataRows

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


class ReshapeSchemaHandlers:
    """Mixin for reshape and config-explicit structure schema handlers."""

    def _schema_pivot(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """pivot: long -> wide when output columns are declared by mapping."""
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        index_cols = [
            name for name in self._normalize_str_list(config.get("index_col"))
            if name in by_name
        ]
        pivot_col = config.get("pivot_col") or ""
        value_col = config.get("value_col") or ""
        # Runtime op_pivot e' no-op se uno dei parametri obbligatori e'
        # assente o punta a una colonna inesistente.
        if not index_cols or pivot_col not in by_name or value_col not in by_name:
            return self._copy_schema(input_schema, node_id)
        value_type = getattr(by_name.get(value_col), "type", None) or "str"
        aggr_func = str(config.get("aggr_func") or "").strip().lower()
        if aggr_func == "count":
            output_type = "int"
        elif aggr_func == "concat":
            output_type = "str"
        else:
            output_type = value_type

        result: List[ColumnSchema] = []
        seen: set[str] = set()
        for name in index_cols:
            result.append(self._copy_column(by_name[name], node_id, is_calculated=False))
            seen.add(name)

        mapping = config.get("mapping")
        if isinstance(mapping, dict):
            output_names = mapping.values()
        else:
            output_names = []

        # Senza mapping i nomi dipendono dai valori runtime di pivot_col e
        # non sono determinabili staticamente: preserva lo schema di input.
        if not mapping:
            return self._copy_schema(input_schema, node_id)

        for raw_name in output_names:
            name = str(raw_name or "").strip()
            if not name or name in seen:
                continue
            result.append(self._column_schema_cls(
                name=name,
                type=output_type,
                source_node=node_id,
                is_calculated=True,
            ))
            seen.add(name)

        return result if result else self._copy_schema(input_schema, node_id)

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
            result.append(self._copy_column(col, node_id, is_calculated=False))
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
            result.append(self._copy_column(col, node_id, is_calculated=False))
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
