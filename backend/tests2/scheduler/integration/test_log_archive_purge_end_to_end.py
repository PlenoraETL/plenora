"""Integration e2e del pipeline archive+purge (O-ARC Batch 4).

Copertura 5 scenari contro PG reale + fake in-memory ObjectStorage
(pattern clone di fakeredis in test_leader_election_e2e.py):

- B4-E1: happy path logs (50 righe archive-eligible → upload NDJSON gzip,
  DELETE bypass, riga observability circolare su plenora.logs).
- B4-E2: happy path audit_actions (gemello B4-E1, observability va su
  plenora.logs anche per archive di audit_actions).
- B4-E3: DELETE senza SET LOCAL retention_cleanup → trigger append-only
  solleva (redundant guard sul cluster O-ARC, §4.3 triage Batch 4).
- B4-E4: re-run idempotent (run 2 senza righe fresh → archived_rows=0,
  batch_runs=0, storage immutato da run 1).
- B4-E5: upload failure → swallow exception, righe restano in DB, slot
  non aggiornato, nessuna riga observability.

Nessun moto/localstack/Docker: il Protocol ObjectStorage e' implementato
da una classe in-memory (`_InMemoryObjectStorage`) ~40 righe. Il contract
S3 reale e' gia' coperto in Batch 1 da `test_log_archive_writer_contract`.

Cleanup: ogni test patch-a `LOG_ARCHIVE_KEY_PREFIX` con un marker unico
`logs-archive/test-oarc-<uuid>/` + marker su op/action delle righe
seed. Il teardown fa DELETE con `SET LOCAL plenora.retention_cleanup`
sia sulle righe sorgenti sia sulla riga observability (filtrata via
`details` JSONB contenente il key prefix unico del test).

Skip graceful se `app.db.pg_connection.get_connection` non riesce ad
aprire una connessione.
"""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.scheduler, pytest.mark.contract]


# Fake in-memory ObjectStorage (Protocol-compatible)


