"""SELECT, aggregation, ordering and final SQL rendering for QueryGenerator."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from app.db.sql_functions import translate_calculated_column
from app.db.sql_translator import sql_translator
from app.shared.models import Node


class QueryProjectionRenderer:
    """Builds projection-oriented clauses while reusing QueryGenerator helpers."""

    def __init__(self, generator):
        self.db_type = generator.db_type
        self._get_node_table_name: Callable[[Node], str] = generator._get_node_table_name
        self._safe_identifier: Callable[[str, str], str] = generator._safe_identifier
        self._prossimo_nome_param: Callable[[], str] = generator._prossimo_nome_param

    def generate_select(
        self,
        table_nodes: List[Node],
        result_node: Node,
        group_by_config: Dict[str, Any] = None,
    ) -> str:
        """
        Genera la clausola SELECT con colonne visibili, alias e colonne calcolate.

        Se GROUP BY e' attivo, delega a generate_select_with_groupby.
        """
        if group_by_config and group_by_config.get("enabled"):
            return self.generate_select_with_groupby(table_nodes, group_by_config)

        columns = []

        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)

            if node.type == "datasource_node":
                visible_cols = [c for c in (node.columns or []) if getattr(c, "visible", True)]
                if not visible_cols:
                    columns.append(f"{table_alias}.*")
                    continue

            for col in node.columns:
                if not col.visible:
                    continue

                col_name = col.name
                col_alias = col.alias
                col_type = getattr(col, "type", "str") or "str"

                col_name_sql = self._safe_identifier(col_name, "Nome colonna")

                if col_type.startswith("geometry"):
                    col_expr = self.wrap_geometry_as_wkt(f"{table_alias}.{col_name_sql}")
                    output_alias = col_alias if col_alias else col_name
                    output_alias_sql = self._safe_identifier(output_alias, "Alias colonna")
                    columns.append(f"{col_expr} AS {output_alias_sql}")
                elif col_alias:
                    col_alias_sql = self._safe_identifier(col_alias, "Alias colonna")
                    columns.append(f"{table_alias}.{col_name_sql} AS {col_alias_sql}")
                else:
                    columns.append(f"{table_alias}.{col_name_sql}")

            calculated_columns = getattr(node, "calculated_columns", None) or []
            for calc_col in calculated_columns:
                try:
                    if calc_col.get("is_manual") and calc_col.get("sql"):
                        sql_expr = calc_col["sql"]
                    elif calc_col.get("function_name") and calc_col.get("params"):
                        sql_expr = translate_calculated_column(
                            db_type=self.db_type,
                            column_name=calc_col["name"],
                            expression={
                                "function": calc_col["function_name"],
                                "params": calc_col["params"],
                            },
                            table_alias=table_alias,
                        )
                    elif calc_col.get("expression") and isinstance(calc_col["expression"], dict):
                        sql_expr = translate_calculated_column(
                            db_type=self.db_type,
                            column_name=calc_col["name"],
                            expression=calc_col["expression"],
                            table_alias=table_alias,
                        )
                    else:
                        continue

                    columns.append(sql_expr)
                except Exception as e:
                    raise ValueError(
                        f"Errore colonna calcolata '{calc_col.get('name', '?')}': {e}"
                    )

        if not columns:
            raise ValueError("Nessuna colonna selezionata")

        return ", ".join(columns)

    def generate_select_with_groupby(
        self,
        table_nodes: List[Node],
        group_by_config: Dict[str, Any],
    ) -> str:
        """Genera SELECT per query con GROUP BY."""
        columns = []
        group_columns = group_by_config.get("columns", [])
        aggregations = group_by_config.get("aggregations", [])
        table_map = self._visible_column_table_map(table_nodes)

        def _resolve_col(col_name, context):
            resolved = table_map.get(col_name)
            if resolved:
                col_real = col_name.split("__", 1)[1] if "__" in col_name else col_name
                col_sql = self._safe_identifier(col_real, context)
                return f"{resolved}.{col_sql}", col_sql
            col_sql = self._safe_identifier(col_name, context)
            return col_sql, col_sql

        for col_name in group_columns:
            col_ref, _ = _resolve_col(col_name, "Nome colonna GROUP BY")
            columns.append(col_ref)

        valid_agg_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX", "FIRST", "LAST"}

        for agg in aggregations:
            if hasattr(agg, "column"):
                col = agg.column or "*"
                func = (agg.function or "COUNT").upper()
                alias = agg.alias or f"{func.lower()}_{col}"
            else:
                col = agg.get("column", "*")
                func = agg.get("function", "COUNT").upper()
                alias = agg.get("alias", f"{func.lower()}_{col}")

            if func not in valid_agg_functions:
                raise ValueError(f"Funzione aggregazione non valida: {func}")
            alias_sql = self._safe_identifier(alias, "Alias aggregazione")

            if col == "*":
                if func != "COUNT":
                    raise ValueError(f"Solo COUNT puo usare *, non {func}")
                columns.append(f"COUNT(*) AS {alias_sql}")
            else:
                col_ref, _ = _resolve_col(col, "Nome colonna aggregazione")
                columns.append(f"{func}({col_ref}) AS {alias_sql}")

        if not columns:
            raise ValueError("Nessuna colonna per GROUP BY")

        return ", ".join(columns)

    def generate_group_by(
        self,
        group_by_config: Dict[str, Any],
        table_nodes: List[Node] = None,
    ) -> str:
        """Genera clausola GROUP BY con colonne qualificate."""
        if not group_by_config or not group_by_config.get("enabled"):
            return ""

        columns = group_by_config.get("columns", [])
        if not columns:
            return ""

        table_map = self._visible_column_table_map(table_nodes or [])

        qualified = []
        for col in columns:
            resolved = table_map.get(col)
            if resolved:
                col_real = col.split("__", 1)[1] if "__" in col else col
                col_sql = self._safe_identifier(col_real, "Nome colonna GROUP BY")
                qualified.append(f"{resolved}.{col_sql}")
            else:
                col_sql = self._safe_identifier(col, "Nome colonna GROUP BY")
                qualified.append(col_sql)

        return ", ".join(qualified)

    def generate_order_by(
        self,
        order_by_config: Dict[str, Any],
        table_nodes: List[Node],
        group_by_config: Dict[str, Any],
    ) -> str:
        """Genera la clausola ORDER BY."""
        if not order_by_config.get("enabled") or not order_by_config.get("columns"):
            return ""

        columns = order_by_config["columns"]
        table_map = self._visible_column_table_map(table_nodes or [])
        aggregate_functions = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

        parts = []
        for item in columns:
            col = item.get("column", "") if isinstance(item, dict) else str(item)
            direction = item.get("direction", "ASC") if isinstance(item, dict) else "ASC"

            direction = direction.upper()
            if direction not in ("ASC", "DESC"):
                direction = "ASC"

            col_upper = col.upper().strip()
            is_aggregate = any(col_upper.startswith(f"{fn}(") for fn in aggregate_functions)

            if is_aggregate:
                if not re.match(r"^[A-Z_]+\([a-zA-Z0-9_.*\s,]+\)$", col.strip()):
                    raise ValueError(f"Espressione aggregata ORDER BY non valida: {col}")
                parts.append(f"{col} {direction}")
            else:
                col_real = col.split("__", 1)[1] if "__" in col else col
                resolved = table_map.get(col)
                if resolved:
                    col_sql = self._safe_identifier(col_real, "Nome colonna ORDER BY")
                    parts.append(f"{resolved}.{col_sql} {direction}")
                else:
                    col_sql = self._safe_identifier(col_real, "Nome colonna ORDER BY")
                    parts.append(f"{col_sql} {direction}")

        return ", ".join(parts)

    def generate_having(
        self,
        having_config: Dict[str, Any],
        group_by_config: Dict[str, Any],
        params: Dict[str, Any],
    ) -> str:
        """Genera la clausola HAVING."""
        if not having_config.get("enabled") or not having_config.get("conditions"):
            return ""
        if not group_by_config.get("enabled"):
            return ""

        valid_operators = {"=", "!=", ">", "<", ">=", "<="}
        parts = []

        for cond in having_config["conditions"]:
            function = cond.get("function", "COUNT").upper()
            column = cond.get("column", "*")
            operator = cond.get("operator", ">")
            value = cond.get("value", "0")

            if function not in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
                continue
            if operator not in valid_operators:
                continue

            if column == "*":
                expression = f"{function}(*)"
            else:
                col_sql = self._safe_identifier(column, "Nome colonna HAVING")
                expression = f"{function}({col_sql})"

            param_name = self._prossimo_nome_param()
            try:
                typed_value = float(value) if "." in str(value) else int(value)
            except (ValueError, TypeError):
                typed_value = value
            params[param_name] = typed_value

            parts.append(f"{expression} {operator} :{param_name}")

        return " AND ".join(parts)

    def wrap_geometry_as_wkt(self, col_ref: str) -> str:
        """Wrappa una colonna geometrica con la funzione ST_AsText del dialetto."""
        db = self.db_type.lower() if self.db_type else "postgres"

        try:
            return sql_translator.translate("ST_AsText", db, geom=col_ref)
        except (ValueError, KeyError):
            return f"ST_AsText({col_ref})"

    def assemble_query(self, sql_parts: Dict) -> str:
        """Assembla le parti SQL in una query completa."""
        query = "SELECT "

        if sql_parts.get("distinct") and not sql_parts.get("group_by"):
            query += "DISTINCT "

        query += sql_parts["select"]
        query += f"\nFROM {sql_parts['from']}"

        if sql_parts["where"]:
            query += f"\nWHERE {sql_parts['where']}"

        if sql_parts.get("group_by"):
            query += f"\nGROUP BY {sql_parts['group_by']}"

        if sql_parts.get("having"):
            query += f"\nHAVING {sql_parts['having']}"

        if sql_parts.get("order_by"):
            query += f"\nORDER BY {sql_parts['order_by']}"

        limit = sql_parts.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"LIMIT deve essere un intero positivo, ricevuto: {limit}")

            db = self.db_type.lower() if self.db_type else "postgres"

            if db == "oracle":
                query += f"\nFETCH FIRST {limit} ROWS ONLY"
            elif db in ("mssql", "sqlserver"):
                query = query.replace("SELECT ", f"SELECT TOP {limit} ", 1)
            else:
                query += f"\nLIMIT {limit}"

        return query

    def _visible_column_table_map(self, table_nodes: List[Node]) -> Dict[str, str]:
        table_map = {}
        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)
            for col in (node.columns or []):
                col_name = col.name if hasattr(col, "name") else col.get("name", "")
                col_alias = col.alias if hasattr(col, "alias") else col.get("alias", "")
                col_visible = col.visible if hasattr(col, "visible") else col.get("visible", True)
                if col_name and col_visible:
                    table_map[col_name] = table_alias
                    if col_alias:
                        table_map[col_alias] = table_alias
        return table_map
