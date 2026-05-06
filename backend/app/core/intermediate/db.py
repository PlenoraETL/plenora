"""
Intermediate DB — accesso a PostgreSQL.

Gestisce runs, node_meta e node_rows (tabella unificata JSONB per i dati
intermedi dei nodi pipeline). Usa pg_connection pool.

Nota design: in passato ogni nodo di ogni run aveva una tabella Postgres
fisica dedicata (run_{uuid}_{node_id}) — anti-pattern che causava bloat
del pg_catalog, lock contention e orphan tables. Ora tutti i dati finiscono
in plenora.node_rows con FK ON DELETE CASCADE a runs, che garantisce
retention atomica.
"""

import json
from typing import Dict, List, Optional

from psycopg2.extras import execute_values

from app.utils.logger import get_logger

# Importa da pg_connection (stesso pool di config/results)
from app.db.pg_connection import (
    fetchone, fetchall, execute, executemany,
    get_connection, put_connection, close_connection as _pg_close,
    transaction,
)

logger = get_logger(__name__)


def close_connection() -> None:
    """No-op per PostgreSQL: il pool gestisce le connessioni.

    Mantiene compatibilità con i task Celery che chiamano
    close_connection() in finally.
    """
    _pg_close()


def init_db():
    """No-op per PostgreSQL: tabelle runs/node_meta già create."""
    logger.info("Intermediate DB: PostgreSQL (tabelle già presenti)", op="init_db")


# fetchone, fetchall, execute sono importati da pg_connection (top del file)

# ============================================================
# RUNS
# ============================================================

def create_run(run_id: str, canvas_id: str, group_id: str,
               total_nodes: int = 0,
               user_id: Optional[str] = None) -> Dict:
    """Crea una nuova run in intermediate.runs.

    N9: user_id persistito per audit (chi ha lanciato la run). None per
    task cron/scheduled o per sub-chain interne (es. sub-task carica
    lanciato dall'orchestrator durante una pipeline gia' validata).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        """INSERT INTO runs
           (run_id, canvas_id, group_id, timestamp, status, total_nodes, user_id)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, canvas_id, group_id, now, "running", total_nodes, user_id)
    )
    return get_run(run_id)


def get_run(run_id: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM runs WHERE run_id = ?", (run_id,))


def list_runs(canvas_id: str, group_id: str, limit: int = 50) -> List[Dict]:
    """Lista run per canvas, isolate per gruppo, più recenti prima."""
    return fetchall(
        "SELECT * FROM runs WHERE canvas_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT ?",
        (canvas_id, group_id, limit)
    )


# Whitelist campi aggiornabili sulla tabella `runs`. Anti-injection: impedisce
# che kwargs arbitrari finiscano in un UPDATE dinamico (vedi update_run).
# Esclude run_id, canvas_id, group_id, timestamp, pinned (immutabili o gestiti
# da operazioni dedicate come pin_run).
_ALLOWED_RUN_UPDATE_FIELDS = {
    "status",
    "input_hash", "output_hash",
    "total_exec_time",
    "total_rows_in", "total_rows_out", "total_rows_dropped",
    "total_nodes", "nodes_ok", "nodes_error", "nodes_skipped",
}


def update_run(run_id: str, **fields) -> None:
    """Aggiorna i campi consentiti di una run.

    Solo i campi in `_ALLOWED_RUN_UPDATE_FIELDS` sono accettati. Solleva
    ValueError se un caller passa un campo non consentito (es. tentativo di
    cambiare canvas_id/group_id, che cambierebbe la proprietà della run).
    """
    if not fields:
        return
    invalid = set(fields) - _ALLOWED_RUN_UPDATE_FIELDS
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE runs: {invalid}. "
            f"Consentiti: {_ALLOWED_RUN_UPDATE_FIELDS}"
        )
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    execute(
        f"UPDATE runs SET {set_clause} WHERE run_id = ?",
        tuple(fields.values()) + (run_id,)
    )


def pin_run(run_id: str, group_id: Optional[str], pinned: bool = True) -> bool:
    """Pinna o spinna una run. Verifica group_id per sicurezza.

    Se `group_id` e' None, il filtro di gruppo viene omesso: usato dal
    router per superadmin, che ha gia' validato l'accesso via
    `_check_run_access`. Senza questa branch il superadmin riceverebbe
    un falso 404 su run di altri gruppi perche' l'UPDATE non matcha mai.
    """
    if group_id is None:
        cur = execute(
            "UPDATE runs SET pinned = ? WHERE run_id = ?",
            (pinned, run_id)
        )
    else:
        cur = execute(
            "UPDATE runs SET pinned = ? WHERE run_id = ? AND group_id = ?",
            (pinned, run_id, group_id)
        )
    return cur.rowcount > 0


