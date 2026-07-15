"""Table-oriented transform schema handlers for schema inference."""

from __future__ import annotations

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

        group_by = config.get("group_by", [])
        aggs = config.get("aggs", {})
        aggregations = config.get("aggregations")

        result = []

        for col_name in group_by:
            if col_name in input_by_name:
                col = input_by_name[col_name]
                result.append(self._copy_column(col, node_id, is_calculated=False))

        agg_items = []
        if isinstance(aggregations, list):
            for agg in aggregations:
                if not isinstance(agg, dict):
                    continue
                col_name = agg.get("column")
                agg_func = agg.get("function", "count")
                raw_alias = agg.get("alias")
                alias = raw_alias.strip() if isinstance(raw_alias, str) else raw_alias
                if col_name:
                    agg_items.append((col_name, agg_func, alias))
        elif isinstance(aggs, dict):
            agg_items = [(col_name, agg_func, None) for col_name, agg_func in aggs.items()]

        for col_name, agg_func, alias in agg_items:
            orig_col = input_by_name.get(col_name)

            if agg_func in ("count", "nunique", "size"):
                out_type = "int"
            elif agg_func in ("sum", "mean", "std", "var", "min", "max"):
                out_type = orig_col.type if orig_col else "real"
                if agg_func in ("mean", "std", "var"):
                    out_type = "real"
            elif agg_func == "first" or agg_func == "last":
                out_type = orig_col.type if orig_col else "str"
            else:
                out_type = "str"

            output_name = alias or col_name
            result.append(self._column_schema_cls(
                name=output_name,
                type=out_type,
                source_node=node_id,
                is_calculated=True,
            ))

        return result

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

        join_mode = config.get("join", "outer")

        if join_mode == "inner":
            common_cols = None
            for schema in input_schemas:
                col_names = {col.name for col in schema}
                if common_cols is None:
                    common_cols = col_names
                else:
                    common_cols &= col_names

            if not common_cols:
                return []

            result = []
            for col in input_schemas[0]:
                if col.name in common_cols:
                    result.append(self._copy_column(col, node_id))
            return result

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

        for stat in stats:
            if stat not in valid_stats:
                continue
            col_name = f"{prefix}{stat}"
            if col_name in existing:
                continue
            stat_type = "int" if stat == "count" else "real"
            result.append(self._column_schema_cls(
                name=col_name,
                type=stat_type,
                source_node=node_id,
                is_calculated=True,
            ))
            existing.add(col_name)
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
        if output_col in by_name:
            return result

        if function in ("rank", "dense_rank", "pct_change", "running_mean"):
            out_type = "real"
        elif function == "cumcount":
            out_type = "int"
        else:
            out_type = by_name[column].type

        result.append(self._column_schema_cls(
            name=output_col,
            type=out_type,
            source_node=node_id,
            is_calculated=True,
        ))
        return result
