"""Parametri di percorso dell'URL: parita' con HEAD 5ea2d748 e regola stretta
solo per i valori degli input runtime (correzione finale prima del deploy,
punto 1; regola in ``app.core.arricchisci.url_path_params``).

- Valori NON runtime (connessione, parametri statici della connessione,
  ``param_mapping`` da colonne e template): l'URL e i parametri della query
  sono IDENTICI a quelli che produceva HEAD 5ea2d748, per riga, generatore e
  prova della connessione. Le funzioni di riferimento qui sotto sono copiate
  da HEAD 5ea2d748 (``enrichment_retry_http`` e
  ``ApiConnectionGeneratorEnricherMixin._substitute_url_path_params``).
- Valori runtime (``runtime_static_parameters``): codificati per intero,
  vuoto / ``.`` / ``..`` rifiutati, per riga e generatore.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from urllib.parse import quote as _quote

import pandas as pd
import pytest

import app.core.arricchisci.api_connection_generator_enricher as generator_mod
import app.core.arricchisci.api_connection_management as management_mod
import app.core.arricchisci.enrichment_retry_http as retry_mod
from app.core.arricchisci.api_connection_generator_enricher import ApiConnectionGeneratorEnricherMixin
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.shared.execution.runtime_bound_fields import RUNTIME_STATIC_PARAMETERS

pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ------------------------------------------------ riferimento HEAD 5ea2d748


def _head_row(url, params):
    """HEAD 5ea2d748, ``EnrichmentRetryHttpMixin._call_api_with_retry``."""
    call_url = url
    call_params_base = dict(params)
    for pname in re.findall(r'\{(\w+)\}', call_url):
        if pname in call_params_base:
            encoded = _quote(str(call_params_base.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
            call_url = call_url.replace(f'{{{pname}}}', encoded)
    return call_url, call_params_base


def _head_keys(url, params, *, safe="-._~:@!$&'()*+,=;/"):
    """HEAD 5ea2d748, ``_substitute_url_path_params`` (prova: default; generatore: safe='')."""
    target_url = url
    query_params = {}
    for pname, pvalue in params.items():
        placeholder = f'{{{pname}}}'
        if placeholder in target_url:
            encoded = _quote(str(pvalue), safe=safe)
            target_url = target_url.replace(placeholder, encoded)
        else:
            query_params[pname] = pvalue
    return target_url, query_params


CORPUS = [
    "2026-01-01T10:00:00", "2026-01-01T10:00:00+01:00", "mario@example.org", "v1/search", "a/b/c",
    "a,b", "45.4,9.2", "x=1;y=2", "a;b", "a+b", "a b", "O'Brien", "(1)", "*", "$5", "!", "~u",
    "città", "Ελλάδα", "日本語", "%41", "100%", "a?b", "a#b", "a&b", "{x}", "", ".", "..", "/..",
    "None", 12, 4.5, True, None,
]

URL = "https://api.example.com/{path}/{lat},{lon}/{id}/x"
CONNECTION = {"id": "api-1", "name": "api", "endpoint": {"url": URL, "method": "GET"},
              "static_parameters": {"path": "v1/search", "f": "json"}, "parameters": [], "output_mapping": []}


class _Response:
    status_code = 200
    text = ""
    headers: dict = {}
    elapsed_ms = 1.0
    content = b'{"ok": true}'

    def json(self):
        return {"ok": True}


def _capture_row_http(monkeypatch) -> list:
    specs: list = []
    monkeypatch.setattr(retry_mod, "validate_external_url", lambda url: None)
    monkeypatch.setattr(retry_mod, "resolve_tls_verify", lambda enabled, *, context: enabled)
    monkeypatch.setattr(retry_mod, "execute_http_request", lambda spec: specs.append(spec) or _Response())
    return specs


def _row_call(monkeypatch, *, node: dict, config: dict, row: dict):
    specs = _capture_row_http(monkeypatch)
    executor = build_enrichment_executor(node, {**config, RUNTIME_STATIC_PARAMETERS: node.get(
        RUNTIME_STATIC_PARAMETERS) or {}})
    params = executor._build_params(pd.Series(row))
    result, error = executor._call_api_with_retry(params, 0)
    return params, specs, error


# ------------------------------------------------ valori non runtime: parita'


@pytest.mark.parametrize("value", CORPUS, ids=repr)
def test_per_row_values_not_from_runtime_match_head(value, monkeypatch):
    """Colonna (``id``), template ``{lat},{lon}`` e parametro statico della
    connessione (``path``): URL e query identici a HEAD."""
    node = {"api_id": "api-1", "param_mapping": {"id": "c", "lat": "{la}", "lon": "lo"}}
    params, specs, error = _row_call(monkeypatch, node=node, config=CONNECTION,
                                     row={"c": value, "la": "45.1", "lo": value})
    assert error is None
    expected_url, expected_query = _head_row(URL, params)
    assert specs[0].url == expected_url
    assert specs[0].params == expected_query


@pytest.mark.parametrize("value", CORPUS, ids=repr)
def test_connection_static_path_matches_head(value, monkeypatch):
    node = {"api_id": "api-1", "param_mapping": {"id": "c", "lat": "c", "lon": "c"}}
    config = {**CONNECTION, "static_parameters": {"path": value}}
    params, specs, error = _row_call(monkeypatch, node=node, config=config, row={"c": "1"})
    assert error is None
    assert (specs[0].url, specs[0].params) == _head_row(URL, params)


@pytest.mark.parametrize("value", CORPUS, ids=repr)
def test_generator_values_not_from_runtime_match_head(value):
    params = {"path": "v1/search", "lat": value, "lon": "9.2", "id": value, "page": 1}
    got = ApiConnectionGeneratorEnricherMixin._substitute_url_path_params(
        URL, params, safe=generator_mod.GENERATOR_SAFE)
    assert got == _head_keys(URL, params, safe="")


@pytest.mark.parametrize("value", CORPUS, ids=repr)
def test_connection_test_matches_head(value):
    params = {"path": "v1/search", "lat": value, "lon": "9.2", "id": value, "f": "json"}
    assert ApiConnectionGeneratorEnricherMixin._substitute_url_path_params(URL, params) == _head_keys(URL, params)


def test_generator_call_uses_the_head_encoding(monkeypatch):
    """Chiamata vera del generatore: codifica stretta di HEAD (senza rifiuti)."""
    specs: list = []
    monkeypatch.setattr(generator_mod, "execute_http_request", lambda spec: specs.append(spec) or _Response())
    monkeypatch.setattr(generator_mod, "parse_response_body", lambda resp, cfg: {"ok": True})
    mixin = SimpleNamespace(_substitute_url_path_params=ApiConnectionGeneratorEnricherMixin._substitute_url_path_params,
                            _extract_rows_from_response=lambda data, mapping: [])
    params = {"path": "v1/search", "lat": "", "lon": "9,2", "id": "a b", "page": 1}
    ApiConnectionGeneratorEnricherMixin._call_generator_endpoint(
        mixin, url=URL, method="GET", headers={}, call_params=params, call_url=None, options={},
        response_preset="", response_config={}, request_config={}, async_config={},
        response_body_config={}, success_config={}, output_mapping=[], runtime_values={})
    assert (specs[0].url, specs[0].params) == _head_keys(URL, params, safe="")


def test_connection_test_call_matches_head(monkeypatch):
    """«Prova connessione» (``test_api_connection``): stessa richiesta di HEAD."""
    from app.core.arricchisci.api_connector import api_connection_service

    specs: list = []

    def fake_http(spec):
        specs.append(spec)
        raise RuntimeError("stop")

    monkeypatch.setattr(management_mod, "execute_http_request", fake_http)
    config = {**CONNECTION, "auth": {"type": "none"}}
    params = {"lat": "2026-01-01T10:00:00", "lon": "a,b", "id": "mario@example.org"}
    api_connection_service.test_api_connection(config=config, test_params=params)
    expected = _head_keys(URL, {"path": "v1/search", "f": "json", **params})
    assert specs and (specs[0].url, specs[0].params) == expected


# ------------------------------------------------ valori runtime: regola stretta


RUNTIME_URL = "https://api.example.com/users/{utente}/data"
RUNTIME_CONNECTION = {**CONNECTION, "endpoint": {"url": RUNTIME_URL, "method": "GET"},
                      "static_parameters": {"utente": "dalla-connessione"}}


@pytest.mark.parametrize("value,encoded", [
    ("/..", "%2F.."), ("%2F", "%252F"), ("?", "%3F"), ("#", "%23"), ("a/b", "a%2Fb"),
    ("a:b@c", "a%3Ab%40c"), ("a,b;c", "a%2Cb%3Bc"),
])
def test_runtime_value_is_fully_encoded_per_row(value, encoded, monkeypatch):
    node = {"api_id": "api-1", "param_mapping": {}, RUNTIME_STATIC_PARAMETERS: {"utente": value}}
    _params, specs, error = _row_call(monkeypatch, node=node, config=RUNTIME_CONNECTION, row={"a": "1"})
    assert error is None
    assert specs[0].url == f"https://api.example.com/users/{encoded}/data"


@pytest.mark.parametrize("value", ["", ".", ".."])
def test_runtime_dot_segments_are_refused_per_row(value, monkeypatch):
    node = {"api_id": "api-1", "param_mapping": {}, RUNTIME_STATIC_PARAMETERS: {"utente": value}}
    _params, specs, error = _row_call(monkeypatch, node=node, config=RUNTIME_CONNECTION, row={"a": "1"})
    assert specs == []
    assert error is not None and "percorso" in error.error_message


@pytest.mark.parametrize("value,expected", [
    ("/..", "%2F.."), ("%2F", "%252F"), ("?", "%3F"), ("#", "%23"),
    (".", ValueError), ("..", ValueError), ("", ValueError),
])
def test_runtime_value_in_the_generator(value, expected):
    params = {"utente": value, "page": 1}
    call = lambda: ApiConnectionGeneratorEnricherMixin._substitute_url_path_params(  # noqa: E731
        RUNTIME_URL, params, safe=generator_mod.GENERATOR_SAFE, runtime_values={"utente": value})
    if expected is ValueError:
        with pytest.raises(ValueError, match="percorso"):
            call()
    else:
        assert call() == (f"https://api.example.com/users/{expected}/data", {"page": 1})


def test_column_value_replacing_a_runtime_key_keeps_the_connection_encoding(monkeypatch):
    """Il ``param_mapping`` sostituisce il valore runtime con una colonna:
    vale la codifica della connessione (il valore non viene dall'input)."""
    node = {"api_id": "api-1", "param_mapping": {"utente": "c"}, RUNTIME_STATIC_PARAMETERS: {"utente": "x"}}
    params, specs, error = _row_call(monkeypatch, node=node, config=RUNTIME_CONNECTION, row={"c": "a/b:c"})
    assert error is None and specs[0].url == "https://api.example.com/users/a/b:c/data"
