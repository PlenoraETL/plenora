"""
Contract: secret redaction in `generate_error_report` (bytes-mode RAM-only).

Post-refactor (QUERY-1 follow-up): la funzione ritorna `Optional[bytes]`
del CSV invece di scrivere su `RESULTS_DIR`. La redaction (api_key/
authorization/password) deve restare invariata.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

from app.core.arricchisci.enrichment_service import generate_error_report
from app.core.arricchisci.models import RowError, RowStatus


SECRET_API_KEY = "sk-live-DO-NOT-LEAK-1234567890abcdef"
SECRET_BEARER = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
SECRET_PASSWORD = "p@ssw0rd-super-secret"
CUSTOM_KEY_VALUE = "az-subscription-key-XYZ"


def _decode_csv_rows(data: Optional[bytes]) -> list[dict]:
    if data is None:
        return []
    text = data.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_generate_error_report_redacts_known_sensitive_keys():
    """Pattern generici (api_key/authorization/password) sempre redatti."""
    err = RowError(
        row_index=42,
        status=RowStatus.ERROR,
        error_message="HTTP 503 Service Unavailable",
        error_type="http_5xx",
        http_status=503,
        retry_count=3,
        input_params={
            "api_key": SECRET_API_KEY,
            "authorization": f"Bearer {SECRET_BEARER}",
            "password": SECRET_PASSWORD,
            "lat": 45.0,
            "lng": 9.0,
            "address": "Via Roma 1",
        },
    )

    data = generate_error_report([err])
    assert data is not None
    raw = data.decode("utf-8")

    # Nessun segreto, in nessuna forma, nei bytes
    assert SECRET_API_KEY not in raw, "api_key in chiaro nel CSV"
    assert SECRET_BEARER not in raw, "bearer token in chiaro nel CSV"
    assert SECRET_PASSWORD not in raw, "password in chiaro nel CSV"
    assert "***REDACTED***" in raw, "marker REDACTED mancante"

    rows = _decode_csv_rows(data)
    assert len(rows) == 1
    parsed = json.loads(rows[0]["input_params"])
    assert parsed["api_key"] == "***REDACTED***"
    assert parsed["authorization"] == "***REDACTED***"
    assert parsed["password"] == "***REDACTED***"
    assert parsed["lat"] == 45.0
    assert parsed["lng"] == 9.0
    assert parsed["address"] == "Via Roma 1"

    assert rows[0]["row_index"] == "42"
    assert rows[0]["http_status"] == "503"
    assert rows[0]["retry_count"] == "3"


def test_generate_error_report_redacts_custom_key_name_via_auth():
    """`auth.key_name` con nome non standard (es. `subscription-key`)
    deve essere redatto SOLO se il caller passa `auth` al report."""
    custom_key_name = "subscription-key"
    err = RowError(
        row_index=7,
        status=RowStatus.ERROR,
        error_message="bad request",
        input_params={
            custom_key_name: CUSTOM_KEY_VALUE,
            "city": "Milano",
        },
    )

    auth = {
        "type": "api_key",
        "location": "query",
        "key_name": custom_key_name,
        "key_value": CUSTOM_KEY_VALUE,
    }

    data = generate_error_report([err], auth=auth)
    assert data is not None
    raw = data.decode("utf-8")
    assert CUSTOM_KEY_VALUE not in raw, "subscription-key in chiaro nel CSV"
    assert "***REDACTED***" in raw

    rows = _decode_csv_rows(data)
    parsed = json.loads(rows[0]["input_params"])
    assert parsed[custom_key_name] == "***REDACTED***"
    assert parsed["city"] == "Milano"


def test_generate_error_report_custom_key_not_redacted_without_auth():
    """Senza `auth`, una chiave custom (non match sui pattern generici) NON
    viene redatta. Documenta il limite del fallback e giustifica la propagazione
    di `auth` dalla call site (workers/tasks/enrichment_task.py)."""
    custom_key_name = "subscription-key"
    err = RowError(
        row_index=1,
        status=RowStatus.ERROR,
        error_message="bad request",
        input_params={custom_key_name: CUSTOM_KEY_VALUE},
    )

    data = generate_error_report([err])
    raw = data.decode("utf-8")
    # Senza auth, il pattern generico non lo matcha -> resta in chiaro.
    assert CUSTOM_KEY_VALUE in raw


def test_generate_error_report_empty_errors_returns_none():
    """Sanity: nessun errore -> None (no bytes generated)."""
    assert generate_error_report([]) is None


def test_generate_error_report_does_not_mutate_input_params():
    """`sanitize_for_log` non deve mutare la struttura originale."""
    original = {
        "api_key": SECRET_API_KEY,
        "lat": 1.0,
    }
    err = RowError(
        row_index=0,
        status=RowStatus.ERROR,
        error_message="x",
        input_params=original,
    )

    generate_error_report([err])

    assert err.input_params["api_key"] == SECRET_API_KEY
    assert original["api_key"] == SECRET_API_KEY


def test_generate_error_report_handles_missing_input_params():
    """`input_params` None o assente non deve far crashare il writer."""
    err = RowError(
        row_index=0,
        status=RowStatus.ERROR,
        error_message="generic",
    )
    err.input_params = None  # type: ignore[assignment]

    data = generate_error_report([err])
    rows = _decode_csv_rows(data)
    assert len(rows) == 1
    assert rows[0]["input_params"] == "{}"


def test_generate_error_report_returns_utf8_bytes():
    """Output e' bytes UTF-8 encoded (assicura compat con
    save_result_blob_from_bytes + content-type text/csv downstream)."""
    err = RowError(
        row_index=0,
        status=RowStatus.ERROR,
        error_message="città: Pordenone — €100",
        input_params={"label": "località 'San José'"},
    )

    data = generate_error_report([err])
    assert isinstance(data, bytes)
    # Round-trip UTF-8.
    text = data.decode("utf-8")
    assert "Pordenone" in text
    assert "località" in text
