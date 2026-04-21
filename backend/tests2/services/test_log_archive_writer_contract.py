"""Contract test — `LogArchiveWriter` NDJSON serialization (O-ARC Batch 1, ADR-0014).

Blinda i vincoli di formato (O-ARC §5.1 + ADR-0014 §Decision 5-8):

- Una riga NDJSON per riga DB, gzip UTF-8.
- Shape canonica per `logs` e `audit_actions` (keyset + ordine).
- `ts`/`timestamp` normalizzati a ISO 8601 UTC con suffisso `Z`.
- `datetime` naive in qualsiasi campo -> `ValueError` (fail-fast su tz
  upstream).

Scope Batch 1 in questo file: solo test di serializzazione (T1-T4).
L'object key naming (T5-T6) e il loop upload/verify/delete (T7-T12)
vengono aggiunti nei commit 1c-1d dello stesso file.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.log_archive import (
    _normalize_datetime,
    _serialize_rows,
    _table_ts_column,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Keyset canonici in ordine; devono combaciare con quelli privati in
# `app.services.log_archive`. La duplicazione qui e' intenzionale: il
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


# ─── T1 ──────────────────────────────────────────────────────────────────

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


# ─── T2 ──────────────────────────────────────────────────────────────────

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


# ─── T3 ──────────────────────────────────────────────────────────────────

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


# ─── T4 ──────────────────────────────────────────────────────────────────

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
