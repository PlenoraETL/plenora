"""
Intermediate snapshot API — Phase 6 Batch 1.

Punto di ingresso unico per gli executor (MANIPOLA/ESTRAI/ARRICCHISCI in
Batch 2/3) per persistere lo stato di un nodo. La funzione interroga la
policy globale (`policy.resolve_intermediate_policy`) PRIMA di toccare
il DataFrame, in modo che lo skip-path sia economico (no
`df.to_dict`, no hash, no DB).

API pubbliche:
    record_final_snapshot(run_id, module, node_id, node_type, df, ...)
    record_checkpoint_snapshot(run_id, module, node_id, node_type, df, ...)
    flush_snapshots(run_id, timeout=None)
    SnapshotHandle, FlushResult
    compute_dataframe_fingerprint(df)

Vincoli Batch 1:
  - mode `metadata_only`, `preview_only`, `full_pg` implementati;
  - mode `full_artifact` rifiutato a settings-validation;
  - **niente** `df.to_dict(orient="records")` salvo `full_pg`;
  - hash via `pd.util.hash_pandas_object` (vettoriale, no Python loop);
  - `flush_snapshots` e' un no-op stub: in Batch 1 le scritture sono
    sync, non c'e' coda. Stub esiste per stabilizzare l'API che gli
    executor di Batch 2 chiameranno prima di `close_run`.
  - **niente** S3 reale: `full_artifact` non e' raggiungibile.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core.intermediate import db as _db
from app.core.intermediate import policy as _policy

logger = logging.getLogger(__name__)


# ============================================================
# Result types
# ============================================================

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


# ============================================================
# Hash dataframe — pandas_hash_v1
# ============================================================

def compute_dataframe_fingerprint(df: Any) -> str:
    """Fingerprint di un DataFrame senza materializzare to_dict.

    Algoritmo `pandas_hash_v1` (versionato in `policy.HASH_ALGO_PANDAS_HASH_V1`):

      1. Schema payload:    `<col>=<dtype>|<col>=<dtype>|...` ordinato
         per nome colonna.
      2. Row count payload: stringa decimale.
      3. Row hash payload:  `pd.util.hash_pandas_object(df[ordered_cols],
         index=False, categorize=True)` -> ndarray uint64 -> bytes raw.
      4. SHA256 di (schema || rowcount || rowhashes).

    Note:
      - Order-sensitive: due DataFrame con righe permutate danno hash
        diversi (decisione v1 documentata in to-be).
      - Per geometrie Shapely `pd.util.hash_pandas_object` chiama
        `__hash__`/`repr` → consistente fra run dello stesso oggetto
        ma puo' divergere fra Python differenti. Per Batch 1 accettiamo
        questo limite; in Batch 2 valuteremo conversione preventiva
        a WKB stabile per le colonne geometriche.

    Ritorna stringa esadecimale SHA256 (64 char).

    Su DataFrame None o vuoti il fingerprint e' costruito comunque
    per restare deterministico:
      - None -> sha256(b"none-df")
      - empty -> sha256(schema || "0" || "")
    """
    if df is None:
        return hashlib.sha256(b"none-df").hexdigest()

    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        # Coercion difensiva: non supportato nel design ma evitiamo
        # crash misteriosi in test.
        try:
            df = pd.DataFrame(df)
        except Exception:
            return hashlib.sha256(
                f"non-dataframe:{type(df).__name__}".encode("utf-8")
            ).hexdigest()

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

    if row_count > 0 and ordered_cols:
        normalized = df[ordered_cols]
        try:
            row_hashes = pd.util.hash_pandas_object(
                normalized, index=False, categorize=True,
            ).to_numpy(dtype="uint64", copy=False)
            digest.update(row_hashes.tobytes())
        except Exception as e:  # pragma: no cover - difensivo
            # Difensivo: hash failure non deve crashare la pipeline.
            # Marca il fingerprint con un tag esplicito per
            # diagnostica; resta deterministico per (schema, len, type).
            logger.warning(
                "compute_dataframe_fingerprint: hash_pandas_object fallito: %s",
                e,
            )
            digest.update(f"hash-failed:{type(e).__name__}".encode("utf-8"))

    return digest.hexdigest()


# ============================================================
# Preview & row-hash helpers (interni)
# ============================================================

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


# ============================================================
# Public API
# ============================================================

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
    """
    policy = _policy.resolve_intermediate_policy(
        module=module,
        snapshot_kind=snapshot_kind,
        node_type=node_type,
        node_data=node_data,
        transform_id=transform_id,
    )
    if not policy.recorded:
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
    node_hash = compute_dataframe_fingerprint(df)

    rows_to_persist: List[Dict] = []
    persistence_mode = policy.persistence_mode

    if policy.should_record_rows:
        if persistence_mode == _policy.MODE_FULL_PG:
            rows_to_persist = _build_full_rows_legacy(df)
        elif persistence_mode == _policy.MODE_PREVIEW_ONLY:
            rows_to_persist = _build_preview_rows(df, policy.preview_rows)
        # mode metadata_only non entra in questo branch (should_record_rows=False).

    persisted_rows_count = len(rows_to_persist)
    preview_truncated = (
        persistence_mode == _policy.MODE_PREVIEW_ONLY
        and persisted_rows_count < row_count
    )

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
        hash_algo=_policy.HASH_ALGO_PANDAS_HASH_V1,
        persistence_mode=persistence_mode,
        preview_truncated=preview_truncated,
        # Batch 1: artifact non implementato. Status "none" esplicito
        # per distinguerlo dai record legacy NULL (che si interpretano
        # come full_pg).
        artifact_status=_policy.ARTIFACT_STATUS_NONE,
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
        hash_algo=_policy.HASH_ALGO_PANDAS_HASH_V1,
        artifact_status=_policy.ARTIFACT_STATUS_NONE,
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
) -> SnapshotHandle:
    """Registra lo snapshot finale di un modulo.

    Snapshot final = output che l'utente vede come "result" del modulo.
    Sempre registrato (la policy globale decide solo MODE).
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
) -> SnapshotHandle:
    """Registra uno snapshot intermedio (per-nodo) di un modulo.

    La policy decide se questo specifico nodo va salvato:
      - ESTRAI / ARRICCHISCI in Batch 1: sempre skip.
      - MANIPOLA: dipende da `MANIPOLA_INTERMEDIATE_CHECKPOINTS` +
        classificazione critical/marked.

    Quando la policy dice skip:
      - ritorna `SnapshotHandle(recorded=False, reason=...)` SUBITO,
        prima di toccare `df` (no `to_dict`, no hash, no DB).
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
