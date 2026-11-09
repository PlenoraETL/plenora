"""
CRUD per la tabella `eseguibili` (storage: PostgreSQL via

`app.db.pg_connection`).
"""

from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause
from app.shared.contracts.payloads import JsonObject, JsonRows


ExpectedColumns = JsonRows


# Regola unica «l'Eseguibile ha un manifest» (UI conferma finale G5): un
# manifest NON VUOTO. La colonna ``eseguibile_versions.manifest`` e' NOT NULL
# e una versione senza modulo puo' avere ``{}``: prima la lista diceva
# ``manifest IS NOT NULL`` (sempre vero) mentre dettaglio e upload trattano
# ``{}`` come assenza. La stessa regola in SQL (lista) e in Python (dettaglio,
# upload, esecuzione); il test di parita' le confronta su PostgreSQL vero.
MANIFEST_PRESENT_SQL = "(COALESCE(v.manifest, '{}'::jsonb) NOT IN ('{}'::jsonb, 'null'::jsonb))"


def canvas_fingerprint(canvas: object) -> str:
    """Impronta di un canvas (N1, 09/11/2026): la stessa alla pubblicazione
    (``eseguibile_versions.source_hash``) e nel confronto con il canvas attuale
    del progetto sorgente (``service.source_status``). Un testo JSON si legge
    prima (colonna storica TEXT)."""
    if isinstance(canvas, str):
        canvas = json.loads(canvas)
    return hashlib.sha256(
        json.dumps(canvas, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# N1: la versione attiva e' ancora il canvas attuale del progetto sorgente?
# Calcolato in SQL per la lista (nessun canvas scaricato): confronto jsonb
# semantico fra la copia congelata della versione e il canvas del progetto.
# Equivale al confronto delle impronte (``canvas_fingerprint``: JSON con le
# chiavi ordinate) salvo numeri scritti diversamente con lo stesso valore
# (1 e 1.0). Colonna del progetto jsonb (produzione) o TEXT storico, come
# ``projects.db.MANIPOLA_SOURCE_REFS_SQL``; un canvas TEXT illeggibile conta
# come cambiato. NULL senza progetto sorgente o senza versione attiva.
SOURCE_CHANGED_SQL = """(CASE
        WHEN v.id IS NULL OR p.id IS NULL THEN NULL
        WHEN pg_typeof(p.canvas) = 'jsonb'::regtype THEN v.canvas::jsonb IS DISTINCT FROM p.canvas::jsonb
        WHEN pg_input_is_valid(p.canvas::text, 'jsonb') THEN v.canvas::jsonb IS DISTINCT FROM p.canvas::text::jsonb
        ELSE true
    END)"""


def manifest_present(manifest: object) -> bool:
    """True se il manifest c'e' e non e' vuoto (stessa regola di MANIFEST_PRESENT_SQL)."""
    return isinstance(manifest, dict) and bool(manifest)


class _DbFacade:
    def fetchone(self, sql: str, params: tuple = ()) -> JsonObject | None:
        with unit_of_work() as session:
            return session.fetchone(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> JsonRows:
        with unit_of_work() as session:
            return session.fetchall(sql, params)

    def execute(self, sql: str, params: tuple = ()):
        with unit_of_work() as session:
            return session.execute(sql, params)


db = _DbFacade()


def _fetchone(sql: str, params: tuple = ()) -> JsonObject | None:
    return db.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> JsonRows:
    return db.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    return db.execute(sql, params)


@dataclass(frozen=True)
class EseguibileRow:
    id: str
    name: str
    description: str
    canvas: JsonObject
    expected_columns: ExpectedColumns
    default_output_format: str
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    default_output_name: str | None = None
    icon: str | None = None
    source_canvas_id: str | None = None
    project_type: str = "arricchisci"
    active_version_id: str | None = None
    active_version: int = 1
    manifest: JsonObject | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(frozen=True)
class EseguibileSummaryRow:
    id: str
    name: str
    description: str
    default_output_format: str
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    expected_columns_count: int = 0
    default_output_name: str | None = None
    icon: str | None = None
    source_canvas_id: str | None = None
    project_type: str = "arricchisci"
    active_version: int = 1
    inputs_count: int = 1
    # La versione attiva ha un manifest: si esegue con il modulo dinamico.
    # Regola unica ``manifest_present`` (UI conferma finale G5): manifest NON
    # VUOTO, come il dettaglio e l'upload (un ``{}`` e' un manifest assente).
    has_manifest: bool = False
    # Binding del manifest attivo e i SOLI nodi del canvas della versione che
    # ne sono bersaglio (per ``binding_warnings`` senza caricare il canvas).
    bindings: tuple[JsonObject, ...] = ()
    binding_target_nodes: tuple[JsonObject, ...] = ()
    # N1: progetto sorgente cambiato dopo la pubblicazione (SOURCE_CHANGED_SQL).
    source_changed: bool | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(frozen=True)
class EseguibileVersionRow:
    id: str
    eseguibile_id: str
    version_number: int
    schema_version: int
    project_type: str
    source_project_id: str | None
    source_hash: str
    canvas: JsonObject
    manifest: JsonObject
    status: str
    created_by: str
    created_at: str
    source_canvas_version_id: str | None = None

@dataclass(frozen=True)
class EseguibileSourceRefRow:
    id: str
    name: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        return getattr(self, key, default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_str(value: object) -> str | None:
    """Best-effort coercion to ISO string for datetime values returned by psycopg2.

    psycopg2 returns ``TIMESTAMPTZ`` as ``datetime`` objects by default; the
    eseguibile DTO declares those columns as ``str``. Without coercion the
    factory raises ``Malformed eseguibile DB row`` even on legitimate rows.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = _coerce_str(row.get(key))
    if value is None:
        raise ValueError(f"Malformed eseguibile DB row: {key} must be a string")
    return value


def _optional_str(row: Mapping[str, object], key: str) -> str | None:
    raw = row.get(key)
    if raw is None:
        return None
    return _coerce_str(raw)


def _parse_json_object(value: object, *, field_name: str) -> JsonObject:
    if value is None or value == "":
        return {}
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Malformed eseguibile DB row: {field_name} must decode to an object"
        )
    return parsed


def _parse_expected_columns(value: object) -> ExpectedColumns:
    if value is None or value == "":
        return []
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError(
            "Malformed eseguibile DB row: expected_columns must decode to a list"
        )
    return [item for item in parsed if isinstance(item, dict)]


def eseguibile_row_from_db(row: Mapping[str, object]) -> EseguibileRow:
    active_manifest = _parse_json_object(
        row.get("active_manifest"), field_name="active_manifest"
    ) if row.get("active_manifest") is not None else None
    return EseguibileRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
        description=_optional_str(row, "description") or "",
        canvas=_parse_json_object(
            row.get("version_canvas") if row.get("version_canvas") is not None else row.get("canvas"),
            field_name="canvas",
        ),
        expected_columns=_parse_expected_columns(row.get("expected_columns")),
        default_output_format=_required_str(row, "default_output_format"),
        default_output_name=_optional_str(row, "default_output_name"),
        icon=_optional_str(row, "icon"),
        source_canvas_id=_optional_str(row, "source_canvas_id"),
        group_id=_required_str(row, "group_id"),
        created_by=_required_str(row, "created_by"),
        created_at=_required_str(row, "created_at"),
        updated_at=_required_str(row, "updated_at"),
        project_type=_optional_str(row, "project_type") or "arricchisci",
        active_version_id=_optional_str(row, "active_version_id"),
        active_version=int(row.get("active_version") or 1),
        manifest=active_manifest,
    )


def eseguibile_summary_row_from_db(row: Mapping[str, object]) -> EseguibileSummaryRow:
    return EseguibileSummaryRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
        description=_optional_str(row, "description") or "",
        default_output_format=_required_str(row, "default_output_format"),
        default_output_name=_optional_str(row, "default_output_name"),
        icon=_optional_str(row, "icon"),
        source_canvas_id=_optional_str(row, "source_canvas_id"),
        group_id=_required_str(row, "group_id"),
        created_by=_required_str(row, "created_by"),
        created_at=_required_str(row, "created_at"),
        updated_at=_required_str(row, "updated_at"),
        expected_columns_count=len(_parse_expected_columns(row.get("expected_columns"))),
        project_type=_optional_str(row, "project_type") or "arricchisci",
        active_version=int(row.get("active_version") or 1),
        inputs_count=(
            int(row["inputs_count"])
            if row.get("inputs_count") is not None
            else 1
        ),
        has_manifest=bool(row.get("has_manifest")),
        bindings=_parse_json_objects(row.get("bindings")),
        binding_target_nodes=_parse_json_objects(row.get("binding_target_nodes")),
        source_changed=None if row.get("source_changed") is None else bool(row.get("source_changed")),
    )


def _parse_json_objects(value: object) -> tuple[JsonObject, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def eseguibile_version_row_from_db(
    row: Mapping[str, object],
) -> EseguibileVersionRow:
    return EseguibileVersionRow(
        id=_required_str(row, "id"),
        eseguibile_id=_required_str(row, "eseguibile_id"),
        version_number=int(row.get("version_number") or 1),
        schema_version=int(row.get("schema_version") or 1),
        project_type=_required_str(row, "project_type"),
        source_project_id=_optional_str(row, "source_project_id"),
        source_canvas_version_id=_optional_str(row, "source_canvas_version_id"),
        source_hash=_required_str(row, "source_hash"),
        canvas=_parse_json_object(row.get("canvas"), field_name="canvas"),
        manifest=_parse_json_object(row.get("manifest"), field_name="manifest"),
        status=_required_str(row, "status"),
        created_by=_required_str(row, "created_by"),
        created_at=_required_str(row, "created_at"),
    )


def eseguibile_source_ref_row_from_db(
    row: Mapping[str, object],
) -> EseguibileSourceRefRow:
    return EseguibileSourceRefRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
    )


def create_eseguibile(
    name: str,
    canvas: JsonObject,
    expected_columns: ExpectedColumns,
    group_id: str,
    created_by: str,
    description: str = "",
    default_output_format: str = "xlsx",
    default_output_name: Optional[str] = None,
    source_canvas_id: Optional[str] = None,
    icon: Optional[str] = None,
    project_type: str = "arricchisci",
    manifest: Optional[JsonObject] = None,
) -> EseguibileRow:
    """Crea un nuovo eseguibile. Ritorna il record creato."""
    eid = f"exec_{uuid.uuid4().hex[:12]}"
    now = _now()
    _execute(
        """
        INSERT INTO eseguibili (
            id, name, description, canvas, expected_columns,
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, created_at, updated_at, project_type
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            eid, name, description,
            json.dumps(canvas, ensure_ascii=False),
            json.dumps(expected_columns, ensure_ascii=False),
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, now, now, project_type,
        )
    )
    version = create_eseguibile_version(
        eseguibile_id=eid,
        canvas=canvas,
        manifest=manifest or {},
        project_type=project_type,
        source_project_id=source_canvas_id,
        created_by=created_by,
    )
    _execute(
        "UPDATE eseguibili SET active_version_id = %s WHERE id = %s",
        (version.id, eid),
    )
    created = get_eseguibile(eid)
    if created is None:
        raise RuntimeError(f"Eseguibile '{eid}' was inserted but not found")
    return created


def get_eseguibile(eid: str, group_id: str = None) -> Optional[EseguibileRow]:
    """Ritorna l'eseguibile con canvas e expected_columns deserializzati.

    group_id=None → no filter (backward compat per uso interno).
    Passare group_id dal router per defense-in-depth multi-tenant.
    """
    if group_id:
        row = _fetchone(
            "SELECT e.*, v.id AS version_id, v.version_number AS active_version, "
            "v.manifest AS active_manifest, v.canvas AS version_canvas "
            "FROM eseguibili e LEFT JOIN eseguibile_versions v "
            "ON v.id = e.active_version_id WHERE e.id = %s AND e.group_id = %s",
            (eid, group_id)
        )
    else:
        row = _fetchone(
            "SELECT e.*, v.id AS version_id, v.version_number AS active_version, "
            "v.manifest AS active_manifest, v.canvas AS version_canvas "
            "FROM eseguibili e LEFT JOIN eseguibile_versions v "
            "ON v.id = e.active_version_id WHERE e.id = %s",
            (eid,),
        )
    return eseguibile_row_from_db(row) if row else None


def list_eseguibili(
    group_id: Optional[str] = None,
    *,
    resource_ids: set[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[EseguibileSummaryRow]:
    """Lista eseguibili, opzionalmente filtrati per gruppo.

    Non carica canvas/expected_columns per la summary (prestazioni).
    Usa get_eseguibile per i dettagli completi.
    """
    columns = f"""e.id, e.name, e.description, e.default_output_format,
                 e.default_output_name, e.icon, e.source_canvas_id, e.group_id,
                 e.created_by, e.created_at, e.updated_at, e.expected_columns,
                 e.project_type, COALESCE(v.version_number, 1) AS active_version,
                 COALESCE(jsonb_array_length(v.manifest->'inputs'), 1) AS inputs_count,
                 {MANIFEST_PRESENT_SQL} AS has_manifest,
                 v.manifest->'bindings' AS bindings,
                 (SELECT jsonb_agg(n.node)
                    FROM jsonb_array_elements(
                         CASE WHEN jsonb_typeof(v.canvas->'nodes') = 'array'
                              THEN v.canvas->'nodes' ELSE '[]'::jsonb END) AS n(node)
                   WHERE n.node->>'id' IN (
                         SELECT b.binding->>'target_node_id'
                           FROM jsonb_array_elements(
                                CASE WHEN jsonb_typeof(v.manifest->'bindings') = 'array'
                                     THEN v.manifest->'bindings' ELSE '[]'::jsonb END) AS b(binding))
                 ) AS binding_target_nodes,
                 {SOURCE_CHANGED_SQL} AS source_changed"""
    join = (" LEFT JOIN eseguibile_versions v ON v.id = e.active_version_id "
            " LEFT JOIN projects p ON p.id = e.source_canvas_id ")
    if resource_ids is not None:
        if not resource_ids:
            return []
        ordered_ids = sorted(resource_ids)
        placeholders = ", ".join(["%s"] * len(ordered_ids))
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili e {join} "
            f"WHERE e.id IN ({placeholders}) ORDER BY e.created_at DESC "
            "LIMIT %s OFFSET %s",
            tuple(ordered_ids) + (limit, offset),
        )
    elif group_id:
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili e {join} WHERE e.group_id = %s "
            "ORDER BY e.created_at DESC LIMIT %s OFFSET %s",
            (group_id, limit, offset)
        )
    else:
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili e {join} ORDER BY e.created_at DESC "
            "LIMIT %s OFFSET %s",
            (limit, offset),
        )
    return [eseguibile_summary_row_from_db(row) for row in rows]


def list_eseguibili_by_source(source_canvas_id: str) -> list[EseguibileSourceRefRow]:
    """Ritorna gli eseguibili pubblicati a partire dal progetto indicato.

    Restituisce solo id e name (sufficienti per messaggi di errore).
    """
    rows = _fetchall(
        "SELECT id, name FROM eseguibili WHERE source_canvas_id = %s",
        (source_canvas_id,)
    )
    return [eseguibile_source_ref_row_from_db(row) for row in rows]


def mark_temp_file_source_ttl(file_source_id: str) -> None:
    """Marca un file_source temporaneo creato dal runtime eseguibili."""
    _execute(
        """
        UPDATE file_sources
        SET is_temp = true,
            expires_at = now() + INTERVAL '24 hours'
        WHERE id = %s
        """,
        (file_source_id,),
    )


def create_eseguibile_version(
    *,
    eseguibile_id: str,
    canvas: JsonObject,
    manifest: JsonObject,
    project_type: str,
    source_project_id: str | None,
    created_by: str,
) -> EseguibileVersionRow:
    source_hash = canvas_fingerprint(canvas)
    now = _now()
    with unit_of_work() as session:
        parent = session.fetchone(
            "SELECT id FROM eseguibili WHERE id = %s FOR UPDATE",
            (eseguibile_id,),
        )
        if not parent:
            raise ValueError("Eseguibile non trovato")
        latest = session.fetchone(
            "SELECT COALESCE(MAX(version_number), 0) AS n "
            "FROM eseguibile_versions WHERE eseguibile_id = %s",
            (eseguibile_id,),
        )
        version_number = int((latest or {}).get("n") or 0) + 1
        version_id = f"{eseguibile_id}_v{version_number}_{uuid.uuid4().hex[:6]}"
        session.execute(
            """
            INSERT INTO eseguibile_versions (
                id, eseguibile_id, version_number, schema_version, project_type,
                source_project_id, source_hash, canvas, manifest, status,
                created_by, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'published', %s, %s)
            """,
            (
                version_id,
                eseguibile_id,
                version_number,
                int(manifest.get("schema_version") or 2),
                project_type,
                source_project_id,
                source_hash,
                json.dumps(canvas, ensure_ascii=False),
                json.dumps(manifest, ensure_ascii=False),
                created_by,
                now,
            ),
        )
        row = session.fetchone(
            "SELECT * FROM eseguibile_versions WHERE id = %s",
            (version_id,),
        )
    if not row:
        raise RuntimeError(f"Versione eseguibile '{version_id}' non trovata dopo insert")
    return eseguibile_version_row_from_db(row)


def get_eseguibile_version(
    version_id: str,
    *,
    eseguibile_id: str | None = None,
) -> EseguibileVersionRow | None:
    if eseguibile_id:
        row = _fetchone(
            "SELECT * FROM eseguibile_versions WHERE id = %s AND eseguibile_id = %s",
            (version_id, eseguibile_id),
        )
    else:
        row = _fetchone("SELECT * FROM eseguibile_versions WHERE id = %s", (version_id,))
    return eseguibile_version_row_from_db(row) if row else None


def get_active_eseguibile_version(
    eseguibile_id: str,
) -> EseguibileVersionRow | None:
    row = _fetchone(
        "SELECT v.* FROM eseguibile_versions v JOIN eseguibili e "
        "ON e.active_version_id = v.id WHERE e.id = %s",
        (eseguibile_id,),
    )
    return eseguibile_version_row_from_db(row) if row else None


def list_eseguibile_versions(eseguibile_id: str) -> list[EseguibileVersionRow]:
    rows = _fetchall(
        "SELECT * FROM eseguibile_versions WHERE eseguibile_id = %s "
        "ORDER BY version_number DESC",
        (eseguibile_id,),
    )
    return [eseguibile_version_row_from_db(row) for row in rows]


def activate_eseguibile_version(eseguibile_id: str, version_id: str) -> None:
    version = get_eseguibile_version(version_id, eseguibile_id=eseguibile_id)
    if version is None:
        raise ValueError("Versione eseguibile non trovata")
    file_input = next(
        (
            item
            for item in (version.manifest.get("inputs") or [])
            if isinstance(item, dict) and item.get("kind") == "file"
        ),
        None,
    )
    expected_columns = (
        file_input.get("expected_columns") or [] if file_input else []
    )
    _execute(
        "UPDATE eseguibili SET active_version_id = %s, project_type = %s, "
        "canvas = %s, expected_columns = %s, updated_at = %s WHERE id = %s",
        (
            version_id,
            version.project_type,
            json.dumps(version.canvas, ensure_ascii=False),
            json.dumps(expected_columns, ensure_ascii=False),
            _now(),
            eseguibile_id,
        ),
    )


def create_eseguibile_run(
    *,
    eseguibile_id: str,
    version_id: str,
    group_id: str,
    user_id: str,
    input_summary: JsonObject,
) -> str:
    run_id = f"erun_{uuid.uuid4().hex}"
    _execute(
        "INSERT INTO eseguibile_runs "
        "(id, eseguibile_id, version_id, group_id, user_id, status, input_summary) "
        "VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
        (
            run_id,
            eseguibile_id,
            version_id,
            group_id,
            user_id,
            json.dumps(input_summary, ensure_ascii=False),
        ),
    )
    return run_id


def bind_eseguibile_run_task(run_id: str, task_id: str) -> None:
    _execute(
        "UPDATE eseguibile_runs SET task_id = %s, status = 'running', "
        "started_at = now() WHERE id = %s",
        (task_id, run_id),
    )


def get_eseguibile_run(run_id: str) -> JsonObject | None:
    return _fetchone(
        "SELECT id, eseguibile_id, task_id, group_id, user_id, status, "
        "output_metadata, error_message, started_at, completed_at "
        "FROM eseguibile_runs WHERE id = %s",
        (run_id,),
    )


def complete_eseguibile_run(
    run_id: str,
    *,
    success: bool,
    output_metadata: JsonObject | None = None,
    error_message: str | None = None,
) -> None:
    _execute(
        "UPDATE eseguibile_runs SET status = %s, output_metadata = %s, "
        "error_message = %s, completed_at = now() WHERE id = %s",
        (
            "success" if success else "failure",
            json.dumps(output_metadata, ensure_ascii=False)
            if output_metadata is not None
            else None,
            error_message,
            run_id,
        ),
    )


def cancel_eseguibile_run(
    run_id: str,
    *,
    error_message: str = "Task annullato",
) -> None:
    """Close a cooperatively revoked executable run as cancelled."""
    _execute(
        "UPDATE eseguibile_runs SET status = 'cancelled', error_message = %s, "
        "completed_at = now() WHERE id = %s "
        "AND status IN ('pending', 'running')",
        (error_message, run_id),
    )


def update_eseguibile(eid: str, **fields: object) -> Optional[EseguibileRow]:
    """Aggiorna i campi passati (solo metadata, non canvas/expected_columns)."""
    to_update = {
        k: v
        for k, v in fields.items()
        if k in {"name", "description", "default_output_format", "default_output_name", "icon"}
        and v is not None
    }
    if not to_update:
        return get_eseguibile(eid)
    to_update["updated_at"] = _now()
    set_clause, vals = safe_set_clause("eseguibili", to_update)
    _execute(f"UPDATE eseguibili SET {set_clause} WHERE id = %s", vals + (eid,))
    return get_eseguibile(eid)


def delete_eseguibile(eid: str) -> bool:
    """Elimina un eseguibile. Ritorna True se almeno una riga è stata cancellata."""
    # db.execute non restituisce rowcount in modo uniforme, verifichiamo prima
    if get_eseguibile(eid) is None:
        return False
    _execute("DELETE FROM eseguibili WHERE id = %s", (eid,))
    return True