# ============================================================
# NODE META
# ============================================================

def upsert_node_meta(run_id: str, node_id: str, node_type: str = None,
                     row_count: int = None, col_schema: dict = None,
                     exec_time: float = None, status: str = None,
                     error_msg: str = None) -> None:
    execute(
        """INSERT INTO node_meta
           (run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (run_id, node_id) DO UPDATE SET
           node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count,
           col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time,
           status=EXCLUDED.status, error_msg=EXCLUDED.error_msg""",
        (run_id, node_id, node_type, row_count,
         json.dumps(col_schema) if col_schema else None,
         exec_time, status, error_msg)
    )


# Phase 6 Batch 1 (2026-04-28): UPSERT esteso con colonne policy.
# Tenuto separato da `upsert_node_meta` legacy per evitare regressioni
# sui caller esistenti (`writer.write_node` resta full_pg).
_SQL_UPSERT_NODE_META_EXT = """
INSERT INTO node_meta (
    run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg,
    node_hash, hash_algo, persistence_mode, persisted_rows_count,
    preview_truncated, artifact_uri, artifact_format, artifact_status, artifact_error
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT (run_id, node_id) DO UPDATE SET
    node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count,
    col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time,
    status=EXCLUDED.status, error_msg=EXCLUDED.error_msg,
    node_hash=EXCLUDED.node_hash, hash_algo=EXCLUDED.hash_algo,
    persistence_mode=EXCLUDED.persistence_mode,
    persisted_rows_count=EXCLUDED.persisted_rows_count,
    preview_truncated=EXCLUDED.preview_truncated,
    artifact_uri=EXCLUDED.artifact_uri, artifact_format=EXCLUDED.artifact_format,
    artifact_status=EXCLUDED.artifact_status, artifact_error=EXCLUDED.artifact_error
"""


def upsert_node_meta_extended(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    row_count: Optional[int] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    node_hash: Optional[str] = None,
    hash_algo: Optional[str] = None,
    persistence_mode: Optional[str] = None,
    persisted_rows_count: Optional[int] = None,
    preview_truncated: Optional[bool] = None,
    artifact_uri: Optional[str] = None,
    artifact_format: Optional[str] = None,
    artifact_status: Optional[str] = None,
    artifact_error: Optional[str] = None,
) -> None:
    """UPSERT node_meta con le colonne Phase 6 Batch 1.

    Usata da `intermediate.snapshot.record_*_snapshot`. I caller legacy
    continuano a usare `upsert_node_meta` (le colonne nuove restano
    NULL = trattate come full_pg).
    """
    execute(
        _SQL_UPSERT_NODE_META_EXT,
        (
            run_id, node_id, node_type, row_count,
            json.dumps(col_schema) if col_schema else None,
            exec_time, status, error_msg,
            node_hash, hash_algo, persistence_mode, persisted_rows_count,
            preview_truncated,
            artifact_uri, artifact_format, artifact_status, artifact_error,
        ),
    )


def write_node_snapshot_atomic(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    rows_to_persist: Optional[List[Dict]] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
    row_count: Optional[int] = None,
    node_hash: Optional[str] = None,
    hash_algo: Optional[str] = None,
    persistence_mode: Optional[str] = None,
    preview_truncated: Optional[bool] = None,
    artifact_uri: Optional[str] = None,
    artifact_format: Optional[str] = None,
    artifact_status: Optional[str] = None,
    artifact_error: Optional[str] = None,
) -> None:
    """Scrittura atomica node_rows + node_meta (estesa, Batch 1).

    Differenze rispetto a `write_node_atomic` legacy:
      - separa `row_count` reale (passato esplicitamente dal writer
        che conosce la lunghezza del DataFrame originale) da
        `persisted_rows_count` (= len(rows_to_persist));
      - persiste le nuove colonne policy.

    `rows_to_persist`:
      - None / [] -> nessun INSERT in `node_rows`, ma il DELETE
        idempotente continua a girare (mode metadata_only o full_pg
        con dataset vuoto).
      - lista [{_row_hash, ...}] -> INSERT executemany (legacy
        serializzazione invariata).

    Atomicita': singola transazione, semantica simmetrica a
    `write_node_atomic`. Su exception rollback completo.
    """
    records = _build_node_row_records(run_id, node_id, rows_to_persist or [])
    persisted_rows_count = len(records)
    # row_count reale: se il caller non lo passa cadiamo sul fallback
    # legacy (count = righe persistite). Per metadata_only il caller
    # DEVE passare row_count esplicito, altrimenti perdiamo l'invariante
    # "row_count e' sempre la lunghezza reale del dataset".
    if row_count is None:
        row_count = persisted_rows_count

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)
            cur.execute(
                # _adapt_params su execute() converte ? -> %s; qui usiamo
                # la connessione PG nativa, quindi sostituiamo manualmente.
                _SQL_UPSERT_NODE_META_EXT.replace("?", "%s"),
                (
                    run_id, node_id, node_type, row_count,
                    json.dumps(col_schema) if col_schema else None,
                    exec_time, status, error_msg,
                    node_hash, hash_algo, persistence_mode,
                    persisted_rows_count, preview_truncated,
                    artifact_uri, artifact_format, artifact_status, artifact_error,
                ),
            )


