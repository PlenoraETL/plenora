"""
Unit contract test per ``app.shared.error_sanitizer``.

(error-handling-governance). Il sanitizer e' applicato
"""

from __future__ import annotations

import re

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


# Regole generiche rinforzate (esito del «Test connessione», S3)

_LEAK = "SuPeRsEcReT9"


@pytest.mark.parametrize(
    "msg",
    [
        f"Max retries exceeded with url: /v1/data?q=roma&x={_LEAK} (Caused by X)",
        f"url: /v1/data?key={_LEAK}",
        *(f"error {name}={_LEAK}" for name in (
            "key", "apikey", "api_key", "x-api-key", "token", "access_token", "refresh_token",
            "secret", "client_secret", "password", "passwd", "pwd", "auth", "authorization",
            "sig", "signature", "appid", "subscription-key", "Ocp-Apim-Subscription-Key",
            "access_key", "credential", "aws_secret_access_key",
        )),
        f'{{"api_key": "{_LEAK}"}}',
        f"{{'password': '{_LEAK}'}}",
        f'{{\\"token\\": \\"{_LEAK}\\"}}',
        f'"pin_token": {_LEAK}',
        f"Authorization: Bearer {_LEAK}",
        f"Authorization: Basic {_LEAK}==",
        f"header Bearer {_LEAK}",
        f"password='a b {_LEAK}'",
        f"postgresql://admin:{_LEAK}@db:5432/x",
        f"oracle://scott:{_LEAK}@h/x",
        f"mongodb://u:{_LEAK}@h",
        f"redis://:{_LEAK}@h:6379",
        f"Driver={{ODBC}};Server=h;UID=sa;PWD={_LEAK};",
        f"Driver={{ODBC}};PWD={{a;{_LEAK}}}}};",
        f"Server=h;Password=a b {_LEAK};",
        f"scott/{_LEAK}@host:1521/svc",
        f"connect admin:{_LEAK}@db.interno:5432",
    ],
)
def test_generic_rules_redact_secret_in_context(msg):
    out = sanitize_http_detail(msg)
    assert _LEAK not in out, out
    assert "[REDACTED]" in out or "[URL]" in out


def test_generic_rules_keep_benign_messages():
    for msg in (
        'FATAL: password authentication failed for user "bob"',
        "Duplicate key value violates unique constraint",
        "Errore: connessione rifiutata alle 12:30",
        "Timeout connessione MS SQL",
    ):
        assert sanitize_http_detail(msg) == msg


def test_known_secrets_redacted_in_every_encoding():
    from base64 import b64encode
    from urllib.parse import quote, quote_plus

    from app.shared.error_sanitizer import collect_secret_values, redact_known_secrets

    secret = "Zk7 p@ss/w&r=d!"
    config = {"auth": {"type": "basic", "username": "mario", "password": secret,
                       "key_name": "key", "value": "Zk7customVal"},
              "custom_headers": {"X-Api-Key": "Zk7header", "Accept": "application/json"}}
    secrets = collect_secret_values(config)
    assert {secret, "Zk7customVal", "Zk7header"} <= secrets
    assert "key" not in secrets and "mario" not in secrets and "application/json" not in secrets
    basic = b64encode(f"mario:{secret}".encode()).decode()
    text = " | ".join([secret, quote(secret, safe=""), quote(secret, safe="").lower(), quote_plus(secret),
                       basic, "Zk7customVal", "Zk7header"])
    out = redact_known_secrets(text, secrets)
    assert "Zk7" not in out and basic not in out, out


# Forme di un segreto noto: la tabella dichiarata SECRET_VARIANTS

_ALL_CHARS_SECRET = "Zk7 p@ss/w&r=d!<x>\"q'\\é€;%+"


def _variant_ids():
    from app.shared.error_sanitizer import SECRET_VARIANTS

    return [name for name, _ in SECRET_VARIANTS]


