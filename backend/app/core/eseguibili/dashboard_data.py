"""Porta unica dei «dati dashboard» di un task di Eseguibile (11/11/2026).

La copia delle prime righe dell'output per la dashboard Grafana. Scelta
dichiarata: e' un ARTIFACT INTERNO nella cartella del task
(``tenants/<g>/eseguibili-outputs/<task_id>/__internal__dashboard__dati__scade-<epoch>.json``),
non un dominio storage nuovo. Ne segue per costruzione:

- tenant, tag effimero (lifecycle del bucket) e pulizia completa
  (``output_store.delete_task_outputs``, chiamata da ``full-cleanup``, dallo
  scarto della finestra e dalla pulizia richiesta) come l'output;
- mai «Output ambiguo»: ``find_output_keys`` esclude gli artifact interni;
- il download (consumo una volta) la CONSERVA: ``delete_task_outputs(
  retain_owner=True)`` tiene i metadati del task (``KEPT_BY_DOWNLOAD``);
- la scadenza (scrittura + ``DASHBOARD_DATA_TTL_SECONDS``, 30 min) sta nel
  nome: la lettura dopo la scadenza risponde «non c'e' piu'» e cancella; il
  beat cancella le copie scadute (``purge_expired``).

Formato: JSON, array di oggetti ``{colonna: valore}`` gia' come lo serve
l'endpoint dati (lettura senza rielaborazione), al massimo
``DASHBOARD_DATA_MAX_ROWS`` righe e ``DASHBOARD_DATA_MAX_BYTES`` byte.

Ogni lettura, scrittura e cancellazione della copia passa da qui
(``STORAGE_ACCESS_SITES`` + test di enumerazione).
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import time
from decimal import Decimal
from typing import Any, Iterable, List, Optional, Sequence

from app.core.grafana import settings as grafana_settings
from app.utils.logger import get_logger

from . import storage

logger = get_logger(__name__)

KIND = "dashboard"
_SOURCE = "dati"
_NAME_RE = re.compile(r"^scade-(\d{1,12})\.json$")
DATA_PREFIX = f"{storage.INTERNAL_ARTIFACT_PREFIX}{KIND}__{_SOURCE}__"


class DashboardDataGone(LookupError):
    """La copia non c'e' (mai scritta, gia' pulita o scaduta)."""


def data_filename(expires_at: int) -> str:
    return storage.internal_artifact_filename(KIND, _SOURCE, f"scade-{int(expires_at)}.json")


def is_dashboard_data_key(key: str) -> bool:
    return str(key).rsplit("/", 1)[-1].startswith(DATA_PREFIX)


def expires_at_of(key: str) -> Optional[int]:
    name = str(key).rsplit("/", 1)[-1]
    if not name.startswith(DATA_PREFIX):
        return None
    match = _NAME_RE.match(name[len(DATA_PREFIX):])
    return int(match.group(1)) if match else None


# ── Valori JSON ─────────────────────────────────────────────────────────────

def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value() and abs(value) < 2**53:
            return int(value)
        as_float = float(value)  # 1E+400 -> inf: il testo e' l'unico valore fedele
        return as_float if math.isfinite(as_float) else str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        try:
            return value.isoformat()
        except ValueError:  # NaT
            return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    try:
        if value != value:  # NaN / NaT / pd.NA
            return None
    except (TypeError, ValueError):
        return None
    item = getattr(value, "item", None)
    if callable(item):  # scalari numpy
        try:
            return _jsonable(item())
        except (TypeError, ValueError):
            pass
    text = str(value)
    return text if len(text) <= 1000 else text[:1000]


class DashboardRowsCollector:
    """Raccoglie le prime righe dell'output (DataFrame o righe di cursore).

    REGOLA (correzione 11/11/2026): la copia per la dashboard non fa MAI
    fallire l'esecuzione. Il punto unico che lo garantisce e' qui: i due
    ingressi pubblici (:meth:`add_rows`, chiamato dal «tee» del cursore in
    streaming dentro il ``fetchmany`` del writer, e :meth:`add_dataframe`)
    intercettano qualunque errore, registrano e mettono il raccoglitore in
    ``failed``: da quel momento non raccoglie piu' nulla e :func:`write` non
    scrive la copia (la dashboard risulta «dati non disponibili», 410).
    Nessun chiamante ha bisogno di un suo ``try``."""

    def __init__(self, *, max_rows: Optional[int] = None, max_bytes: Optional[int] = None) -> None:
        self.max_rows = int(max_rows if max_rows is not None else grafana_settings.data_max_rows())
        self.max_bytes = int(max_bytes if max_bytes is not None else grafana_settings.data_max_bytes())
        self.rows = 0
        self.truncated = False
        self.failed = False
        self._chunks: List[bytes] = []
        self._size = 2  # "[" + "]"

    @property
    def full(self) -> bool:
        return self.truncated or self.rows >= self.max_rows

    def _fail(self) -> None:
        self.failed = True
        self._chunks = []
        logger.warning("[dashboard] righe per la dashboard non raccolte: copia non scritta", exc_info=True)

    def add_rows(self, columns: Sequence[object], rows: Iterable[Sequence[Any]]) -> None:
        if self.failed:
            return
        try:
            self._add_rows(columns, rows)
        except Exception:  # regola: la copia non fa mai fallire l'esecuzione
            self._fail()

    def add_dataframe(self, df: Any, *, skip_columns: Sequence[str] = ()) -> None:
        if self.failed:
            return
        try:
            self._add_dataframe(df, skip_columns=skip_columns)
        except Exception:  # regola: la copia non fa mai fallire l'esecuzione
            self._fail()

    def _add_rows(self, columns: Sequence[object], rows: Iterable[Sequence[Any]]) -> None:
        names = [str(name) for name in columns]
        for row in rows:
            if self.full:
                self.truncated = True
                return
            record = {name: _jsonable(value) for name, value in zip(names, row)}
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            extra = len(encoded) + (1 if self._chunks else 0)
            if self._size + extra > self.max_bytes:
                self.truncated = True
                return
            self._chunks.append(encoded)
            self._size += extra
            self.rows += 1

    def _add_dataframe(self, df: Any, *, skip_columns: Sequence[str] = ()) -> None:
        remaining = self.max_rows - self.rows
        if remaining <= 0 or df is None:
            self.truncated = self.truncated or df is not None and len(df) > 0
            return
        head = df.head(remaining)
        skipped = {str(name) for name in skip_columns}
        columns = [col for col in head.columns if str(col) not in skipped]
        self._add_rows(columns, head[columns].itertuples(index=False, name=None))
        if len(df) > remaining:
            self.truncated = True

    def to_bytes(self) -> bytes:
        if self.failed:
            raise RuntimeError("raccolta delle righe per la dashboard fallita")
        return b"[" + b",".join(self._chunks) + b"]"

    def save(self, task_id: str, *, group_id: str) -> Optional[str]:
        """Scrive la copia DOPO l'output riuscito; uno storage che fallisce
        si registra e basta (stessa regola: mai far fallire l'esecuzione)."""
        try:
            return write(task_id, group_id=group_id, collector=self)
        except Exception:  # regola: la copia non fa mai fallire l'esecuzione
            logger.warning(f"[Task {task_id}] copia dati per la dashboard non scritta", exc_info=True)
            return None


# ── Porta ───────────────────────────────────────────────────────────────────

def _task_keys(task_id: str, group_id: str) -> list[str]:
    return [key for key in storage.find_keys(task_id, group_id=group_id) if is_dashboard_data_key(key)]


def write(
    task_id: str,
    *,
    group_id: str,
    collector: DashboardRowsCollector,
    now: Optional[float] = None,
) -> Optional[str]:
    """Scrive (o sostituisce) la copia del task; scade dopo il TTL. Con il
    raccoglitore ``failed`` non scrive nulla e toglie una copia precedente
    (mai dati di un'altra esecuzione o parziali)."""
    if collector.failed:
        delete(task_id, group_id=group_id)
        return None
    current = time.time() if now is None else now
    expires_at = int(current) + grafana_settings.data_ttl_seconds()
    stale = _task_keys(task_id, group_id)
    key = storage.save_bytes(task_id, data_filename(expires_at), collector.to_bytes(), group_id=group_id)
    for old in stale:
        if old != key:
            storage.delete_key(old, expected_group_id=group_id)
    logger.info(f"[dashboard] copia dati task {task_id}: {collector.rows} righe (troncata={collector.truncated})")
    return key


def current_key(task_id: str, *, group_id: str, now: Optional[float] = None) -> tuple[str, int]:
    """La copia valida del task e la sua scadenza; ``DashboardDataGone`` se
    non c'e' o e' scaduta (la copia scaduta si cancella subito)."""
    current = time.time() if now is None else now
    candidates = sorted(
        ((expires_at_of(key) or 0, key) for key in _task_keys(task_id, group_id)), reverse=True,
    )
    if not candidates:
        raise DashboardDataGone(task_id)
    expires_at, key = candidates[0]
    if expires_at <= current:
        for _exp, expired in candidates:
            try:
                storage.delete_key(expired, expected_group_id=group_id)
            except Exception:
                logger.warning(f"[dashboard] cancellazione copia scaduta {expired} fallita", exc_info=True)
        raise DashboardDataGone(task_id)
    return key, expires_at


def read(task_id: str, *, group_id: str, now: Optional[float] = None) -> bytes:
    """Lettura NON distruttiva della copia (array JSON)."""
    key, _expires_at = current_key(task_id, group_id=group_id, now=now)
    limit = grafana_settings.data_max_bytes()
    stream = storage.open_task_stream(task_id, key, expected_group_id=group_id)
    body = stream["body"]
    try:
        chunks: list[bytes] = []
        size = 0
        for chunk in ([body] if isinstance(body, (bytes, bytearray)) else body):
            data = chunk if isinstance(chunk, bytes) else bytes(chunk)
            size += len(data)
            if size > limit + 2:
                raise ValueError("copia dati dashboard oltre il limite")
            chunks.append(data)
        return b"".join(chunks)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


def delete(task_id: str, *, group_id: str) -> int:
    deleted = 0
    for key in _task_keys(task_id, group_id):
        storage.delete_key(key, expected_group_id=group_id)
        deleted += 1
    return deleted


def purge_expired(group_ids: Iterable[str], *, now: Optional[float] = None) -> int:
    """Beat: cancella le copie scadute di tutti i task dei gruppi dati."""
    current = time.time() if now is None else now
    removed = 0
    for group_id in group_ids:
        try:
            keys = storage.list_domain_keys(group_id=str(group_id))
        except Exception:
            logger.warning(f"[dashboard] elenco copie del gruppo {group_id} fallito", exc_info=True)
            continue
        for key in keys:
            expires_at = expires_at_of(key) if is_dashboard_data_key(key) else None
            if expires_at is None or expires_at > current:
                continue
            try:
                storage.delete_key(key, expected_group_id=str(group_id))
                removed += 1
            except Exception:
                logger.warning(f"[dashboard] cancellazione copia scaduta {key} fallita", exc_info=True)
    return removed


# Ogni chiamante delle funzioni a basso livello di ``eseguibili.storage``
# (lettura, scrittura, cancellazione nella cartella del task), con cosa fa
# della copia della dashboard. Il test di enumerazione
# (tests2/eseguibili/unit/test_dashboard_data_port.py) analizza backend/app e
# workers e fallisce se un chiamante nuovo non e' dichiarato qui.
# Valori: "porta" (questa porta), "pulizia completa" (X: la cancella),
# "consumo download" (K: la conserva), "esclude" (non la vede: filtro degli
# artifact interni o chiave esplicita di altro artifact), "scrive output"
# (scrive un altro oggetto nella cartella, mai la copia).
STORAGE_ACCESS_SITES: dict[tuple[str, str], str] = {
    ("app/core/eseguibili/dashboard_data.py", "_task_keys"): "porta",
    ("app/core/eseguibili/dashboard_data.py", "write"): "porta",
    ("app/core/eseguibili/dashboard_data.py", "current_key"): "porta",
    ("app/core/eseguibili/dashboard_data.py", "read"): "porta",
    ("app/core/eseguibili/dashboard_data.py", "delete"): "porta",
    ("app/core/eseguibili/dashboard_data.py", "purge_expired"): "porta",
    ("app/core/eseguibili/output_store.py", "delete_task_outputs"): "pulizia completa / consumo download",
    ("app/core/eseguibili/output_store.py", "find_output_keys"): "esclude",
    ("app/core/eseguibili/output_store.py", "open_stream"): "esclude",
    ("app/core/eseguibili/output_store.py", "save_task_owner"): "scrive output",
    ("app/core/eseguibili/output_store.py", "load_task_owner"): "esclude",
    ("app/core/eseguibili/output_store.py", "delete_task_owner"): "esclude",
    ("app/core/eseguibili/output_store.py", "task_owner_key"): "esclude",
    ("app/core/eseguibili/output_persistence.py", "_writer"): "scrive output",
    # Artifact interno rest_file: scrive il suo, rilegge il suo per chiave.
    ("app/core/arricchisci/api_connection_rest_file.py", "_execute_rest_file"): "scrive output",
    ("app/core/arricchisci/api_connection_rest_file.py", "_resolve_input_path_for_file_processor"): "esclude",
    ("workers/tasks/enrichment_task.py", "execute_enrichment_task"): "scrive output",
    ("workers/tasks/pipeline_task.py", "_persist_executable_output"): "scrive output",
}


__all__ = [
    "DATA_PREFIX",
    "DashboardDataGone",
    "DashboardRowsCollector",
    "STORAGE_ACCESS_SITES",
    "current_key",
    "data_filename",
    "delete",
    "expires_at_of",
    "is_dashboard_data_key",
    "purge_expired",
    "read",
    "write",
]