def get_node_meta(run_id: str, node_id: str) -> Optional[Dict]:
    row = fetchone(
        "SELECT * FROM node_meta WHERE run_id = ? AND node_id = ?",
        (run_id, node_id)
    )
    if row and row.get("col_schema"):
        row["col_schema"] = json.loads(row["col_schema"])
    return row


def list_node_meta(run_id: str) -> List[Dict]:
    rows = fetchall("SELECT * FROM node_meta WHERE run_id = ?", (run_id,))
    for r in rows:
        if r.get("col_schema"):
            r["col_schema"] = json.loads(r["col_schema"])
    return rows


# ============================================================
# NODE ROWS — tabella unificata JSONB (ex tabelle dinamiche)
# ============================================================
#
# Design precedente (rimosso): una tabella Postgres fisica per ogni nodo di
# ogni run, naming convention run_{uuid}_{node_id}. Causava:
#   - bloat del pg_catalog (una riga in pg_class / pg_attribute per ogni col)
#   - AccessExclusiveLock su pg_class ad ogni CREATE/DROP → contesa
#   - orphan tables se drop_run_tables falliva
#   - impossibile query cross-run (schemi potenzialmente diversi)
#
# Design corrente: una singola tabella plenora.node_rows con row_data jsonb.
# La FK run_id → plenora.runs(run_id) ON DELETE CASCADE garantisce retention
# atomica gratuita: basta DELETE FROM runs WHERE run_id = ? per pulire tutto.
# ============================================================


# ============================================================
# F-DATA-01 / P1-04: SENTINEL per NaN/Inf in JSONB
# ============================================================
#
# JSON strict non ammette NaN / +Inf / -Inf. Pre-fix `_to_jsonable` li
# mappava a None: divergenza fantasma con `compute_row_hash` (che li
# preserva come "nan" / "+inf" / "-inf" via `_format_float_stable`).
# Reload da `node_rows.row_data` perdeva la distinzione e ricalcolare
# l'hash dei dati ricostruiti dava un hash diverso da quello stored.
#
# Fix simmetrico via sentinel object JSONB namespaced. Sentinel string
# semplici (es. "__nan__") sarebbero rischiosi: un valore utente reale
# uguale alla stringa sentinel verrebbe convertito a NaN al reverse,
# corrompendo dati legittimi. Usiamo invece un dict di forma esatta:
#
#     {"__plenora_special_float__": "nan"}
#     {"__plenora_special_float__": "+inf"}
#     {"__plenora_special_float__": "-inf"}
#
# Il reverse mapping converte SOLO dict con esattamente quella chiave
# (singola, valore in {"nan","+inf","-inf"}). Stringhe omonime e dict
# utente con altre chiavi restano invariati. Hash storico invariato
# (writer.py NON e' toccato).
#
# Compat: dati pre-fix scritti come `null` continuano a venire letti
# come `None` (il reverse tocca solo dict di forma esatta). Nessuna
# migration richiesta.

_SPECIAL_FLOAT_KEY = "__plenora_special_float__"
_SPECIAL_FLOAT_TAG_NAN = "nan"
_SPECIAL_FLOAT_TAG_INF_POS = "+inf"
_SPECIAL_FLOAT_TAG_INF_NEG = "-inf"


def _make_special_float_sentinel(tag: str) -> dict:
    """Costruisce il dict-sentinel canonico per NaN/+Inf/-Inf."""
    return {_SPECIAL_FLOAT_KEY: tag}


