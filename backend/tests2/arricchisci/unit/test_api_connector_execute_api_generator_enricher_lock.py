"""
Lock tests per _execute_api_generator e _execute_api_enricher.

Scopo: congelare il contratto osservabile di entrambi i metodi prima
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Mock HTTP response builder


def _resp(status=200, json_body=None, text="", headers=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("not json")
    return r


# Fixture


@pytest.fixture
def gen_env(api_service):
    """Patcha collaborator HTTP per _execute_api_generator e _enricher."""
    stack = ExitStack()

    def _p(target, **kwargs):
        return stack.enter_context(patch(target, **kwargs))

    def _po(attr, **kwargs):
        return stack.enter_context(patch.object(api_service, attr, **kwargs))

    with stack:
        # session mock con .get e .post che ritornano il prossimo resp configurato
        session = MagicMock()
        session.get = MagicMock()
        session.post = MagicMock()

        mocks = SimpleNamespace(
            svc=api_service,
            session=session,
            shared_session=_p(
                "app.core.arricchisci.api_connection_generator_enricher."
                "get_shared_session",
                return_value=session,
            ),
            # `api_connection_generator_enricher` fa `from ... import
            # validate_external_url`, quindi il binding e' locale al modulo
            # consumer: patch dell'origine non basta.
            ssrf=_p(
                "app.core.arricchisci.api_connection_generator_enricher."
                "validate_external_url",
                return_value=None,
            ),
            prep_headers=_po(
                "_prepare_headers",
                return_value={"Authorization": "Bearer x"},
            ),
        )
        # default success
        session.get.return_value = _resp(200, json_body={"items": []})
        session.post.return_value = _resp(200, json_body={"items": []})
        yield mocks


# _execute_api_generator — config & basic


class TestGeneratorBasics:
    def test_missing_url_raises_value_error(self, gen_env):
        with pytest.raises(ValueError, match="URL API non configurato"):
            gen_env.svc._execute_api_generator({"endpoint": {"url": ""}})

    def test_no_pagination_single_call_returns_dataframe(self, gen_env):
        gen_env.session.get.return_value = _resp(
            200, json_body=[{"a": 1}, {"a": 2}]
        )
        cfg = {
            "endpoint": {"url": "https://api.test/items", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "a", "column": "a"}],
        }
        result = gen_env.svc._execute_api_generator(cfg)
        assert isinstance(result, pd.DataFrame)
        gen_env.session.get.assert_called_once()

    def test_input_df_first_row_used_as_base_params(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [],
        }
        input_df = pd.DataFrame([{"city": "Roma", "country": "IT"}])
        gen_env.svc._execute_api_generator(cfg, input_df=input_df)

        kwargs = gen_env.session.get.call_args.kwargs
        params = kwargs.get("params", {})
        assert params.get("city") == "Roma"
        assert params.get("country") == "IT"

    def test_static_params_used_when_no_input_df(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "static_parameters": {"key": "value"},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)

        kwargs = gen_env.session.get.call_args.kwargs
        assert kwargs.get("params", {}).get("key") == "value"

    def test_api_key_in_query_appended_to_params(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {
                "type": "api_key",
                "location": "query",
                "key_name": "apikey",
                "key_value": "SECRET123",
            },
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)
        kwargs = gen_env.session.get.call_args.kwargs
        assert kwargs.get("params", {}).get("apikey") == "SECRET123"

    def test_http_error_raises_value_error(self, gen_env):
        gen_env.session.get.return_value = _resp(500, text="server crashed")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [],
        }
        with pytest.raises(ValueError, match="HTTP 500"):
            gen_env.svc._execute_api_generator(cfg)


# _execute_api_generator — URL path param substitution


class TestGeneratorPathParams:
    def test_path_param_in_url_substituted(self, gen_env):
        """{city} nel URL deve essere sostituito col valore dei params,
        e non passato come query param."""
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/cities/{city}/data", "method": "GET"},
            "auth": {},
            "static_parameters": {"city": "Roma"},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)

        called_url = gen_env.session.get.call_args[0][0]
        assert "Roma" in called_url
        assert "{city}" not in called_url
        # E NON deve essere nel query params
        kwargs = gen_env.session.get.call_args.kwargs
        assert "city" not in kwargs.get("params", {})


# _execute_api_generator — pagination


class TestGeneratorPagination:
    def test_offset_pagination_iterates_until_empty(self, gen_env):
        """Offset pagination: chiama N volte finche' il response e' vuoto
        o si raggiunge max_rows."""
        gen_env.session.get.side_effect = [
            _resp(200, json_body=[{"x": 1}, {"x": 2}]),
            _resp(200, json_body=[{"x": 3}]),
            _resp(200, json_body=[]),  # stop
        ]
        cfg = {
            "endpoint": {"url": "https://api.test/items", "method": "GET"},
            "auth": {},
            "output_mapping": [],
            "pagination": {
                "type": "offset",
                "page_size": 2,
                "max_rows": 100,
                "offset_param": "offset",
                "limit_param": "limit",
            },
        }
        gen_env.svc._execute_api_generator(cfg)
        # Almeno 2 chiamate (la terza opzionale dipende dall'early-break
        # "len(rows) < page_size")
        assert gen_env.session.get.call_count >= 2

    def test_offset_pagination_respects_max_rows(self, gen_env):
        gen_env.session.get.return_value = _resp(
            200, json_body=[{"x": i} for i in range(10)]
        )
        cfg = {
            "endpoint": {"url": "https://api.test/items", "method": "GET"},
            "auth": {},
            "output_mapping": [],
            "pagination": {
                "type": "offset",
                "page_size": 10,
                "max_rows": 15,  # cap dopo 15
            },
        }
        result = gen_env.svc._execute_api_generator(cfg)
        # Result deve avere al massimo 15 righe
        assert len(result) <= 15

    def test_cursor_pagination_follows_cursor_path(self, gen_env):
        gen_env.session.get.side_effect = [
            _resp(200, json_body={"items": [{"x": 1}], "next_cursor": "c1"}),
            _resp(200, json_body={"items": [{"x": 2}], "next_cursor": "c2"}),
            _resp(200, json_body={"items": [{"x": 3}], "next_cursor": None}),
        ]
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "items[*].x", "column": "x"}],
            "pagination": {
                "type": "cursor",
                "cursor_param": "cursor",
                "cursor_path": "next_cursor",
            },
        }
        gen_env.svc._execute_api_generator(cfg)
        # Almeno 2 chiamate (la terza con cursor=c2)
        assert gen_env.session.get.call_count >= 2

        # Verifica che la 2a chiamata abbia cursor=c1
        second_call_kwargs = gen_env.session.get.call_args_list[1].kwargs
        assert second_call_kwargs.get("params", {}).get("cursor") == "c1"

    def test_link_pagination_follows_next_url_in_body(self, gen_env):
        gen_env.session.get.side_effect = [
            _resp(
                200,
                json_body={"items": [{"x": 1}], "next": "https://api.test/p2"},
            ),
            _resp(200, json_body={"items": [{"x": 2}], "next": None}),
        ]
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "items[*].x", "column": "x"}],
            "pagination": {"type": "link", "link_path": "next"},
        }
        gen_env.svc._execute_api_generator(cfg)
        # 2 chiamate (la seconda con URL p2)
        assert gen_env.session.get.call_count == 2
        second_url = gen_env.session.get.call_args_list[1][0][0]
        assert "p2" in second_url


# _execute_api_enricher


class TestEnricher:
    def test_missing_url_raises_value_error(self, gen_env):
        with pytest.raises(ValueError, match="URL API non configurato"):
            gen_env.svc._execute_api_enricher(
                pd.DataFrame([{"a": 1}]),
                {"endpoint": {"url": ""}},
                {},
            )

    def test_output_columns_initialized_to_none(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body={"name": "Mario"})
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "name", "column": "user_name"}],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"id": 1}])
        result = gen_env.svc._execute_api_enricher(df, cfg, {})

        assert "user_name" in result.columns
        assert result.at[0, "user_name"] == "Mario"

    def test_param_mapping_extracts_from_row_columns(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body={})
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"city_col": "Roma", "country_col": "IT"}])
        gen_env.svc._execute_api_enricher(
            df, cfg,
            param_mapping={"city": "city_col", "country": "country_col"},
        )

        kwargs = gen_env.session.get.call_args.kwargs
        params = kwargs.get("params", {})
        assert params.get("city") == "Roma"
        assert params.get("country") == "IT"

    def test_static_parameters_merged_with_mapped(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body={})
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "static_parameters": {"key": "static_value"},
            "output_mapping": [],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"col": "v"}])
        gen_env.svc._execute_api_enricher(df, cfg, {"mapped": "col"})

        params = gen_env.session.get.call_args.kwargs.get("params", {})
        assert params.get("key") == "static_value"
        assert params.get("mapped") == "v"

    def test_http_error_does_not_raise_increments_error_count(self, gen_env):
        gen_env.session.get.side_effect = [
            _resp(200, json_body={"x": 1}),  # OK
            _resp(500, text="err"),  # FAIL
            _resp(200, json_body={"x": 3}),  # OK
        ]
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "x", "column": "x"}],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"id": 1}, {"id": 2}, {"id": 3}])
        # Non solleva
        result = gen_env.svc._execute_api_enricher(df, cfg, {})
        assert len(result) == 3
        # Le righe OK hanno valore, la riga in errore resta None
        assert result.at[0, "x"] == 1
        assert result.at[1, "x"] is None
        assert result.at[2, "x"] == 3

    def test_url_path_param_substituted_per_row(self, gen_env):
        """`{city}` nel URL deve essere sostituito col valore della riga,
        e NON passato come query param."""
        gen_env.session.get.return_value = _resp(200, json_body={})
        cfg = {
            "endpoint": {"url": "https://api.test/cities/{city}", "method": "GET"},
            "auth": {},
            "output_mapping": [],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"city_col": "Milano"}])
        gen_env.svc._execute_api_enricher(df, cfg, {"city": "city_col"})

        called_url = gen_env.session.get.call_args[0][0]
        assert "Milano" in called_url
        assert "{city}" not in called_url
        assert "city" not in gen_env.session.get.call_args.kwargs.get("params", {})

    def test_result_df_is_a_copy_not_the_input(self, gen_env):
        """`_execute_api_enricher` deve ritornare una copia, non mutare il
        DataFrame originale."""
        gen_env.session.get.return_value = _resp(200, json_body={"x": 99})
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "x", "column": "x"}],
            "options": {"rate_limit": 0},
        }
        df = pd.DataFrame([{"id": 1}])
        result = gen_env.svc._execute_api_enricher(df, cfg, {})

        assert "x" not in df.columns
        assert "x" in result.columns
