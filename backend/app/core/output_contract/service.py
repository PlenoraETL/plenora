"""Pure helpers for building and reading output contracts."""

from __future__ import annotations

import uuid
from typing import Any, Optional, Iterable, Sequence

from app.core.dataframes.schema import dtype_compatible_with_logical, resolve_column_type
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
    if value in {"real", "float", "float16", "float32", "float64", "double", "decimal", "numeric", "number"}:
        return "number"
    if value in {"bool", "boolean"}:
        return "boolean"
    if value in {"date", "datetime", "timestamp"}:
        return "datetime"
    if value.startswith("geometry"):
        return "geometry"
    return "string"


def _previous_columns(previous_contract: Any) -> ColumnPayloadRows:
    if not previous_contract:
        return []
    if isinstance(previous_contract, OutputContract):
        return [c.model_dump() for c in previous_contract.columns]
    if isinstance(previous_contract, dict):
        cols = previous_contract.get("columns") or []
        return [c for c in cols if isinstance(c, dict)]
    return []


def result_columns_from_plan(*, project_id: str, columns: Iterable[str], plan: list[Any],
                             physical_names: Optional[Sequence[str]] = None,
                             types: Optional[Sequence[str]] = None,
                             dtypes: Optional[Sequence[str]] = None, dataframe: Any = None,
                             inferred_types: Optional[Sequence[str]] = None) -> ColumnPayloadRows:
    """Associa ogni colonna prodotta al piano, mantenendo le corrispondenze parziali."""
    names = list(columns)
    physical = list(physical_names) if physical_names is not None else names
    normalized = [str(name) for name in physical]
    remaining = list(plan)
    result = []
    for index, name in enumerate(names):
        candidates = [col for col in remaining if str(_field(col, "name")) == str(name)]
        if not candidates:
            candidates = [col for col in remaining if str(_field(col, "source_name")) == str(name)]
        matched = candidates[0] if len(candidates) == 1 else None
        if matched is not None:
            remaining.remove(matched)
        result.append({
            "id": _field(matched, "id") or f"col_{uuid.uuid5(uuid.NAMESPACE_URL, project_id + ':runtime:' + str(name)).hex}",
            "source_name": _field(matched, "source_name") or str(name),
            "source_node": _field(matched, "source_node"),
            "name": normalized[index],
            "type": resolve_column_type(
                runtime_dtype=dtypes[index] if dtypes is not None else None,
                series=dataframe.iloc[:, index] if dataframe is not None else None,
                declared=(types[index] if types is not None else None) or _field(matched, "type"),
                inferred=inferred_types[index] if inferred_types is not None else None,
            ),
        })
    return result


def build_output_contract(
    *,
    project_id: str,
    result_columns: list[Any],
    previous_contract: Any = None,
) -> OutputContract:
    """Riusa le identit? pubblicate per origine, anche con nuovi id sintetici."""

    previous = _previous_columns(previous_contract)
    previous_by_source: dict[str, list[dict[str, Any]]] = {}
    previous_by_physical: dict[str, list[dict[str, Any]]] = {}
    for col in previous:
        previous_by_source.setdefault(str(col.get("source_name") or ""), []).append(col)
        previous_by_physical.setdefault(str(col.get("physical_name") or ""), []).append(col)

    prepared: list[tuple[Any, str, str]] = []
    for raw in result_columns or []:
        source_name = str(_field(raw, "source_name", None) or _field(raw, "name", "") or "")
        # Il nome fisico non e' un alias: anche None e la stringa vuota
        # sono etichette effettive del DataFrame e vanno serializzate tali.
        physical_name = str(raw.get("name", source_name) if isinstance(raw, dict)
                            else getattr(raw, "name", source_name))
        prepared.append((raw, source_name, physical_name))

    unique_physical_names = [name for _, _, name in prepared]
    current_origins = [(_field(item, "source_node"), source) for item, source, _ in prepared]
    occurrences: dict[tuple, int] = {}

    built: list[OutputColumn] = []
    used_column_ids: set[str] = set()
    for ordinal, ((raw, source_name, _physical_name), physical_name) in enumerate(
        zip(prepared, unique_physical_names)
    ):
        display_name = physical_name

        raw_id = str(_field(raw, "id", "") or "")
        origin = (_field(raw, "source_node"), source_name)
        occurrence = occurrences.get(origin, 0)
        occurrences[origin] = occurrence + 1
        candidates = [col for col in previous if origin[0] and
                      (col.get("source_node"), col.get("source_name")) == origin]
        reused = candidates[0] if len(candidates) == 1 and current_origins.count(origin) == 1 else None
        if reused is None:
            # Un'origine ripetuta non autorizza un rename. La coppia origine/
            # nome fisico invariata e univoca conserva invece anche gli id legacy.
            exact = [col for col in previous if
                     (col.get("source_node"), col.get("source_name")) == origin
                     and col.get("physical_name") == physical_name]
            if len(exact) == 1 and unique_physical_names.count(physical_name) == 1:
                reused = exact[0]
        if reused is None:
            candidates = previous_by_physical.get(physical_name, [])
            if (len(candidates) == 1 and unique_physical_names.count(physical_name) == 1
                    and not candidates[0].get("source_node")):
                reused = candidates[0]
        if reused is None:
            candidates = previous_by_source.get(source_name, [])
            if (len(candidates) == 1 and sum(source == source_name for _, source, _ in prepared) == 1
                    and not candidates[0].get("source_node")
                    and not previous_by_physical.get(physical_name)
                    and candidates[0].get("physical_name") not in unique_physical_names):
                reused = candidates[0]
        conflicting_target = reused is not None and any(col.get("id") != reused.get("id") for col in previous_by_physical.get(physical_name, []))
        if conflicting_target:
            reused = None
        if reused is not None and reused.get("id") in used_column_ids:
            reused = None
        # L'identita sintetica non consulta mai l'insieme degli id precedenti.
        # L'occorrenza separa origini ambigue, senza dipendere dai vecchi id.
        derived_id = f"col_{uuid.uuid5(uuid.NAMESPACE_URL, project_id + ':origin:' + str(origin) + ':' + str(occurrence)).hex}"
        column_id = str(reused["id"]) if reused else (raw_id if raw_id and current_origins.count(origin) == 1 and not conflicting_target else derived_id)
        if column_id in used_column_ids:
            column_id = derived_id
        used_column_ids.add(column_id)

        built.append(
            OutputColumn(
                id=column_id,
                source_name=source_name,
                source_node=_field(raw, "source_node"),
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
        name = col.get("physical_name", col.get("display_name", col.get("source_name")))
        logical = ((col.get("type") or {}).get("logical") if isinstance(col.get("type"), dict) else None) or "string"
        if name is not None:
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
            col.get("physical_name", col.get("display_name", col.get("source_name", "")))
        )
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
        if isinstance(col, dict) and col.get("visible", True) and col.get("name") is not None
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
        logical = _logical_type(col.get("type"))
        if not logical:
            continue
        # Il confronto usa le etichette serializzate anche per None/nan;
        # una selezione per nome restituirebbe un DataFrame per i duplicati.
        for label, dtype in zip(dataframe.columns, dataframe.dtypes):
            if str(label) == name and not _dtype_compatible_with_logical(dtype, logical):
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
