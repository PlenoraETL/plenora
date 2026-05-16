"""
String SQL function translators for ``sql_functions``.

The mixin deliberately avoids importing ``sql_functions`` so the main module
can keep owning the public API while delegating this cohesive function family.
"""

from typing import Any

from app.db.sql_types import SqlParamMap, SqlTranslationMap


def _sql_escape(value: str) -> str:
    """Escape single quotes for SQL string literals."""
    if value is None:
        return ""
    return str(value).replace("'", "''")


def _safe_number(value, kind: str = "float"):
    """Validate a numeric value before interpolating it into SQL."""
    if value is None:
        raise ValueError("_safe_number: valore None")
    try:
        if kind == "int":
            numeric = float(value)
            if numeric != int(numeric):
                raise ValueError(
                    f"_safe_number: valore '{value}' non e' intero"
                )
            return int(numeric)
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"_safe_number: valore '{value}' non numerico (kind={kind}): {e}"
        )


class SqlStringFunctionMixin:
    """Translator methods for string functions."""

    db_type: Any

    def _string_translation_for(
        self,
        translations: SqlTranslationMap,
        function_name: str,
    ) -> str:
        db_value = self.db_type.value
        if db_value in translations:
            return translations[db_value]
        raise ValueError(f"{function_name} non supportato per {db_value}")

    def _translate_upper(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Conversione in maiuscolo - identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"UPPER({col})"

    def _translate_lower(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Conversione in minuscolo - identica su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"LOWER({col})"

    def _translate_trim(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Rimozione spazi - MSSQL usa LTRIM(RTRIM(...)), gli altri TRIM(...)."""
        col = self._qualify_column(params["column"], table_alias)
        if self.db_type.value == "mssql":
            return f"LTRIM(RTRIM({col}))"
        return f"TRIM({col})"

    def _translate_concat(self, params: SqlParamMap, table_alias: str = None) -> str:
        """
        Concatenazione di piu colonne con separatore opzionale.

        Oracle e SQLite non supportano CONCAT_WS nativo, quindi usano
        l'operatore || con il separatore inserito tra i valori.
        """
        values = params["values"]
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",") if v.strip()]
        separator = _sql_escape(params.get("separator", ""))
        qualified_values = [self._qualify_column(v, table_alias) for v in values]

        if self.db_type.value in ("postgres", "mssql", "mysql"):
            if separator:
                return f"CONCAT_WS('{separator}', {', '.join(qualified_values)})"
            return f"CONCAT({', '.join(qualified_values)})"

        if self.db_type.value in ("oracle", "sqlite"):
            if separator:
                parts = [f"{v} || '{separator}'" for v in qualified_values[:-1]]
                parts.append(qualified_values[-1])
                return " || ".join(parts)
            return " || ".join(qualified_values)

        return f"CONCAT({', '.join(qualified_values)})"

    def _translate_substring(self, params: SqlParamMap, table_alias: str = None) -> str:
        """
        Estrazione sottostringa.

        Ogni database ha una sintassi leggermente diversa:
            - PostgreSQL: SUBSTRING(col FROM start FOR length)
            - MSSQL: SUBSTRING(col, start, length) - richiede length
            - MySQL: SUBSTRING(col, start[, length])
            - Oracle/SQLite: SUBSTR(col, start[, length])
        """
        col = self._qualify_column(params["column"], table_alias)
        start = _safe_number(params.get("start", 1), "int")
        length = params.get("length")
        if length is not None and length != "":
            length = _safe_number(length, "int")
        else:
            length = None

        if self.db_type.value == "postgres":
            if length:
                return f"SUBSTRING({col} FROM {start} FOR {length})"
            return f"SUBSTRING({col} FROM {start})"
        if self.db_type.value == "mssql":
            if length:
                return f"SUBSTRING({col}, {start}, {length})"
            return f"SUBSTRING({col}, {start}, LEN({col}))"
        if self.db_type.value == "mysql":
            if length:
                return f"SUBSTRING({col}, {start}, {length})"
            return f"SUBSTRING({col}, {start})"
        if self.db_type.value in ("oracle", "sqlite"):
            if length:
                return f"SUBSTR({col}, {start}, {length})"
            return f"SUBSTR({col}, {start})"

        return f"SUBSTRING({col}, {start}, {length or 'NULL'})"

    def _translate_replace(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Sostituzione testo - REPLACE() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        search = _sql_escape(params["search"])
        replace = _sql_escape(params.get("replace", ""))
        return f"REPLACE({col}, '{search}', '{replace}')"

    def _translate_left(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Primi N caratteri - Oracle/SQLite usano SUBSTR."""
        col = self._qualify_column(params["column"], table_alias)
        length = _safe_number(params["length"], "int")
        translations = {
            "postgres": f"LEFT({col}, {length})",
            "mssql": f"LEFT({col}, {length})",
            "mysql": f"LEFT({col}, {length})",
            "oracle": f"SUBSTR({col}, 1, {length})",
            "sqlite": f"SUBSTR({col}, 1, {length})",
        }
        return self._string_translation_for(translations, "LEFT")

    def _translate_right(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Ultimi N caratteri - Oracle/SQLite usano SUBSTR con indice negativo."""
        col = self._qualify_column(params["column"], table_alias)
        length = _safe_number(params["length"], "int")
        translations = {
            "postgres": f"RIGHT({col}, {length})",
            "mssql": f"RIGHT({col}, {length})",
            "mysql": f"RIGHT({col}, {length})",
            "oracle": f"SUBSTR({col}, -{length})",
            "sqlite": f"SUBSTR({col}, -{length})",
        }
        return self._string_translation_for(translations, "RIGHT")

    def _translate_position(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Trova posizione - sintassi diversa per ogni database."""
        col = self._qualify_column(params["column"], table_alias)
        search = _sql_escape(params["search"])
        translations = {
            "postgres": f"POSITION('{search}' IN {col})",
            "mssql": f"CHARINDEX('{search}', {col})",
            "mysql": f"LOCATE('{search}', {col})",
            "oracle": f"INSTR({col}, '{search}')",
            "sqlite": f"INSTR({col}, '{search}')",
        }
        return self._string_translation_for(translations, "POSITION")

    def _translate_ltrim(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Rimuovi spazi a sinistra - LTRIM() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"LTRIM({col})"

    def _translate_rtrim(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Rimuovi spazi a destra - RTRIM() identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"RTRIM({col})"
