"""WHERE-clause helpers for EstrAi SQL generation."""

from __future__ import annotations

import re
from typing import Any, Callable, List

from app.core.estrai.types import JsonObject


class WhereClauseBuilder:
    """Builds parametrized WHERE filters for QueryGenerator."""

    _SQL_FUNCTION_RE = None

    def __init__(
        self,
        db_type: str,
        next_param_name: Callable[[], str],
        is_valid_identifier: Callable[[str], bool],
        get_node_table_name: Callable[[Any], str],
    ) -> None:
        self.db_type = db_type
        self._next_param_name = next_param_name
        self._is_valid_identifier = is_valid_identifier
        self._get_node_table_name = get_node_table_name

    def generate_where(self, table_nodes: List[Any], params: JsonObject) -> str:
        conditions = []

        for node in table_nodes:
            table_alias = node.alias or self._get_node_table_name(node)
            filters = getattr(node, "filters", []) or []

            column_type_map = {}
            for col in node.columns:
                col_name = col.name if hasattr(col, "name") else col.get("name")
                col_type = (
                    (col.type if hasattr(col, "type") else None)
                    or (col.get("type") if isinstance(col, dict) else None)
                    or "str"
                )
                if col_name:
                    column_type_map[col_name] = col_type

            for filter_item in filters:
                column = filter_item.column
                operator = filter_item.operator
                value = filter_item.value

                if not column or not operator:
                    continue
                if not self._is_valid_identifier(column):
                    raise ValueError(f"Nome colonna filtro non valido: {column}")

                column_type = column_type_map.get(column, "str")

                condition = self.create_condition(
                    table_alias, column, operator, value, params, column_type
                )
                if condition:
                    conditions.append(condition)

        if not conditions:
            return ""

        return " AND ".join(conditions)

    def create_condition(
        self,
        table_alias: str,
        column: str,
        operator: str,
        value: Any,
        params: JsonObject,
        column_type: str = "str",
    ) -> str:
        col_ref = f"{table_alias}.{column}"

        db = self.db_type.lower() if self.db_type else "postgres"
        col_type_upper = (column_type or "").upper()
        is_oracle_lob = (
            db == "oracle"
            and col_type_upper in ("CLOB", "NCLOB", "LONG", "TEXT", "TESTO", "STRING", "STR")
        )
        if is_oracle_lob:
            col_ref = f"TO_CHAR({table_alias}.{column})"

        valid_operators = {
            "=", "!=", ">", "<", ">=", "<=",
            "LIKE", "NOT LIKE",
            "IN", "NOT IN",
            "IS NULL", "IS NOT NULL",
            "BETWEEN",
        }
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}")

        if operator == "IS NULL":
            return f"{col_ref} IS NULL"
        if operator == "IS NOT NULL":
            return f"{col_ref} IS NOT NULL"

        if operator == "IN":
            return self.create_condition_in(col_ref, value, params)
        if operator == "NOT IN":
            inner = self.create_condition_in(col_ref, value, params)
            # `create_condition_in` ritorna gia' `col IN (:p1, :p2, ...)`.
            # Per NOT IN sostituiamo l'operatore preservando i param.
            return inner.replace(" IN ", " NOT IN ", 1)

        if operator == "LIKE":
            param_name = self._next_param_name()
            params[param_name] = f"%{value}%"
            return f"{col_ref} LIKE :{param_name}"
        if operator == "NOT LIKE":
            param_name = self._next_param_name()
            params[param_name] = f"%{value}%"
            return f"{col_ref} NOT LIKE :{param_name}"

        if operator == "BETWEEN":
            # Il FE manda BETWEEN come stringa "lo AND hi" (vedi
            # FilterModal placeholder "valore1 AND valore2").
            # Supportiamo anche un value list/tuple per call programmatica.
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                lo_raw, hi_raw = value[0], value[1]
            elif isinstance(value, str):
                parts = re.split(r"\s+and\s+", value.strip(), maxsplit=1,
                                 flags=re.IGNORECASE)
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    raise ValueError(
                        "BETWEEN richiede due valori separati da 'AND' "
                        "(es. '10 AND 20')"
                    )
                lo_raw, hi_raw = parts[0].strip(), parts[1].strip()
            else:
                raise ValueError(
                    "BETWEEN richiede un valore stringa 'lo AND hi' "
                    "o una lista di 2 elementi"
                )

            col_type_norm = (column_type or "str").lower().split(":")[0]

            def _coerce(v):
                if col_type_norm in ("int", "integer", "bigint", "smallint"):
                    try: return int(v)
                    except (ValueError, TypeError): return v
                if col_type_norm in ("real", "float", "double", "numeric", "decimal"):
                    try: return float(v)
                    except (ValueError, TypeError): return v
                return v

            p_lo = self._next_param_name()
            p_hi = self._next_param_name()
            params[p_lo] = _coerce(lo_raw)
            params[p_hi] = _coerce(hi_raw)
            return f"{col_ref} BETWEEN :{p_lo} AND :{p_hi}"

        if isinstance(value, str) and self.is_sql_function(value):
            translated = self.translate_date_function(value)
            return f"{col_ref} {operator} {translated}"

        param_name = self._next_param_name()
        col_type_norm = (column_type or "str").lower().split(":")[0]
        typed_value = value
        if col_type_norm in ("int", "integer", "bigint", "smallint"):
            try:
                typed_value = int(value)
            except (ValueError, TypeError):
                typed_value = value
        elif col_type_norm in ("real", "float", "double", "numeric", "decimal"):
            try:
                typed_value = float(value)
            except (ValueError, TypeError):
                typed_value = value
        params[param_name] = typed_value
        return f"{col_ref} {operator} :{param_name}"

    @classmethod
    def is_sql_function(cls, value: str) -> bool:
        if cls._SQL_FUNCTION_RE is None:
            cls._SQL_FUNCTION_RE = re.compile(
                r"^("
                r"GETDATE\s*\(\s*\)"
                r"|GETUTCDATE\s*\(\s*\)"
                r"|SYSDATETIME\s*\(\s*\)"
                r"|DATEADD\s*\("
                r"|DATEDIFF\s*\("
                r"|DATEPART\s*\("
                r"|DATEFROMPARTS\s*\("
                r"|EOMONTH\s*\("
                r"|NOW\s*\(\s*\)"
                r"|CURDATE\s*\(\s*\)"
                r"|CURRENT_DATE\b"
                r"|CURRENT_TIMESTAMP\b"
                r"|CURRENT_TIME\b"
                r"|SYSDATE\b"
                r"|SYSTIMESTAMP\b"
                r"|ADD_MONTHS\s*\("
                r"|TRUNC\s*\("
                r"|date_trunc\s*\("
                r"|INTERVAL\b"
                r"|EXTRACT\s*\("
                r"|DATE_ADD\s*\("
                r"|DATE_SUB\s*\("
                r"|DATE_FORMAT\s*\("
                r")",
                re.IGNORECASE,
            )
        stripped = value.strip()
        if not cls._SQL_FUNCTION_RE.match(stripped):
            return False
        unsafe_sql_pattern = re.compile(
            r"(;|--|\bUNION\b|\bSELECT\b|\bDROP\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b"
            r"|\bEXEC\b|\bEXECUTE\b|\bALTER\b|\bCREATE\b)",
            re.IGNORECASE,
        )
        if unsafe_sql_pattern.search(stripped):
            return False
        return True

    def translate_date_function(self, value: str) -> str:
        v = value.strip()
        db = (self.db_type or "postgres").lower()

        dialects = {
            "mssql": {
                "now": "GETDATE()",
                "today": "CAST(GETDATE() AS DATE)",
                "add_months": lambda n: f"DATEADD(month, {n}, GETDATE())",
                "add_days": lambda n: f"DATEADD(day, {n}, GETDATE())",
            },
            "postgres": {
                "now": "NOW()",
                "today": "CURRENT_DATE",
                "add_months": lambda n: f"NOW() + INTERVAL '{n} months'",
                "add_days": lambda n: f"NOW() + INTERVAL '{n} days'",
            },
            "mysql": {
                "now": "NOW()",
                "today": "CURDATE()",
                "add_months": lambda n: f"DATE_ADD(NOW(), INTERVAL {n} MONTH)",
                "add_days": lambda n: f"DATE_ADD(NOW(), INTERVAL {n} DAY)",
            },
            "oracle": {
                "now": "SYSDATE",
                "today": "TRUNC(SYSDATE)",
                "add_months": lambda n: f"ADD_MONTHS(SYSDATE, {n})",
                "add_days": lambda n: f"SYSDATE + {n}",
            },
            "sqlite": {
                "now": "datetime('now')",
                "today": "date('now')",
                "add_months": lambda n: f"datetime('now', '+{n} months')",
                "add_days": lambda n: f"datetime('now', '+{n} days')",
            },
        }
        dialect = dialects.get(db, dialects["postgres"])

        m = re.match(r"DATEADD\s*\(\s*month\s*,\s*(\d+)\s*,", v, re.IGNORECASE)
        if m:
            return dialect["add_months"](m.group(1))

        m = re.match(r"DATEADD\s*\(\s*day\s*,\s*(\d+)\s*,", v, re.IGNORECASE)
        if m:
            return dialect["add_days"](m.group(1))

        m = re.match(r"ADD_MONTHS\s*\(\s*\w+\s*,\s*(\d+)\s*\)", v, re.IGNORECASE)
        if m:
            return dialect["add_months"](m.group(1))

        m = re.match(r"DATE_ADD\s*\(.*?INTERVAL\s+(\d+)\s+MONTH", v, re.IGNORECASE)
        if m:
            return dialect["add_months"](m.group(1))

        m = re.match(r"NOW\s*\(\s*\)\s*\+\s*INTERVAL\s+'(\d+)\s+months?'", v, re.IGNORECASE)
        if m:
            return dialect["add_months"](m.group(1))

        if re.match(r"CURRENT_DATE\x08|CURDATE\s*\(\s*\)|TRUNC\s*\(\s*SYSDATE", v, re.IGNORECASE):
            return dialect["today"]

        if re.match(
            r"GETDATE\s*\(\s*\)|GETUTCDATE\s*\(\s*\)|SYSDATETIME\s*\(\s*\)|NOW\s*\(\s*\)|"
            r"SYSDATE\x08|SYSTIMESTAMP\x08",
            v,
            re.IGNORECASE,
        ):
            return dialect["now"]

        raise ValueError(
            f"Funzione SQL '{v[:50]}' riconosciuta come funzione ma non traducibile "
            f"per il dialetto '{db}'. Usare una funzione supportata."
        )

    def create_condition_in(self, col_ref: str, value: Any, params: JsonObject) -> str:
        if isinstance(value, str) and "," in value:
            value = [v.strip() for v in value.split(",") if v.strip()]

        if isinstance(value, list):
            if not value:
                return "1=0"
            placeholders = []
            for v in value:
                param_name = self._next_param_name()
                try:
                    params[param_name] = int(v) if isinstance(v, str) and v.isdigit() else v
                except (ValueError, TypeError):
                    params[param_name] = v
                placeholders.append(f":{param_name}")
            return f"{col_ref} IN ({', '.join(placeholders)})"

        param_name = self._next_param_name()
        try:
            params[param_name] = int(value) if isinstance(value, str) and value.isdigit() else value
        except (ValueError, TypeError):
            params[param_name] = value
        return f"{col_ref} IN (:{param_name})"
