"""
Lock tests per _execute_api_generator.

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
                "app.core.arricchisci.http_executor."
                "get_shared_session",
                return_value=session,
            ),
            ssrf=_p(
                "app.core.arricchisci.http_executor."
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

    def test_csv_response_format_returns_dataframe(self, gen_env):
        gen_env.session.get.return_value = _resp(
            200,
            text="city,pop\nRoma,2873000\nMilano,1352000\n",
            headers={"Content-Type": "text/csv"},
        )
        cfg = {
            "endpoint": {"url": "https://api.test/items.csv", "method": "GET"},
            "auth": {},
            "response": {"format": "csv"},
            "output_mapping": [
                {"path": "city", "column": "city"},
                {"path": "pop", "column": "population"},
            ],
        }

        result = gen_env.svc._execute_api_generator(cfg)

        assert result["city"].tolist() == ["Roma", "Milano"]
        assert result["population"].tolist() == ["2873000", "1352000"]

    def test_xml_response_format_returns_dataframe(self, gen_env):
        gen_env.session.get.return_value = _resp(
            200,
            text=(
                "<items>"
                "<item><city>Roma</city><pop>2873000</pop></item>"
                "<item><city>Milano</city><pop>1352000</pop></item>"
                "</items>"
            ),
            headers={"Content-Type": "application/xml"},
        )
        cfg = {
            "endpoint": {"url": "https://api.test/items.xml", "method": "GET"},
            "auth": {},
            "response": {"format": "xml"},
            "output_mapping": [
                {"path": "items.item[*].city", "column": "city"},
                {"path": "items.item[*].pop", "column": "population"},
            ],
        }

        result = gen_env.svc._execute_api_generator(cfg)

        assert result["city"].tolist() == ["Roma", "Milano"]
        assert result["population"].tolist() == ["2873000", "1352000"]

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

    def test_options_timeout_used_for_generator_call(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "options": {"timeout": 12},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)
        assert gen_env.session.get.call_args.kwargs["timeout"] == 12

    def test_split_timeout_options_used_for_generator_call(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "options": {"connect_timeout": 2, "request_timeout": 9},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)
        assert gen_env.session.get.call_args.kwargs["timeout"] == (2, 9)

    def test_http_error_raises_value_error(self, gen_env):
        gen_env.session.get.return_value = _resp(500, text="server crashed")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [],
        }
        with pytest.raises(ValueError, match="HTTP 500"):
            gen_env.svc._execute_api_generator(cfg)

    def test_application_error_path_raises_value_error(self, gen_env):
        gen_env.session.get.return_value = _resp(
            200, json_body={"error": {"message": "quota exceeded"}}
        )
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "error_path": "error",
            "output_mapping": [],
        }
        with pytest.raises(ValueError, match="quota exceeded"):
            gen_env.svc._execute_api_generator(cfg)

    def test_form_urlencoded_request_uses_data_body(self, gen_env):
        gen_env.session.post.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "POST"},
            "auth": {},
            "static_parameters": {"x": "1"},
            "request": {"body_type": "form-urlencoded"},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)

        kwargs = gen_env.session.post.call_args.kwargs
        assert kwargs["data"] == {"x": "1"}
        assert "json" not in kwargs

    def test_allow_redirects_config_is_passed_to_http_call(self, gen_env):
        gen_env.session.get.return_value = _resp(200, json_body=[])
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "request": {"allow_redirects": True},
            "output_mapping": [],
        }
        gen_env.svc._execute_api_generator(cfg)
        assert gen_env.session.get.call_args.kwargs["allow_redirects"] is True

    def test_async_generator_polls_and_parses_final_result(self, gen_env):
        gen_env.session.post.return_value = _resp(202, json_body={"id": "job-1"})
        gen_env.session.get.return_value = _resp(
            200,
            json_body={
                "status": "completed",
                "result": {"items": [{"x": 1}, {"x": 2}]},
            },
        )
        cfg = {
            "endpoint": {"url": "https://api.test/run", "method": "POST"},
            "auth": {},
            "output_mapping": [{"path": "items[*].x", "column": "x"}],
            "async": {
                "enabled": True,
                "job_id_from": "body",
                "job_id_path": "id",
                "accepted_status": [202],
                "status_url": "{base}/jobs/{job_id}",
                "status_path": "status",
                "done_values": ["completed"],
                "failed_values": ["failed"],
                "result_from": "status_response",
                "result_path": "result",
                "poll_interval_s": 0,
                "max_wait_s": 1,
            },
        }

        result = gen_env.svc._execute_api_generator(cfg)

        assert result["x"].tolist() == [1, 2]
        gen_env.session.post.assert_called_once()
        gen_env.session.get.assert_called_once()
        assert gen_env.session.get.call_args[0][0] == "https://api.test/jobs/job-1"

    def test_async_generator_rejects_pagination(self, gen_env):
        cfg = {
            "endpoint": {"url": "https://api.test/run", "method": "POST"},
            "auth": {},
            "output_mapping": [],
            "async": {
                "enabled": True,
                "job_id_from": "body",
                "job_id_path": "id",
                "status_url": "{base}/jobs/{job_id}",
            },
            "pagination": {"type": "offset", "page_size": 100},
        }

        with pytest.raises(ValueError, match="async.*pagination"):
            gen_env.svc._execute_api_generator(cfg)

        gen_env.session.post.assert_not_called()
        gen_env.session.get.assert_not_called()


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

    def test_link_pagination_reads_lowercase_link_header(self, gen_env):
        gen_env.session.get.side_effect = [
            _resp(
                200,
                json_body={"items": [{"x": 1}], "next": None},
                headers={"link": '<https://api.test/p2>; rel="next"'},
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
        assert gen_env.session.get.call_count == 2
        second_url = gen_env.session.get.call_args_list[1][0][0]
        assert "p2" in second_url

    def test_page_number_pagination_increments_page_param(self, gen_env):
        gen_env.session.get.side_effect = [
            _resp(200, json_body={"items": [{"x": 1}, {"x": 2}]}),
            _resp(200, json_body={"items": [{"x": 3}]}),
        ]
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "output_mapping": [{"path": "items[*].x", "column": "x"}],
            "pagination": {
                "type": "page-number",
                "page_param": "page",
                "page_size_param": "per_page",
                "page_size": 2,
                "start_page": 1,
                "max_rows": 10,
            },
        }
        gen_env.svc._execute_api_generator(cfg)

        assert gen_env.session.get.call_count == 2
        first_params = gen_env.session.get.call_args_list[0].kwargs["params"]
        second_params = gen_env.session.get.call_args_list[1].kwargs["params"]
        assert first_params["page"] == 1
        assert first_params["per_page"] == 2
        assert second_params["page"] == 2
