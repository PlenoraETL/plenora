"""
Unit contract test per ``app.shared.error_sanitizer``.

(error-handling-governance). Il sanitizer e' applicato
"""

from __future__ import annotations

import pytest

from app.shared.error_sanitizer import sanitize_http_detail


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


def test_strips_https_url_with_query_token():
    msg = "Layer https://tenant.arcgis.com/rest/FS/0?token=abc123 non trovato"
    out = sanitize_http_detail(msg)
    assert "https://" not in out
    assert "tenant.arcgis.com" not in out
    assert "abc123" not in out
    assert "[URL]" in out
    assert "non trovato" in out


def test_strips_http_url():
    out = sanitize_http_detail("connect failed http://internal.db:5432/foo")
    assert "http://" not in out
    assert "internal.db" not in out
    assert "[URL]" in out


def test_strips_sftp_url():
    out = sanitize_http_detail("upload failed sftp://user@host/path")
    assert "sftp://" not in out
    assert "[URL]" in out


def test_strips_standalone_jwt():
    # JWT fake ma con marker ``eyJ`` + 3 sezioni
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.s1gn4tur3"
    out = sanitize_http_detail(f"Invalid token {jwt}")
    assert jwt not in out
    assert "[REDACTED]" in out


def test_strips_password_equals():
    out = sanitize_http_detail("auth failed: password=supersecret123")
    assert "supersecret123" not in out
    assert "[REDACTED]" in out
    assert "password" in out  # key preservata


def test_strips_token_colon():
    out = sanitize_http_detail("upstream error: token: xyz789")
    assert "xyz789" not in out
    assert "[REDACTED]" in out


def test_strips_api_key_variant():
    # api_key, api-key, Api-Key, API_KEY — tutti devono matchare.
    for variant in ("api_key=abc", "api-key=abc", "Api-Key=abc", "API_KEY=abc"):
        out = sanitize_http_detail(f"error {variant}")
        assert "abc" not in out, f"variant '{variant}' non sanitizzata"
        assert "[REDACTED]" in out


def test_strips_windows_path():
    msg = r"Errore parsing C:\Users\Marco\tmp\file.dxf: PROJCRS not defined"
    out = sanitize_http_detail(msg)
    assert "C:\\" not in out
    assert "Marco" not in out
    assert "[PATH]" in out
    assert "PROJCRS not defined" in out  # parte benigna preservata


def test_strips_posix_home_path():
    out = sanitize_http_detail("cannot read /home/urby/data/export.csv")
    assert "/home/urby" not in out
    assert "[PATH]" in out


def test_does_not_strip_non_sensitive_posix_path():
    # /projects/x NON e' in blacklist: non va strippato.
    out = sanitize_http_detail("file /projects/proj-1 non trovato")
    assert "/projects/proj-1" in out  # preservato


def test_fallback_on_none():
    assert sanitize_http_detail(None) == "Errore non specificato"


def test_fallback_on_empty_string():
    assert sanitize_http_detail("") == "Errore non specificato"


def test_preserves_benign_message_unchanged():
    msg = "Parametro 'foo' mancante nella richiesta"
    assert sanitize_http_detail(msg) == msg


def test_multi_pattern_combined():
    msg = (
        r"GET https://x.com/y?token=T failed at C:\tmp\x.txt "
        "password=PW and jwt eyJh.eyJs.SiG"
    )
    out = sanitize_http_detail(msg)
    # Nessuno dei payload originali sopravvive.
    for leak in (
        "https://", "x.com", "token=T",
        "C:\\tmp", "PW",
        "eyJh.eyJs.SiG",
    ):
        assert leak not in out, f"leak '{leak}' sopravvissuto in: {out}"
    # Tutti i marker sono presenti.
    assert "[URL]" in out
    assert "[PATH]" in out
    assert "[REDACTED]" in out


def test_truncate_at_max_len():
    msg = "x" * 2000
    out = sanitize_http_detail(msg)
    assert len(out) <= 500
    assert out.endswith("...[troncato]")
