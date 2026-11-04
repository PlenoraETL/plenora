"""Table-oriented transform schema handlers for schema inference."""

from __future__ import annotations

from app.core.manipola.transforms.aggregation import prepare_aggregation_dict, aggregation_aliases
from app.shared.execution.schema_column_plans import aggregation_output_type
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from app.shared.execution.schema_types import ColumnSchema


class TableSchemaHandlers:
    """Mixin for aggregate, set, order, and analytic table operations."""

    def _schema_aggregate(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """
        Aggregate: group_by columns + aggregated columns
        """
        if not input_schemas:
            return []

        input_schema = input_schemas[0]
        input_by_name = {col.name: col for col in input_schema}

        group_by = self._normalize_str_list(config.get("group_by"))

        aggregations = config.get("aggregations") or []
        group_by = [name for name in group_by if name in input_by_name]
        if not group_by:
            return self._copy_schema(input_schema, node_id)
        result = [self._copy_column(input_by_name[name], node_id) for name in group_by]
        functions = prepare_aggregation_dict(list(input_by_name), aggregations)
        multiple = any(isinstance(value, list) for value in functions.values())
        if not functions:
            result.append(self._column_schema_cls(name="count", type="int", source_node=node_id, is_calculated=True))
        for name, funcs in functions.items():
            for func in (funcs if isinstance(funcs, list) else [funcs]):
                label = func.__name__ if callable(func) else func
                output = f"{name}_{label}".strip("_") if multiple else name
                type_ = aggregation_output_type("concat" if callable(func) else func, input_by_name[name].type)
                result.append(self._column_schema_cls(name=output, type=type_, source_node=node_id, is_calculated=True))
        aliases = aggregation_aliases([col.name for col in result], group_by, aggregations)
        return [self._copy_column(col, node_id, name=aliases.get(col.name, col.name)) for col in result]

    def _schema_concat(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """
        Concat: unione colonne (outer) o intersezione (inner)
        """
        if not input_schemas:
            return []

        seen = {}
        for schema in input_schemas:
            for col in schema:
                if col.name not in seen:
                    seen[col.name] = self._copy_column(col, node_id)
        return list(seen.values())

    def _schema_distinct(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """
        Distinct: stesso schema dell'input (rimuove solo righe duplicate)
        """
        if not input_schemas:
            return []

        return self._copy_schema(input_schemas[0], node_id)

    def _schema_reorder_columns(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """reorder_columns: stesso column-set, ordine ricalcolato."""
        if not input_schemas:
            return []
        input_schema = input_schemas[0]
        by_name = {c.name: c for c in input_schema}

        cols_cfg = config.get("columns") or []
        sort_alpha = bool(config.get("sort_alphabetical", False))

        if cols_cfg:
            valid = [name for name in cols_cfg if name in by_name]
            remaining = [c.name for c in input_schema if c.name not in valid]
            ordered = valid + remaining
        elif sort_alpha:
            ordered = sorted(by_name.keys())
        else:
            ordered = [c.name for c in input_schema]

        return [self._copy_column(by_name[name], node_id) for name in ordered if name in by_name]

    def _schema_statistics(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """statistics: input + 1 colonna per ogni stat valida."""
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        existing = {c.name for c in result}
        if column not in existing:
            return result

        raw_stats = config.get("stats")
        if raw_stats is None:
            stats = ["count", "min", "max", "mean", "median", "std"]
        elif isinstance(raw_stats, str):
            stats = [s.strip() for s in raw_stats.split(",") if s.strip()]
        elif isinstance(raw_stats, list):
            stats = [str(s).strip() for s in raw_stats if str(s).strip()]
        else:
            stats = ["count", "min", "max", "mean", "median", "std"]

        valid_stats = {
            "count", "min", "max", "sum", "mean", "median",
            "std", "var", "q25", "q75",
        }
        prefix = config.get("output_prefix") or f"{column}_"
        source_type = next(col.type for col in result if col.name == column)
        derived = {}
        for stat in stats:
            if stat not in valid_stats:
                continue
            col_name = f"{prefix}{stat}"
            stat_type = "int" if stat == "count" else source_type if stat in {"min", "max", "sum"} else "real"
            derived[col_name] = self._column_schema_cls(
                name=col_name,
                type=stat_type,
                source_node=node_id,
                is_calculated=True,
            )
        if config.get("group_by") in existing:
            collisions = existing & derived.keys()
            result = [self._copy_column(col, node_id, name=f"{col.name}_x" if col.name in collisions else col.name) for col in result]
            result += [self._copy_column(col, node_id, name=f"{col.name}_y" if col.name in collisions else col.name) for col in derived.values()]
        else:
            for col in derived.values():
                self._assign_column(result, col)
        return result

    def _schema_window_function(
        self,
        input_schemas: List[List[ColumnSchema]],
        config: dict,
        node_id: str,
    ) -> List[ColumnSchema]:
        """window_function: input + 1 colonna `output_column`."""
        if not input_schemas:
            return []
        result = self._copy_schema(input_schemas[0], node_id)
        column = config.get("column") or ""
        if not column:
            return result
        by_name = {c.name: c for c in result}
        if column not in by_name:
            return result

        function = config.get("function") or "rank"
        valid_functions = {
            "rank", "dense_rank", "cumsum", "cumcount",
            "lag", "lead", "pct_change", "running_mean",
        }
        if function not in valid_functions:
            return result

        output_col = config.get("output_column") or f"{column}_{function}"

        if function in ("rank", "dense_rank", "pct_change", "running_mean"):
            out_type = "real"
        elif function == "cumcount":
            out_type = "int"
        else:
            out_type = by_name[column].type

        self._assign_column(result, self._column_schema_cls(
            name=output_col,
            type=out_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result