def test_secret_variants_table_declares_every_encoding():
    names = _variant_ids()
    assert len(names) == len(set(names))
    assert set(names) >= {
        "raw", "strip", "percent", "percent_path", "percent_plus", "percent_lower",
        "percent_path_lower", "percent_plus_lower", "json", "json_unicode", "repr",
        "base64", "html", "html_noquote", "html_decimal", "xml",
    }


@pytest.mark.parametrize("variant", _variant_ids())
@pytest.mark.parametrize("context", ["plain", "html_body", "json_body"])
def test_every_declared_variant_is_redacted(variant, context):
    from app.shared.error_sanitizer import SECRET_VARIANTS, sanitize_with_secrets

    form = dict(SECRET_VARIANTS)[variant](_ALL_CHARS_SECRET)
    msg = {
        "plain": f"login fallito con {form} su host",
        "html_body": f"HTTP 403: <html><body><p>Chiave {form} non valida</p></body></html>",
        "json_body": f'HTTP 401: {{"error": "bad", "detail": "{form}"}}',
    }[context]
    out = sanitize_with_secrets(msg, {_ALL_CHARS_SECRET}, max_len=2000)
    assert form not in out, out
    assert "Zk7" not in out, out


def test_basic_auth_base64_is_a_collected_secret():
    from base64 import b64encode

    from app.shared.error_sanitizer import collect_secret_values, sanitize_with_secrets

    secrets = collect_secret_values({"auth": {"type": "basic", "username": "mario", "password": _ALL_CHARS_SECRET}})
    basic = b64encode(f"mario:{_ALL_CHARS_SECRET}".encode()).decode()
    assert basic in secrets
    assert basic not in sanitize_with_secrets(f"echo Authorization: Basic {basic}", secrets)


# Caratteri di controllo: tolti sempre, come prima regola

_CONTROL_CHARS = [chr(c) for c in (*range(0x00, 0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0))] + ["\ud800", "\udfff"]


@pytest.mark.parametrize("char", _CONTROL_CHARS, ids=lambda c: f"U+{ord(c):04X}")
def test_control_chars_are_removed(char):
    from app.shared.error_sanitizer import sanitize_with_secrets

    assert sanitize_http_detail(f"HTTP 500: a{char}b") == "HTTP 500: ab"
    assert sanitize_with_secrets(f"HTTP 500: a{char}b", ()) == "HTTP 500: ab"


def test_tab_and_newline_are_kept():
    assert sanitize_http_detail("riga 1\n\triga 2") == "riga 1\n\triga 2"


def test_only_control_chars_gives_the_fallback():
    assert sanitize_http_detail("\x00\x01\x02") == "Errore non specificato"


def test_secret_split_by_control_chars_is_still_redacted():
    from app.shared.error_sanitizer import sanitize_with_secrets

    out = sanitize_with_secrets("chiave Zk7sec\x00ret99 rifiutata", {"Zk7secret99"})
    assert "Zk7" not in out and "\x00" not in out, out


def test_binary_body_is_storable_in_postgresql():
    """Un corpo binario (NUL, byte di controllo) non fa fallire l'INSERT del
    «Test connessione» (psycopg rifiuta NUL con DataError)."""
    from psycopg.types.string import StrDumper

    from app.shared.connection_tests import results

    body = bytes(range(256)).decode("latin-1")
    raws = (f"HTTP 500: {body}", "PK\x03\x04\x00\x00binary\x00", b"\x00\xff\xfe".decode("utf-8", "surrogateescape"))
    for raw in raws:
        _, message = results.outcome_of({"success": False, "error": raw}, set())
        StrDumper(str).dump(message)  # non solleva
        assert "\x00" not in message


# Segreti di una risorsa: nomi sensibili, UNA lista per i due livelli

