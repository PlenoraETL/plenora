"""
Numeric SQL function translators for ``sql_functions``.

The mixin keeps numeric dialect logic outside the main registry/API module
while depending only on translator methods supplied by ``SqlFunctionTranslator``.
"""

from typing import Any, Dict


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


class SqlNumericFunctionMixin:
    """Translator methods for numeric functions."""

    db_type: Any

    def _numeric_translation_for(
        self,
        translations: Dict[str, str],
        function_name: str,
    ) -> str:
        db_value = self.db_type.value
        if db_value in translations:
            return translations[db_value]
        raise ValueError(f"{function_name} non supportato per {db_value}")

    def _translate_round(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento - al numero di decimali specificato."""
        col = self._qualify_column(params["column"], table_alias)
        decimals = params.get("decimals")

        if decimals is None or decimals == "":
            decimals = 0
        else:
            try:
                decimals = int(decimals)
            except (ValueError, TypeError):
                decimals = 0

        if self.db_type.value == "postgres":
            return f"ROUND({col}::numeric, {decimals})"
        return f"ROUND({col}, {decimals})"

    def _translate_abs(self, params: Dict, table_alias: str = None) -> str:
        """Valore assoluto - identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"ABS({col})"

    def _translate_ceil(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento per eccesso - MSSQL usa CEILING, altri usano CEIL."""
        col = self._qualify_column(params["column"], table_alias)
        if self.db_type.value == "mssql":
            return f"CEILING({col})"
        return f"CEIL({col})"

    def _translate_floor(self, params: Dict, table_alias: str = None) -> str:
        """Arrotondamento per difetto - identico su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"FLOOR({col})"

    def _translate_math(self, params: Dict, table_alias: str = None) -> str:
        """Operazione aritmetica tra colonne o con valore fisso."""
        col1 = self._qualify_column(params["column1"], table_alias)
        operator = params.get("operator") or "+"

        valid_operators = {"+", "-", "*", "/"}
        if operator not in valid_operators:
            raise ValueError(f"Operatore non valido: {operator}. Usa: +, -, *, /")

        col2 = params.get("column2")
        value = params.get("value")

        if col2:
            second_operand = self._qualify_column(col2, table_alias)
        elif value is not None:
            second_operand = str(_safe_number(value, "float"))
        else:
            raise ValueError("Specificare column2 o value per l'operazione matematica")

        if operator == "/":
            protections = {
                "postgres": f"({col1} / NULLIF({second_operand}, 0))",
                "mssql": f"CASE WHEN {second_operand} = 0 THEN NULL ELSE {col1} / {second_operand} END",
                "mysql": f"IF({second_operand} = 0, NULL, {col1} / {second_operand})",
                "oracle": f"CASE WHEN {second_operand} = 0 THEN NULL ELSE {col1} / {second_operand} END",
                "sqlite": f"({col1} / NULLIF({second_operand}, 0))",
            }
            return protections.get(
                self.db_type.value,
                f"({col1} / NULLIF({second_operand}, 0))",
            )

        return f"({col1} {operator} {second_operand})"

    def _translate_mod(self, params: Dict, table_alias: str = None) -> str:
        """Resto divisione - MSSQL/SQLite usano %, altri MOD()."""
        col = self._qualify_column(params["column"], table_alias)
        divisor = _safe_number(params["divisor"], "float")
        if divisor == 0:
            raise ValueError("MOD: divisore non puo essere zero")
        safe_divisor = f"NULLIF({divisor}, 0)"
        translations = {
            "postgres": f"MOD({col}, {safe_divisor})",
            "mssql": f"({col} % {safe_divisor})",
            "mysql": f"MOD({col}, {safe_divisor})",
            "oracle": f"MOD({col}, {safe_divisor})",
            "sqlite": f"({col} % {safe_divisor})",
        }
        return self._numeric_translation_for(translations, "MOD")

    def _translate_power(self, params: Dict, table_alias: str = None) -> str:
        """Potenza - POWER() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        exponent = _safe_number(params["exponent"], "float")
        return f"POWER({col}, {exponent})"

    def _translate_sqrt(self, params: Dict, table_alias: str = None) -> str:
        """Radice quadrata - SQRT() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"SQRT({col})"

    def _translate_sign(self, params: Dict, table_alias: str = None) -> str:
        """Segno - SIGN() su tutti i database."""
        col = self._qualify_column(params["column"], table_alias)
        return f"SIGN({col})"

    def _translate_greatest(self, params: Dict, table_alias: str = None) -> str:
        """Massimo tra valori - MSSQL non ha GREATEST nativo pre-2022."""
        values = params["values"]
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",") if v.strip()]
        qualified_values = [self._qualify_column(v, table_alias) for v in values]

        if self.db_type.value == "mssql":
            result = qualified_values[0]
            for value in qualified_values[1:]:
                result = f"IIF({result} > {value}, {result}, {value})"
            return result
        if self.db_type.value == "sqlite":
            return f"MAX({', '.join(qualified_values)})"
        return f"GREATEST({', '.join(qualified_values)})"

    def _translate_least(self, params: Dict, table_alias: str = None) -> str:
        """Minimo tra valori - MSSQL non ha LEAST nativo pre-2022."""
        values = params["values"]
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",") if v.strip()]
        qualified_values = [self._qualify_column(v, table_alias) for v in values]

        if self.db_type.value == "mssql":
            result = qualified_values[0]
            for value in qualified_values[1:]:
                result = f"IIF({result} < {value}, {result}, {value})"
            return result
        if self.db_type.value == "sqlite":
            return f"MIN({', '.join(qualified_values)})"
        return f"LEAST({', '.join(qualified_values)})"