def _to_jsonable(v):
    """Normalizza un valore per la serializzazione JSONB.

    JSONB non accetta nativamente: datetime, date, Decimal, bytes, geometrie
    Shapely, pandas Timestamp, numpy scalar. Questa funzione li converte in
    tipi primitivi JSON-compatibili (str, int, float, bool, None, list, dict).

    NaN / +Inf / -Inf sono mappati a un dict-sentinel namespaced
    (`{"__plenora_special_float__": "nan"|"+inf"|"-inf"}`) per allineamento
    con il row hash (vedi commento sezione sopra). La forma object-namespaced
    evita la collisione con valori utente uguali a stringhe sentinel.
    """
    if v is None:
        return None
    # datetime / date / pandas Timestamp
    if hasattr(v, "isoformat"):
        return v.isoformat()
    # decimal.Decimal
    import decimal
    if isinstance(v, decimal.Decimal):
        return float(v)
    # Geometrie Shapely -> WKT (deve venire prima dei check numerici perche'
    # "Point"/"Polygon" hanno __name__ che contengono "int")
    try:
        from shapely.geometry.base import BaseGeometry
        if isinstance(v, BaseGeometry):
            return v.wkt
    except ImportError:
        pass
    # numpy / pandas scalar
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        # NaN / Inf non sono validi in JSON strict: usiamo dict-sentinel
        # namespaced invece di None, cosi' il reverse mapping ricostruisce
        # esattamente il float originale e l'hash recompute matcha lo
        # stored (F-DATA-01).
        if v != v:  # NaN: distinto da +Inf/-Inf perche' nan != nan
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_NAN)
        if v == float("inf"):
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_INF_POS)
        if v == float("-inf"):
            return _make_special_float_sentinel(_SPECIAL_FLOAT_TAG_INF_NEG)
        return float(v)
    # bytes/bytearray → base64 o hex-repr; per intermediate usiamo un
    # placeholder lungo quanto servono sapere bytes_len
    if isinstance(v, (bytes, bytearray)):
        return f"bytes:{len(v)}"
    # list/dict: ricorsione
    if isinstance(v, list):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_jsonable(val) for k, val in v.items()}
    return str(v)


def _from_jsonable_value(v):
    """Reverse mapping di `_to_jsonable` per i sentinel NaN/Inf.

    Converte SOLO dict di forma ESATTA
    `{_SPECIAL_FLOAT_KEY: "nan"|"+inf"|"-inf"}` (singola chiave, valore
    nel set noto) in float speciale. Tutto il resto passa invariato:
      - stringhe omonime ai vecchi sentinel (es. "__nan__") restano stringhe
      - dict utente che includono `_SPECIAL_FLOAT_KEY` ma con altre chiavi
        o tag sconosciuti vengono ricorsi come dict normali
      - list/dict propagano la trasformazione ricorsivamente
      - `None`, numeri, bool, stringhe non-sentinel: invariati

    Compat: dati pre-fix scritti come `null` JSONB restano `None`.
    """
    # Dict-sentinel: forma esatta, singola chiave, tag noto.
    if (
        isinstance(v, dict)
        and len(v) == 1
        and _SPECIAL_FLOAT_KEY in v
    ):
        tag = v[_SPECIAL_FLOAT_KEY]
        if tag == _SPECIAL_FLOAT_TAG_NAN:
            return float("nan")
        if tag == _SPECIAL_FLOAT_TAG_INF_POS:
            return float("inf")
        if tag == _SPECIAL_FLOAT_TAG_INF_NEG:
            return float("-inf")
        # Tag sconosciuto: NON consumiamo il dict, lo trattiamo come
        # dato utente normale e ricorriamo (sotto).
    if isinstance(v, list):
        return [_from_jsonable_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _from_jsonable_value(val) for k, val in v.items()}
    return v


# A3 (Phase 4): SQL costanti riusate da insert_node_rows + write_node_atomic.
# Il placeholder e' %s diretto (non ?): scriviamo via cursor PG nativo, fuori
# dal wrapper _adapt_params di pg_connection. Tenere allineati questi due nomi
# e' importante perche' i contract test sotto verificano testualmente che il
# DELETE preceda l'INSERT.
_SQL_DELETE_NODE_ROWS = (
    "DELETE FROM node_rows WHERE run_id = %s AND node_id = %s"
)
_SQL_INSERT_NODE_ROW = (
    "INSERT INTO node_rows (run_id, node_id, row_idx, row_hash, row_data) "
    "VALUES (%s, %s, %s, %s, %s::jsonb)"
)

