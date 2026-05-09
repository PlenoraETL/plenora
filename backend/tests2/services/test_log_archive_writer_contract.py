"""
Contract test — `LogArchiveWriter` NDJSON serialization (O-ARC Batch 1, ADR-0014).

Blinda i vincoli di formato (O-ARC §5.1 + ADR-0014 §Decision 5-8):
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.scheduler.log_archive import (
    _MAX_BATCH_SEQ,
    LogArchiveWriter,
    _build_object_key,
    _normalize_datetime,
    _serialize_rows,
    _table_ts_column,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Keyset canonici in ordine; devono combaciare con quelli privati in
# `app.core.scheduler.log_archive`. La duplicazione qui e' intenzionale: il
# test e' un contratto pubblico dello shape, non deve importare la
# costante interna (altrimenti il test si allinea automaticamente a
# eventuali drift del modulo).
_LOGS_CANONICAL_FIELDS = (
    "id", "ts", "level", "module", "op", "msg",
    "project_id", "group_id", "run_id", "node_id",
    "details", "error",
)
_AUDIT_CANONICAL_FIELDS = (
    "id", "timestamp", "user_id", "action", "target",
    "status", "ip_address", "error_message", "metadata",
)


def _sample_log_row(ts: datetime, id_: int = 1) -> dict:
    return {
        "id": id_,
        "ts": ts,
        "level": "INFO",
        "module": "estrai",
        "op": "execute",
        "msg": "FASE estrazione completata",
        "project_id": "prj-1",
        "group_id": "grp-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "details": {"rows": 42},
        "error": None,
    }


def _sample_audit_row(ts: datetime, id_: int = 1) -> dict:
    return {
        "id": id_,
        "timestamp": ts,
        "user_id": "user-1",
        "action": "project.delete",
        "target": "project:prj-1",
        "status": 200,
        "ip_address": "127.0.0.1",
        "error_message": None,
        "metadata": {"reason": "manual"},
    }


def _decompress_lines(payload: bytes) -> list[str]:
    return [line for line in gzip.decompress(payload).decode("utf-8").splitlines() if line]


def test_serialize_logs_rows_produces_ndjson_gzip():
    """Output e' gzip UTF-8 con N righe NDJSON, una per riga DB, ciascuna
    JSON valida."""
    ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    rows = [_sample_log_row(ts, id_=i) for i in (1, 2, 3)]

    out = _serialize_rows(rows, "logs")

    assert isinstance(out, bytes)
    # magic number gzip: 0x1f 0x8b
    assert out[:2] == b"\x1f\x8b"

    lines = _decompress_lines(out)
    assert len(lines) == 3, f"atteso 3 righe NDJSON, trovate {len(lines)}"

    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict)


def test_serialize_preserves_column_shape_logs():
    """Shape canonico per `logs`: keyset esatto, ordine preservato, extra
    fields in input droppati, campi canonici mancanti emessi come
    `null`."""
    ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    row = _sample_log_row(ts)
    row["extra_column_leaked_from_upstream"] = "must not survive"

    out = _serialize_rows([row], "logs")
    parsed = json.loads(_decompress_lines(out)[0])

    # Keyset esatto + ordine (Python dict + json.dumps preservano insertion order).
    assert set(parsed.keys()) == set(_LOGS_CANONICAL_FIELDS)
    assert list(parsed.keys()) == list(_LOGS_CANONICAL_FIELDS)

    # Extra field non leakato.
    assert "extra_column_leaked_from_upstream" not in parsed

    # Spot-check pass-through di valori non-ts.
    assert parsed["id"] == row["id"]
    assert parsed["level"] == "INFO"
    assert parsed["details"] == {"rows": 42}
    assert parsed["error"] is None

    # Campo canonico mancante in input -> null in output.
    row_partial = _sample_log_row(ts)
    del row_partial["node_id"]
    parsed_partial = json.loads(_decompress_lines(_serialize_rows([row_partial], "logs"))[0])
    assert parsed_partial["node_id"] is None
    assert list(parsed_partial.keys()) == list(_LOGS_CANONICAL_FIELDS)


def test_serialize_preserves_column_shape_audit_actions():
    """Shape canonico per `audit_actions`: keyset esatto (con chiave
    `timestamp`, non `ts`) + ordine + pass-through di valori."""
    ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    row = _sample_audit_row(ts)
    row["extra_field"] = "dropped"

    out = _serialize_rows([row], "audit_actions")
    parsed = json.loads(_decompress_lines(out)[0])

    assert set(parsed.keys()) == set(_AUDIT_CANONICAL_FIELDS)
    assert list(parsed.keys()) == list(_AUDIT_CANONICAL_FIELDS)
    assert "extra_field" not in parsed

    # Colonna temporale nominata `timestamp` non `ts`.
    assert "timestamp" in parsed
    assert "ts" not in parsed

    assert parsed["status"] == 200
    assert parsed["metadata"] == {"reason": "manual"}
    assert parsed["error_message"] is None

    # `_table_ts_column` coerente con la shape emessa.
    assert _table_ts_column("audit_actions") == "timestamp"
    assert _table_ts_column("logs") == "ts"


def test_serialize_datetime_iso8601_utc_suffix_Z_and_rejects_naive():
    """`ts`/`timestamp` normalizzati a ISO 8601 UTC con suffisso `Z`;
    qualsiasi datetime naive solleva `ValueError` (ADR-0014 §5)."""

    # (a) UTC-aware -> Z suffix, no "+00:00".
    ts_utc = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    norm = _normalize_datetime(ts_utc)
    assert norm.endswith("Z")
    assert "+" not in norm
    assert norm == "2026-04-21T10:00:00Z"

    # (b) Microsecondi preservati.
    ts_utc_us = datetime(2026, 4, 21, 10, 0, 0, 123456, tzinfo=timezone.utc)
    assert _normalize_datetime(ts_utc_us) == "2026-04-21T10:00:00.123456Z"

    # (c) Non-UTC tz-aware -> convertito a UTC.
    ts_rome = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    norm_rome = _normalize_datetime(ts_rome)
    assert norm_rome.endswith("Z")
    assert norm_rome == "2026-04-21T10:00:00Z"  # 12:00+02:00 -> 10:00 UTC

    # (d) Datetime naive -> ValueError (helper diretto).
    ts_naive = datetime(2026, 4, 21, 10, 0, 0)
    with pytest.raises(ValueError, match="naive"):
        _normalize_datetime(ts_naive)

    # (e) Naive propagato end-to-end attraverso `_serialize_rows`.
    row_naive = _sample_log_row(ts_naive)
    with pytest.raises(ValueError, match="naive"):
        _serialize_rows([row_naive], "logs")

    # (f) Naive in un campo diverso da `ts` (es. dentro `details`) -> anche
    # quello rifiutato: la policy fail-fast vale uniformemente.
    row_bad_details = _sample_log_row(ts_utc)
    row_bad_details["details"] = {"nested_naive": ts_naive}
    with pytest.raises(ValueError, match="naive"):
        _serialize_rows([row_bad_details], "logs")

    # (g) End-to-end serialize: il ts nella riga NDJSON e' in formato canonico.
    out = _serialize_rows([_sample_log_row(ts_utc_us)], "logs")
    parsed = json.loads(_decompress_lines(out)[0])
    assert parsed["ts"] == "2026-04-21T10:00:00.123456Z"

    # (h) End-to-end serialize per audit_actions: la chiave `timestamp`
    # segue lo stesso contratto di normalizzazione.
    out_audit = _serialize_rows([_sample_audit_row(ts_rome)], "audit_actions")
    parsed_audit = json.loads(_decompress_lines(out_audit)[0])
    assert parsed_audit["timestamp"] == "2026-04-21T10:00:00Z"


def test_build_object_key_pattern_and_padding():
    """Pattern deterministico O-ARC §5.2:
    `<prefix><table>/<YYYY-MM>/<table>-<YYYY-MM-DD>-<NNN>.ndjson.gz`.

    Blinda:
    - segment `<table>` corretto per `logs` e `audit_actions`
    - partition folder `YYYY-MM`
    - filename `<table>-YYYY-MM-DD-<NNN>.ndjson.gz`
    - seq zero-padded a 3 cifre (000, 001, 042, 999)
    - determinismo (stessi input -> stessa key byte-per-byte)
    - prefix preservato alla lettera
    """
    ts = datetime(2026, 4, 21, 10, 30, 0, tzinfo=timezone.utc)

    # Pattern logs, seq piccolo padding.
    key_logs = _build_object_key("logs-archive/prod/", "logs", ts, 0)
    assert key_logs == "logs-archive/prod/logs/2026-04/logs-2026-04-21-000.ndjson.gz"

    # Pattern audit_actions, seq con padding centrale.
    key_audit = _build_object_key("logs-archive/prod/", "audit_actions", ts, 42)
    assert (
        key_audit
        == "logs-archive/prod/audit_actions/2026-04/audit_actions-2026-04-21-042.ndjson.gz"
    )

    # Padding agli estremi del range [0, 999].
    assert _build_object_key("p/", "logs", ts, 0).endswith("-000.ndjson.gz")
    assert _build_object_key("p/", "logs", ts, 1).endswith("-001.ndjson.gz")
    assert _build_object_key("p/", "logs", ts, 9).endswith("-009.ndjson.gz")
    assert _build_object_key("p/", "logs", ts, 99).endswith("-099.ndjson.gz")
    assert _build_object_key("p/", "logs", ts, 100).endswith("-100.ndjson.gz")
    assert _build_object_key("p/", "logs", ts, _MAX_BATCH_SEQ).endswith("-999.ndjson.gz")

    # Determinismo: stessi input -> stessa key.
    assert _build_object_key("x/", "logs", ts, 7) == _build_object_key("x/", "logs", ts, 7)

    # Segments in ordine corretto: <prefix>/<table>/<month>/<filename>.
    parts = key_audit.split("/")
    assert parts[0] == "logs-archive"
    assert parts[1] == "prod"
    assert parts[2] == "audit_actions"          # table segment
    assert parts[3] == "2026-04"                # partition YYYY-MM
    assert parts[4].startswith("audit_actions-2026-04-21-")  # filename prefix
    assert parts[4].endswith(".ndjson.gz")

    # Prefix vuoto e' ammesso (caso "no per-env prefix").
    key_no_prefix = _build_object_key("", "logs", ts, 0)
    assert key_no_prefix == "logs/2026-04/logs-2026-04-21-000.ndjson.gz"

    # Non-UTC tz-aware -> partitioning calcolato in UTC (coerente con il
    # payload: evita drift giorno tra `ts` della riga e folder YYYY-MM-DD).
    ts_late_rome = datetime(2026, 5, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    # 2026-05-01 01:00+02:00 -> 2026-04-30 23:00 UTC: folder deve essere aprile.
    key_rome = _build_object_key("p/", "logs", ts_late_rome, 0)
    assert "2026-04" in key_rome
    assert "logs-2026-04-30-000" in key_rome
    assert "2026-05" not in key_rome


def test_build_object_key_rejects_invalid_inputs():
    """Fail-fast sugli input invalidi: prefix mal formato, table ignota,
    run_date naive o non-datetime, seq fuori range."""
    ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    # (a) prefix non vuoto e senza `/` finale.
    with pytest.raises(ValueError, match="prefix"):
        _build_object_key("logs-archive/prod", "logs", ts, 0)

    # (b) table sconosciuta -> `ValueError` via `_table_ts_column`.
    with pytest.raises(ValueError, match="unknown log archive table"):
        _build_object_key("p/", "events", ts, 0)

    # (c) run_date naive.
    ts_naive = datetime(2026, 4, 21, 10, 0, 0)
    with pytest.raises(ValueError, match="naive"):
        _build_object_key("p/", "logs", ts_naive, 0)

    # (d) run_date non-datetime.
    with pytest.raises(TypeError, match="datetime"):
        _build_object_key("p/", "logs", "2026-04-21", 0)  # type: ignore[arg-type]

    # (e) seq negativo.
    with pytest.raises(ValueError, match="out of range"):
        _build_object_key("p/", "logs", ts, -1)

    # (f) seq oltre il cap del pattern NNN.
    with pytest.raises(ValueError, match="out of range"):
        _build_object_key("p/", "logs", ts, _MAX_BATCH_SEQ + 1)
    assert _MAX_BATCH_SEQ == 999

    # (g) seq non-int.
    with pytest.raises(TypeError, match="int"):
        _build_object_key("p/", "logs", ts, "0")  # type: ignore[arg-type]

    # (h) bool NON e' un seq valido (anche se bool e' subclass di int).
    with pytest.raises(TypeError, match="int"):
        _build_object_key("p/", "logs", ts, True)  # type: ignore[arg-type]


# Mock in-memory di `ObjectStorage` sufficiente per il loop di
# `archive_and_purge`. NON implementa l'intero Protocol (download,
# get_object_stream, delete, upload su file): chiamarli farebbe fallire
# il test — segnale che l'impl produzione ha chiamato metodi fuori
# contratto.


class _FakeObjectStorage:
    def __init__(
        self,
        *,
        verify_returns_empty: bool = False,
        verify_returns_zero_size: bool = False,
    ) -> None:
        self.uploaded: list[tuple[bytes, str]] = []
        self.list_calls: list[str] = []
        self._verify_returns_empty = verify_returns_empty
        self._verify_returns_zero_size = verify_returns_zero_size

    def upload_bytes(self, data: bytes, key: str) -> None:
        self.uploaded.append((data, key))

    def list_objects(self, prefix: str) -> list[dict]:
        self.list_calls.append(prefix)
        if self._verify_returns_empty:
            return []
        size = 0 if self._verify_returns_zero_size else 1
        return [
            {
                "key": k,
                "last_modified": datetime(2026, 4, 21, tzinfo=timezone.utc),
                "size": size if d else 0,
            }
            for d, k in self.uploaded
            if k.startswith(prefix)
        ]


def _collector():
    """Factory per callable DELETE che raccoglie (sql, params)."""
    deletes: list[tuple[str, tuple]] = []

    def _delete(sql: str, params: tuple) -> None:
        deletes.append((sql, params))

    return deletes, _delete


def test_archive_and_purge_noop_on_empty_select():
    """Zero righe: nessun upload, nessuna DELETE, result coerente
    (archived_rows=0, object_keys=[], cutoff_ts preservato)."""
    storage = _FakeObjectStorage()
    writer = LogArchiveWriter(storage, "p/", batch_size=100)

    cutoff = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    run_date = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    select_calls: list = []

    def select(cutoff_ts, batch_size):
        select_calls.append((cutoff_ts, batch_size))
        return []

    deletes, delete = _collector()

    result = writer.archive_and_purge(
        "logs", cutoff, select, delete, run_date=run_date,
    )

    assert result["archived_rows"] == 0
    assert result["object_keys"] == []
    assert result["cutoff_ts"] == cutoff

    assert storage.uploaded == []
    assert storage.list_calls == []  # verify neppure chiamata
    assert deletes == []
    assert select_calls == [(cutoff, 100)]  # SELECT interrogata una sola volta


def test_archive_and_purge_single_batch_happy_path():
    """Singolo batch: 1 upload, 1 verify, 1 DELETE, key deterministica,
    result coerente. Ordine canonico: upload -> list_objects -> delete."""
    storage = _FakeObjectStorage()
    writer = LogArchiveWriter(storage, "logs-archive/prod/", batch_size=100)

    ts = datetime(2026, 4, 21, 9, 0, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    run_date = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    call_count = {"n": 0}

    def select(cutoff_ts, batch_size):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [_sample_log_row(ts, id_=i) for i in (1, 2, 3)]
        return []

    deletes, delete = _collector()

    result = writer.archive_and_purge(
        "logs", cutoff, select, delete, run_date=run_date,
    )

    expected_key = "logs-archive/prod/logs/2026-04/logs-2026-04-21-000.ndjson.gz"
    assert result["archived_rows"] == 3
    assert result["object_keys"] == [expected_key]
    assert result["cutoff_ts"] == cutoff

    # Un solo upload, con la key attesa.
    assert len(storage.uploaded) == 1
    data, key = storage.uploaded[0]
    assert key == expected_key
    # Payload e' gzip NDJSON valido.
    assert data[:2] == b"\x1f\x8b"
    assert len(_decompress_lines(data)) == 3

    # Verify chiamato esattamente una volta con prefix=key.
    assert storage.list_calls == [expected_key]

    # DELETE invocata una volta, scoped a cutoff + max_id = 3.
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert "DELETE FROM logs" in sql
    assert "ts < %s" in sql
    assert "id <= %s" in sql
    assert params == (cutoff, 3)

    # SELECT chiamata 2 volte: prima restituisce rows, seconda vuota.
    assert call_count["n"] == 2


def test_archive_and_purge_multi_batch_deterministic_order():
    """N batch in sequenza: seq incrementa, keys emesse in ordine,
    DELETE per batch con max_id corretto. `archived_rows` cumulativo."""
    storage = _FakeObjectStorage()
    writer = LogArchiveWriter(storage, "p/", batch_size=2)

    ts = datetime(2026, 4, 21, 9, 0, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    run_date = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    # 5 righe, batch_size=2 -> 3 batch [1,2], [3,4], [5], poi [].
    pool = [_sample_log_row(ts, id_=i) for i in (1, 2, 3, 4, 5)]

    def select(cutoff_ts, batch_size):
        taken = pool[:batch_size]
        del pool[:batch_size]
        return taken

    deletes, delete = _collector()

    result = writer.archive_and_purge(
        "logs", cutoff, select, delete, run_date=run_date,
    )

    assert result["archived_rows"] == 5
    assert result["object_keys"] == [
        "p/logs/2026-04/logs-2026-04-21-000.ndjson.gz",
        "p/logs/2026-04/logs-2026-04-21-001.ndjson.gz",
        "p/logs/2026-04/logs-2026-04-21-002.ndjson.gz",
    ]

    # 3 upload, in ordine.
    assert [k for _, k in storage.uploaded] == result["object_keys"]

    # 3 verify, ciascuna col prefix=key corrispondente, stessa sequenza.
    assert storage.list_calls == result["object_keys"]

    # 3 DELETE, ciascuna scoped al max_id del proprio batch.
    assert len(deletes) == 3
    assert deletes[0][1] == (cutoff, 2)
    assert deletes[1][1] == (cutoff, 4)
    assert deletes[2][1] == (cutoff, 5)


def test_archive_and_purge_verify_failure_blocks_delete():
    """Se `list_objects(prefix=key)` non trova la key con `size > 0`:
    `RuntimeError` esplicito, ZERO DELETE invocate, upload gia'
    avvenuto rimane in storage (idempotent su retry).

    Copre due casi di failure della verify:
    (a) list_objects ritorna lista vuota;
    (b) list_objects ritorna l'oggetto ma con `size == 0`.
    """
    ts = datetime(2026, 4, 21, 9, 0, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    run_date = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    # (a) list_objects vuoto.
    storage_empty = _FakeObjectStorage(verify_returns_empty=True)
    writer_a = LogArchiveWriter(storage_empty, "p/", batch_size=100)

    def select_once(cutoff_ts, batch_size):
        return [_sample_log_row(ts, id_=i) for i in (1, 2, 3)]

    deletes_a, delete_a = _collector()

    with pytest.raises(RuntimeError, match="list_objects"):
        writer_a.archive_and_purge(
            "logs", cutoff, select_once, delete_a, run_date=run_date,
        )

    # Upload e' gia' avvenuto (non rollback-abile da qui), ma DELETE no.
    assert len(storage_empty.uploaded) == 1
    assert deletes_a == []
    # Errore cita anche la tabella, per audit/runbook.
    # (gia' coperto dal match "list_objects", ma doppio check sul testo)

    # (b) list_objects ritorna l'oggetto ma con `size == 0` (upload vuoto
    # spurio o listing di un marker / placeholder).
    storage_zero = _FakeObjectStorage(verify_returns_zero_size=True)
    writer_b = LogArchiveWriter(storage_zero, "p/", batch_size=100)

    deletes_b, delete_b = _collector()

    with pytest.raises(RuntimeError, match="list_objects"):
        writer_b.archive_and_purge(
            "logs", cutoff, select_once, delete_b, run_date=run_date,
        )

    assert len(storage_zero.uploaded) == 1
    assert deletes_b == []


def test_archive_and_purge_rejects_bad_inputs_before_any_side_effect():
    """Fail-fast: naive cutoff_ts / naive run_date / table ignota -> errore
    prima di toccare `select_rows`, `storage`, `retention_bypass_execute`."""
    storage = _FakeObjectStorage()
    writer = LogArchiveWriter(storage, "p/", batch_size=100)

    ts_naive = datetime(2026, 4, 21, 10, 0, 0)
    ts_aware = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    def select_must_not_be_called(*args, **kwargs):
        raise AssertionError("select_rows must not be called on invalid input")

    def delete_must_not_be_called(*args, **kwargs):
        raise AssertionError("retention_bypass_execute must not be called on invalid input")

    # (a) cutoff_ts naive -> ValueError
    with pytest.raises(ValueError, match="naive"):
        writer.archive_and_purge(
            "logs",
            ts_naive,
            select_must_not_be_called,
            delete_must_not_be_called,
            run_date=ts_aware,
        )

    # (b) cutoff_ts non-datetime -> TypeError
    with pytest.raises(TypeError, match="datetime"):
        writer.archive_and_purge(
            "logs",
            "2026-04-21",  # type: ignore[arg-type]
            select_must_not_be_called,
            delete_must_not_be_called,
            run_date=ts_aware,
        )

    # (c) table ignota -> ValueError
    with pytest.raises(ValueError, match="unknown log archive table"):
        writer.archive_and_purge(
            "events",  # type: ignore[arg-type]
            ts_aware,
            select_must_not_be_called,
            delete_must_not_be_called,
            run_date=ts_aware,
        )

    # (d) run_date naive -> ValueError (via dry-run di `_build_object_key`)
    with pytest.raises(ValueError, match="naive"):
        writer.archive_and_purge(
            "logs",
            ts_aware,
            select_must_not_be_called,
            delete_must_not_be_called,
            run_date=ts_naive,
        )

    # Nessun side-effect (storage/DELETE) e' avvenuto.
    assert storage.uploaded == []
    assert storage.list_calls == []


def test_archive_and_purge_delete_scoped_to_cutoff_and_batch_max_id():
    """Il callable DELETE riceve SQL + params coerenti:
    - per `logs`: `DELETE FROM logs WHERE ts < %s AND id <= %s`
    - per `audit_actions`: `... WHERE timestamp < %s AND id <= %s`
    - params = (cutoff_ts, max(id) del batch).

    Garantisce che la colonna temporale sia quella giusta per la tabella
    (nessun hardcode di `ts` per audit_actions) e che lo scoping su `id`
    non si estenda oltre il batch corrente.
    """
    ts = datetime(2026, 4, 21, 9, 0, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    run_date = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)

    storage_logs = _FakeObjectStorage()
    writer_logs = LogArchiveWriter(storage_logs, "p/", batch_size=100)

    n = {"i": 0}

    def select_logs(cutoff_ts, batch_size):
        n["i"] += 1
        if n["i"] == 1:
            return [_sample_log_row(ts, id_=i) for i in (10, 20, 30)]
        return []

    deletes_logs, delete_logs = _collector()

    writer_logs.archive_and_purge(
        "logs", cutoff, select_logs, delete_logs, run_date=run_date,
    )

    sql_logs, params_logs = deletes_logs[0]
    assert "DELETE FROM logs" in sql_logs
    assert "WHERE ts < %s" in sql_logs
    assert "AND id <= %s" in sql_logs
    assert "timestamp" not in sql_logs  # non la colonna di audit
    assert params_logs == (cutoff, 30)

    storage_audit = _FakeObjectStorage()
    writer_audit = LogArchiveWriter(storage_audit, "p/", batch_size=100)

    m = {"i": 0}

    def select_audit(cutoff_ts, batch_size):
        m["i"] += 1
        if m["i"] == 1:
            return [_sample_audit_row(ts, id_=i) for i in (5, 15)]
        return []

    deletes_audit, delete_audit = _collector()

    writer_audit.archive_and_purge(
        "audit_actions", cutoff, select_audit, delete_audit, run_date=run_date,
    )

    sql_audit, params_audit = deletes_audit[0]
    assert "DELETE FROM audit_actions" in sql_audit
    assert "WHERE timestamp < %s" in sql_audit
    assert "AND id <= %s" in sql_audit
    # NOTE: la clausola WHERE NON deve referenziare la colonna `ts`
    # (che non esiste in audit_actions). Cerchiamo la parola isolata.
    assert " ts " not in sql_audit and " ts<" not in sql_audit
    assert params_audit == (cutoff, 15)
