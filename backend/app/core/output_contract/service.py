"""Pure helpers for building and reading output contracts."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.core.dataframes.schema import dtype_compatible_with_logical
from app.core.dataframes.tabular import make_unique_column_names
from app.utils.logger import get_logger

from .models import OutputColumn, OutputColumnType, OutputContract

logger = get_logger(__name__)

ColumnPayload = dict[str, Any]
ColumnPayloadRows = list[ColumnPayload]
ColumnTypeMap = dict[str, str]


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    value = getattr(obj, key, default)
    return value if value is not None else default


def _logical_type(raw_type: Any) -> str:
    value = str(raw_type or "str").lower()
    if value in {"str", "string", "text", "object"}:
        return "string"
    if value in {"int", "int8", "int16", "int32", "int64", "integer", "bigint", "smallint"}:
        return "integer"
    if value in {"real", "float", "float16", "float32", "float64", "double", "decimal", "numeric"}:
        return "number"
    if value in {"bool", "boolean"}:
        return "boolean"
    if value in {"date", "datetime", "timestamp"}:
        return "datetime"
    if value.startswith("geometry"):
        return "geometry"
    return value


def _previous_columns(previous_contract: Any) -> ColumnPayloadRows:
    if not previous_contract:
        return []
    if isinstance(previous_contract, OutputContract):
        return [c.model_dump() for c in previous_contract.columns]
    if isinstance(previous_contract, dict):
        cols = previous_contract.get("columns") or []
        return [c for c in cols if isinstance(c, dict)]
    return []


def _new_column_id() -> str:
    return f"col_{uuid.uuid4().hex}"


def build_output_contract(
    *,
    project_id: str,
    result_columns: list[Any],
    previous_contract: Any = None,
) -> OutputContract:
    """Build a public output contract from final result columns.

    Identity preservation is intentionally based on ``source_name`` first:
    alias/display changes must not create a new semantic column id.
    """

    previous = _previous_columns(previous_contract)
    previous_by_source: dict[str, list[dict[str, Any]]] = {}
    previous_by_physical: dict[str, list[dict[str, Any]]] = {}
    for col in previous:
        previous_by_source.setdefault(str(col.get("source_name") or ""), []).append(col)
        previous_by_physical.setdefault(str(col.get("physical_name") or ""), []).append(col)

    prepared: list[tuple[Any, str, str]] = []
    for raw in result_columns or []:
        source_name = str(_field(raw, "source_name", None) or _field(raw, "name", "") or "")
        if not source_name:
            continue
        alias = _field(raw, "alias", None)
        physical_name = str(alias or _field(raw, "name", source_name) or source_name)
        prepared.append((raw, source_name, physical_name))

    unique_physical_names, _renames = make_unique_column_names(
        physical_name for _raw, _source_name, physical_name in prepared
    )

    built: list[OutputColumn] = []
    used_column_ids: set[str] = set()
    for ordinal, ((raw, source_name, _physical_name), physical_name) in enumerate(
        zip(prepared, unique_physical_names)
    ):
        display_name = physical_name

        reused = None
        source_bucket = previous_by_source.get(source_name) or []
        if source_bucket:
            reused = source_bucket.pop(0)
        else:
            physical_bucket = previous_by_physical.get(physical_name) or []
            if physical_bucket:
                reused = physical_bucket.pop(0)

        column_id = (
            (str(reused.get("id")) if reused and reused.get("id") else "")
            or str(_field(raw, "id", "") or "")
            or _new_column_id()
        )
        if column_id in used_column_ids:
            column_id = _new_column_id()
        used_column_ids.add(column_id)

        built.append(
            OutputColumn(
                id=column_id,
                source_name=source_name,
                physical_name=physical_name,
                display_name=display_name,
                type=OutputColumnType(logical=_logical_type(_field(raw, "type", "str"))),
                ordinal=ordinal,
            )
        )

    previous_revision = 0
    if isinstance(previous_contract, dict):
        previous_revision = int(previous_contract.get("revision") or 0)
    elif isinstance(previous_contract, OutputContract):
        previous_revision = previous_contract.revision

    return OutputContract(
        producer_project_id=project_id,
        revision=previous_revision + 1,
        columns=built,
    )


def persist_output_contract(
    *,
    project_service: Any,
    project_id: str,
    result_columns: list[Any],
) -> ColumnPayload:
    """Build the project's output contract and persist it; return the dump.

    Unica fonte di verita' della sequenza *build(previous) + update + dump*,
    condivisa dai finalizer di estrai/manipola/arricchisci + enrichment_run_mode
    (prima riscritta inline in ognuno). La policy d'errore resta del CALLER
    (estrai propaga; manipola/arricchisci wrappano in try/except + flag
    ``contract_update_failed``): questo helper non la decide.

    Il ``previous_contract=`` e' OBBLIGATORIO e centralizzato qui: e' cio' che
    preserva l'identita' delle colonne (``id``) attraverso i rename
    (``source_name`` -> stesso id). Ometterlo significherebbe id-churn ad ogni
    run, che romperebbe i ref a colonna nei progetti a valle.

    ``project_service`` e' iniettato (duck-typed: deve esporre
    ``get_project_output_contract`` e ``update_project_output_contract``): cosi'
    questo modulo resta privo di dipendenze verso ``app.core.projects``.
    """
    output_contract = build_output_contract(
        project_id=project_id,
        result_columns=result_columns,
        previous_contract=project_service.get_project_output_contract(project_id),
    )
    dumped = output_contract.model_dump()
    project_service.update_project_output_contract(project_id, dumped)
    return dumped


def column_types_from_contract(contract: Any) -> ColumnTypeMap:
    """Return ``physical_name -> logical_type`` from an output contract."""
    if isinstance(contract, OutputContract):
        columns = [c.model_dump() for c in contract.columns]
    elif isinstance(contract, dict):
        columns = contract.get("columns") or []
    else:
        columns = []
    out: dict[str, str] = {}
    for col in columns:
        if not isinstance(col, dict) or col.get("status", "active") != "active":
            continue
        name = col.get("physical_name") or col.get("display_name") or col.get("source_name")
        logical = ((col.get("type") or {}).get("logical") if isinstance(col.get("type"), dict) else None) or "string"
        if name:
            out[str(name)] = str(logical)
    return out


def node_columns_from_contract(
    contract: Any,
    *,
    previous_node_columns: list[Any] | None = None,
) -> ColumnPayloadRows:
    """Return canvas-compatible source columns from an output contract.

    Visibility is preserved by column id from the previous project_source node.
    """
    previous_visible_by_id: dict[str, bool] = {}
    previous_visible_by_name: dict[str, bool] = {}
    previous_visible_by_source: dict[str, bool] = {}
    for col in previous_node_columns or []:
        col_id = _field(col, "id", None) or _field(col, "column_id", None)
        if col_id:
            previous_visible_by_id[str(col_id)] = bool(_field(col, "visible", True))
        for name_key in ("name", "display_name"):
            name = _field(col, name_key, None)
            if name:
                previous_visible_by_name[str(name)] = bool(_field(col, "visible", True))
        # source_name e' l'identita' stabile: se l'upstream sostituisce l'id
        # della colonna E ne rinomina il physical, ne' id ne' name matchano
        # piu' — il fallback su source_name conserva comunque la visibilita'.
        source = _field(col, "source_name", None)
        if source:
            previous_visible_by_source[str(source)] = bool(_field(col, "visible", True))

    if isinstance(contract, OutputContract):
        columns = [c.model_dump() for c in contract.columns]
    elif isinstance(contract, dict):
        columns = contract.get("columns") or []
    else:
        columns = []

    out: list[dict[str, Any]] = []
    for col in columns:
        if not isinstance(col, dict) or col.get("status", "active") != "active":
            continue
        col_id = str(col.get("id") or "")
        if not col_id:
            continue
        physical_name = str(
            col.get("physical_name") or col.get("display_name") or col.get("source_name") or ""
        )
        if not physical_name:
            continue
        logical_type = (
            (col.get("type") or {}).get("logical")
            if isinstance(col.get("type"), dict)
            else None
        ) or "string"
        out.append({
            "id": col_id,
            "name": physical_name,
            "source_name": col.get("source_name") or physical_name,
            "display_name": col.get("display_name") or physical_name,
            "type": logical_type,
            "visible": previous_visible_by_id.get(
                col_id,
                previous_visible_by_name.get(
                    physical_name,
                    previous_visible_by_source.get(
                        str(col.get("source_name") or ""),
                        True,
                    ),
                ),
            ),
        })
    return out


def _dtype_compatible_with_logical(dtype: Any, logical: str) -> bool:
    """Return True when the frame-engine dtype satisfies the contract logical type.

    `string`, `boolean`, `geometry` accettano `object` perche' pandas
    rappresenta queste tipologie via dtype generico. `datetime` accetta
    `object` perche' molti adapter (CSV, JSON) restituiscono str ISO che
    il consumer puo' convertire; il dtype dedicato `datetime64[ns]` viene
    matchato sotto via `is_datetime64_any_dtype`. `number` accetta
    qualsiasi numerico inclusi gli interi (un consumer che si aspetta
    `number` accetta `integer` come sottoinsieme — promotion safe).
    Per i tipi `integer`/`number` NON accettiamo `float64` quando il
    contratto dice `integer` esplicito, perche' il round-trip parquet
    su NaN puo' degradare Int64→float64 con perdita di precisione su
    valori > 2^53.
    """
    return dtype_compatible_with_logical(dtype, logical)


def ensure_dataframe_matches_contract(
    *,
    dataframe_columns: Any,
    node_columns: list[dict[str, Any]],
    source_project_id: str,
    dataframe: Optional[Any] = None,
) -> None:
    """Fail when the materialized project data does not match its contract.

    Cross-project consumers are allowed to exchange only:
    - materialized data, whose physical columns must match the public contract
    - output_contract, which describes the public schema

    A mismatch means the upstream artifact is stale or corrupt and must not be
    silently consumed by downstream projects.

    Type drift: se `dataframe` e' passato, verifica anche che i dtype
    delle colonne visibili siano compatibili coi logical type del
    contratto. Mismatch emette `logger.warning` (non raise) — il caller
    riceve comunque il DataFrame, ma il drift e' tracciato per debug.
    Soft-fail e' intenzionale: pipeline storiche con tipi divergenti
    (es. Int64 degradato a float64 dal round-trip parquet) non
    devono crashare ma essere riportate ai maintainer per fix
    incrementali.
    """
    available = {str(col) for col in dataframe_columns}
    visible_columns = [
        col
        for col in node_columns
        if isinstance(col, dict) and col.get("visible", True) and col.get("name")
    ]
    visible_names = [str(col.get("name") or "") for col in visible_columns]
    missing_names = [name for name in visible_names if name not in available]
    if missing_names:
        raise ValueError(
            "artifact sorgente non allineato a output_contract: "
            f"progetto={source_project_id} colonne mancanti {missing_names}"
        )

    if dataframe is None:
        return

    type_mismatches: list[str] = []
    for col in visible_columns:
        name = str(col.get("name") or "")
        if name not in dataframe.columns:
            continue
        logical = _logical_type(col.get("type"))
        if not logical:
            continue
        dtype = dataframe[name].dtype
        if not _dtype_compatible_with_logical(dtype, logical):
            type_mismatches.append(f"{name}({dtype} != {logical})")

    if type_mismatches:
        logger.warning(
            "[OutputContract] type drift su artifact sorgente "
            "project_id=%s: %s. La pipeline prosegue ma il consumer "
            "downstream potrebbe ricevere dtype divergenti dal "
            "contratto. Verifica il writer (parquet engine, dtype "
            "coercion) del progetto sorgente.",
            source_project_id, ", ".join(type_mismatches),
        )
