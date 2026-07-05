"""
Behavioral unit tests per `EnrichmentBatchModeMixin`
(`app/core/arricchisci/enrichment_batch_mode.py`).

Il mixin e' un "batch native API path": raggruppa righe, chiama l'API una
volta per batch, mappa i risultati alle righe originali. Dipende da numerosi
collaboratori del suo host (`EnrichmentExecutor`): `output_mapping`,
`batch_config`, `auth`, `options`, `_rate_limiter`, `_headers`, `url`,
`method`, `_stats_lock`, `_response_times`, piu' i metodi mixin
`_build_params`, `_has_null_params`, `_apply_result`, `_extract_json_value`,
`_update_progress`.

Strategia: costruiamo un host minimale che mixa il mixin reale ma fornisce i
collaboratori come stub deterministici. Le chiamate HTTP vengono intercettate
montando una fake session via monkeypatch su `get_shared_session` nel modulo
sotto test. Cosi' esercitiamo la logica reale del modulo (batching, fallback
row-by-row, retry/429, costruzione payload, estrazione output) senza rete.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, List, Optional

import pandas as pd
import pytest

import app.core.arricchisci.enrichment_batch_mode as batch_mod
from app.core.arricchisci.enrichment_batch_mode import EnrichmentBatchModeMixin
from app.core.arricchisci.models import (
    BatchConfig,
    BatchInputFormat,
    EnrichmentOptions,
    EnrichmentStats,
    RowStatus,
)
from app.shared.resources.json_path import (
    extract_json_value as real_extract_json_value,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
]


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class _NoopRateLimiter:
    """Rate limiter inerte: conta le acquire ma non dorme."""

    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


class _FakeResponse:
    def __init__(self, status_code: int, json_data: Any = None,
                 text: str = "", headers: Optional[dict] = None) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        return self._json


class _FakeSession:
    """Restituisce risposte da una coda; registra le chiamate.

    `responses` puo' contenere _FakeResponse o eccezioni (che verranno
    sollevate quando estratte) per simulare timeout / errori di rete.
    """

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.get_calls: List[dict] = []
        self.post_calls: List[dict] = []

    def _next(self) -> Any:
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.get_calls.append({"url": url, "params": params,
                               "headers": headers, "timeout": timeout,
                               **kwargs})
        return self._next()

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.post_calls.append({"url": url, "json": json,
                                "headers": headers, "timeout": timeout,
                                **kwargs})
        return self._next()


class _Host(EnrichmentBatchModeMixin):
    """Host minimale che fornisce gli attributi richiesti dal mixin.

    `_build_params` ritorna il dict-riga grezzo; `_has_null_params` considera
    null se uno dei valori e' None; `_apply_result` scrive le colonne del
    result sulla riga; `_extract_json_value` delega all'implementazione reale
    condivisa; `_update_progress` registra le chiamate.
    """

    def __init__(
        self,
        *,
        output_mapping: List[dict],
        batch_config: BatchConfig,
        options: EnrichmentOptions,
        auth: Optional[dict] = None,
        url: str = "https://api.example.com/batch",
        method: str = "POST",
    ) -> None:
        self.output_mapping = output_mapping
        self.batch_config = batch_config
        self.options = options
        self.auth = auth or {}
        self.url = url
        self.method = method
        self._headers = {"Accept": "application/json"}
        self._rate_limiter = _NoopRateLimiter()
        self._stats_lock = threading.Lock()
        self._response_times: deque = deque(maxlen=1000)
        self.progress_calls: List[tuple] = []

    # --- collaboratori normalmente forniti da altri mixin ---

    def _build_params(self, row: Any) -> dict:
        return dict(row)

    def _has_null_params(self, params: dict) -> bool:
        return any(pd.isna(v) for v in params.values())

    def _apply_result(self, df: Any, idx: int, result: dict) -> None:
        for col, value in result.items():
            df.at[idx, col] = value

    def _extract_json_value(self, data: Any, path: str) -> Any:
        return real_extract_json_value(data, path)

    def _update_progress(self, processed: int, total: int,
                         stats: EnrichmentStats) -> None:
        self.progress_calls.append((processed, total))


# --------------------------------------------------------------------------
# Factory helpers
# --------------------------------------------------------------------------

def _make_host(monkeypatch, *, responses, output_mapping=None,
               batch_config=None, options=None, auth=None,
               url="https://api.example.com/batch", method="POST"):
    """Crea un host e installa la fake session nel modulo sotto test."""
    if output_mapping is None:
        output_mapping = [{"path": "value", "column": "out"}]
    if batch_config is None:
        batch_config = BatchConfig(enabled=True, max_size=2, input_key="items",
                                   input_format=BatchInputFormat.ARRAY,
                                   output_path="results")
    if options is None:
        options = EnrichmentOptions(max_retries=0, rate_limit=1000.0)

    session = _FakeSession(responses)
    monkeypatch.setattr(
        "app.core.arricchisci.http_executor.get_shared_session",
        lambda: session,
    )
    monkeypatch.setattr(
        "app.core.arricchisci.http_executor.validate_external_url",
        lambda _url: None,
    )
    monkeypatch.setattr(batch_mod.time, "sleep", lambda *_a, **_k: None)

    host = _Host(output_mapping=output_mapping, batch_config=batch_config,
                 options=options, auth=auth, url=url, method=method)
    return host, session


def _resp(items, output_path="results", status=200):
    """Risposta 200 con array di risultati al path indicato."""
    body = {output_path: items} if output_path else items
    return _FakeResponse(status, json_data=body)


# --------------------------------------------------------------------------
# _build_batch_payload
# --------------------------------------------------------------------------

class TestBuildBatchPayload:
    def test_array_format_wraps_clean_params_under_input_key(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            batch_config=BatchConfig(enabled=True, input_key="addresses",
                                     input_format=BatchInputFormat.ARRAY),
        )
        payload = host._build_batch_payload([{"q": "a"}, {"q": "b"}])
        assert payload == {"addresses": [{"q": "a"}, {"q": "b"}]}

    def test_array_format_strips_api_key_param(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            auth={"type": "api_key", "key_name": "apiKey", "location": "header",
                  "key_value": "secret"},
            batch_config=BatchConfig(enabled=True, input_key="items",
                                     input_format=BatchInputFormat.ARRAY),
        )
        payload = host._build_batch_payload([{"q": "a", "apiKey": "secret"}])
        # api key rimossa dai params del batch (location header, non query)
        assert payload == {"items": [{"q": "a"}]}

    def test_query_api_key_added_top_level(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            auth={"type": "api_key", "key_name": "apiKey", "location": "query",
                  "key_value": "secret"},
        )
        payload = host._build_batch_payload([{"q": "a", "apiKey": "secret"}])
        assert payload["apiKey"] == "secret"
        # e rimossa dal corpo dei params
        assert payload["items"] == [{"q": "a"}]

    def test_flat_array_takes_first_non_keyvalue(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            batch_config=BatchConfig(enabled=True, input_key="items",
                                     input_format=BatchInputFormat.FLAT_ARRAY),
        )
        payload = host._build_batch_payload([{"q": "addr1"}, {"q": "addr2"}])
        assert payload == {"items": ["addr1", "addr2"]}

    def test_flat_array_skips_api_key_and_none(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            auth={"type": "api_key", "key_name": "apiKey", "location": "header"},
            batch_config=BatchConfig(enabled=True, input_key="items",
                                     input_format=BatchInputFormat.FLAT_ARRAY),
        )
        # apiKey deve essere saltato, None deve essere saltato, primo valore
        # utile = "addr"
        payload = host._build_batch_payload([
            {"apiKey": "secret", "skip_me": None, "q": "addr"},
        ])
        assert payload == {"items": ["addr"]}


# --------------------------------------------------------------------------
# _extract_batch_output
# --------------------------------------------------------------------------

class TestExtractBatchOutput:
    def test_extracts_mapped_columns_per_element(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            output_mapping=[{"path": "name", "column": "city"},
                            {"path": "score", "column": "rank"}],
            batch_config=BatchConfig(enabled=True, output_path="results"),
        )
        data = {"results": [{"name": "Roma", "score": 1},
                            {"name": "Milano", "score": 2}]}
        out = host._extract_batch_output(data)
        assert out == [{"city": "Roma", "rank": 1},
                       {"city": "Milano", "rank": 2}]

    def test_none_element_preserved_as_none(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            output_mapping=[{"path": "name", "column": "city"}],
            batch_config=BatchConfig(enabled=True, output_path="results"),
        )
        data = {"results": [{"name": "Roma"}, None]}
        out = host._extract_batch_output(data)
        assert out[0] == {"city": "Roma"}
        assert out[1] is None

    def test_non_array_response_returns_empty(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            batch_config=BatchConfig(enabled=True, output_path="results"),
        )
        # output_path punta a un dict, non a una lista
        out = host._extract_batch_output({"results": {"oops": True}})
        assert out == []

    def test_empty_output_path_uses_root_array(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            output_mapping=[{"path": "v", "column": "out"}],
            batch_config=BatchConfig(enabled=True, output_path=""),
        )
        out = host._extract_batch_output([{"v": 10}, {"v": 20}])
        assert out == [{"out": 10}, {"out": 20}]

    def test_column_defaults_to_path_when_absent(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch, responses=[],
            output_mapping=[{"path": "name"}],  # nessun 'column'
            batch_config=BatchConfig(enabled=True, output_path="results"),
        )
        out = host._extract_batch_output({"results": [{"name": "X"}]})
        assert out == [{"name": "X"}]


# --------------------------------------------------------------------------
# _call_batch_api
# --------------------------------------------------------------------------

class TestCallBatchApi:
    def test_success_returns_extracted_results(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "ok"}])],
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert err is None
        assert results == [{"out": "ok"}]
        assert len(session.post_calls) == 1

    def test_get_method_uses_query_params(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "ok"}])],
            method="GET",
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert err is None
        assert len(session.get_calls) == 1
        assert session.post_calls == []

    def test_endpoint_and_method_override_applied(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "ok"}])],
            batch_config=BatchConfig(enabled=True, output_path="results",
                                     endpoint_override="https://override/x",
                                     method_override="get"),
            method="POST",
        )
        host._call_batch_api([{"q": "a"}], [0])
        assert session.get_calls[0]["url"] == "https://override/x"

    def test_non_retriable_4xx_returns_error_no_retry(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(404, text="missing")],
            options=EnrichmentOptions(max_retries=3),
        )
        results, err = host._call_batch_api([{"q": "a"}], [7])
        assert results is None
        assert err is not None
        assert err.http_status == 404
        assert err.error_type == "http_404"
        assert err.row_index == 7
        # nessun retry: una sola chiamata
        assert len(session.post_calls) == 1

    def test_retriable_500_then_success(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(500, text="boom"),
                       _resp([{"value": "ok"}])],
            options=EnrichmentOptions(max_retries=2),
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert err is None
        assert results == [{"out": "ok"}]
        assert len(session.post_calls) == 2

    def test_retry_exhausted_sets_status(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(503), _FakeResponse(503)],
            options=EnrichmentOptions(max_retries=1),
        )
        results, err = host._call_batch_api([{"q": "a"}], [3])
        assert results is None
        assert err is not None
        assert err.status == RowStatus.RETRY_EXHAUSTED
        # max_retries=1 → 2 tentativi totali
        assert len(session.post_calls) == 2

    def test_429_rate_limit_then_success(self, monkeypatch):
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(429, headers={"Retry-After": "1"}),
                       _resp([{"value": "ok"}])],
            options=EnrichmentOptions(max_retries=3, respect_retry_after=True),
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert err is None
        assert results == [{"out": "ok"}]
        assert len(session.post_calls) == 2

    def test_timeout_then_success(self, monkeypatch):
        import requests
        host, session = _make_host(
            monkeypatch,
            responses=[requests.Timeout("slow"), _resp([{"value": "ok"}])],
            options=EnrichmentOptions(max_retries=2),
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert err is None
        assert results == [{"out": "ok"}]

    def test_timeout_exhausted_returns_typed_error(self, monkeypatch):
        import requests
        host, session = _make_host(
            monkeypatch,
            responses=[requests.Timeout("slow"), requests.Timeout("slow")],
            options=EnrichmentOptions(max_retries=1),
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert results is None
        assert err.error_type == "timeout"
        assert err.status == RowStatus.RETRY_EXHAUSTED

    def test_request_exception_returns_typed_error(self, monkeypatch):
        import requests
        host, session = _make_host(
            monkeypatch,
            responses=[requests.ConnectionError("no route")],
            options=EnrichmentOptions(max_retries=0),
        )
        results, err = host._call_batch_api([{"q": "a"}], [0])
        assert results is None
        assert err.error_type == "request_error"
        assert "no route" in err.error_message

    def test_response_time_recorded_on_http_call(self, monkeypatch):
        host, _ = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "ok"}])],
        )
        host._call_batch_api([{"q": "a"}], [0])
        assert len(host._response_times) == 1


# --------------------------------------------------------------------------
# _execute_batch_native (orchestration)
# --------------------------------------------------------------------------

class TestExecuteBatchNative:
    def test_happy_path_single_batch(self, monkeypatch):
        df = pd.DataFrame({"q": ["a", "b"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}, {"value": "B"}])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert errors == []
        assert stats.success == 2
        assert stats.errors == 0
        assert stats.api_calls_made == 1
        # 2 righe in una chiamata → 1 risparmiata
        assert stats.api_calls_saved == 1
        assert list(result_df["out"]) == ["A", "B"]
        assert stats.total_rows == 2

    def test_multiple_batches_respect_max_size(self, monkeypatch):
        df = pd.DataFrame({"q": ["a", "b", "c"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}, {"value": "B"}]),
                       _resp([{"value": "C"}])],
            batch_config=BatchConfig(enabled=True, max_size=2,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 3
        assert stats.api_calls_made == 2
        assert list(result_df["out"]) == ["A", "B", "C"]

    def test_skip_null_params(self, monkeypatch):
        # riga 1 ha q=None → skippata (skip_null_params default True)
        df = pd.DataFrame({"q": ["a", None, "c"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}, {"value": "C"}])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
            options=EnrichmentOptions(skip_null_params=True),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.skipped == 1
        assert stats.success == 2
        # solo le 2 righe valide processate, una chiamata
        assert stats.api_calls_made == 1
        assert result_df.loc[1, "out"] is None

    def test_null_result_in_batch_recorded_as_error(self, monkeypatch):
        df = pd.DataFrame({"q": ["a", "b"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}, None])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 1
        assert stats.errors == 1
        assert errors[0].error_type == "null_result"
        assert errors[0].row_index == 1

    def test_incomplete_batch_response_recorded_as_error(self, monkeypatch):
        df = pd.DataFrame({"q": ["a", "b", "c"]})
        host, session = _make_host(
            monkeypatch,
            # risposta piu' corta dell'input (2 invece di 3)
            responses=[_resp([{"value": "A"}, {"value": "B"}])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 2
        assert stats.errors == 1
        assert errors[0].error_type == "incomplete_batch"
        assert errors[0].row_index == 2

    def test_batch_error_blocking_marks_whole_batch(self, monkeypatch):
        # continue_on_error False → niente fallback row-by-row, tutto errore
        df = pd.DataFrame({"q": ["a", "b"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(404, text="nope")],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
            options=EnrichmentOptions(continue_on_error=False, max_retries=0),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 0
        assert stats.errors == 2
        assert {e.row_index for e in errors} == {0, 1}
        assert all(e.error_type == "http_404" for e in errors)

    def test_batch_error_fallback_isolates_good_rows(self, monkeypatch):
        # batch (2 righe) fallisce; fallback row-by-row: prima riga ok,
        # seconda riga errore. continue_on_error=True (default).
        df = pd.DataFrame({"q": ["good", "bad"]})
        host, session = _make_host(
            monkeypatch,
            responses=[
                _FakeResponse(500, text="batch boom"),       # batch fallisce
                _resp([{"value": "GOOD"}]),                   # row 0 ok
                _FakeResponse(404, text="bad row"),           # row 1 errore
            ],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
            options=EnrichmentOptions(continue_on_error=True, max_retries=0),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 1
        assert stats.errors == 1
        assert result_df.loc[0, "out"] == "GOOD"
        assert errors[0].row_index == 1
        # 1 chiamata batch + 2 chiamate single = 3
        assert stats.api_calls_made == 3

    def test_fallback_null_single_result_recorded(self, monkeypatch):
        # fallback row-by-row dove la single call ritorna lista con None
        df = pd.DataFrame({"q": ["x", "y"]})
        host, session = _make_host(
            monkeypatch,
            responses=[
                _FakeResponse(500),               # batch fallisce
                _resp([None]),                    # row 0: risultato nullo
                _resp([{"value": "Y"}]),          # row 1 ok
            ],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
            options=EnrichmentOptions(continue_on_error=True, max_retries=0),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.success == 1
        assert stats.errors == 1
        assert errors[0].row_index == 0
        assert result_df.loc[1, "out"] == "Y"

    def test_single_row_batch_error_no_fallback(self, monkeypatch):
        # batch di 1 riga: len(batch) == 1 → niente fallback anche con
        # continue_on_error, va nel ramo "blocking" che conta come errore.
        df = pd.DataFrame({"q": ["solo"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_FakeResponse(404, text="nope")],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
            options=EnrichmentOptions(continue_on_error=True, max_retries=0),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.errors == 1
        assert stats.success == 0
        # nessun fallback: una sola chiamata
        assert stats.api_calls_made == 1
        assert errors[0].row_index == 0

    def test_output_columns_created_when_absent(self, monkeypatch):
        df = pd.DataFrame({"q": ["a"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}])],
            output_mapping=[{"path": "value", "column": "brand_new_col"}],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert "brand_new_col" in result_df.columns
        assert result_df.loc[0, "brand_new_col"] == "A"

    def test_empty_dataframe_no_calls(self, monkeypatch):
        df = pd.DataFrame({"q": []})
        host, session = _make_host(
            monkeypatch,
            responses=[],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        assert stats.total_rows == 0
        assert stats.api_calls_made == 0
        assert stats.success == 0
        assert errors == []
        assert len(session.post_calls) == 0

    def test_progress_and_timing_finalized(self, monkeypatch):
        df = pd.DataFrame({"q": ["a", "b"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}, {"value": "B"}])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        result_df, stats, errors = host._execute_batch_native(df)
        # _update_progress chiamato almeno una volta
        assert host.progress_calls
        # total_time_ms popolato; avg_response_time_ms derivato da una chiamata
        assert stats.total_time_ms >= 0
        assert stats.avg_response_time_ms >= 0

    def test_does_not_mutate_input_dataframe(self, monkeypatch):
        df = pd.DataFrame({"q": ["a"]})
        host, session = _make_host(
            monkeypatch,
            responses=[_resp([{"value": "A"}])],
            batch_config=BatchConfig(enabled=True, max_size=10,
                                     output_path="results"),
        )
        host._execute_batch_native(df)
        # copy_frame_for_update deve aver protetto l'input: 'out' non scritta
        assert "out" not in df.columns