class _InMemoryObjectStorage:
    """Fake in-memory ObjectStorage per Batch 4 integration e2e.

    Implementa il Protocol minimo esercitato da LogArchiveWriter:
    `upload_bytes(data, key)` + `list_objects(prefix)`. Storage in-process
    (dict bytes), nessuna rete, nessuna persistenza. `upload_error`
    opzionale solleva l'eccezione passata al prossimo `upload_bytes`
    (scenario B4-E5).
    """

    def __init__(self, upload_error: BaseException | None = None):
        self._store: dict[str, bytes] = {}
        self._upload_error = upload_error

    def upload_bytes(self, data: bytes, key: str) -> None:
        if self._upload_error is not None:
            raise self._upload_error
        self._store[key] = data

    def list_objects(self, prefix: str) -> list[dict]:
        return [
            {
                "key": k,
                "last_modified": datetime.now(timezone.utc),
                "size": len(v),
            }
            for k, v in self._store.items()
            if k.startswith(prefix)
        ]

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    # I metodi sotto non sono invocati dal path archive+purge. Implementati
    # come no-op/raise per completare il Protocol senza introdurre branch
    # accidentali.
    def upload(self, local_path, key: str) -> None:  # pragma: no cover
        raise NotImplementedError("stream upload non usato in archive e2e")

    def download(self, key: str, local_path) -> None:  # pragma: no cover
        raise NotImplementedError

    def get_object_stream(self, key: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    # Helper solo-test: decompress NDJSON gzip per verifiche contenuto.
    def decode_ndjson(self, key: str) -> list[dict]:
        raw = gzip.decompress(self._store[key])
        return [json.loads(ln) for ln in raw.splitlines() if ln]


# Skip graceful su PG non raggiungibile


def _pg_available() -> bool:
    """True se pg_connection pool apre una connessione e risponde a
    `SELECT 1 FROM logs LIMIT 0`. Copre sia il caso `DATABASE_URL` unset
    sia il caso schema `plenora.logs` non inizializzato nel DB puntato.
    """
    try:
        from app.db.pg_connection import get_connection, put_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM logs LIMIT 0")
        finally:
            put_connection(conn)
        return True
    except Exception:
        return False


_SKIP_NO_PG = pytest.mark.skipif(
    not _pg_available(),
    reason="Richiede PG reale raggiungibile + schema plenora.logs inizializzato",
)


# Helpers di test


def _make_scheduler():
    """Scheduler con bypass __init__ (pattern clonato da unit contract +
    leader_election_e2e). Azzera tutti gli slot time-gate.
    """
    from workers.beat_scheduler import PlenoraBeatScheduler

    scheduler = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    scheduler._last_stuck_check = 0.0
    scheduler._last_eseguibili_cleanup = 0.0
    scheduler._last_logs_retention = 0.0
    scheduler._last_logs_archive = 0.0
    scheduler._last_audit_archive = 0.0
    scheduler._last_schedule_logs_retention = 0.0
    scheduler._last_imdf_bundles_cleanup = 0.0
    scheduler._last_temp_orphan_cleanup = 0.0
    return scheduler


def _settings_ns(key_prefix: str, **overrides) -> SimpleNamespace:
    defaults = dict(
        LOG_ARCHIVE_ENABLED=True,
        LOG_RETENTION_DAYS=30,
        AUDIT_RETENTION_DAYS=365,
        LOG_ARCHIVE_KEY_PREFIX=key_prefix,
        LOG_ARCHIVE_BATCH_ROWS=10_000,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _bypass_delete(table: str, where_clause: str, params: tuple) -> int:
    """DELETE sullo stesso cursor con `SET LOCAL plenora.retention_cleanup`.

    Unico path consentito dai trigger append-only. Usato sia dai test
    per cleanup dei dati seed sia per cleanup della riga observability
    emessa dagli hook.
    """
    from app.db.pg_connection import transaction

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL plenora.retention_cleanup = 'true'")
            cur.execute(
                f"DELETE FROM plenora.{table} WHERE {where_clause}", params
            )
            return cur.rowcount


@pytest.fixture
def test_marker():
    """Marker unico per test (uuid 12 hex). Usato in key_prefix
    observability e come op/action delle righe seed. Cleanup teardown:
    - righe seed su plenora.logs con op = <marker>
    - righe seed su plenora.audit_actions con action = <marker>
    - righe observability su plenora.logs con details LIKE key_prefix
    - righe observability empty-batch (details senza key_prefix perche'
      object_keys=[]): filtro per ts >= started_at sullo scope
      module='workers' + op='log_archive'. Safe perche' scheduler/
      integration serial (niente xdist) + beat hook non gira in background.

    Il key_prefix unico per test permette cleanup chirurgico della riga
    observability (scritta con INSERT diretta, passa trigger senza bypass)
    senza falsi positivi cross-test in CI concorrente.
    """
    marker = f"oarc-e2e-{uuid.uuid4().hex[:12]}"
    key_prefix = f"logs-archive/test-{marker}/"
    started_at = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3]
    yield {
        "marker": marker,
        "key_prefix": key_prefix,
        "started_at": started_at,
    }
    # Teardown best-effort: errori di cleanup non mascherano fail del test
    for table, clause, params in (
        ("logs", "op = %s", (marker,)),
        ("audit_actions", "action = %s", (marker,)),
        (
            "logs",
            "op = 'log_archive' AND details::text LIKE %s",
            (f"%{marker}%",),
        ),
        # Empty-batch obs rows (details senza key_prefix). Finestra ts
        # ristretta a questo test per evitare cleanup cross-test.
        (
            "logs",
            "op = 'log_archive' AND module = 'workers' AND ts >= %s",
            (started_at,),
        ),
    ):
        try:
            _bypass_delete(table, clause, params)
        except Exception:
            pass


def _seed_log_row(marker: str, ts_offset_days: int, idx: int = 0) -> None:
    """INSERT una riga in plenora.logs con ts = now - ts_offset_days."""
    from app.db.pg_connection import execute as pg_execute

    ts = (
        datetime.now(timezone.utc) - timedelta(days=ts_offset_days)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    pg_execute(
        "INSERT INTO logs (ts, level, module, op, msg, "
        "project_id, group_id, run_id, node_id, details, error) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            ts, "INFO", "test.oarc.e2e", marker,
            f"{marker} seed row {idx}",
            "", "", "", "", None, "",
        ),
    )


def _seed_audit_row(marker: str, ts_offset_days: int, idx: int = 0) -> None:
    """INSERT una riga in plenora.audit_actions con timestamp = now - offset."""
    from app.db.pg_connection import execute as pg_execute

    ts = datetime.now(timezone.utc) - timedelta(days=ts_offset_days)
    pg_execute(
        "INSERT INTO plenora.audit_actions "
        "(timestamp, user_id, action, target, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (ts, f"user_{idx}", marker, f"target_{idx}", 200),
    )


def _count_rows(table: str, where_clause: str, params: tuple) -> int:
    from app.db.pg_connection import fetchall

    rows = fetchall(
        f"SELECT COUNT(*) AS c FROM plenora.{table} WHERE {where_clause}",
        params,
    )
    return int(rows[0]["c"]) if rows else 0


def _fetch_observability_rows(
    key_prefix: str, started_at: str | None = None,
) -> list[dict]:
    """Fetch righe observability scritte dal hook con details JSONB
    contenente key_prefix del test (filtro chirurgico cross-test safe).

    Se `started_at` ISO string e' passato, include anche righe empty-batch
    (details senza key_prefix perche' object_keys=[]) emesse da questo
    test (filtro per ts >= started_at + module='workers').
    """
    from app.db.pg_connection import fetchall

    if started_at is None:
        return fetchall(
            "SELECT ts, level, module, op, msg, details "
            "FROM logs WHERE op = 'log_archive' "
            "AND details::text LIKE %s ORDER BY ts ASC",
            (f"%{key_prefix}%",),
        )
    return fetchall(
        "SELECT ts, level, module, op, msg, details "
        "FROM logs WHERE op = 'log_archive' AND module = 'workers' "
        "AND (details::text LIKE %s OR ts >= %s) "
        "ORDER BY ts ASC",
        (f"%{key_prefix}%", started_at),
    )


# B4-E1 — archive+purge logs end-to-end


@_SKIP_NO_PG
def test_end_to_end_archive_and_purge_logs_real_pg(test_marker):
    """B4-E1: 50 righe archive-eligible -> upload NDJSON + purge + 1 obs row.

    Golden path del hook _archive_old_logs su PG reale:
    - setup 50 righe con op=marker, ts=now-40gg (oltre cutoff 30gg)
    - run hook con fake storage injection
    - verifica: 1 object in fake store, 50 righe nel NDJSON decodificato,
      0 righe marker in DB (purged), 1 riga observability con
      details.archived_rows=50.
    """
    marker = test_marker["marker"]
    key_prefix = test_marker["key_prefix"]

    for i in range(50):
        _seed_log_row(marker, ts_offset_days=40, idx=i)

    assert _count_rows("logs", "op = %s", (marker,)) == 50

    fake_storage = _InMemoryObjectStorage()
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings", new=_settings_ns(key_prefix),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=fake_storage,
    ):
        scheduler._archive_old_logs()

    # (a) fake storage ha 1 oggetto con key pattern corretto
    stored_keys = list(fake_storage._store.keys())
    assert len(stored_keys) == 1, f"expected 1 object, got {stored_keys}"
    key = stored_keys[0]
    assert key.startswith(key_prefix + "logs/"), (
        f"key {key!r} non conforme al pattern atteso"
    )
    assert key.endswith(".ndjson.gz")

    # (b) decoded NDJSON ha 50 righe col marker
    lines = fake_storage.decode_ndjson(key)
    assert len(lines) == 50
    assert {l["op"] for l in lines} == {marker}

    # (c) DB purged delle righe marker
    assert _count_rows("logs", "op = %s", (marker,)) == 0, (
        "righe archive-eligible non purged dal DB"
    )

    # (d) riga observability presente con shape attesa
    obs_rows = _fetch_observability_rows(key_prefix)
    assert len(obs_rows) == 1, (
        f"expected 1 observability row, got {len(obs_rows)}"
    )
    obs = obs_rows[0]
    assert obs["level"] == "INFO"
    assert obs["module"] == "workers"
    assert obs["op"] == "log_archive"
    assert "Archive logs: 50 righe" in obs["msg"]

    details = json.loads(obs["details"])
    assert details["table"] == "logs"
    assert details["archived_rows"] == 50
    assert details["batch_runs"] == 1
    assert len(details["object_keys"]) == 1

    # Slot aggiornato (24h gate al prossimo tick)
    assert scheduler._last_logs_archive > 0