# Bug #22 (perf, 2026-04-28): SQL e template per insert batch via
# `psycopg2.extras.execute_values`. Pre-fix: `cur.executemany(...)` su
# 500 record preview faceva 500 round-trip al server (4–8s misurati).
# Post-fix: 1 sola INSERT multi-VALUES, ~50–300ms.
# - `_SQL_INSERT_NODE_ROWS_HEAD`: prefisso INSERT condiviso da
#   execute_values; il placeholder `VALUES %s` viene espanso da
#   psycopg2 nelle tuple del template.
# - `_INSERT_NODE_ROW_TEMPLATE`: cast `::jsonb` esplicito preservato
#   verbatim dal singolo-row pattern. Senza, psycopg2 manda `row_data`
#   come stringa e PG la persiste come text non-jsonb.
_SQL_INSERT_NODE_ROWS_HEAD = (
    "INSERT INTO node_rows (run_id, node_id, row_idx, row_hash, row_data) "
    "VALUES %s"
)
_INSERT_NODE_ROW_TEMPLATE = "(%s, %s, %s, %s, %s::jsonb)"


def _bulk_insert_node_rows(cur, records: List[tuple]) -> None:
    """Bulk insert via `psycopg2.extras.execute_values`.

    Wrapper unico riusato dai 3 call site (`write_node_snapshot_atomic`,
    `insert_node_rows`, `write_node_atomic`). No-op se `records` e' vuoto:
    coerente con il branch legacy `if records: cur.executemany(...)`.

    `page_size=1000`: cap sicuro su payload INSERT (preview default = 500,
    full_pg legacy puo' essere maggiore). Sopra 1000 psycopg2 split in
    piu' INSERT, mantenendo deterministico l'ordine.
    """
    if not records:
        return
    execute_values(
        cur,
        _SQL_INSERT_NODE_ROWS_HEAD,
        records,
        template=_INSERT_NODE_ROW_TEMPLATE,
        page_size=1000,
    )
_SQL_UPSERT_NODE_META = (
    "INSERT INTO node_meta "
    "(run_id, node_id, node_type, row_count, col_schema, exec_time, status, error_msg) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (run_id, node_id) DO UPDATE SET "
    "node_type=EXCLUDED.node_type, row_count=EXCLUDED.row_count, "
    "col_schema=EXCLUDED.col_schema, exec_time=EXCLUDED.exec_time, "
    "status=EXCLUDED.status, error_msg=EXCLUDED.error_msg"
)


def _build_node_row_records(
    run_id: str, node_id: str, rows: List[Dict],
) -> List[tuple]:
    """Materializza la lista (run_id, node_id, row_idx, row_hash, row_data_json).

    Estratto per condividere la logica fra insert_node_rows (idempotent
    standalone) e write_node_atomic (idempotent + meta in singola tx).
    Comportamento serializzazione invariato: row_hash da `_row_hash`,
    row_data normalizzato via `_to_jsonable` (NaN/Inf sentinel inclusi).
    """
    records: List[tuple] = []
    for idx, row in enumerate(rows or []):
        row_hash = row.get("_row_hash", "")
        # row_data = tutti i valori della riga ECCETTO _row_hash
        row_data = {
            k: _to_jsonable(v) for k, v in row.items() if k != "_row_hash"
        }
        records.append((
            run_id, node_id, idx, row_hash,
            json.dumps(row_data, ensure_ascii=False),
        ))
    return records


def insert_node_rows(run_id: str, node_id: str,
                     rows: List[Dict], col_schema: dict) -> None:
    """Bulk insert IDEMPOTENTE delle righe di un nodo in plenora.node_rows.

    A3 (Phase 4) idempotency: la funzione DELETE pre-esistenti righe per
    (run_id, node_id) PRIMA dell'INSERT, dentro una singola transazione.
    Pre-fix una doppia invocazione (es. retry Celery con `acks_late=True`)
    produceva duplicati silenti, perche' lo schema `node_rows` non ha
    UNIQUE su (run_id, node_id, row_idx) e l'INSERT era plain.
    Post-fix invocazioni N >= 2 sullo stesso (run_id, node_id) lasciano
    sempre solo l'ultima scrittura.

    Caso `rows=[]`: la funzione esegue comunque il DELETE per evitare
    inconsistenza con node_meta.row_count=0 dopo un retry "vuoto".

    Args:
        run_id: uuid della run
        node_id: id del nodo nel canvas
        rows: lista di dict dove ogni dict contiene '_row_hash' + i valori
              della riga (gia' calcolati dal writer). Il row_hash viene
              estratto e messo in una colonna dedicata; il resto finisce in
              row_data (jsonb).
        col_schema: {"col_name": "dtype_str", ...} — ignorato dal nuovo
              design ma mantenuto nella signature per compat col writer.

    Nota atomicita': insert_node_rows e' atomico per se' stesso (DELETE +
    INSERT in singola tx), ma NON copre la successiva scrittura di
    node_meta. Per atomicita' end-to-end usare `write_node_atomic`.
    """
    records = _build_node_row_records(run_id, node_id, rows)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)


