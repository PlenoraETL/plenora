"""SELECT, aggregation, ordering and final SQL rendering for QueryGenerator."""

from __future__ import annotations

import re
from typing import Any, Callable, List

from app.db.sql_functions import translate_calculated_column
from app.db.sql_translator import sql_translator
from app.core.estrai.query_aliases import effective_alias
from app.core.estrai.types import JsonObject, StringMap
from app.core.projects.canvas_models import Node
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _declared_column_type(column: object) -> str | None:
    """Il default del modello non dimostra il tipo della sorgente reale."""
    fields = getattr(column, "model_fields_set", None)
    if fields is not None and "type" not in fields:
        return None
    return getattr(column, "type", None)


def _calculated_output_name(expression: str, provider: str = "") -> str | None:
    """Riconosce un solo elemento SQL con alias, senza indovinare espressioni libere."""
    depth = 0
    quote = None
    index = 0
    while index < len(expression):
        char = expression[index]
        if quote:
            if char == quote:
                if index + 1 < len(expression) and expression[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            return None
        index += 1
    match = re.search(r'\s+AS\s+([A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")+"|\[[^\]]+\]|`[^`]+`)\s*$', expression, re.I)
    if not match:
        return None
    alias = match.group(1)
    if alias[0] in '"[`':
        return alias[1:-1].replace('""', '"')
    return alias.lower() if provider in {"postgres", "postgresql"} else alias


class QueryProjectionRenderer:
    """Builds projection-oriented clauses while reusing QueryGenerator helpers."""

    def __init__(self, generator):
        self.db_type = generator.db_type
        self._get_node_table_name: Callable[[Node], str] = generator._get_node_table_name
        self._safe_identifier: Callable[[str, str], str] = generator._safe_identifier
        self._prossimo_nome_param: Callable[[], str] = generator._prossimo_nome_param

    def qualified_column(self, col: Any, table_nodes: List[Node]):
        """``tabella.colonna`` -> (riferimento SQL, nome reale, nodo) o None.

        Fase 5, T6 (R3.16): GROUP BY, ORDER BY, aggregazioni e HAVING possono
        indicare la colonna di UNA tabella anche quando lo stesso nome e' in
        piu' tabelle (``comuni.id`` e ``province.id``). Solo questa forma: un
        nome semplice segue la regola di sempre (``_visible_column_table_map``),
        quindi i progetti salvati producono lo stesso SQL.
        """
        if not isinstance(col, str) or "." not in col:
            return None
        for node in table_nodes or []:
            table_alias = effective_alias(node)
            if not table_alias or not col.startswith(f"{table_alias}."):
                continue
            name = col[len(table_alias) + 1:]
            for column in (node.columns or []):
                col_name = column.name if hasattr(column, "name") else column.get("name", "")
                visible = column.visible if hasattr(column, "visible") else column.get("visible", True)
                if col_name and col_name == name and visible:
                    col_sql = self._safe_identifier(name, "Nome colonna")
                    return f"{table_alias}.{col_sql}", name, node
        return None

    def _output_name(self, name: str) -> str:
        """PostgreSQL restituisce in minuscolo gli identificatori non quotati."""
        quoted = self._safe_identifier(name).startswith('"')
        return name.lower() if self.db_type in {"postgres", "postgresql"} and not quoted else name

    def generate_select(
        self,
        table_nodes: List[Node],
        result_node: Node,
        group_by_config: JsonObject = None,
        *,
        column_metadata: list | None = None,
    ) -> str:
        """
        Genera la clausola SELECT con colonne visibili, alias e colonne calcolate.

        Se GROUP BY e' attivo, delega a generate_select_with_groupby.
        """
        if group_by_config and group_by_config.get("enabled"):
            return self.generate_select_with_groupby(table_nodes, group_by_config, column_metadata=column_metadata)

        columns = []

        for node in table_nodes:
            table_alias = effective_alias(node)

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
                if column_metadata is not None:
                    column_metadata.append({"name": self._output_name(col_alias or col_name), "type": _declared_column_type(col),
                                            "id": getattr(col, "id", None), "source_name": col_name,
                                            "source_node": node.id})

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
                    elif isinstance(calc_col.get("expression"), str) and calc_col["expression"].strip():
                        # Manual SQL come stringa: il FE (ColumnBuilderModal in
                        # modalita' manuale) serializza la colonna con
                        # `function_name=null, params=null, expression="<sql>"`
                        # senza il flag `is_manual`. Senza questo branch la
                        # colonna veniva droppata silenziosamente al SELECT
                        # build (calculated_columns "manual" persi nell'output).
                        # Stesso modello di trust di `is_manual+sql`: l'utente
                        # ha gia' accettato di scrivere SQL grezzo.
                        col_alias_sql = self._safe_identifier(
                            calc_col["name"], "Nome colonna calcolata"
                        )
                        sql_expr = f"({calc_col['expression']}) AS {col_alias_sql}"
                    else:
                        logger.warning(
                            "calculated_column '%s' senza expression/function valida, skip",
                            calc_col.get("name", "?"),
                        )
                        continue

                    columns.append(sql_expr)
                    if column_metadata is not None:
                        # Il SQL manuale può dichiarare un alias diverso dal nome nel canvas.
                        name = _calculated_output_name(sql_expr, self.db_type)
                        column_metadata.append({"name": name, "type": calc_col.get("output_type"),
                                                "source_name": calc_col.get("name"),
                                                "source_node": node.id, "is_calculated": True})
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
        group_by_config: JsonObject,
        *,
        column_metadata: list | None = None,
    ) -> str:
        """Genera SELECT per query con GROUP BY."""
        columns = []
        group_columns = group_by_config.get("columns", [])
        aggregations = group_by_config.get("aggregations", [])
        table_map = self._visible_column_table_map(table_nodes)

        def _resolve_col(col_name, context):
            qualified = self.qualified_column(col_name, table_nodes)
            if qualified:
                return qualified[0], qualified[1]
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
            qualified = self.qualified_column(col_name, table_nodes)
            if column_metadata is not None and qualified:
                _, real_name, owner = qualified
                source = next((col for col in owner.columns if col.name == real_name), None)
                column_metadata.append({"name": self._output_name(real_name), "type": _declared_column_type(source),
                                        "source_name": real_name, "id": getattr(source, "id", None),
                                        "source_node": owner.id})
            elif column_metadata is not None:
                real_name = col_name.split("__", 1)[1] if table_map.get(col_name) and "__" in col_name else col_name
                source = next((col for node in reversed(table_nodes)
                               if effective_alias(node) == table_map.get(col_name)
                               for col in node.columns if col.name == real_name), None)
                column_metadata.append({"name": self._output_name(real_name), "type": _declared_column_type(source),
                                        "source_name": real_name, "id": getattr(source, "id", None),
                                        "source_node": next((node.id for node in table_nodes if effective_alias(node) == table_map.get(col_name)), None)})

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
            if column_metadata is not None:
                column_metadata.append({"name": self._output_name(alias), "type": "int" if func == "COUNT" else None,
                                        "source_name": f"{func}({col})", "is_calculated": True})

        if not columns:
            raise ValueError("Nessuna colonna per GROUP BY")

        return ", ".join(columns)

    def generate_group_by(
        self,
        group_by_config: JsonObject,
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
            explicit = self.qualified_column(col, table_nodes or [])
            if explicit:
                qualified.append(explicit[0])
                continue
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
        order_by_config: JsonObject,
        table_nodes: List[Node],
        group_by_config: JsonObject,
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
            elif self.qualified_column(col, table_nodes or []):
                parts.append(f"{self.qualified_column(col, table_nodes or [])[0]} {direction}")
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
        having_config: JsonObject,
        group_by_config: JsonObject,
        params: JsonObject,
        *,
        table_nodes: List[Node] | None = None,
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

            qualified = self.qualified_column(column, table_nodes or [])
            if column == "*":
                expression = f"{function}(*)"
            elif qualified:
                expression = f"{function}({qualified[0]})"
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

    def assemble_query(self, sql_parts: JsonObject) -> str:
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

    def _visible_column_table_map(self, table_nodes: List[Node]) -> StringMap:
        table_map = {}
        for node in table_nodes:
            table_alias = effective_alias(node)
            for col in (node.columns or []):
                col_name = col.name if hasattr(col, "name") else col.get("name", "")
                col_alias = col.alias if hasattr(col, "alias") else col.get("alias", "")
                col_visible = col.visible if hasattr(col, "visible") else col.get("visible", True)
                if col_name and col_visible:
                    table_map[col_name] = table_alias
                    if col_alias:
                        table_map[col_alias] = table_alias
        return table_map