# B4-E2 — archive+purge audit_actions end-to-end


@_SKIP_NO_PG
def test_end_to_end_archive_and_purge_audit_actions_real_pg(test_marker):
    """B4-E2: gemello B4-E1 su audit_actions con AUDIT_RETENTION_DAYS=365.

    La riga observability va comunque su plenora.logs (chiusura cerchio
    log-del-log su tabella unica); `details.table="audit_actions"` la
    discrimina dalla riga logs-archive.
    """
    marker = test_marker["marker"]
    key_prefix = test_marker["key_prefix"]

    for i in range(15):
        _seed_audit_row(marker, ts_offset_days=400, idx=i)

    assert _count_rows("audit_actions", "action = %s", (marker,)) == 15

    fake_storage = _InMemoryObjectStorage()
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings", new=_settings_ns(key_prefix),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=fake_storage,
    ):
        scheduler._archive_old_audit_actions()

    stored_keys = list(fake_storage._store.keys())
    assert len(stored_keys) == 1
    key = stored_keys[0]
    assert key.startswith(key_prefix + "audit_actions/")
    assert key.endswith(".ndjson.gz")

    lines = fake_storage.decode_ndjson(key)
    assert len(lines) == 15
    assert {l["action"] for l in lines} == {marker}

    assert _count_rows("audit_actions", "action = %s", (marker,)) == 0

    obs_rows = _fetch_observability_rows(key_prefix)
    assert len(obs_rows) == 1
    obs = obs_rows[0]
    assert "Archive audit_actions: 15 righe" in obs["msg"]
    details = json.loads(obs["details"])
    assert details["table"] == "audit_actions"
    assert details["archived_rows"] == 15

    assert scheduler._last_audit_archive > 0


