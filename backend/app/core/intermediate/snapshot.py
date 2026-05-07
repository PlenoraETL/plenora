"""
Intermediate snapshot API.

Punto di ingresso unico per gli executor (MANIPOLA/ESTRAI/ARRICCHISCI)
per persistere lo stato di un nodo. La funzione interroga la policy
globale (`policy.resolve_intermediate_policy`) PRIMA di toccare il
DataFrame, in modo che lo skip-path sia economico (no `df.to_dict`,
no hash, no DB).

API pubbliche:
    record_final_snapshot(run_id, module, node_id, node_type, df, ...)
    record_checkpoint_snapshot(run_id, module, node_id, node_type, df, ...)
    flush_snapshots(run_id, timeout=None)
    SnapshotHandle, FlushResult
    compute_dataframe_fingerprint(df)

Vincoli runtime:
  - mode supportati: metadata_only / preview_only / full_pg / full_artifact.
  - full_artifact richiede capability runtime object storage attiva
    (`OBJECT_STORAGE_ENABLED=true`) + bucket configurato; cross-validato
    al boot da `policy.validate_intermediate_settings`. Persistenza
    Parquet + manifest delegata ad `artifact_writer.persist_full_artifact`.
  - **niente** `df.to_dict(orient="records")` salvo `full_pg`;
    full_artifact serializza via `df.to_parquet(BytesIO)`.
  - fingerprint via `pd.util.hash_pandas_object` (vettoriale, no Python
    loop). Mode `sample` (default) o `full` da `INTERMEDIATE_FINGERPRINT_MODE`.
  - `flush_snapshots` resta no-op stub: le scritture sono sync, non c'e'
    coda async. Lo stub esiste per stabilizzare l'API che gli executor
    chiamano prima di `close_run`. Async writer e' follow-up futuro.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core.intermediate import db as _db
from app.core.intermediate import policy as _policy

logger = logging.getLogger(__name__)


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
      - `persisted_rows_count` e':
          0  in mode `metadata_only`;
          min(N, row_count) in `preview_only`;
          row_count in `full_pg`.
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

    Batch 1: stub. Le scritture sono sync, quindi `pending=0` e
    `errors=[]` sempre.
    """
    run_id: str
    flushed: int = 0
    pending: int = 0
    errors: List[str] = field(default_factory=list)


# Hash dataframe — pandas_hash_v1

def _hash_pandas_block(digest, df, *, label: str) -> None:
    """Aggiorna `digest` con `pd.util.hash_pandas_object(df).tobytes()`.

    Estratto in helper per riusarlo dal full mode e dal sample mode.
    Su exception logga warning e marca il digest con un tag stabile
    (deterministico per (schema, len, type-of-error)).
    """
    import pandas as pd
    try:
        row_hashes = pd.util.hash_pandas_object(
            df, index=False, categorize=True,
        ).to_numpy(dtype="uint64", copy=False)
        digest.update(row_hashes.tobytes())
    except Exception as e:  # pragma: no cover - difensivo
        logger.warning(
            "compute_dataframe_fingerprint(%s): hash_pandas_object fallito: %s",
            label, e,
        )
        digest.update(f"hash-failed:{type(e).__name__}".encode("utf-8"))


def _compute_dataframe_fingerprint_full(df: Any) -> str:
    """Algoritmo `pandas_hash_v1`: hash di tutto il DataFrame.

    Costo O(n*cols) — su DF da 275k righe e 56 colonne 1.0–1.5 s.
    Esatto: ogni cambio in qualunque riga produce hash diverso.

    1. Schema payload:    `<col>=<dtype>|<col>=<dtype>|...` ordinato.
    2. Row count payload: stringa decimale.
    3. Row hash payload:  `pd.util.hash_pandas_object(df[ordered_cols],
       index=False, categorize=True)` -> ndarray uint64 -> bytes raw.
    4. SHA256 di (schema || rowcount || rowhashes).
    """
    digest, ordered_cols, row_count = _init_digest_with_schema(df)
    if row_count > 0 and ordered_cols:
        _hash_pandas_block(digest, df[ordered_cols], label="full")
    return digest.hexdigest()


