"""
Intermediate snapshot API.

Punto di ingresso unico per gli executor (MANIPOLA/ESTRAI/ARRICCHISCI)
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypeAlias

from app.core.intermediate import node_snapshots_db as _node_snapshots_db
from app.core.intermediate import policy as _policy
from app.core.intermediate import runs_db as _runs_db
from app.core.dataframes.frame_io import (
    dataframe_from_mapping,
    dataframe_hash_bytes,
    is_dataframe,
)

logger = logging.getLogger(__name__)

ColumnSchema: TypeAlias = Dict[str, str]


def _ms(t0: float) -> float:
    """Round monotonic delta in millisecondi (1 decimale)."""
    return round((time.monotonic() - t0) * 1000, 1)


# Result types

@dataclass
class SnapshotHandle:
    """Esito di una chiamata `record_*_snapshot`.

    `recorded=False` -> la policy ha deciso di non registrare; nessun
    side-effect (no DB, no hash). `reason` espone la decisione per
    log/test/diagnostica.

    Quando `recorded=True`:
      - `node_hash` e' presente (salvo errore di compute);
      - `row_count` e' la lunghezza reale del DataFrame;
      - `persisted_rows_count` e' sempre 0: PostgreSQL contiene solo
        metadati, mentre `full_artifact` salva il payload su object storage.
    """
    recorded: bool
    reason: str
    run_id: str
    node_id: str
    persistence_mode: str = ""
    row_count: int = 0
    persisted_rows_count: int = 0
    preview_truncated: bool = False
    node_hash: Optional[str] = None
    hash_algo: Optional[str] = None
    artifact_status: str = _policy.ARTIFACT_STATUS_NONE


@dataclass
class FlushResult:
    """Esito di `flush_snapshots`.

    stub. Le scritture sono sync, quindi `pending=0` e
    `errors=[]` sempre.
    """
    run_id: str
    flushed: int = 0
    pending: int = 0
    errors: List[str] = field(default_factory=list)


# Hash dataframe - pandas_hash_v1

def _hash_pandas_block(digest, df, *, label: str) -> None:
    """Aggiorna `digest` con i byte hash prodotti dal dataframe boundary.

    Estratto in helper per riusarlo dal full mode e dal sample mode.
    Su exception logga warning e marca il digest con un tag stabile
    (deterministico per (schema, len, type-of-error)).
    """
    try:
        digest.update(dataframe_hash_bytes(df, index=False, categorize=True))
    except Exception as e:  # pragma: no cover - difensivo
        logger.warning(
            "compute_dataframe_fingerprint(%s): hash_pandas_object fallito: %s",
            label, e,
        )
        digest.update(f"hash-failed:{type(e).__name__}".encode("utf-8"))


def _compute_dataframe_fingerprint_full(df: Any) -> str:
    """Algoritmo `pandas_hash_v1`: hash di tutto il DataFrame.

    Costo O(n*cols) - su DF da 275k righe e 56 colonne circa 1.0-1.5 s.
    Esatto: ogni cambio in qualunque riga produce hash diverso.

    1. Schema payload: `<col>=<dtype>|<col>=<dtype>|...` ordinato.
    2. Row count payload: stringa decimale.
    3. Row hash payload: `dataframe_hash_bytes(df[ordered_cols],
       index=False, categorize=True)` dal boundary `app.core.dataframes`.
    4. SHA256 di (schema || rowcount || rowhashes).
    """
    digest, ordered_cols, row_count = _init_digest_with_schema(df)
    if row_count > 0 and ordered_cols:
        _hash_pandas_block(digest, df[ordered_cols], label="full")
    return digest.hexdigest()


def _compute_dataframe_fingerprint_sample(df: Any, sample_rows: int) -> str:
    """Algoritmo `pandas_sample_hash_v1`: hash sample-based.

    Trade-off:
      + costo O(min(n, sample_rows) * cols), basso anche su DF grandi
      - cambi che toccano solo righe oltre la N-esima possono non essere
        rilevati al checkpoint. Audit forte richiede mode="full".

    1. Schema payload identico al full mode.
    2. Row count payload: stringa decimale (cambi di cardinalita'
       sono SEMPRE rilevati anche se il sample e' identico).
    3. Sample size payload: `sample_rows` (cambiando sample_rows fra
       run l'hash diventa diverso -> documentato).
    4. Sample hash payload: `dataframe_hash_bytes(df.head(N))`.
    5. SHA256 finale.

    `df.head(sample_rows)` e' deterministico (no shuffle, no random
    seed). Su DataFrame piu' piccoli del sample, l'hash equivale al
    full hash modulo lo schema-tag aggiuntivo.
    """
    if sample_rows <= 0:
        # Difensivo: validate dovrebbe gia' aver bloccato. Restiamo
        # deterministici fallback
        sample_rows = 1
    digest, ordered_cols, row_count = _init_digest_with_schema(df)
    digest.update(f"sample={sample_rows}".encode("ascii"))
    digest.update(b"\x00")
    if row_count > 0 and ordered_cols:
        sample = df[ordered_cols].head(sample_rows)
        _hash_pandas_block(digest, sample, label="sample")
    return digest.hexdigest()


def _init_digest_with_schema(df: Any):
    """Inizializza il SHA256 con `schema || row_count` comuni a full e
    sample. Ritorna `(digest, ordered_cols, row_count)`.
    """
    digest = hashlib.sha256()
    ordered_cols = sorted(df.columns.tolist(), key=str)
    schema_payload = "|".join(
        f"{col}={str(df[col].dtype)}" for col in ordered_cols
    ).encode("utf-8", errors="replace")
    digest.update(schema_payload)
    digest.update(b"\x00")
    row_count = len(df)
    digest.update(str(row_count).encode("ascii"))
    digest.update(b"\x00")
    return digest, ordered_cols, row_count


def compute_dataframe_fingerprint(df: Any) -> tuple[str, str]:
    """Fingerprint del DataFrame con dispatch via policy.

    Dispatch sul setting globale `INTERMEDIATE_FINGERPRINT_MODE`:
      - "sample" (default): `pandas_sample_hash_v1`, sample
        `INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS` (default 1000).
      - "full": `pandas_hash_v1`, hash sull'intero DataFrame.

    Ritorna `(node_hash_hex, hash_algo)`. Il caller persiste entrambi
    in `node_meta.node_hash` / `node_meta.hash_algo` cosi' un
    consumer downstream sa con quale algoritmo confrontare.

    Su DataFrame None / non-DataFrame il fingerprint resta
    deterministico (sha256 di un tag), `hash_algo` riflette comunque
    la mode richiesta.

    Note (sample mode):
      - Cambi su righe oltre la N-esima possono non cambiare l'hash:
        questo e' il trade-off documentato. Audit forte richiede
        `INTERMEDIATE_FINGERPRINT_MODE=full`.
      - `len(df)` e' sempre incluso: cambi di cardinalita' sono
        rilevati anche con sample.
      - `df.head(N)` e' deterministico (no shuffle, no random seed).
    """
    fingerprint_policy = _policy.resolve_fingerprint_policy()
    mode = fingerprint_policy.mode
    sample_rows = fingerprint_policy.sample_rows

    if df is None:
        # None DF: stesso digest legacy, ma algo riflette la mode
        digest_hex = hashlib.sha256(b"none-df").hexdigest()
        return digest_hex, fingerprint_policy.hash_algo

    if not is_dataframe(df):
        # Coercion difensiva
        try:
            df = dataframe_from_mapping(df)
        except Exception:
            digest_hex = hashlib.sha256(
                f"non-dataframe:{type(df).__name__}".encode("utf-8")
            ).hexdigest()
            return digest_hex, fingerprint_policy.hash_algo

    if mode == _policy.FINGERPRINT_MODE_FULL:
        return (
            _compute_dataframe_fingerprint_full(df),
            _policy.HASH_ALGO_PANDAS_HASH_V1,
        )
    # Default sample
    return (
        _compute_dataframe_fingerprint_sample(df, sample_rows),
        _policy.HASH_ALGO_PANDAS_SAMPLE_V1,
    )


def _col_schema_from_df(df: Any) -> ColumnSchema:
    """`{col_name: dtype_str}` come gia' fatto da writer.write_node."""
    if df is None:
        return {}
    return {str(c): str(df[c].dtype) for c in df.columns}


# Public API

def _record_snapshot(
    *,
    run_id: str,
    module: str,
    node_id: str,
    node_type: str,
    df: Any,
    snapshot_kind: str,
    exec_time: float,
    status: str,
    error_msg: Optional[str],
    node_data: Optional[dict],
    transform_id: Optional[str],
    mode_override: Optional[str] = None,
) -> SnapshotHandle:
    """Implementazione condivisa fra final/checkpoint.

    Algoritmo:
      1. resolve_intermediate_policy(...). Se skip -> ritorno
         `SnapshotHandle(recorded=False, reason=...)`.
      2. row_count = len(df); col_schema da df.dtypes.
      3. compute_dataframe_fingerprint(df) -> node_hash.
      4. `full_artifact` salva il payload su object storage.
      5. write_node_snapshot_atomic(...) persiste soltanto i metadati.

    `mode_override` (R-25 PR-B2): kwarg per-call che sostituisce
    `INTERMEDIATE_PERSISTENCE_MODE` globale. None = bit-compat default.
    Override invalido propaga `IntermediateSettingsError` da
    `resolve_intermediate_policy` -> `resolve_intermediate_mode`.
    """
    _t_total = time.monotonic()
    policy = _policy.resolve_intermediate_policy(
        module=module,
        snapshot_kind=snapshot_kind,
        node_type=node_type,
        node_data=node_data,
        transform_id=transform_id,
        mode_override=mode_override,
    )
    if not policy.recorded:
        logger.info(
            "[PERF] op=skip_checkpoint module=%s node_id=%s node_type=%s "
            "snapshot_kind=%s reason=%s total_ms=%s",
            module, node_id, node_type, snapshot_kind,
            policy.reason or f"{module}_{snapshot_kind}_skip",
            _ms(_t_total),
        )
        return SnapshotHandle(
            recorded=False,
            reason=policy.reason or f"{module}_{snapshot_kind}_skip",
            run_id=run_id,
            node_id=node_id,
        )

    # Lazy len/schema/hash: arriviamo qui solo se la policy ha deciso
    # di registrare. Hash sempre calcolato sul DataFrame; nessuna riga
    # viene materializzata per PostgreSQL.
    row_count = 0 if df is None else int(len(df))
    col_schema = _col_schema_from_df(df)

    _t_fp = time.monotonic()
    node_hash, hash_algo = compute_dataframe_fingerprint(df)
    fingerprint_ms = _ms(_t_fp)

    persistence_mode = policy.persistence_mode
    persisted_rows_count = 0
    preview_truncated = False

    # In mode full_artifact serializziamo data.parquet + manifest.json
    # su runtime object storage. Il caller persistera' artifact_uri /
    # _status / _format in `node_meta`. Best-effort: errori tornano
    # `failed` e proseguono.
    artifact_uri: Optional[str] = None
    artifact_format: Optional[str] = None
    artifact_status: str = _policy.ARTIFACT_STATUS_NONE
    artifact_error: Optional[str] = None
    artifact_ms: float = 0.0

    if persistence_mode == _policy.MODE_FULL_ARTIFACT and df is not None:
        _t_art = time.monotonic()
        # Lookup group_id + project_id dalla run intermediate
        run_meta = _runs_db.get_run(run_id) or {}
        run_project_id = run_meta.get("canvas_id") or ""
        run_group_id = run_meta.get("group_id") or ""
        if not run_project_id or not run_group_id:
            artifact_status = "failed"
            artifact_error = (
                "run intermediate priva di canvas_id/group_id; "
                "impossibile costruire layout artifact"
            )
            artifact_format = "parquet"
            logger.warning(
                "[ARTIFACT] run_meta incompleto per run=%s: %s",
                run_id, run_meta,
            )
        else:
            from app.core.intermediate.artifact_writer import persist_full_artifact
            res = persist_full_artifact(
                df=df,
                run_id=run_id,
                project_id=run_project_id,
                group_id=run_group_id,
                module=module,
                node_id=node_id,
                node_type=node_type,
                snapshot_kind=snapshot_kind,
                node_hash=node_hash,
                hash_algo=hash_algo,
                row_count=row_count,
                column_count=len(col_schema or {}),
            )
            artifact_uri = res.artifact_uri
            artifact_format = res.artifact_format
            artifact_status = res.artifact_status
            artifact_error = res.error
        artifact_ms = _ms(_t_art)

    _t_db = time.monotonic()
    try:
        _node_snapshots_db.write_node_snapshot_atomic(
            run_id=run_id,
            node_id=node_id,
            node_type=node_type,
            rows_to_persist=[],
            col_schema=col_schema,
            exec_time=exec_time,
            status=status,
            error_msg=error_msg,
            row_count=row_count,
            node_hash=node_hash,
            hash_algo=hash_algo,
            persistence_mode=persistence_mode,
            preview_truncated=preview_truncated,
            artifact_uri=artifact_uri,
            artifact_format=artifact_format,
            artifact_status=artifact_status,
            artifact_error=artifact_error,
        )
    except Exception:
        if artifact_uri:
            try:
                from app.infrastructure.storage.compensation import compensating_delete

                keys = [artifact_uri]
                if artifact_uri.endswith("/data.parquet"):
                    keys.append(artifact_uri[: -len("data.parquet")] + "manifest.json")
                for key in keys:
                    compensating_delete(
                        key,
                        domain="intermediate_artifact",
                        reason="db_snapshot_failed_after_artifact_upload",
                        extras={"run_id": run_id, "node_id": node_id},
                        logger=logger,
                    )
            except Exception as cleanup_exc:
                logger.warning(
                    "[ARTIFACT] cleanup compensativo fallito per run=%s node=%s: %s",
                    run_id,
                    node_id,
                    cleanup_exc,
                    exc_info=True,
                )
        raise
    db_ms = _ms(_t_db)

    op = (
        "record_final_snapshot"
        if snapshot_kind == _policy.SNAPSHOT_KIND_FINAL
        else "record_checkpoint_snapshot"
    )
    logger.info(
        "[PERF] op=%s module=%s node_id=%s node_type=%s snapshot_kind=%s "
        "persistence_mode=%s reason=%s row_count=%d "
        "persisted_rows_count=%d preview_truncated=%s "
        "fingerprint_ms=%s db_ms=%s artifact_ms=%s artifact_status=%s "
        "total_ms=%s",
        op, module, node_id, node_type, snapshot_kind,
        persistence_mode, policy.reason,
        row_count, persisted_rows_count, preview_truncated,
        fingerprint_ms, db_ms, artifact_ms, artifact_status,
        _ms(_t_total),
    )

    return SnapshotHandle(
        recorded=True,
        reason=policy.reason,
        run_id=run_id,
        node_id=node_id,
        persistence_mode=persistence_mode,
        row_count=row_count,
        persisted_rows_count=persisted_rows_count,
        preview_truncated=preview_truncated,
        node_hash=node_hash,
        hash_algo=hash_algo,
        artifact_status=artifact_status,
    )


def record_final_snapshot(
    run_id: str,
    module: str,
    node_id: str,
    node_type: str,
    df: Any,
    *,
    exec_time: float = 0.0,
    status: str = "ok",
    error_msg: Optional[str] = None,
    node_data: Optional[dict] = None,
    mode_override: Optional[str] = None,
) -> SnapshotHandle:
    """Registra lo snapshot finale di un modulo.

    Snapshot final = output che l'utente vede come "result" del modulo.
    Sempre registrato (la policy globale decide solo MODE).

    `mode_override` (R-25 PR-B2): override per-call della
    `INTERMEDIATE_PERSISTENCE_MODE` globale. Default None = bit-compat.
    """
    return _record_snapshot(
        run_id=run_id,
        module=module,
        node_id=node_id,
        node_type=node_type,
        df=df,
        snapshot_kind=_policy.SNAPSHOT_KIND_FINAL,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        node_data=node_data,
        transform_id=None,
        mode_override=mode_override,
    )


def record_checkpoint_snapshot(
    run_id: str,
    module: str,
    node_id: str,
    node_type: str,
    df: Any,
    *,
    exec_time: float = 0.0,
    status: str = "ok",
    error_msg: Optional[str] = None,
    node_data: Optional[dict] = None,
    transform_id: Optional[str] = None,
    mode_override: Optional[str] = None,
) -> SnapshotHandle:
    """Registra uno snapshot intermedio (per-nodo) di un modulo.

    La policy decide se questo specifico nodo va salvato:
      - ESTRAI / ARRICCHISCI in sempre skip.
      - MANIPOLA: dipende da `MANIPOLA_INTERMEDIATE_CHECKPOINTS` +
        classificazione critical/marked.

    Quando la policy dice skip:
      - ritorna `SnapshotHandle(recorded=False, reason=...)` SUBITO,
        prima di toccare `df` (no `to_dict`, no hash, no DB).

    `mode_override` (R-25 PR-B2): override per-call della
    `INTERMEDIATE_PERSISTENCE_MODE` globale. Default None = bit-compat.
    Nota: l'override agisce SOLO sulla mode di persistenza
    (`metadata_only/full_artifact`), NON sulla
    decisione di skip checkpoint per un nodo (governata da
    `MANIPOLA_INTERMEDIATE_CHECKPOINTS`). Un nodo skipped resta skipped
    anche con override.
    """
    return _record_snapshot(
        run_id=run_id,
        module=module,
        node_id=node_id,
        node_type=node_type,
        df=df,
        snapshot_kind=_policy.SNAPSHOT_KIND_CHECKPOINT,
        exec_time=exec_time,
        status=status,
        error_msg=error_msg,
        node_data=node_data,
        transform_id=transform_id,
        mode_override=mode_override,
    )


def flush_snapshots(
    run_id: str,
    timeout: Optional[float] = None,
) -> FlushResult:
    """Drena gli snapshot pending per la run.

    stub. Le scritture in sono sync (nessuna queue
    async), quindi non c'e' nulla da drenare. La firma esiste per
    permettere agli executor di di chiamare
    `flush_snapshots(run_id)` prima di `close_run` senza aspettare
    refactor sequenziali.
    """
    return FlushResult(run_id=run_id, flushed=0, pending=0, errors=[])