# B4-E3 — trigger append-only guard (redundant, closes cluster invariant)


@_SKIP_NO_PG
def test_delete_on_logs_without_bypass_raises_from_trigger(test_marker):
    """B4-E3: DELETE senza SET LOCAL retention_cleanup -> trigger solleva.

    Contratto gia' coperto da test_log_immutable_contract.py ma replicato
    qui nel perimetro O-ARC per fissare esplicitamente che il bypass e'
    l'unica via d'uscita del ramo purge del writer. Se questo trigger
    fosse disabilitato/alterato, il path purge continuerebbe a funzionare
    tramite SET LOCAL ma il guardrail difensivo sarebbe perso: il test
    lo chiude dentro il cluster.
    """
    import psycopg2
    from app.db.pg_connection import get_connection, put_connection

    marker = test_marker["marker"]
    _seed_log_row(marker, ts_offset_days=40, idx=0)

    conn = get_connection()
    try:
        # autocommit esplicito per questa fixture locale, evitiamo
        # che l'eccezione aborti altre operazioni
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT sp_no_bypass_delete")
            with pytest.raises(psycopg2.Error) as excinfo:
                cur.execute(
                    "DELETE FROM plenora.logs WHERE op = %s", (marker,)
                )
            cur.execute("ROLLBACK TO SAVEPOINT sp_no_bypass_delete")
        conn.rollback()
    finally:
        put_connection(conn)

    msg = str(excinfo.value).lower()
    assert "append-only" in msg or "retention_cleanup" in msg, (
        f"errore del trigger atteso, ricevuto: {excinfo.value!r}"
    )


# B4-E4 — re-run idempotent su tabella gia' vuota


