"""
tests/arricchisci/unit/test_enrichment_execute.py
"""
import pytest
import pandas as pd
import json
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import EnrichmentOptions


def make_executor(url="http://mock.example.com/api", method="GET",
                  output_mapping=None, param_mapping=None, options=None, auth=None):
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": url, "method": method},
        "auth": auth or {"type": "none"},
        "output_mapping": output_mapping or [
            {"path": "capital", "column": "capitale"},
            {"path": "population", "column": "pop"},
        ],
        "static_parameters": {},
        "batch": {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping=param_mapping or {"country": "paese"},
        options=options or EnrichmentOptions(
            rate_limit=0, max_retries=0, request_timeout=5,
            continue_on_error=False, parallel_workers=1, validate_input=True,
        ),
    )


def mock_response(data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return resp


class TestExecuteValidation:

    def test_colonna_mancante_early_return(self):
        ex = make_executor(param_mapping={"country": "COLONNA_XYZ"})
        df = pd.DataFrame({"paese": ["Italy", "France"]})
        result_df, result = ex.execute(df)
        assert result.success is False

    def test_df_vuoto_non_crasha(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": []})
        result_df, result = ex.execute(df)
        assert isinstance(result_df, pd.DataFrame)

    def test_validate_input_false_skip_validation(self):
        options = EnrichmentOptions(
            rate_limit=0, max_retries=0, request_timeout=5,
            continue_on_error=True, parallel_workers=1, validate_input=False,
        )
        ex = make_executor(param_mapping={"country": "COLONNA_XYZ"}, options=options)
        df = pd.DataFrame({"paese": ["Italy"]})
        with patch("requests.get", return_value=mock_response({"capital": "Rome", "population": 60000000})):
            result_df, result = ex.execute(df)
        assert isinstance(result_df, pd.DataFrame)


class TestExecuteSequenziale:

    def test_success_10_righe(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy","France","Germany","Spain","Portugal",
                                      "Netherlands","Belgium","Austria","Sweden","Poland"]})
        def mock_get(*args, **kwargs):
            country = kwargs.get("params", {}).get("country", "Italy")
            return mock_response({"capital": f"Capital_{country}", "population": 60000000})
        with patch("requests.get", side_effect=mock_get):
            result_df, result = ex.execute(df)
        assert result.success is True
        assert result.stats.success == 10

    def test_colonne_aggiunte(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        with patch("requests.get", return_value=mock_response({"capital": "Rome", "population": 60000000})):
            result_df, result = ex.execute(df)
        assert "capitale" in result_df.columns
        assert "pop" in result_df.columns

    def test_api_error_continue_on_error_false(self):
        options = EnrichmentOptions(rate_limit=0, max_retries=0, request_timeout=5,
                                    continue_on_error=False, parallel_workers=1)
        ex = make_executor(options=options)
        df = pd.DataFrame({"paese": ["Italy"]})
        with patch("requests.get", return_value=mock_response({}, status_code=404)):
            result_df, result = ex.execute(df)
        assert result.success is False

    def test_api_error_continue_on_error_true(self):
        options = EnrichmentOptions(rate_limit=0, max_retries=0, request_timeout=5,
                                    continue_on_error=True, parallel_workers=1)
        ex = make_executor(options=options)
        df = pd.DataFrame({"paese": ["Italy", "INVALID"]})
        call_count = [0]
        def mock_get(*args, **kwargs):
            country = kwargs.get("params", {}).get("country", "")
            call_count[0] += 1
            if country == "INVALID":
                return mock_response({}, status_code=404)
            return mock_response({"capital": "Rome", "population": 60000000})
        with patch("requests.get", side_effect=mock_get):
            result_df, result = ex.execute(df)
        assert result.success is True
        assert result.stats.success >= 1

    def test_null_params_skippati(self):
        options = EnrichmentOptions(rate_limit=0, max_retries=0, request_timeout=5,
                                    continue_on_error=True, parallel_workers=1, skip_null_params=True)
        ex = make_executor(options=options)
        df = pd.DataFrame({"paese": ["Italy", None, "France"]})
        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            return mock_response({"capital": "Capital", "population": 1000000})
        with patch("requests.get", side_effect=mock_get):
            result_df, result = ex.execute(df)
        assert call_count[0] <= 2


class TestExecuteDeduplicazione:

    def test_righe_duplicate_una_sola_chiamata(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy","Italy","France","France","Italy",
                                      "Germany","Germany","Italy","France","Germany"]})
        call_count = [0]
        def mock_get(*args, **kwargs):
            call_count[0] += 1
            return mock_response({"capital": "Capital", "population": 1000000})
        with patch("requests.get", side_effect=mock_get):
            result_df, result = ex.execute(df)
        assert call_count[0] <= 3
        assert len(result_df) == 10


class TestExtractOutput:

    def test_path_semplice(self):
        ex = make_executor(output_mapping=[{"path": "capital", "column": "capitale"}])
        data = {"capital": "Rome", "population": 60000000}
        result = ex._extract_output(data)
        assert len(result) == 1
        assert result[0]["capitale"] == "Rome"

    def test_path_nested(self):
        ex = make_executor(output_mapping=[{"path": "name.common", "column": "nome"}])
        data = {"name": {"common": "Italy"}}
        result = ex._extract_output(data)
        assert result[0]["nome"] == "Italy"

    def test_path_inesistente_none(self):
        ex = make_executor(output_mapping=[{"path": "CAMPO_XYZ", "column": "col"}])
        data = {"capital": "Rome"}
        result = ex._extract_output(data)
        # Path inesistente → None o campo assente
        if result:
            val = result[0].get("col")
            assert val is None or "col" not in result[0]

    def test_lista_di_oggetti(self):
        ex = make_executor(output_mapping=[{"path": "name", "column": "nome"}])
        data = [{"name": "Italy"}, {"name": "France"}]
        result = ex._extract_output(data)
        assert len(result) == 2

    def test_output_mapping_vuoto_auto_flatten(self):
        ex = make_executor(output_mapping=[])
        data = {"capital": "Rome", "population": 60000000}
        result = ex._extract_output(data)
        assert len(result) >= 1
