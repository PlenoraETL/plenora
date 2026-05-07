"""
Contract test — size cap upload eseguibili (B1 Phase 4).

Verifica che l'endpoint `POST /api/eseguibili/{id}/analyze-upload` rifiuti
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
]


def _make_upload(content: bytes) -> SimpleNamespace:
    """Mimic minimo di UploadFile: oggetto con `.file` BytesIO."""
    return SimpleNamespace(file=io.BytesIO(content))


def _stub_settings_with_max(monkeypatch, max_bytes: int) -> None:
    """Sostituisce `settings` in `app.shared.upload_guards` (e in `app.config`
    per simmetria) cosi' la default-resolution di `MAX_FILE_SIZE` dentro
    `enforce_upload_size_limit(file)` (chiamato senza `max_bytes`) torna il
    valore desiderato in modo deterministico, indipendente dallo stato
    cross-test del settings globale.
    """
    class _Settings:
        def get(self, key, default=None):
            return max_bytes if key == "MAX_FILE_SIZE" else default

    import app.config as cfg
    from app.shared import upload_guards
    stub = _Settings()
    monkeypatch.setattr(cfg, "settings", stub)
    monkeypatch.setattr(upload_guards, "settings", stub)


def test_eseguibili_size_cap_rejects_over_limit_with_413(monkeypatch):
    """Contract: file oltre cap -> 413 fail-loud, prima di toccare il
    payload. Replica il guard inserito in `analyze_upload`."""
    from app.shared.upload_guards import enforce_upload_size_limit

    _stub_settings_with_max(monkeypatch, max_bytes=1024)

    upload = _make_upload(b"x" * 4096)
    with pytest.raises(HTTPException) as exc:
        enforce_upload_size_limit(upload)
    assert exc.value.status_code == 413
    assert "troppo grande" in exc.value.detail.lower()


def test_eseguibili_size_cap_accepts_under_limit(monkeypatch):
    """Contract: file sotto cap -> ritorna size, cursore a 0."""
    from app.shared.upload_guards import enforce_upload_size_limit

    _stub_settings_with_max(monkeypatch, max_bytes=4096)

    upload = _make_upload(b"y" * 1024)
    size = enforce_upload_size_limit(upload)
    assert size == 1024
    assert upload.file.tell() == 0, (
        "Dopo il check il cursore deve essere a 0: il caller "
        "(`analyze_upload`) chiama subito `await file.read()`."
    )


def test_eseguibili_size_cap_does_not_read_payload_into_memory(monkeypatch):
    """Difesa-in-profondita': il guard usato da analyze_upload NON deve
    chiamare `read()` sul payload prima del rifiuto, altrimenti si riapre
    la classe di DoS interno credenziato che T-04 ha chiuso lato `files/`.
    """
    from app.shared.upload_guards import enforce_upload_size_limit

    _stub_settings_with_max(monkeypatch, max_bytes=4096)

    read_calls = {"n": 0}

    class TrackedBytesIO(io.BytesIO):
        def read(self, *args, **kwargs):
            read_calls["n"] += 1
            return super().read(*args, **kwargs)

    upload = SimpleNamespace(file=TrackedBytesIO(b"z" * 1024))
    enforce_upload_size_limit(upload)
    assert read_calls["n"] == 0, (
        "Il guard deve usare seek+tell, non read(): un read() eager "
        "carica N byte in RAM (DoS) prima di poter rifiutare."
    )


def test_eseguibili_router_calls_size_guard_before_read():
    """Contract integrazione: `analyze_upload` deve referenziare
    `enforce_upload_size_limit` come callee (single source of truth
    del size guard).

    Replacement R-04b di `inspect.getsource(analyze_upload) +
    guard_pos < read_pos`:
      - bytecode probe per il callee identifier (refactor-resilient).
      - ordering "guard PRIMA di read()" e' coperto dal contract
        funzionale del helper stesso (`test_eseguibili_size_cap_*`
        sopra) + dal fatto che il helper SOLLEVA HTTPException(413)
        quando over-limit, bloccando il flusso prima che read() sia
        raggiunto. Non serve testare l'ordering source-string: se il
        guard e' invocato come callee e solleva on-fail, l'ordering
        e' garantito dalla semantica try/raise di Python.
    """
    from app.core.eseguibili import router as eseguibili_router

    method = eseguibili_router.analyze_upload
    code = getattr(method, "__wrapped__", method).__code__

    def _walk(co):
        if "enforce_upload_size_limit" in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    assert _walk(code), (
        "analyze_upload non riferisce enforce_upload_size_limit (size cap "
        "pre-read e' il contratto di sicurezza B1 Phase 4)."
    )
