"""
Date/time SQL function translators for ``sql_functions``.

The mixin keeps date dialect logic outside the main registry/API module while
depending only on the translator methods supplied by ``SqlFunctionTranslator``.
"""

from typing import Any

import re as _re

from app.db.sql_types import SqlParamMap, SqlTranslationMap


class SqlDateFunctionMixin:
    """Translator methods for date/time functions."""

    db_type: Any

    def _date_translation_for(
        self,
        translations: SqlTranslationMap,
        function_name: str,
    ) -> str:
        db_value = self.db_type.value
        if db_value in translations:
            return translations[db_value]
        raise ValueError(f"{function_name} non supportato per {db_value}")

    def _translate_year(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Estrazione anno - PostgreSQL/Oracle: EXTRACT, MSSQL/MySQL: YEAR(), SQLite: STRFTIME."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            "postgres": f"EXTRACT(YEAR FROM {col})",
            "mssql": f"YEAR({col})",
            "mysql": f"YEAR({col})",
            "oracle": f"EXTRACT(YEAR FROM {col})",
            "sqlite": f"CAST(STRFTIME('%Y', {col}) AS INTEGER)",
        }
        return self._date_translation_for(traduzioni, "YEAR")

    def _translate_month(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Estrazione mese - stessi dialetti di YEAR."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            "postgres": f"EXTRACT(MONTH FROM {col})",
            "mssql": f"MONTH({col})",
            "mysql": f"MONTH({col})",
            "oracle": f"EXTRACT(MONTH FROM {col})",
            "sqlite": f"CAST(STRFTIME('%m', {col}) AS INTEGER)",
        }
        return self._date_translation_for(traduzioni, "MONTH")

    def _translate_day(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Estrazione giorno - stessi dialetti di YEAR."""
        col = self._qualify_column(params["column"], table_alias)
        traduzioni = {
            "postgres": f"EXTRACT(DAY FROM {col})",
            "mssql": f"DAY({col})",
            "mysql": f"DAY({col})",
            "oracle": f"EXTRACT(DAY FROM {col})",
            "sqlite": f"CAST(STRFTIME('%d', {col}) AS INTEGER)",
        }
        return self._date_translation_for(traduzioni, "DAY")

    def _translate_now(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Data/ora corrente - sintassi specifica per ogni database."""
        traduzioni = {
            "postgres": "NOW()",
            "mssql": "GETDATE()",
            "mysql": "NOW()",
            "oracle": "SYSDATE",
            "sqlite": "datetime('now')",
        }
        return self._date_translation_for(traduzioni, "NOW")

    def _translate_current_date(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Data corrente - sintassi specifica per ogni database."""
        traduzioni = {
            "postgres": "CURRENT_DATE",
            "mssql": "CAST(GETDATE() AS DATE)",
            "mysql": "CURDATE()",
            "oracle": "TRUNC(SYSDATE)",
            "sqlite": "date('now')",
        }
        return self._date_translation_for(traduzioni, "CURRENT_DATE")

    def _translate_date_diff(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Differenza tra date in giorni."""
        col1 = self._qualify_column(params["column"], table_alias)
        col2 = self._qualify_column(params["column2"], table_alias)
        traduzioni = {
            "postgres": f"({col1}::date - {col2}::date)",
            "mssql": f"DATEDIFF(day, {col2}, {col1})",
            "mysql": f"DATEDIFF({col1}, {col2})",
            "oracle": f"({col1} - {col2})",
            "sqlite": f"CAST(julianday({col1}) - julianday({col2}) AS INTEGER)",
        }
        return self._date_translation_for(traduzioni, "DATE_DIFF")

    def _translate_date_add(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Aggiunta intervallo a data - sintassi molto diversa per ogni DB."""
        col = self._qualify_column(params["column"], table_alias)
        _VALID_UNITS = {"year", "month", "day", "hour", "minute", "second", "week"}
        try:
            intervallo = int(params["interval"])
        except (ValueError, TypeError):
            raise ValueError(
                f"DATE_ADD: interval deve essere numerico, ricevuto: {params.get('interval')}"
            )
        unita = str(params.get("unit", "day")).lower()
        if unita not in _VALID_UNITS:
            raise ValueError(f"DATE_ADD: unit '{unita}' non valida. Valide: {_VALID_UNITS}")
        traduzioni = {
            "postgres": f"{col} + INTERVAL '{intervallo} {unita}'",
            "mssql": f"DATEADD({unita}, {intervallo}, {col})",
            "mysql": f"DATE_ADD({col}, INTERVAL {intervallo} {unita})",
            "oracle": f"{col} + INTERVAL '{intervallo}' {unita}",
            "sqlite": f"datetime({col}, '+{intervallo} {unita}')",
        }
        return self._date_translation_for(traduzioni, "DATE_ADD")

    def _translate_date_trunc(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Tronca data a unita - sintassi molto diversa per ogni DB."""
        col = self._qualify_column(params["column"], table_alias)
        unita = str(params["unit"]).lower()
        _VALID_TRUNC_UNITS = {"year", "month", "week", "day", "hour", "minute", "second"}
        if unita not in _VALID_TRUNC_UNITS:
            raise ValueError(f"DATE_TRUNC: unit '{unita}' non valida. Valide: {_VALID_TRUNC_UNITS}")
        oracle_fmt = {"year": "YYYY", "month": "MM", "week": "IW", "day": "DD"}.get(unita, "MM")
        traduzioni = {
            "postgres": f"DATE_TRUNC('{unita}', {col})",
            "mssql": f"DATETRUNC({unita}, {col})",
            "mysql": self._mysql_date_trunc(col, unita),
            "oracle": f"TRUNC({col}, '{oracle_fmt}')",
            "sqlite": f"date({col}, 'start of {unita}')" if unita in ("year", "month", "day") else f"date({col})",
        }
        return self._date_translation_for(traduzioni, "DATE_TRUNC")

    def _mysql_date_trunc(self, col: str, unita: str) -> str:
        """Helper MySQL per DATE_TRUNC - MySQL non ha DATE_TRUNC nativo."""
        formati = {
            "year": f"DATE_FORMAT({col}, '%Y-01-01')",
            "month": f"DATE_FORMAT({col}, '%Y-%m-01')",
            "week": f"DATE(DATE_SUB({col}, INTERVAL WEEKDAY({col}) DAY))",
            "day": f"DATE({col})",
        }
        return formati.get(unita, f"DATE({col})")

    def _translate_date_format(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Formatta data come stringa - ogni DB ha il suo sistema di formattazione."""
        col = self._qualify_column(params["column"], table_alias)
        fmt = params["format"]
        if not _re.match(r"^[A-Za-z0-9\-_/:\s\.]+$", fmt):
            raise ValueError(f"DATE_FORMAT: format '{fmt}' contiene caratteri non permessi")
        pg_fmt = fmt.replace("DD", "DD").replace("MM", "MM").replace("YYYY", "YYYY").replace("YY", "YY").replace("HH", "HH24").replace("mm", "MI").replace("ss", "SS")
        mysql_fmt = fmt.replace("YYYY", "%Y").replace("YY", "%y").replace("MM", "%m").replace("DD", "%d").replace("HH", "%H").replace("mm", "%i").replace("ss", "%s")
        mssql_fmt = fmt
        sqlite_fmt = fmt.replace("YYYY", "%Y").replace("YY", "%y").replace("MM", "%m").replace("DD", "%d").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
        traduzioni = {
            "postgres": f"TO_CHAR({col}, '{pg_fmt}')",
            "mssql": f"FORMAT({col}, '{mssql_fmt}')",
            "mysql": f"DATE_FORMAT({col}, '{mysql_fmt}')",
            "oracle": f"TO_CHAR({col}, '{pg_fmt}')",
            "sqlite": f"STRFTIME('{sqlite_fmt}', {col})",
        }
        return self._date_translation_for(traduzioni, "DATE_FORMAT")

    def _translate_extract(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Estrazione parte da data - generalizzazione di YEAR/MONTH/DAY."""
        col = self._qualify_column(params["column"], table_alias)
        ammesse = {"year", "month", "day", "hour", "minute", "second"}
        raw = params.get("part")
        if not isinstance(raw, str) or raw.lower() not in ammesse:
            raise ValueError(
                f"EXTRACT: part '{raw}' non valida. Ammesse: {sorted(ammesse)}"
            )
        parte = raw.lower()
        sqlite_format_map = {
            "year": "%Y", "month": "%m", "day": "%d",
            "hour": "%H", "minute": "%M", "second": "%S"
        }
        sqlite_fmt = sqlite_format_map[parte]
        traduzioni = {
            "postgres": f"EXTRACT({parte} FROM {col})",
            "mssql": f"DATEPART({parte}, {col})",
            "mysql": f"EXTRACT({parte} FROM {col})",
            "oracle": f"EXTRACT({parte} FROM {col})",
            "sqlite": f"CAST(STRFTIME('{sqlite_fmt}', {col}) AS INTEGER)",
        }
        return self._date_translation_for(traduzioni, "EXTRACT")