def _compute_dataframe_fingerprint_sample(df: Any, sample_rows: int) -> str:
    """Algoritmo `pandas_sample_hash_v1`: hash sample-based.

    Bug #24 (perf, 2026-04-28). Trade-off:
      + costo O(min(n, sample_rows) * cols) -> ~10–20 ms anche su DF
        grandi
      - cambi che toccano SOLO righe oltre la N-esima possono non
        essere rilevati al checkpoint (audit alto livello, non
        equivalence check)

    1. Schema payload identico al full mode.
    2. Row count payload: stringa decimale (cambi di cardinalita'
       sono SEMPRE rilevati anche se il sample e' identico).
    3. Sample size payload: `sample_rows` (cambiando sample_rows fra
       run l'hash diventa diverso -> documentato).
    4. Sample hash payload: `pd.util.hash_pandas_object(df.head(N))`.
    5. SHA256 finale.

    `df.head(sample_rows)` e' deterministico (no shuffle, no random
    seed). Su DataFrame piu' piccoli del sample, l'hash equivale al
    full hash modulo lo schema-tag aggiuntivo.
    """
    if sample_rows <= 0:
        # Difensivo: validate dovrebbe gia' aver bloccato. Restiamo
        # deterministici fallback.
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
    # Risoluzione mode + sample size dalla policy (lazy lettura env).
    cfg = _policy._read_settings()
    mode = (cfg.get("fingerprint_mode") or _policy.FINGERPRINT_MODE_SAMPLE)
    sample_rows = int(cfg.get("fingerprint_sample_rows") or 1000)

    if df is None:
        # None DF: stesso digest legacy, ma algo riflette la mode.
        digest_hex = hashlib.sha256(b"none-df").hexdigest()
        algo = (
            _policy.HASH_ALGO_PANDAS_SAMPLE_V1
            if mode == _policy.FINGERPRINT_MODE_SAMPLE
            else _policy.HASH_ALGO_PANDAS_HASH_V1
        )
        return digest_hex, algo

    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        # Coercion difensiva
        try:
            df = pd.DataFrame(df)
        except Exception:
            digest_hex = hashlib.sha256(
                f"non-dataframe:{type(df).__name__}".encode("utf-8")
            ).hexdigest()
            algo = (
                _policy.HASH_ALGO_PANDAS_SAMPLE_V1
                if mode == _policy.FINGERPRINT_MODE_SAMPLE
                else _policy.HASH_ALGO_PANDAS_HASH_V1
            )
            return digest_hex, algo

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


# Preview & row-hash helpers (interni)

def _build_preview_rows(df: Any, n_rows: int) -> List[Dict]:
    """Ritorna `df.head(n_rows).to_dict(orient='records')` con
    `_row_hash` per ogni riga.

    Per Batch 1 riusa `writer.compute_row_hash` per garantire che
    il `row_hash` di una riga preview sia identico a quello legacy
    `full_pg` (cosi' diff L2 fra una run preview_only e una full_pg
    sullo stesso dataset combacia sulle prime N righe).
    """
    if df is None or n_rows <= 0:
        return []
    head = df.head(n_rows)
    if len(head) == 0:
        return []

    from app.core.intermediate.writer import compute_row_hash

    cols_sorted = sorted(map(str, df.columns.tolist()))
    out: List[Dict] = []
    # `to_dict(orient="records")` sulle prime N righe: O(N), accettabile
    # con N=500 anche su DataFrame grandi.
    for row in head.to_dict(orient="records"):
        out.append({
            "_row_hash": compute_row_hash(row, cols_sorted),
            **row,
        })
    return out