# Nomi (campi e parametri) il cui valore e' un segreto; compresi tutti quelli
# dell'elenco storico del livello 1.
_SENSITIVE_NAMES = (
    "key", "appid", "app_id", "apikey", "api_key", "apiKey", "x-api-key", "X-API-Key", "subscription-key",
    "Ocp-Apim-Subscription-Key", "access_key", "secret_key", "token", "access_token", "accessToken",
    "refresh_token", "id_token", "sas_token", "secret", "client_secret", "clientSecret", "password",
    "passwd", "pwd", "passphrase", "private_key", "privateKey", "private_key_passphrase",
    "privateKeyPassphrase", "key_value", "keyValue", "secret_access_key", "secretAccessKey",
    "aws_secret_access_key", "authorization", "Authorization", "auth", "credential", "credentials",
    "sig", "signature", "connection_string", "connectionString", "X-Auth-Token", "db_password",
    "dbPassword", "session_id", "app_key", "auth_key",
)
_PLAIN_NAMES = (
    "f", "q", "outFields", "where", "page", "format", "username", "user", "client_id", "tenant_id",
    "key_name", "token_url", "auth_type", "type", "location", "host", "name", "Accept", "monkey",
    "keyboard", "tokens_used", "access_key_id",
)


@pytest.mark.parametrize("name", _SENSITIVE_NAMES)
def test_sensitive_name_is_the_same_list_for_both_levels(name):
    from app.shared.error_sanitizer import is_sensitive_name

    assert is_sensitive_name(name)
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    # Livello 2: lo stesso nome nel messaggio oscura il valore.
    assert "Zk7val" not in sanitize_http_detail(f"errore {snake}=Zk7val99"), name


@pytest.mark.parametrize("name", _PLAIN_NAMES)
def test_plain_name_is_not_sensitive(name):
    from app.shared.error_sanitizer import is_sensitive_name

    assert not is_sensitive_name(name)


def test_level_one_has_no_second_name_list():
    from app.shared import error_sanitizer

    assert not hasattr(error_sanitizer, "_SECRET_FIELD_NAMES")
    assert not hasattr(error_sanitizer, "_SECRET_FIELD_SUFFIXES")


def _param_forms(name: str, value: str) -> dict[str, dict]:
    """Tutte le forme dei parametri di un connettore API."""
    return {
        "static_parameters": {"static_parameters": {name: value, "f": "json"}},
        "parameters_default_value": {"parameters": [{"name": name, "default_value": value, "type": "string"},
                                                    {"name": "f", "default_value": "json"}]},
        "parameters_value": {"parameters": [{"name": name, "value": value}]},
        "query_params_key_value": {"endpoint": {"query_params": [{"key": name, "value": value}]}},
        "headers_list": {"headers": [{"name": name, "value": value}]},
        "custom_headers_dict": {"custom_headers": {name: value}},
        "runtime_static_parameters": {"runtime_static_parameters": {name: value}},
    }


_FORM_NAMES = sorted(_param_forms("x", "y"))


@pytest.mark.parametrize("form", _FORM_NAMES)
@pytest.mark.parametrize("name", _SENSITIVE_NAMES)
def test_api_connector_sensitive_parameters_are_secrets(form, name):
    from app.shared.error_sanitizer import collect_secret_values

    config = {"endpoint": {"url": "https://h/x", "method": "GET"}, "auth": {"type": "none"},
              **_param_forms(name, "Zk7paramVal")[form]}
    secrets = collect_secret_values(config)
    assert "Zk7paramVal" in secrets, (form, name)
    assert name not in secrets and "json" not in secrets


@pytest.mark.parametrize("form", _FORM_NAMES)
@pytest.mark.parametrize("name", _PLAIN_NAMES)
def test_api_connector_plain_parameters_are_not_secrets(form, name):
    from app.shared.error_sanitizer import collect_secret_values

    assert "roma-centro" not in collect_secret_values(_param_forms(name, "roma-centro")[form]), (form, name)
