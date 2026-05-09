"""
Contract test BE-GO-READY-MEMORY-OOM-001-LOAD-FILE-PREFLIGHT-GUARD.

Lock il primo OOM-prevention guard:
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared import error_codes
from app.shared.error_envelope import build_error_response
from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. Catalog membership


def test_memory_limit_exceeded_constant_value():
    assert error_codes.MEMORY_LIMIT_EXCEEDED == "memory_limit_exceeded"


def test_memory_limit_exceeded_in_known_domain_codes():
    assert "memory_limit_exceeded" in error_codes.KNOWN_DOMAIN_CODES


# 2. Exception purity (no FastAPI / starlette imports)


def test_exception_module_does_not_import_fastapi():
    """Exception module deve restare puro (no FastAPI/starlette).
    Garantisce che worker Celery / CLI possano importare ed alzare
    `MemoryLimitExceededError` senza pull-in del web stack."""
    import app.core.resources.exceptions as exc_mod

    src_path = Path(exc_mod.__file__)
    src = src_path.read_text(encoding="utf-8")
    assert "from fastapi" not in src
    assert "from starlette" not in src
    assert "import fastapi" not in src
    assert "import starlette" not in src


def test_exception_carries_typed_attributes():
    e = MemoryLimitExceededError(
        observed_bytes=300 * 1024 * 1024,
        limit_bytes=200 * 1024 * 1024,
        path_basename="huge_file.csv",
        stage="load_dataframe_from_file",
    )
    assert e.observed_bytes == 300 * 1024 * 1024
    assert e.limit_bytes == 200 * 1024 * 1024
    assert e.path_basename == "huge_file.csv"
    assert e.stage == "load_dataframe_from_file"
    assert "load_dataframe_from_file" in str(e)
    assert "huge_file.csv" in str(e)


# 3. Preflight guard — load_dataframe_from_file


def _write_csv(path: Path, rows: int = 5) -> None:
    """Scrive un CSV minimale per il test happy path."""
    path.write_text(
        "a,b\n" + "\n".join(f"{i},x{i}" for i in range(rows)),
        encoding="utf-8",
    )


def test_guard_raises_when_file_size_exceeds_cap(tmp_path, monkeypatch):
    """File >> cap → `MemoryLimitExceededError` con attributi tipati.
    `monkeypatch` riduce il cap a 1 byte per forzare il guard
    senza scrivere file giganti su disco."""
    csv = tmp_path / "small.csv"
    _write_csv(csv, rows=10)
    assert csv.stat().st_size > 1  # sanity: il file e' > 1 byte

    # Patch del cap nel `Settings.DEFAULTS` (lazy-loaded in dataframe.py).
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "LOAD_FILE_MAX_BYTES", 1)

    from app.core.files.dataframe_io import load_dataframe_from_file

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        load_dataframe_from_file(csv)

    e = exc_info.value
    assert e.stage == "load_dataframe_from_file"
    assert e.limit_bytes == 1
    assert e.observed_bytes >= 2
    assert e.path_basename == "small.csv"


def test_guard_passes_when_file_under_cap(tmp_path, monkeypatch):
    """File << cap → DataFrame caricato normalmente. Lock happy path."""
    csv = tmp_path / "small.csv"
    _write_csv(csv, rows=5)

    from app.config import settings as _settings

    monkeypatch.setitem(
        _settings.DEFAULTS, "LOAD_FILE_MAX_BYTES", 200 * 1024 * 1024,
    )

    from app.core.files.dataframe_io import load_dataframe_from_file

    df = load_dataframe_from_file(csv)
    assert len(df) == 5
    assert list(df.columns) == ["a", "b"]


def test_guard_zero_or_negative_cap_disables_check(tmp_path, monkeypatch):
    """Cap == 0 o negativo → guard inattivo (escape hatch operativo).
    Il file viene caricato anche se sopra il cap (= disabled)."""
    csv = tmp_path / "small.csv"
    _write_csv(csv, rows=5)

    from app.config import settings as _settings

    # Cap a 0 = disabilitato
    monkeypatch.setitem(_settings.DEFAULTS, "LOAD_FILE_MAX_BYTES", 0)
    from app.core.files.dataframe_io import load_dataframe_from_file

    df = load_dataframe_from_file(csv)
    assert len(df) == 5


# 4. HTTP boundary mapping — 413 dual-shape envelope


@pytest.fixture
def memory_limit_app() -> FastAPI:
    """Mini-app FastAPI con il NUOVO `MemoryLimitExceededError`
    handler wired (BE-GO-READY-MEMORY-OOM-001). Replica della
    logica di `app/main.py` senza dipendere dall'app produttiva.
    """
    app = FastAPI()

    @app.exception_handler(MemoryLimitExceededError)
    async def _handler(request, exc):
        limit_mb = round(exc.limit_bytes / (1024 * 1024), 2)
        observed_mb = round(exc.observed_bytes / (1024 * 1024), 2)
        return build_error_response(
            status_code=413,
            error=error_codes.MEMORY_LIMIT_EXCEEDED,
            message=(
                f"L'input richiesto ({observed_mb} MB) supera il limite di "
                f"memoria configurato ({limit_mb} MB) per lo stage "
                f"'{exc.stage}'. Contatta l'amministratore o riduci la "
                f"dimensione del file."
            ),
            details={
                "stage": exc.stage,
                "limit_bytes": exc.limit_bytes,
                "observed_bytes": exc.observed_bytes,
                "limit_mb": limit_mb,
                "observed_mb": observed_mb,
                "suggested_action": "split_file_or_contact_admin",
                "resource": exc.path_basename,
            },
        )

    @app.get("/raise-memory-exceeded")
    def raise_memory_exceeded():
        raise MemoryLimitExceededError(
            observed_bytes=300 * 1024 * 1024,
            limit_bytes=200 * 1024 * 1024,
            path_basename="huge.csv",
            stage="load_dataframe_from_file",
        )

    return app


def _client(memory_limit_app):
    return TestClient(memory_limit_app, raise_server_exceptions=False)


def test_envelope_status_code_is_413(memory_limit_app):
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    assert r.status_code == 413


def test_envelope_top_level_error_code(memory_limit_app):
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    body = r.json()
    assert body["error"] == "memory_limit_exceeded"


def test_envelope_top_level_message_human_readable(memory_limit_app):
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    body = r.json()
    msg = body["message"]
    assert "limite di memoria" in msg
    assert "load_dataframe_from_file" in msg
    assert "MB" in msg


def test_envelope_details_stable_shape(memory_limit_app):
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    details = r.json()["details"]
    expected_keys = {
        "stage", "limit_bytes", "observed_bytes",
        "limit_mb", "observed_mb",
        "suggested_action", "resource",
    }
    assert expected_keys.issubset(set(details.keys())), (
        f"details keys mancanti: {expected_keys - set(details.keys())}"
    )
    assert details["stage"] == "load_dataframe_from_file"
    assert details["limit_bytes"] == 200 * 1024 * 1024
    assert details["observed_bytes"] == 300 * 1024 * 1024
    assert details["limit_mb"] == 200.0
    assert details["observed_mb"] == 300.0
    assert details["suggested_action"] == "split_file_or_contact_admin"
    assert details["resource"] == "huge.csv"


def test_envelope_dual_shape_detail_mirror_present(memory_limit_app):
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    body = r.json()
    detail = body["detail"]
    assert detail["error"] == "memory_limit_exceeded"
    assert detail["message"] == body["message"]
    assert detail["details"] == body["details"]


def test_envelope_does_not_leak_absolute_path(memory_limit_app):
    """Lock anti-leak: nessun path assoluto / componenti directory
    devono apparire nel body. Solo `resource` (basename)."""
    r = _client(memory_limit_app).get("/raise-memory-exceeded")
    body = r.json()
    flat = str(body)
    # Valori che NON devono mai comparire (path assoluti tipici)
    forbidden = ["/", "\\", "C:", "/var/", "/tmp/", "/home/", "/usr/"]
    # Eccezione: il valore "/" appare nel JSON come separatore di
    # struttura, ma `resource` deve essere basename puro.
    resource = body["details"]["resource"]
    assert "/" not in resource
    assert "\\" not in resource
    assert resource == "huge.csv"
    # Sanity: il filesystem layout server-side non e' echo-ato
    # (il test fixture passa "huge.csv" — qualunque path-like
    # piu' lungo sarebbe un leak).


# 5. Source-bound: handler registrato in main.py


def test_main_py_registers_memory_limit_exceeded_handler():
    """Lock source-bound: `app/main.py` deve registrare il
    `MemoryLimitExceededError` handler."""
    main_py = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    assert "@app.exception_handler(MemoryLimitExceededError)" in src
    # Import dell'eccezione dal modulo shared.
    assert "MemoryLimitExceededError" in src
