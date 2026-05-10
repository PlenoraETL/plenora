"""
Contract: secret redaction in generate_error_report CSV.

Previous behavior `generate_error_report` faceva `json.dumps(error.input_params)` senza
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


from app.core.arricchisci.enrichment_service import generate_error_report
from app.core.arricchisci.models import RowError, RowStatus


SECRET_API_KEY = "sk-live-DO-NOT-LEAK-1234567890abcdef"
SECRET_BEARER = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
SECRET_PASSWORD = "p@ssw0rd-super-secret"
CUSTOM_KEY_VALUE = "az-subscription-key-XYZ"


def _read_csv_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_generate_error_report_redacts_known_sensitive_keys(tmp_path: Path):
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

    out_base = str(tmp_path / "report")
    csv_path = generate_error_report([err], out_base)

    raw = Path(csv_path).read_text(encoding="utf-8")

    # Nessun segreto, in nessuna forma, nel file
    assert SECRET_API_KEY not in raw, "api_key in chiaro nel CSV"
    assert SECRET_BEARER not in raw, "bearer token in chiaro nel CSV"
    assert SECRET_PASSWORD not in raw, "password in chiaro nel CSV"
    assert "***REDACTED***" in raw, "marker REDACTED mancante"

    # Dati benigni preservati
    rows = _read_csv_rows(csv_path)
    assert len(rows) == 1
    parsed = json.loads(rows[0]["input_params"])
    assert parsed["api_key"] == "***REDACTED***"
    assert parsed["authorization"] == "***REDACTED***"
    assert parsed["password"] == "***REDACTED***"
    assert parsed["lat"] == 45.0
    assert parsed["lng"] == 9.0
    assert parsed["address"] == "Via Roma 1"

    # Metadati riga preservati
    assert rows[0]["row_index"] == "42"
    assert rows[0]["http_status"] == "503"
    assert rows[0]["retry_count"] == "3"


def test_generate_error_report_redacts_custom_key_name_via_auth(tmp_path: Path):
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

    out_base = str(tmp_path / "report_auth")
    csv_path = generate_error_report([err], out_base, auth=auth)

    raw = Path(csv_path).read_text(encoding="utf-8")
    assert CUSTOM_KEY_VALUE not in raw, "subscription-key in chiaro nel CSV"
    assert "***REDACTED***" in raw

    rows = _read_csv_rows(csv_path)
    parsed = json.loads(rows[0]["input_params"])
    assert parsed[custom_key_name] == "***REDACTED***"
    assert parsed["city"] == "Milano"


def test_generate_error_report_custom_key_not_redacted_without_auth(tmp_path: Path):
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

    out_base = str(tmp_path / "report_noauth")
    csv_path = generate_error_report([err], out_base)

    raw = Path(csv_path).read_text(encoding="utf-8")
    # Senza auth, il pattern generico non lo matcha -> resta in chiaro.
    # Questo TEST documenta il comportamento per evitare regressioni: chi
    # introduce una nuova path che chiama generate_error_report DEVE passare
    # `auth` se l'API ha una key con nome custom.
    assert CUSTOM_KEY_VALUE in raw


def test_generate_error_report_empty_errors_returns_none(tmp_path: Path):
    """Sanity: nessun errore -> nessun file."""
    result = generate_error_report([], str(tmp_path / "empty"))
    assert result is None


def test_generate_error_report_does_not_mutate_input_params(tmp_path: Path):
    """`sanitize_for_log` non deve mutare la struttura originale: i RowError
    in memoria mantengono i valori reali (servono per debugging del worker)."""
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

    generate_error_report([err], str(tmp_path / "nomutate"))

    # Il dict originale del RowError DEVE essere ancora intatto
    assert err.input_params["api_key"] == SECRET_API_KEY
    assert original["api_key"] == SECRET_API_KEY


def test_generate_error_report_handles_missing_input_params(tmp_path: Path):
    """`input_params` None o assente non deve far crashare il writer."""
    err = RowError(
        row_index=0,
        status=RowStatus.ERROR,
        error_message="generic",
    )
    # Forzo None per simulare un caller che imposta esplicitamente null
    err.input_params = None  # type: ignore[assignment]

    csv_path = generate_error_report([err], str(tmp_path / "noparams"))
    rows = _read_csv_rows(csv_path)
    assert len(rows) == 1
    assert rows[0]["input_params"] == "{}"
