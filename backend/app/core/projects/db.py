"""Domain-owned project persistence access.

This module owns project SQL, typed project rows, and row hydration for the
projects domain. Generic DB helpers remain in ``app.db``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause
from app.utils.logger import get_logger
from app.core.projects.canvas_revision import CanvasRevisionConflict, canvas_version

logger = get_logger(__name__)

__all__ = [
    "ManipolaSourceRefs",
    "ProjectGraphRefs",
    "ProjectCanvasCheckRow",
    "ProjectDetailRow",
    "ProjectDependencyRow",
    "ProjectRepository",
    "ProjectRawCanvasRow",
    "ProjectRow",
    "create_project_row",
    "delete_comparison_keys",
    "delete_project",
    "get_project_retention_n",
    "get_project_raw_canvas_row",
    "get_project_row",
    "list_manipola_source_refs",
    "list_project_canvas_check_rows",
    "list_project_graph_refs",
    "list_project_dependency_rows_using_connection",
    "list_owned_project_ids",
    "list_project_ids_for_group",
    "list_projects",
    "update_project_output_contract_row",
    "update_project_row",
    "update_project_canvas_key_atomic",
]


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string")
    return value


def _optional_str(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Malformed project DB row: {key} must be a string or null")
    return value


def _optional_datetime_or_str(
    row: Mapping[str, object],
    key: str,
) -> datetime | str | None:
    """Accetta sia `datetime` sia ISO `str` per i timestamp project.

    Divergenza intenzionale rispetto al `_optional_datetime` canonico di
    `tickets/db.py` che ritorna `datetime | None`. Motivo: psycopg2
    builds storici (pre-rimozione del custom `JSONB_AS_TEXT`
    typecaster) potevano ritornare `created_at`/`updated_at` come
    `str` ISO invece che come `datetime` aware, in funzione del
    binding driver. Il pattern stricter spaccava `from_mapping` su
    rows legacy.

    Per allineare a `_optional_datetime` canonico bisognerebbe
    garantire che il binding psycopg2 corrente restituisca SEMPRE
    `datetime` (verificato ok in produzione attuale ma non testato
    per ogni driver version supportata). Lasciamo `datetime | str`
    come back-compat policy esplicita; gli helper a valle che fanno
    `value.isoformat()` accettano entrambi (i `str` sono no-op se
    gia' ISO).
    """
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, (datetime, str)):
        return value
    raise ValueError(
        f"Malformed project DB row: {key} must be a datetime, string, or null"
    )


def _optional_int(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Malformed project DB row: {key} must be an int or null")
    return value


def _optional_bool(row: Mapping[str, object], key: str) -> bool | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Malformed project DB row: {key} must be a bool or null")
    return value


def _normalize_json_column(
    row: Mapping[str, object],
    key: str,
    *,
    fallback: object,
) -> object:
    """Hydrate JSON/JSONB columns into Python objects with fallback.

    Psycopg2 può ritornare colonne JSONB come dict (typecaster attivo) o
    come str (rows persistite prima del cambio typecaster, oppure
    psycopg2 build con `JSONB_AS_TEXT`). Questo helper accetta entrambe
    e fallisce soft su payload corrotto, allineato al pattern di
    `ProjectRepository.get_project_row`. Usato dai `from_mapping`
    cosi' un consumer che istanzia `ProjectDetailRow` o
    `ProjectCanvasCheckRow` da una mapping qualsiasi (test, replay,
    list query non-detail) ottiene sempre `canvas`/`output_contract`
    come dict/None, non come str.
    """
    value = row.get(key)
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Corrupted JSON column %s in DB row %s: %s. Falling back.",
                key, row.get("id"), exc,
            )
            return fallback
    return fallback


def _fetchone(sql: str, params: tuple = ()) -> Mapping[str, object] | None:
    return fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    return fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    return execute(sql, params)


def fetchone(sql: str, params: tuple = ()) -> Mapping[str, object] | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def delete_comparison_keys(canvas_id: str, node_id: str | None = None) -> None:
    """Delete comparison-key config rows owned by a project canvas."""
    project_repository.delete_comparison_keys(canvas_id, node_id=node_id)


@dataclass(frozen=True)
class ProjectRow:
    """Read model for the project list row shape.

    Include `description` per mantenere coerenti list e detail metadata.
    """

    id: str
    group_id: str | None
    name: str
    project_type: str
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None
    description: str | None = None
    canvas_validated: bool = False
    tags: object | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectRow":
        return cls(
            id=_required_str(row, "id"),
            group_id=_optional_str(row, "group_id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            connection_id=_optional_str(row, "connection_id"),
            created_at=_optional_datetime_or_str(row, "created_at"),
            updated_at=_optional_datetime_or_str(row, "updated_at"),
            created_by=_optional_str(row, "created_by"),
            description=_optional_str(row, "description"),
            canvas_validated=_optional_bool(row, "canvas_validated") or False,
            tags=_normalize_json_column(row, "tags", fallback=[]),
        )


@dataclass(frozen=True)
class ProjectDetailRow:
    """Read model for the full project row shape."""

    id: str
    group_id: str | None
    name: str
    project_type: str
    canvas: object
    connection_id: str | None
    created_at: datetime | str | None
    updated_at: datetime | str | None
    created_by: str | None
    output_contract: object | None = None
    description: str | None = None
    retention_n: int | None = None
    canvas_validated: bool = False
    tags: object | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDetailRow":
        return cls(
            id=_required_str(row, "id"),
            group_id=_optional_str(row, "group_id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas") or '{"nodes": [], "edges": []}',
            output_contract=_normalize_json_column(
                row, "output_contract", fallback=None
            ),
            connection_id=_optional_str(row, "connection_id"),
            created_at=_optional_datetime_or_str(row, "created_at"),
            updated_at=_optional_datetime_or_str(row, "updated_at"),
            created_by=_optional_str(row, "created_by"),
            description=_optional_str(row, "description"),
            retention_n=_optional_int(row, "retention_n"),
            canvas_validated=_optional_bool(row, "canvas_validated") or False,
            tags=_normalize_json_column(row, "tags", fallback=[]),
        )


@dataclass(frozen=True)
class ProjectDependencyRow:
    """Read model for lightweight project dependency checks."""

    id: str
    name: str
    project_type: str
    group_id: str | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectDependencyRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            group_id=_optional_str(row, "group_id"),
        )


@dataclass(frozen=True)
class ProjectCanvasCheckRow:
    """Read model for datasource cascade checks over persisted canvases."""

    id: str
    name: str
    project_type: str
    canvas: object
    group_id: str | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectCanvasCheckRow":
        return cls(
            id=_required_str(row, "id"),
            name=_required_str(row, "name"),
            project_type=_required_str(row, "project_type"),
            canvas=row.get("canvas") or '{"nodes": [], "edges": []}',
            group_id=_optional_str(row, "group_id"),
        )


@dataclass(frozen=True)
class ManipolaSourceRefs:
    """Sorgenti di un progetto Manipola lette dal DB (UI-1, C24)."""

    project_id: str
    source_project_ids: tuple[str, ...]
    file_source_ids: tuple[str, ...]
    corrupted: bool


@dataclass(frozen=True)
class ProjectGraphRefs:
    """Collegamenti di un progetto letti dal DB, senza canvas (home per
    catene, 09/11/2026): da dove legge e dove scrive."""

    project_id: str
    source_project_ids: tuple[str, ...]
    file_source_ids: tuple[str, ...]
    # Connessioni lette dai nodi: (id, "db" | "arcgis").
    connections: tuple[tuple[str, str], ...]
    # Data Source letti: (id, nome salvato nel nodo).
    datasources: tuple[tuple[str, str], ...]
    # Destinazioni di Carica: "db", "rest", "arcgis", "datasource".
    loads: tuple[str, ...]
    # Tabelle lette o scritte (nodi tabella e Carica su database), per la ricerca.
    tables: tuple[str, ...]
    corrupted: bool


@dataclass(frozen=True)
class ProjectRawCanvasRow:
    """Typed raw canvas read used by recovery/debug service paths."""

    id: str
    canvas: object

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ProjectRawCanvasRow":
        return cls(
            id=_required_str(row, "id"),
            canvas=row.get("canvas"),
        )


def _project_rows(rows: Iterable[Mapping[str, object]]) -> list[ProjectRow]:
    return [ProjectRow.from_mapping(row) for row in rows]


def _project_dependency_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[ProjectDependencyRow]:
    return [ProjectDependencyRow.from_mapping(row) for row in rows]


def _json_str_list(value: object) -> list[str]:
    """Una colonna jsonb (lista o testo JSON) come lista di stringhe."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v not in (None, "")]


def _project_canvas_check_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[ProjectCanvasCheckRow]:
    return [ProjectCanvasCheckRow.from_mapping(row) for row in rows]



# Sorgenti dei progetti Manipola (GET /projects; UI-1 C24, conferma A-K1, F9,
# collaudo live L1). Regole:
#  - la colonna `canvas` e' `jsonb` nelle migration (0000_bootstrap) e `TEXT`
#    nello schema storico (`app/db/config_schema.sql`): `pg_typeof` sceglie
#    riga per riga. Con jsonb il documento e' la colonna stessa (`canvas::jsonb`
#    non converte nulla): nessuna serializzazione ne' rilettura del canvas.
#    Prima (42563405) ogni canvas passava da `canvas::text` (serializzato),
#    `pg_input_is_valid` (letto) e `::jsonb` (letto di nuovo): 154 ms contro
#    18 ms su 20 canvas da 75 kB, misurati su PostgreSQL 17 (L1);
#  - solo un canvas TEXT si controlla (`pg_input_is_valid(text, ...)`; la
#    versione jsonb non esiste): illeggibile -> `corrupted`, la query non
#    fallisce; un JSON che non e' un oggetto non ha nodi (nessuna sorgente);
#  - costo lineare (F9): i nodi di ogni progetto si scorrono una volta, in un
#    LATERAL con due aggregati FILTER.
# `CASE` valuta solo il ramo scelto: con una colonna TEXT `canvas::jsonb` non
# si esegue mai (verificato su PostgreSQL vero, colonne jsonb e text:
# tests2/contract/test_ui_conferma_data_cache_contract.py).
MANIPOLA_SOURCE_REFS_SQL = """
    WITH p AS (
        SELECT id,
               CASE WHEN pg_typeof(canvas) = 'jsonb'::regtype THEN canvas::jsonb
                    WHEN pg_input_is_valid(canvas::text, 'jsonb') THEN canvas::text::jsonb
               END AS doc
        FROM projects
        WHERE project_type = 'manipola' AND id IN ({placeholders})
    )
    SELECT p.id,
           p.doc IS NULL AS corrupted,
           COALESCE(refs.source_project_ids, '[]'::jsonb) AS source_project_ids,
           COALESCE(refs.file_source_ids, '[]'::jsonb) AS file_source_ids
    FROM p
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(COALESCE(node -> 'data' ->> 'source_project_id',
                                  node ->> 'source_project_id'))
                   FILTER (WHERE node ->> 'type' = 'project_source'
                             AND COALESCE(node -> 'data' ->> 'source_project_id',
                                          node ->> 'source_project_id', '') <> '')
                   AS source_project_ids,
               jsonb_agg(node ->> 'file_source_id')
                   FILTER (WHERE node ->> 'type' = 'file_source'
                             AND COALESCE(node ->> 'file_source_id', '') <> '')
                   AS file_source_ids
        FROM jsonb_array_elements(
                 CASE WHEN jsonb_typeof(p.doc -> 'nodes') = 'array'
                      THEN p.doc -> 'nodes' ELSE '[]'::jsonb END
             ) AS node
    ) AS refs ON true
"""


# Collegamenti di OGNI progetto per la home per catene (09/11/2026): stesse
# regole di MANIPOLA_SOURCE_REFS_SQL (colonna jsonb o TEXT scelta riga per
# riga, canvas TEXT illeggibile -> `corrupted`, nodi scorsi una volta in un
# LATERAL), per tutti i tipi di progetto. Il tipo del nodo e' normalizzato
# (alias del registro dei nodi del client: tableNode, featureLayerNode, ...);
# ogni campo si legge in `data` o in cima al nodo, come la query Manipola.
# Il SELECT finale restituisce solo id e liste: nessun canvas verso Python.
def _graph_node_field(name: str) -> str:
    return f"COALESCE(node -> 'data' ->> '{name}', node ->> '{name}', '')"


PROJECT_GRAPH_REFS_SQL = f"""
    WITH p AS (
        SELECT id,
               CASE WHEN pg_typeof(canvas) = 'jsonb'::regtype THEN canvas::jsonb
                    WHEN pg_input_is_valid(canvas::text, 'jsonb') THEN canvas::text::jsonb
               END AS doc
        FROM projects
        WHERE id IN ({{placeholders}})
    )
    SELECT p.id,
           p.doc IS NULL AS corrupted,
           COALESCE(refs.source_project_ids, '[]'::jsonb) AS source_project_ids,
           COALESCE(refs.file_source_ids, '[]'::jsonb) AS file_source_ids,
           COALESCE(refs.connections, '[]'::jsonb) AS connections,
           COALESCE(refs.datasources, '[]'::jsonb) AS datasources,
           COALESCE(refs.loads, '[]'::jsonb) AS loads,
           COALESCE(refs.tables, '[]'::jsonb) AS tables
    FROM p
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(DISTINCT to_jsonb(n.source_project_id))
                   FILTER (WHERE n.kind = 'project_source' AND n.source_project_id <> '')
                   AS source_project_ids,
               jsonb_agg(DISTINCT to_jsonb(n.file_source_id))
                   FILTER (WHERE n.kind = 'file_source' AND n.file_source_id <> '')
                   AS file_source_ids,
               jsonb_agg(DISTINCT jsonb_build_array(n.connection_id,
                   CASE WHEN n.kind = 'feature_source' THEN 'arcgis' ELSE 'db' END))
                   FILTER (WHERE n.kind IN ('source', 'feature_source') AND n.connection_id <> '')
                   AS connections,
               jsonb_agg(DISTINCT jsonb_build_array(n.datasource_id, n.datasource_name))
                   FILTER (WHERE n.kind = 'datasource_node' AND n.ds_mode <> 'carica'
                             AND n.datasource_id <> '')
                   AS datasources,
               jsonb_agg(DISTINCT to_jsonb(CASE
                   WHEN n.kind = 'carica' THEN CASE WHEN n.dest_type = 'rest' THEN 'rest' ELSE 'db' END
                   WHEN n.kind = 'carica_arcgis' THEN 'arcgis'
                   ELSE 'datasource' END))
                   FILTER (WHERE n.kind IN ('carica', 'carica_arcgis')
                             OR (n.kind = 'datasource_node' AND n.ds_mode = 'carica'))
                   AS loads,
               jsonb_agg(DISTINCT to_jsonb(n.table_name))
                   FILTER (WHERE n.kind IN ('source', 'carica') AND n.table_name <> '')
                   AS tables
        FROM (
            SELECT CASE node ->> 'type'
                       WHEN 'projectSource' THEN 'project_source'
                       WHEN 'fileSource' THEN 'file_source'
                       WHEN 'tableNode' THEN 'source'
                       WHEN 'featureLayerNode' THEN 'feature_source'
                       WHEN 'datasourceNode' THEN 'datasource_node'
                       WHEN 'caricaArcgis' THEN 'carica_arcgis'
                       ELSE node ->> 'type'
                   END AS kind,
                   {_graph_node_field('source_project_id')} AS source_project_id,
                   {_graph_node_field('file_source_id')} AS file_source_id,
                   {_graph_node_field('connection_id')} AS connection_id,
                   {_graph_node_field('datasource_id')} AS datasource_id,
                   {_graph_node_field('datasource_name')} AS datasource_name,
                   COALESCE(NULLIF({_graph_node_field('mode')}, ''), 'source') AS ds_mode,
                   {_graph_node_field('dest_type')} AS dest_type,
                   {_graph_node_field('table_name')} AS table_name
            FROM jsonb_array_elements(
                     CASE WHEN jsonb_typeof(p.doc -> 'nodes') = 'array'
                          THEN p.doc -> 'nodes' ELSE '[]'::jsonb END
                 ) AS node
        ) AS n
    ) AS refs ON true
"""


def _json_pairs(value: object) -> tuple[tuple[str, str], ...]:
    """Una colonna jsonb di coppie [a, b] come tuple ordinate di stringhe."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, list) and len(item) == 2 and item[0] not in (None, ""):
            out.append((str(item[0]), "" if item[1] is None else str(item[1])))
    return tuple(sorted(out))


class ProjectRepository:
    """Repository boundary for project-owned SQL persistence."""

    def delete_comparison_keys(
        self,
        canvas_id: str,
        node_id: str | None = None,
    ) -> None:
        if node_id:
            _execute(
                "DELETE FROM comparison_keys WHERE canvas_id = %s AND node_id = %s",
                (canvas_id, node_id),
            )
        else:
            _execute("DELETE FROM comparison_keys WHERE canvas_id = %s", (canvas_id,))

    def get_project_row(
        self,
        project_id: str,
        group_id: str | None = None,
    ) -> ProjectDetailRow | None:
        if group_id:
            row = _fetchone(
                "SELECT * FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            row = _fetchone("SELECT * FROM projects WHERE id = %s", (project_id,))
        if not row:
            return None
        # Costruisci un dict mutabile: `RealDictRow` puo' essere read-only
        # a seconda della versione/configurazione psycopg.
        normalized = dict(row)
        canvas = normalized.get("canvas")
        if canvas:
            # psycopg2 auto-deserializes JSONB columns into Python dicts after
            # the custom JSONB_AS_TEXT typecaster was removed by the
            # "Harden PostgreSQL boundary" commit. Older rows persisted as
            # text still come back as str. Accept both transparently.
            if isinstance(canvas, (dict, list)):
                normalized["canvas"] = canvas
            else:
                try:
                    normalized["canvas"] = json.loads(canvas)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "Corrupted canvas in DB for project %s: %s. "
                        "Falling back to empty canvas.",
                        project_id,
                        exc,
                    )
                    normalized["canvas"] = {"nodes": [], "edges": []}
        contract = normalized.get("output_contract")
        if contract:
            if isinstance(contract, (dict, list)):
                normalized["output_contract"] = contract
            else:
                try:
                    normalized["output_contract"] = json.loads(contract)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "Corrupted output_contract in DB for project %s: %s. "
                        "Falling back to null contract.",
                        project_id,
                        exc,
                    )
                    normalized["output_contract"] = None
        return ProjectDetailRow.from_mapping(normalized)

    def get_project_raw_canvas_row(
        self,
        project_id: str,
        group_id: str | None = None,
    ) -> ProjectRawCanvasRow | None:
        if group_id:
            row = _fetchone(
                "SELECT id, canvas FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            row = _fetchone(
                "SELECT id, canvas FROM projects WHERE id = %s",
                (project_id,),
            )
        return ProjectRawCanvasRow.from_mapping(row) if row else None

    def list_projects(
        self,
        group_id: str | None = None,
        project_type: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProjectRow]:
        # Helper centralizzato per evitare drift fra i 4 SELECT del list.
        _COLS = (
            "id, group_id, name, project_type, connection_id, "
            "created_at, updated_at, created_by, description, canvas_validated, tags"
        )
        predicates: list[str] = []
        params: list[object] = []
        if group_id is not None:
            predicates.append("group_id = %s")
            params.append(group_id)
        if project_type:
            predicates.append("project_type = %s")
            params.append(project_type)
        where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
        paging = ""
        if limit is not None:
            paging = " LIMIT %s OFFSET %s"
            params.extend((limit, offset))
        return _project_rows(
            _fetchall(
                f"SELECT {_COLS} FROM projects{where} "
                f"ORDER BY updated_at DESC, id{paging}",
                tuple(params),
            )
        )

    def create_project_row(
        self,
        project_id: str,
        group_id: str,
        name: str,
        project_type: str,
        connection_id: str | None = None,
        created_by: str | None = None,
    ) -> ProjectDetailRow:
        # Passa datetime nativo a psycopg2: il roundtrip ritorna `datetime`
        # aware e i boundary Pydantic normalizzano datetime|str.
        now = datetime.now(timezone.utc)
        canvas = json.dumps({"nodes": [], "edges": []})
        _execute(
            "INSERT INTO projects (id, group_id, name, project_type, canvas, "
            "output_contract, connection_id, created_at, updated_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                project_id,
                group_id,
                name,
                project_type,
                canvas,
                None,
                connection_id,
                now,
                now,
                created_by,
            ),
        )
        row = get_project_row(project_id)
        if row is None:
            raise RuntimeError(
                f"Project write hydration failed for created project {project_id}"
            )
        return row

    def update_project_row(
        self,
        project_id: str,
        base_canvas_version: str | None = None,
        revision_out: dict | None = None,
        **fields: object,
    ) -> ProjectDetailRow | None:
        if "canvas" in fields and isinstance(fields["canvas"], dict):
            fields["canvas"] = json.dumps(fields["canvas"])
        if "output_contract" in fields and isinstance(fields["output_contract"], dict):
            fields["output_contract"] = json.dumps(fields["output_contract"])
        # tags è jsonb: serializza la lista (psycopg non adatta list→jsonb).
        if "tags" in fields and isinstance(fields["tags"], (list, dict)):
            fields["tags"] = json.dumps(fields["tags"])
        # Datetime nativo per coerenza con create_project_row.
        fields["updated_at"] = datetime.now(timezone.utc)
        set_clause, vals = safe_set_clause("projects", fields)
        if base_canvas_version is None:
            _execute(f"UPDATE projects SET {set_clause} WHERE id = %s", vals + (project_id,))
            if revision_out is not None:
                revision_out["updated_at"] = fields["updated_at"]
                written = get_project_raw_canvas_row(project_id)
                revision_out["canvas_version"] = canvas_version(written.canvas) if written is not None else None
            return get_project_row(project_id)
        # Versione del canvas: confronto per uguaglianza del token, nella
        # stessa transazione dell'UPDATE con la riga bloccata.
        with unit_of_work() as session:
            locked = session.fetchone("SELECT canvas, updated_at FROM projects WHERE id = %s FOR UPDATE", (project_id,))
            if locked is None:
                return None
            if base_canvas_version is not None and canvas_version(locked.get("canvas")) != base_canvas_version:
                raise CanvasRevisionConflict(canvas_version(locked.get("canvas")), locked.get("updated_at"))
            session.execute(f"UPDATE projects SET {set_clause} WHERE id = %s", vals + (project_id,))
            written = session.fetchone("SELECT canvas FROM projects WHERE id = %s", (project_id,))
        if revision_out is not None:
            revision_out["updated_at"] = fields["updated_at"]
            revision_out["canvas_version"] = canvas_version((written or {}).get("canvas"))
        return get_project_row(project_id)

    def update_project_output_contract_row(
        self,
        project_id: str,
        output_contract: Mapping[str, object],
    ) -> ProjectDetailRow | None:
        """Persist runtime output schema without touching editor updated_at."""
        _execute(
            "UPDATE projects SET output_contract = %s WHERE id = %s",
            (json.dumps(output_contract), project_id),
        )
        return get_project_row(project_id)

    def delete_project(self, project_id: str, group_id: str | None = None) -> bool:
        if group_id:
            cur = _execute(
                "DELETE FROM projects WHERE id = %s AND group_id = %s",
                (project_id, group_id),
            )
        else:
            cur = _execute("DELETE FROM projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0

    def get_project_retention_n(self, project_id: str) -> int | None:
        row = _fetchone("SELECT retention_n FROM projects WHERE id = %s", (project_id,))
        if not row:
            return None
        return _optional_int(row, "retention_n")

    def list_owned_project_ids(
        self,
        project_ids: Iterable[str],
        *,
        owner_group_id: str,
    ) -> set[str]:
        ids = {pid for pid in (project_ids or ()) if pid}
        if not ids or not owner_group_id:
            return set()
        placeholders = ",".join(["%s"] * len(ids))
        rows = _fetchall(
            f"SELECT id FROM projects WHERE id IN ({placeholders}) AND group_id = %s",
            tuple(ids) + (owner_group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_ids_for_group(self, group_id: str) -> set[str]:
        if not group_id:
            return set()
        rows = _fetchall(
            "SELECT id FROM projects WHERE group_id = %s",
            (group_id,),
        )
        return {_required_str(row, "id") for row in rows}

    def list_project_dependency_rows_using_connection(
        self,
        connection_id: str,
    ) -> list[ProjectDependencyRow]:
        return _project_dependency_rows(
            _fetchall(
                "SELECT id, name, project_type, group_id "
                "FROM projects WHERE connection_id = %s",
                (connection_id,),
            )
        )

    def list_project_canvas_check_rows(
        self,
        project_ids: set[str] | None = None,
    ) -> list[ProjectCanvasCheckRow]:
        if project_ids is not None:
            if not project_ids:
                return []
            ordered_ids = sorted(project_ids)
            placeholders = ", ".join(["%s"] * len(ordered_ids))
            return _project_canvas_check_rows(
                _fetchall(
                    f"SELECT id, name, project_type, canvas, group_id "
                    f"FROM projects WHERE id IN ({placeholders})",
                    tuple(ordered_ids),
                )
            )
        return _project_canvas_check_rows(
            _fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")
        )

    def list_manipola_source_refs(
        self,
        project_ids: set[str],
    ) -> list[ManipolaSourceRefs]:
        """Sorgenti dei progetti Manipola, calcolate nel DB (UI-1, C24).

        GET /projects mostrava i progetti sorgente leggendo il canvas intero
        di ogni progetto visibile. Qui il DB estrae solo gli id dei nodi
        `project_source` e `file_source`: nessun canvas viaggia verso
        l'applicazione. La query e' `MANIPOLA_SOURCE_REFS_SQL` (regole li').
        """
        if not project_ids:
            return []
        ordered_ids = sorted(project_ids)
        placeholders = ", ".join(["%s"] * len(ordered_ids))
        rows = _fetchall(
            MANIPOLA_SOURCE_REFS_SQL.format(placeholders=placeholders),
            tuple(ordered_ids),
        )
        return [
            ManipolaSourceRefs(
                project_id=str(row["id"]),
                source_project_ids=tuple(_json_str_list(row.get("source_project_ids"))),
                file_source_ids=tuple(_json_str_list(row.get("file_source_ids"))),
                corrupted=bool(row.get("corrupted")),
            )
            for row in rows
        ]

    def list_project_graph_refs(
        self,
        project_ids: set[str],
    ) -> list[ProjectGraphRefs]:
        """Collegamenti dei progetti calcolati nel DB (home per catene):
        `PROJECT_GRAPH_REFS_SQL` (regole li'). Nessun canvas verso Python."""
        if not project_ids:
            return []
        ordered_ids = sorted(project_ids)
        placeholders = ", ".join(["%s"] * len(ordered_ids))
        rows = _fetchall(
            PROJECT_GRAPH_REFS_SQL.format(placeholders=placeholders),
            tuple(ordered_ids),
        )
        return [
            ProjectGraphRefs(
                project_id=str(row["id"]),
                source_project_ids=tuple(sorted(_json_str_list(row.get("source_project_ids")))),
                file_source_ids=tuple(sorted(_json_str_list(row.get("file_source_ids")))),
                connections=_json_pairs(row.get("connections")),
                datasources=_json_pairs(row.get("datasources")),
                loads=tuple(sorted(_json_str_list(row.get("loads")))),
                tables=tuple(sorted(_json_str_list(row.get("tables")))),
                corrupted=bool(row.get("corrupted")),
            )
            for row in rows
        ]

    def update_project_canvas_key_atomic(
        self,
        project_id: str,
        key: str,
        value: object,
    ) -> bool:
        # Datetime nativo per coerenza con create/update project row.
        now = datetime.now(timezone.utc)
        with unit_of_work() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT canvas FROM projects WHERE id = %s FOR UPDATE",
                    (project_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                # Accesso uniforme via mapping: psycopg2 e' configurato con
                # RealDictCursor di default (vedi pg_connection).
                canvas_raw = row["canvas"]
                if isinstance(canvas_raw, str):
                    try:
                        canvas_obj = json.loads(canvas_raw) if canvas_raw else {}
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Corrupted canvas for project {project_id}: JSON is not "
                            "parseable. Refusing to overwrite it."
                        ) from exc
                elif canvas_raw is None:
                    canvas_obj = {}
                elif isinstance(canvas_raw, dict):
                    canvas_obj = canvas_raw
                else:
                    raise ValueError(
                        f"Unexpected canvas type for project {project_id}: "
                        f"{type(canvas_raw).__name__}"
                    )

                canvas_obj[key] = value
                cur.execute(
                    "UPDATE projects SET canvas = %s, updated_at = %s WHERE id = %s",
                    (json.dumps(canvas_obj), now, project_id),
                )
        return True


project_repository = ProjectRepository()


def get_project_row(
    project_id: str,
    group_id: str | None = None,
) -> ProjectDetailRow | None:
    return project_repository.get_project_row(project_id, group_id=group_id)


def get_project_raw_canvas_row(
    project_id: str,
    group_id: str | None = None,
) -> ProjectRawCanvasRow | None:
    return project_repository.get_project_raw_canvas_row(
        project_id,
        group_id=group_id,
    )


def list_projects(
    group_id: str | None = None,
    project_type: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[ProjectRow]:
    return project_repository.list_projects(
        group_id=group_id,
        project_type=project_type,
        limit=limit,
        offset=offset,
    )


def create_project_row(
    project_id: str,
    group_id: str,
    name: str,
    project_type: str,
    connection_id: str | None = None,
    created_by: str | None = None,
) -> ProjectDetailRow:
    return project_repository.create_project_row(
        project_id=project_id,
        group_id=group_id,
        name=name,
        project_type=project_type,
        connection_id=connection_id,
        created_by=created_by,
    )


def update_project_row(
    project_id: str,
    **fields: object,
) -> ProjectDetailRow | None:
    return project_repository.update_project_row(project_id, **fields)


def update_project_output_contract_row(
    project_id: str,
    output_contract: Mapping[str, object],
) -> ProjectDetailRow | None:
    return project_repository.update_project_output_contract_row(
        project_id,
        output_contract,
    )


def delete_project(project_id: str, group_id: str | None = None) -> bool:
    return project_repository.delete_project(project_id, group_id=group_id)


def get_project_retention_n(project_id: str) -> int | None:
    return project_repository.get_project_retention_n(project_id)


# ── Versioning canvas (#12) ─────────────────────────────────────────────────

def insert_canvas_version(
    version_id: str,
    project_id: str,
    group_id: str | None,
    label: str | None,
    canvas: object,
    created_by: str | None,
) -> None:
    _execute(
        "INSERT INTO project_canvas_versions "
        "(id, project_id, group_id, label, canvas, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (version_id, project_id, group_id, label,
         json.dumps(canvas) if isinstance(canvas, (dict, list)) else canvas,
         created_by),
    )


def list_canvas_versions(project_id: str) -> list[Mapping[str, object]]:
    return _fetchall(
        "SELECT id, label, created_at, created_by "
        "FROM project_canvas_versions WHERE project_id = %s "
        "ORDER BY created_at DESC",
        (project_id,),
    )


def get_canvas_version(version_id: str, project_id: str) -> Mapping[str, object] | None:
    return _fetchone(
        "SELECT id, canvas FROM project_canvas_versions "
        "WHERE id = %s AND project_id = %s",
        (version_id, project_id),
    )


def prune_canvas_versions(project_id: str, keep: int = 30) -> None:
    """Mantiene solo le `keep` versioni più recenti del progetto."""
    _execute(
        "DELETE FROM project_canvas_versions WHERE project_id = %s AND id NOT IN ("
        " SELECT id FROM project_canvas_versions WHERE project_id = %s "
        " ORDER BY created_at DESC LIMIT %s)",
        (project_id, project_id, keep),
    )


def list_owned_project_ids(
    project_ids: Iterable[str],
    *,
    owner_group_id: str,
) -> set[str]:
    return project_repository.list_owned_project_ids(
        project_ids,
        owner_group_id=owner_group_id,
    )


def list_project_ids_for_group(group_id: str) -> set[str]:
    return project_repository.list_project_ids_for_group(group_id)


def list_project_dependency_rows_using_connection(
    connection_id: str,
) -> list[ProjectDependencyRow]:
    return project_repository.list_project_dependency_rows_using_connection(
        connection_id
    )


def list_project_canvas_check_rows(
    project_ids: set[str] | None = None,
) -> list[ProjectCanvasCheckRow]:
    return project_repository.list_project_canvas_check_rows(project_ids)


def list_manipola_source_refs(project_ids: set[str]) -> list[ManipolaSourceRefs]:
    """Id delle sorgenti dei progetti Manipola, estratti dal DB (UI-1, C24)."""
    return project_repository.list_manipola_source_refs(project_ids)


def list_project_graph_refs(project_ids: set[str]) -> list[ProjectGraphRefs]:
    """Collegamenti di ogni progetto, estratti dal DB (home per catene)."""
    return project_repository.list_project_graph_refs(project_ids)


def update_project_canvas_key_atomic(project_id: str, key: str, value: object) -> bool:
    """Atomically merge one key into a project's persisted canvas."""
    return project_repository.update_project_canvas_key_atomic(project_id, key, value)
