"""
Contract test — size cap upload (T-04 Batch B).

Verifica che `_enforce_upload_size_limit` rifiuti con 413 file oltre la
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _make_upload(content: bytes) -> SimpleNamespace:
    """Mimic minimo di UploadFile: oggetto con `.file` BytesIO."""
    return SimpleNamespace(file=io.BytesIO(content))


def _stub_settings_with_max(monkeypatch, max_bytes: int) -> None:
    """Sostituisce l'oggetto `settings` con uno stub deterministico.

    Pattern robusto contro test-order interference: altri test della suite
    monkeypatchano attributi su `app.config.settings` e l'isolamento per-
    fixture non e' garantito su side-effect cross-modulo. Sostituiamo
    l'oggetto in entrambi i punti di uso (`app.config` e
    `app.core.files.router`) cosi' la `_Settings.get` controlla il cap
    indipendentemente dallo stato globale residuo.
    """
    class _Settings:
        def get(self, key, default=None):
            return max_bytes if key == "MAX_FILE_SIZE" else default

    import app.config as cfg
    from app.core.files import router as files_router
    stub = _Settings()
    monkeypatch.setattr(cfg, "settings", stub)
    monkeypatch.setattr(files_router, "settings", stub)


def test_size_cap_accepts_under_limit(monkeypatch):
    from app.core.files import router as files_router

    _stub_settings_with_max(monkeypatch, max_bytes=1024)

    upload = _make_upload(b"x" * 512)
    size = files_router._enforce_upload_size_limit(upload)
    assert size == 512
    # cursore deve essere a 0 dopo il check (caller deve poter rileggere).
    assert upload.file.tell() == 0


def test_size_cap_rejects_over_limit_with_413(monkeypatch):
    from app.core.files import router as files_router

    _stub_settings_with_max(monkeypatch, max_bytes=1024)

    upload = _make_upload(b"x" * 2048)
    with pytest.raises(HTTPException) as exc:
        files_router._enforce_upload_size_limit(upload)
    assert exc.value.status_code == 413
    assert "troppo grande" in exc.value.detail.lower()


def test_size_cap_does_not_read_payload_into_memory(monkeypatch):
    """Difesa-in-profondita': l'helper deve usare seek+tell, non read().

    Costruiamo un BytesIO che traccia ogni `.read()`. Se l'helper legge il
    contenuto, il contatore va > 0 — vogliamo che resti 0 (solo seek/tell).
    """
    from app.core.files import router as files_router

    _stub_settings_with_max(monkeypatch, max_bytes=4096)

    read_calls = {"n": 0}

    class TrackedBytesIO(io.BytesIO):
        def read(self, *args, **kwargs):
            read_calls["n"] += 1
            return super().read(*args, **kwargs)

    upload = SimpleNamespace(file=TrackedBytesIO(b"y" * 1024))
    files_router._enforce_upload_size_limit(upload)
    assert read_calls["n"] == 0, (
        "size cap deve usare seek+tell, non read(): un read() eager apre "
        "la classe DoS che il fix chiude."
    )


def test_size_cap_uses_default_when_setting_missing(monkeypatch):
    """Regressione: se MAX_FILE_SIZE non e' settato, default 100MB.

    La signature `settings.get("MAX_FILE_SIZE", 100 * 1024 * 1024)` deve
    restare il contratto — un cambio a `settings.MAX_FILE_SIZE` (proprieta')
    senza fallback rompe gli ambienti dev senza .env esplicito.
    """
    from app.core.files import router as files_router

    captured = {}

    class _Settings:
        def get(self, key, default=None):
            captured["key"] = key
            captured["default"] = default
            return default

    import app.config as cfg
    monkeypatch.setattr(cfg, "settings", _Settings())
    # Importiamo settings DAL router per intercettare la stessa reference.
    monkeypatch.setattr(files_router, "settings", _Settings())

    upload = _make_upload(b"z" * 64)
    size = files_router._enforce_upload_size_limit(upload)
    assert size == 64
    assert captured["key"] == "MAX_FILE_SIZE"
    assert captured["default"] == 100 * 1024 * 1024


def test_upload_endpoint_preserves_size_limit_http_status(monkeypatch):
    """The endpoint must not turn the helper's 413 into a generic 500."""
    from app.core.files import router as files_router

    def _reject(_file):
        raise HTTPException(status_code=413, detail="File troppo grande")

    monkeypatch.setattr(files_router, "_enforce_upload_size_limit", _reject)
    upload = UploadFile(filename="large.csv", file=io.BytesIO(b"x"))

    with pytest.raises(HTTPException) as exc_info:
        files_router.upload_file.__wrapped__(
            file=upload,
            name="large",
            current_user=SimpleNamespace(group_id="g1", username="user"),
        )

    assert exc_info.value.status_code == 413
