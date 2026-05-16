"""Pre-flight validation and estimate helpers for enrichment endpoints."""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.core.arricchisci.types import JsonObject
from app.core.dataframes.tabular import null_count_for_columns


def required_mapping_error(
    request: Any,
    api_conn: JsonObject,
) -> Optional[JsonObject]:
    """Return a validate response when required API parameters are unmapped."""
    required_params = [
        param["name"]
        for param in (api_conn.get("parameters") or [])
        if param.get("required", False)
    ]
    static_cols = set(request.static_columns or []) | set(
        (request.static_parameters or {}).keys()
    )
    unmapped_required = [
        param
        for param in required_params
        if not request.param_mapping.get(param) and param not in static_cols
    ]
    if not unmapped_required:
        return None
    return {
        "valid": False,
        "total_rows": 0,
        "valid_rows": 0,
        "invalid_rows": 0,
        "missing_columns": unmapped_required,
        "null_param_rows_count": 0,
        "warnings": [
            f"Parametri obbligatori senza mapping: {', '.join(unmapped_required)}"
        ],
    }


def generator_validate_response() -> JsonObject:
    """Return the validate response for REST generator APIs."""
    return {
        "valid": True,
        "total_rows": 0,
        "valid_rows": 0,
        "invalid_rows": 0,
        "missing_columns": [],
        "null_param_rows_count": 0,
        "warnings": [
            "Estrazione REST: il numero di righe sara' determinato dalla risposta dell'API."
        ],
    }


def build_validate_response(request: Any, df: Any) -> JsonObject:
    """Build the enrichment validate response for an input dataframe."""
    if df is None or df.empty:
        static_cols = set(request.static_columns or [])
        all_static = all(col in static_cols for col in request.param_mapping.values())

        if df is not None and df.empty:
            missing = [
                col
                for col in request.param_mapping.values()
                if col not in static_cols and col not in df.columns
            ]
            return {
                "valid": len(missing) == 0,
                "total_rows": 0,
                "valid_rows": 0,
                "invalid_rows": 0,
                "missing_columns": missing,
                "null_param_rows_count": 0,
                "warnings": ["File sorgente vuoto (0 righe) - nessun dato da arricchire"],
            }

        return {
            "valid": all_static or len(request.param_mapping) == 0,
            "total_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "missing_columns": []
            if all_static
            else list(request.param_mapping.values()),
            "null_param_rows_count": 0,
            "warnings": [
                "Nessun dato trovato nel progetto - validazione solo configurazione"
            ]
            if not all_static
            else [],
        }

    static_cols = set(request.static_columns or [])
    missing_columns = [
        df_column
        for df_column in request.param_mapping.values()
        if df_column not in static_cols and df_column not in df.columns
    ]
    if missing_columns:
        return {
            "valid": False,
            "total_rows": len(df),
            "valid_rows": 0,
            "invalid_rows": len(df),
            "missing_columns": missing_columns,
            "null_param_rows_count": 0,
            "warnings": [
                f"Colonne mancanti nel dataset: {', '.join(missing_columns)}"
            ],
        }

    total_rows = len(df)
    null_rows = null_count_for_columns(df, request.param_mapping.values())
    valid_rows = total_rows - null_rows if null_rows < total_rows else 0
    warnings = []
    if null_rows > 0:
        warnings.append(
            f"{null_rows} valori null nei parametri mappati - quelle righe verranno saltate"
        )

    return {
        "valid": True,
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "invalid_rows": null_rows,
        "missing_columns": [],
        "null_param_rows_count": null_rows,
        "warnings": warnings,
    }


def build_estimate_response(
    request: Any,
    api_config: JsonObject,
    df: Any,
    *,
    executor_cls: Callable[..., Any],
    options_cls: Callable[..., Any],
    format_time: Callable[[float], str],
) -> JsonObject:
    """Build the enrichment estimate response without performing API calls."""
    if df is None or df.empty:
        return {
            "total_rows": 0,
            "unique_combinations": 0,
            "estimated_api_calls": 0,
            "estimated_time_seconds": 0,
            "estimated_time_formatted": "0s",
            "deduplication_savings_percent": 0,
        }

    total_rows = len(df)
    executor = executor_cls(
        api_config=api_config,
        param_mapping=request.param_mapping,
        options=options_cls(enable_deduplication=True),
    )
    unique_combinations = executor._count_unique_combinations(df)
    rate_limit = api_config.get("options", {}).get("rate_limit", 5)
    estimated_time = (
        unique_combinations / rate_limit if rate_limit > 0 else unique_combinations
    )
    savings = (
        (total_rows - unique_combinations) / total_rows * 100
        if total_rows > 0
        else 0
    )
    return {
        "total_rows": total_rows,
        "unique_combinations": unique_combinations,
        "estimated_api_calls": unique_combinations,
        "estimated_time_seconds": estimated_time,
        "estimated_time_formatted": format_time(estimated_time),
        "deduplication_savings_percent": round(savings, 1),
    }