def write_node_atomic(
    run_id: str,
    node_id: str,
    *,
    node_type: Optional[str] = None,
    rows: Optional[List[Dict]] = None,
    col_schema: Optional[dict] = None,
    exec_time: Optional[float] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> None:
    """Scrittura atomica e idempotente di node_rows + node_meta.

    A3 (Phase 4): unica transazione che:
      1. DELETE eventuali righe pre-esistenti per (run_id, node_id) — idempotency
      2. INSERT tutte le nuove righe (executemany)
      3. INSERT ... ON CONFLICT DO UPDATE su node_meta

    Pre-fix `writer.write_node` chiamava `insert_node_rows` + `upsert_node_meta`
    in DUE transazioni separate. Se la seconda falliva (DB pool exhausted,
    psycopg2.OperationalError, ecc.), node_rows era gia' committato e
    node_meta restava al valore della run precedente — divergenza permanente
    fra row_count meta e count effettivo di node_rows.

    Post-fix: tutto succede su una singola conn dentro `transaction()`. Su
    qualsiasi exception il rollback rimuove sia il DELETE che gli INSERT
    (incluso l'upsert meta), preservando lo stato pre-call.

    Idempotency garantita anche su rows vuoto: il DELETE evicts pre-esistenti.

    Args:
        run_id, node_id: chiave logica
        rows: lista [{_row_hash, ...col}] gia' arricchita dal writer
        col_schema: schema usato anche per node_meta.col_schema (JSON)
        node_type, exec_time, status, error_msg: campi node_meta

    Note:
      - row_data format invariato (passa per _to_jsonable / sentinel NaN/Inf)
      - row_hash invariato (il writer lo calcola, qui lo persistiamo verbatim)
      - signature kw-only per evitare confusione posizionale con upsert_node_meta
    """
    records = _build_node_row_records(run_id, node_id, rows or [])
    meta_params = (
        run_id, node_id, node_type,
        len(records),
        json.dumps(col_schema) if col_schema else None,
        exec_time, status, error_msg,
    )

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_NODE_ROWS, (run_id, node_id))
            _bulk_insert_node_rows(cur, records)
            cur.execute(_SQL_UPSERT_NODE_META, meta_params)


def get_node_table_rows(run_id: str, node_id: str,
                        group_id: Optional[str], limit: int = None) -> List[Dict]:
    """Legge le righe di un nodo da plenora.node_rows.

    Verifica ownership via runs.group_id. Ritorna una lista di dict nel
    formato del DataFrame originale (cioe' row_data espanso + _row_hash).

    Se `group_id` e' None il filtro di gruppo viene omesso: usato dal
    router per superadmin, che ha gia' validato l'accesso via
    `_check_run_access` prima di chiamare questa funzione. Senza questa
    branch il superadmin riceverebbe sempre lista vuota per run di altri
    gruppi perche' il WHERE group_id = ? non matcha.
    """
    # Verifica ownership — skippata se group_id None (caller superadmin)
    if group_id is None:
        run = fetchone("SELECT run_id FROM runs WHERE run_id = ?", (run_id,))
    else:
        run = fetchone(
            "SELECT run_id FROM runs WHERE run_id = ? AND group_id = ?",
            (run_id, group_id)
        )
    if not run:
        return []

    sql = (
        "SELECT row_hash, row_data FROM node_rows "
        "WHERE run_id = ? AND node_id = ? ORDER BY row_idx"
    )
    params: tuple = (run_id, node_id)
    if limit:
        sql += " LIMIT ?"
        params = params + (int(limit),)

    raw_rows = fetchall(sql, params)

    # Ricostruisci il formato originale: _row_hash + colonne row_data
    # (row_data arriva già come dict da psycopg2 per JSONB).
    # F-DATA-01 / P1-04: applichiamo `_from_jsonable_value` per
    # ricostruire NaN / +Inf / -Inf dai sentinel persistiti in JSONB.
    # Hash recompute sui dati ricostruiti matcha l'hash originale stored.
    result = []
    for r in raw_rows:
        row_data = r.get("row_data") or {}
        if isinstance(row_data, str):
            # Safety: se arriva come stringa JSON invece che dict
            row_data = json.loads(row_data)
        row_data = {k: _from_jsonable_value(v) for k, v in row_data.items()}
        result.append({"_row_hash": r.get("row_hash", ""), **row_data})
    return result


# ============================================================
# RETENTION POLICY
# ============================================================

DEFAULT_RUNS_RETENTION = 5