@_SKIP_NO_PG
def test_re_run_same_day_idempotent_empty_batch(test_marker):
    """B4-E4: run 1 archivia 20 righe; run 2 (stesso giorno, slot reset,
    nessuna riga fresh) non produce nuovi upload e writer ritorna
    archived_rows=0/batch_runs=0.

    Fake storage post-run-2 ha ancora solo il 1 object del run 1 (il
    writer non chiama upload_bytes su batch vuoto). La riga observability
    del run 1 resta; il run 2 ne emette un'altra "vuota" (comportamento
    corrente: osservabilita' emessa anche a archived_rows=0, leggermente
    piu' verbosa del raccomandato §3.3 triage ma fedele al contratto
    pg_execute del hook). L'invariante di idempotenza §4.4 master e'
    sullo storage, non sull'observability.
    """
    marker = test_marker["marker"]
    key_prefix = test_marker["key_prefix"]

    for i in range(20):
        _seed_log_row(marker, ts_offset_days=40, idx=i)

    fake_storage = _InMemoryObjectStorage()
    scheduler = _make_scheduler()
    settings_ns = _settings_ns(key_prefix)

    # Run 1
    with patch(
        "app.config.settings", new=settings_ns,
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=fake_storage,
    ):
        scheduler._archive_old_logs()

    keys_after_run1 = list(fake_storage._store.keys())
    assert len(keys_after_run1) == 1
    assert _count_rows("logs", "op = %s", (marker,)) == 0

    # Reset slot per aggirare 24h gate; nessuna nuova riga seed
    scheduler._last_logs_archive = 0.0

    # Run 2: tabella vuota (zero righe archive-eligible fresh)
    with patch(
        "app.config.settings", new=settings_ns,
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=fake_storage,
    ):
        scheduler._archive_old_logs()

    keys_after_run2 = list(fake_storage._store.keys())
    # Invariante idempotenza storage: nessun nuovo oggetto
    assert keys_after_run2 == keys_after_run1

    # Filtro esteso: la run 2 ha object_keys=[] quindi `details` non
    # contiene key_prefix: cerchiamo anche via ts-window (started_at).
    obs_rows = _fetch_observability_rows(
        key_prefix, started_at=test_marker["started_at"]
    )
    # 2 righe osservabilita' (run 1 con 20 righe, run 2 con 0 righe)
    assert len(obs_rows) == 2
    details_run1 = json.loads(obs_rows[0]["details"])
    details_run2 = json.loads(obs_rows[1]["details"])
    assert details_run1["archived_rows"] == 20
    assert details_run1["batch_runs"] == 1
    assert details_run2["archived_rows"] == 0
    assert details_run2["batch_runs"] == 0
    assert details_run2["object_keys"] == []


# B4-E5 — upload failure: righe restano in DB, no observability


@_SKIP_NO_PG
def test_upload_failure_leaves_rows_in_db_and_no_observability_log(
    test_marker,
):
    """B4-E5: fake storage.upload_bytes solleva IOError.

    Verifiche:
    (a) hook non propaga (swallow Batch 3 invariato)
    (b) le 10 righe seed restano in plenora.logs
    (c) _last_logs_archive non aggiornato (retry al prossimo tick)
    (d) nessuna riga observability con questo key_prefix
        (success-only §B3 ADR-0014)
    """
    marker = test_marker["marker"]
    key_prefix = test_marker["key_prefix"]

    for i in range(10):
        _seed_log_row(marker, ts_offset_days=40, idx=i)

    fake_storage = _InMemoryObjectStorage(
        upload_error=IOError("fake storage down")
    )
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings", new=_settings_ns(key_prefix),
    ), patch(
        "app.shared.cloud_backends.get_log_archive_storage",
        return_value=fake_storage,
    ):
        # (a) must not raise
        scheduler._archive_old_logs()

    # (b) righe seed intatte
    assert _count_rows("logs", "op = %s", (marker,)) == 10, (
        "righe seed non devono essere purgate su upload failure"
    )
    # Fake store vuoto (upload non e' mai riuscito)
    assert fake_storage._store == {}

    # (c) slot non aggiornato: retry alla prossima tick-elapsed
    assert scheduler._last_logs_archive == 0.0

    # (d) nessuna riga observability con questo key_prefix
    obs_rows = _fetch_observability_rows(key_prefix)
    assert obs_rows == [], (
        f"observability deve essere success-only; trovato {obs_rows!r}"
    )