def _build_full_rows_legacy(df: Any) -> List[Dict]:
    """Materializza l'intero DataFrame in lista di dict + `_row_hash`.

    Equivalente al path legacy `writer.write_node`. SOLO mode
    `full_pg`. Costoso su DataFrame grandi: per definizione.
    """
    if df is None:
        return []

    from app.core.intermediate.writer import compute_row_hash

    cols_sorted = sorted(map(str, df.columns.tolist()))
    out: List[Dict] = []
    for row in df.to_dict(orient="records"):
        out.append({
            "_row_hash": compute_row_hash(row, cols_sorted),
            **row,
        })
    return out


def _col_schema_from_df(df: Any) -> Dict[str, str]:
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
      4. preview/full rows secondo `policy.persistence_mode`:
         - metadata_only -> rows_to_persist = []
         - preview_only  -> rows_to_persist = head(N) + _row_hash
         - full_pg       -> rows_to_persist = full to_dict + _row_hash
      5. write_node_snapshot_atomic(...) -> persiste meta + rows.

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
    # di registrare. Hash sempre calcolato sul full DataFrame; le righe
    # da scrivere variano per mode.
    row_count = 0 if df is None else int(len(df))
    col_schema = _col_schema_from_df(df)

    _t_fp = time.monotonic()
    node_hash, hash_algo = compute_dataframe_fingerprint(df)
    fingerprint_ms = _ms(_t_fp)

    rows_to_persist: List[Dict] = []
    persistence_mode = policy.persistence_mode

    if policy.should_record_rows:
        if persistence_mode == _policy.MODE_FULL_PG:
            rows_to_persist = _build_full_rows_legacy(df)
        elif persistence_mode == _policy.MODE_PREVIEW_ONLY:
            rows_to_persist = _build_preview_rows(df, policy.preview_rows)
        # mode metadata_only / full_artifact non entrano in questo branch
        # (should_record_rows=False -> zero node_rows).

    persisted_rows_count = len(rows_to_persist)
    preview_truncated = (
        persistence_mode == _policy.MODE_PREVIEW_ONLY
        and persisted_rows_count < row_count
    )

    # Batch object storage intermedi (2026-04-28): in mode full_artifact
    # serializziamo data.parquet + manifest.json su runtime object
    # storage. Il caller persistera' artifact_uri / _status / _format
    # in `node_meta`. Best-effort: errori tornano `failed` e proseguono.
    artifact_uri: Optional[str] = None
    artifact_format: Optional[str] = None
    artifact_status: str = _policy.ARTIFACT_STATUS_NONE
    artifact_error: Optional[str] = None
    artifact_ms: float = 0.0

    if persistence_mode == _policy.MODE_FULL_ARTIFACT and df is not None:
        _t_art = time.monotonic()
        # Lookup group_id + project_id dalla run intermediate.
        run_meta = _db.get_run(run_id) or {}
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
    _db.write_node_snapshot_atomic(
        run_id=run_id,
        node_id=node_id,
        node_type=node_type,
        rows_to_persist=rows_to_persist,
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
      - ESTRAI / ARRICCHISCI in Batch 1: sempre skip.
      - MANIPOLA: dipende da `MANIPOLA_INTERMEDIATE_CHECKPOINTS` +
        classificazione critical/marked.

    Quando la policy dice skip:
      - ritorna `SnapshotHandle(recorded=False, reason=...)` SUBITO,
        prima di toccare `df` (no `to_dict`, no hash, no DB).

    `mode_override` (R-25 PR-B2): override per-call della
    `INTERMEDIATE_PERSISTENCE_MODE` globale. Default None = bit-compat.
    Nota: l'override agisce SOLO sulla mode di persistenza
    (`metadata_only/preview_only/full_pg/full_artifact`), NON sulla
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

    Batch 1: stub. Le scritture in Batch 1 sono sync (nessuna queue
    async), quindi non c'e' nulla da drenare. La firma esiste per
    permettere agli executor di Batch 2 di chiamare
    `flush_snapshots(run_id)` prima di `close_run` senza aspettare
    refactor sequenziali.
    """
    return FlushResult(run_id=run_id, flushed=0, pending=0, errors=[])