def apply_runs_retention(canvas_id: str, group_id: str) -> None:
    """
    Applica retention FIFO sulle run del canvas.
    Conserva le N più recenti non pinnate, elimina le più vecchie.
    N viene letto da projects.retention_n (config.db), default DEFAULT_RUNS_RETENTION.

    Chiamare dopo ogni close_run.
    """
    try:
        # Legge retention_n dal progetto in config.db
        try:
            # BE-GO-READY-DB-ENGINE-004: leaf canonico
            # (`app.db.config.projects.get_project_retention_n`) invece
            # del facade `config_db_service`. Allowlist trimmed.
            from app.db.config.projects import get_project_retention_n
            retention_n = get_project_retention_n(canvas_id)
            if retention_n is None:
                retention_n = DEFAULT_RUNS_RETENTION
        except Exception as e:
            logger.warning("apply_runs_retention: lettura retention_n fallita per %s, uso default: %s", canvas_id, e)
            retention_n = DEFAULT_RUNS_RETENTION

        # Lista run del canvas ordinate dalla più recente
        all_runs = list_runs(canvas_id, group_id, limit=1000)

        # Separa pinnate (escluse da retention) e non pinnate
        unpinned = [r for r in all_runs if not r.get("pinned")]

        if len(unpinned) <= retention_n:
            return  # nulla da eliminare

        # Le più vecchie sono in fondo (list_runs ordina DESC)
        to_delete = unpinned[retention_n:]
        for run in to_delete:
            _delete_run(run["run_id"])

        from app.utils.logger import get_logger
        get_logger(__name__).info(
            f"Retention intermediate: canvas={canvas_id} "
            f"conservate={retention_n} eliminate={len(to_delete)}",
            op="retention"
        )

    except Exception as e:
        from app.utils.logger import get_logger
        get_logger(__name__).warning(
            f"Errore retention intermediate ({canvas_id}): {e}",
            op="retention"
        )


def _delete_run(run_id: str) -> None:
    """Elimina una run e tutti i dati correlati.

    Grazie alla FK ON DELETE CASCADE su node_meta.run_id e node_rows.run_id,
    basta una singola DELETE sulla tabella runs per pulire tutto atomicamente.
    Prima il codice droppava tabelle dinamiche, poi DELETE node_meta,
    poi DELETE runs — tre statement non atomici che potevano lasciare orphan.

    Flow (BE-GO-READY-STORAGE-ENGINE-016 — W1 race coverage per
    intermediate full_artifact):

      1. `transaction()`: SELECT artifact_uri da node_meta WHERE
         run_id=? AND artifact_uri IS NOT NULL AND
         artifact_status='complete'. Per ogni row, record_pending
         per (a) data.parquet (artifact_uri) + (b) manifest.json
         (sibling derivato dallo stesso prefix). Poi
         DELETE FROM runs WHERE run_id=? (cascade su node_meta /
         node_rows). Tutto in una sola transazione → commit
         atomico.
      2. Post-commit: per ogni reconciliation_ref,
         compensating_delete + mark_done/mark_failed_attempt.

    Nodi senza artifact (mode metadata_only / preview_only /
    full_pg, oppure artifact_status="failed" o artifact_uri NULL)
    vengono saltati interamente: niente record_pending, niente
    compensating_delete. Comportamento user-visible invariato per
    quei mode (la grande maggioranza in produzione, dove il
    default e' metadata_only).

    Manifest sibling derivato come `<prefix>manifest.json` da
    `<prefix>data.parquet` solo se l'`artifact_uri` termina con
    `/data.parquet` (forma canonica del writer). Forme diverse
    (writer futuro con layout differente, o URI con scheme come
    `s3://`) NON producono record_pending: sicurezza > completeness
    su un orphan da pochi KB di JSON manifest.

    ADR-0011 contract preservato: storage failure NON propaga; la
    row DB resta cancellata. La differenza vs pre-batch 016 e' che
    l'orphan e' ora **tracked** + retry-able dal reconciler.
    """
    # Pre-fetch artifact metadata fuori dalla transaction principale
    # (read-only, niente lock pessimistico necessario: la run
    # appartiene per costruzione al chiamante che ha gia' deciso
    # di cancellarla).
    artifact_rows = fetchall(
        "SELECT node_id, artifact_uri "
        "FROM node_meta "
        "WHERE run_id = ? "
        "  AND artifact_uri IS NOT NULL "
        "  AND artifact_status = 'complete'",
        (run_id,),
    )

    # Filtra a path-shaped storage keys (no scheme). Defensive:
    # protegge contro un futuro writer che persistesse `s3://...`
    # o `file://...` come artifact_uri — quei shape non sono
    # cleanable via runtime object storage delete.
    pending_specs: List[tuple] = []
    for row in artifact_rows:
        node_id = row["node_id"]
        artifact_uri = row["artifact_uri"]
        if not artifact_uri or "://" in artifact_uri:
            continue
        # Parquet (sempre presente per artifact_status="complete").
        pending_specs.append((node_id, artifact_uri, "data.parquet"))
        # Manifest sibling (solo se la forma e' canonica).
        if artifact_uri.endswith("/data.parquet"):
            manifest_uri = (
                artifact_uri[: -len("data.parquet")] + "manifest.json"
            )
            pending_specs.append((node_id, manifest_uri, "manifest.json"))

    # Apri transaction con record_pending atomico col DELETE.
    # `pending_refs` accumula (storage_key, ref) per il post-commit.
    pending_refs: List[tuple] = []
    with transaction() as conn:
        with conn.cursor() as cur:
            if pending_specs:
                from app.shared.storage_reconciliation import record_pending
                for node_id, storage_key, kind in pending_specs:
                    ref = record_pending(
                        cur,
                        domain="intermediate_artifact",
                        storage_key=storage_key,
                        operation="delete",
                        reason="run_delete_cascade",
                        extras={
                            "run_id": run_id,
                            "node_id": node_id,
                            "kind": kind,
                        },
                    )
                    pending_refs.append((storage_key, ref))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))

    # Post-commit: cleanup best-effort + reconciliation transition.
    if pending_refs:
        from app.shared.storage_compensation import compensating_delete
        from app.shared.storage_reconciliation import (
            mark_done,
            mark_failed_attempt,
        )
        for storage_key, ref in pending_refs:
            outcome = compensating_delete(
                storage_key,
                domain="intermediate_artifact",
                reason="run_delete_cascade",
                extras={
                    "run_id": run_id,
                    "reconciliation_id": ref.id,
                },
                logger=logger,
            )
            if outcome in ("ok", "skipped"):
                mark_done(ref.id)
            else:
                # outcome == "failed": ADR-0011 posture preserved
                # (orphan accepted, run row stays deleted), but now
                # persisted for the reconciler beat hook.
                mark_failed_attempt(
                    ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )


def delete_all_runs_for_canvas(canvas_id: str) -> int:
    """
    Elimina tutte le run di un canvas (chiamata da delete_project).
    Rimuove tabelle dinamiche, node_meta e righe in runs.
    Ritorna il numero di run eliminate.

    Contratto errori (Batch 8 C5):
      - Happy path / 0 run / inner per-row fail: ritorna count successi.
        Un fail su singola run non blocca le altre (best-effort cascade) e
        viene loggato a warning con exc_info.
      - Outer catastrophic failure (fetchall esplode, DB down): solleva
        l'eccezione originale. Il caller (`project_service.delete_project`
        step 2 critical) accumula in `critical_failures` e solleva
        `ProjectCleanupPartialError` → response 500 strutturata.
        Pre-fix il `return 0` silent rendeva indistinguibile "canvas senza
        run" da "DB catastrofico" → orfani garantiti se lo step 5 cascade
        procedeva a rimuovere la row config.
    """
    try:
        rows = fetchall(
            "SELECT run_id FROM runs WHERE canvas_id = ?",
            (canvas_id,)
        )
        if not rows:
            return 0
        count = 0
        for row in rows:
            run_id = row["run_id"] if isinstance(row, dict) else row[0]
            # intent: optional feature — una run singola non cancellabile non
            # blocca le altre nel contesto cascade / outcome: fail-open con breadcrumb
            try:
                _delete_run(run_id)
                count += 1
            except Exception as e:
                logger.warning(
                    f"delete_all_runs_for_canvas: errore su run {run_id}: {e}",
                    op="delete_project",
                    exc_info=True,
                )
        logger.info(
            f"delete_all_runs_for_canvas: canvas={canvas_id} -> {count} run eliminate",
            op="delete_project"
        )
        return count
    # intent: resource guard outer / outcome: fail-closed (scrittura dati)
    # Il caller deve sapere se la cancellazione e' fallita catastroficamente:
    # un return 0 silent sarebbe indistinguibile da "canvas senza run" e
    # garantirebbe orfani cross-DB se il caller procedesse alla rimozione
    # della row config.projects.
    except Exception as e:
        logger.error(
            f"[CRASH] delete_all_runs_for_canvas: errore canvas={canvas_id}: {e}",
            op="delete_project",
            exc_info=True,
        )
        raise
