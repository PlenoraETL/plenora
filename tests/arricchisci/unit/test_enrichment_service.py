"""
tests/arricchisci/unit/test_enrichment_service.py
==================================================
Unit test sui metodi puri di EnrichmentExecutor e RateLimiter.

NON richiede backend, API o Celery.

Copre:
  - RateLimiter: rate=0 (passthrough), rate>0 (throttle)
  - EnrichmentExecutor._build_params: static, mapping, api_key query, null, numpy
  - EnrichmentExecutor._has_null_params: None, stringa vuota, "None", api_key esclusa
  - EnrichmentExecutor._make_cache_key: determinismo, ordine-indipendente
  - EnrichmentExecutor._flatten_dict: scalari, float→int, dict→json, list→json
  - EnrichmentExecutor._infer_scalar_type: tutti i tipi
  - EnrichmentExecutor._group_by_params: dedup, gruppi multipli
  - EnrichmentExecutor._count_unique_combinations
  - EnrichmentExecutor.validate_input: colonna mancante, null rows, tutto ok
"""

import pytest
import pandas as pd
import numpy as np
import json
import time

pytestmark = pytest.mark.unit

from app.core.arricchisci.enrichment_service import (
    EnrichmentExecutor,
    EnrichmentOptions,
    RateLimiter,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_executor(
    url="http://example.com/api",
    method="GET",
    output_mapping=None,
    param_mapping=None,
    static_params=None,
    auth=None,
    options=None,
):
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": url, "method": method},
        "auth": auth or {"type": "none"},
        "output_mapping": output_mapping or [],
        "static_parameters": static_params or {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping=param_mapping or {},
        options=options or EnrichmentOptions(),
    )


# =============================================================================
# RateLimiter
# =============================================================================

class TestRateLimiter:

    def test_rate_zero_no_sleep(self):
        """rate=0 → nessuna attesa."""
        rl = RateLimiter(rate=0)
        start = time.time()
        rl.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.1

    def test_rate_positivo_esiste(self):
        """rate>0 → intervallo calcolato correttamente."""
        rl = RateLimiter(rate=10.0)
        assert abs(rl.interval - 0.1) < 1e-9

    def test_rate_alto_no_wait(self):
        """rate molto alto → prima chiamata non aspetta."""
        rl = RateLimiter(rate=1000.0)
        start = time.time()
        rl.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.05


# =============================================================================
# _build_params
# =============================================================================

class TestBuildParams:

    def test_mapping_semplice(self):
        ex = make_executor(param_mapping={"country": "paese"})
        row = pd.Series({"paese": "Italy", "codice": "IT"})
        params = ex._build_params(row)
        assert params["country"] == "Italy"

    def test_static_params_inclusi(self):
        ex = make_executor(
            param_mapping={"country": "paese"},
            static_params={"lang": "it", "format": "json"}
        )
        row = pd.Series({"paese": "Italy"})
        params = ex._build_params(row)
        assert params["lang"] == "it"
        assert params["format"] == "json"
        assert params["country"] == "Italy"

    def test_dynamic_sovrascrive_static(self):
        """Mutation target: param_mapping sovrascrive static_parameters."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            static_params={"country": "default_country"}
        )
        row = pd.Series({"paese": "Italy"})
        params = ex._build_params(row)
        assert params["country"] == "Italy"

    def test_valore_null_diventa_none(self):
        ex = make_executor(param_mapping={"country": "paese"})
        row = pd.Series({"paese": None})
        params = ex._build_params(row)
        assert params["country"] is None

    def test_valore_nan_diventa_none(self):
        ex = make_executor(param_mapping={"country": "paese"})
        row = pd.Series({"paese": float("nan")})
        params = ex._build_params(row)
        assert params["country"] is None

    def test_int_preservato(self):
        """Mutation target: int non convertito a stringa."""
        ex = make_executor(param_mapping={"id": "id_val"})
        row = pd.Series({"id_val": 42})
        params = ex._build_params(row)
        assert params["id"] == 42
        assert isinstance(params["id"], int)

    def test_float_preservato(self):
        ex = make_executor(param_mapping={"lat": "latitudine"})
        row = pd.Series({"latitudine": 41.9})
        params = ex._build_params(row)
        assert abs(params["lat"] - 41.9) < 1e-9

    def test_numpy_int64_convertito(self):
        ex = make_executor(param_mapping={"id": "id_val"})
        row = pd.Series({"id_val": np.int64(100)})
        params = ex._build_params(row)
        assert params["id"] == 100
        assert isinstance(params["id"], int)

    def test_numpy_float64_convertito(self):
        ex = make_executor(param_mapping={"lat": "lat_val"})
        row = pd.Series({"lat_val": np.float64(41.9)})
        params = ex._build_params(row)
        assert isinstance(params["lat"], float)

    def test_api_key_query_aggiunta(self):
        """API key in query → aggiunta ai params."""
        ex = make_executor(
            param_mapping={"country": "paese"},
            auth={"type": "api_key", "location": "query",
                  "key_name": "apiKey", "key_value": "my-key"}
        )
        row = pd.Series({"paese": "Italy"})
        params = ex._build_params(row)
        assert params["apiKey"] == "my-key"

    def test_colonna_inesistente_skippata(self):
        ex = make_executor(param_mapping={"country": "COLONNA_XYZ"})
        row = pd.Series({"paese": "Italy"})
        params = ex._build_params(row)
        assert "country" not in params


# =============================================================================
# _has_null_params
# =============================================================================

class TestHasNullParams:

    def test_nessun_null(self):
        ex = make_executor()
        assert ex._has_null_params({"country": "Italy", "lang": "it"}) is False

    def test_valore_none(self):
        ex = make_executor()
        assert ex._has_null_params({"country": None}) is True

    def test_stringa_vuota(self):
        ex = make_executor()
        assert ex._has_null_params({"country": ""}) is True

    def test_stringa_none(self):
        ex = make_executor()
        assert ex._has_null_params({"country": "None"}) is True

    def test_api_key_esclusa(self):
        """Mutation target: API key non deve essere considerata null."""
        ex = make_executor(
            auth={"type": "api_key", "key_name": "apiKey", "key_value": "key"}
        )
        params = {"apiKey": "my-key", "country": "Italy"}
        assert ex._has_null_params(params) is False

    def test_api_key_null_non_conta(self):
        ex = make_executor(
            auth={"type": "api_key", "key_name": "apiKey"}
        )
        params = {"apiKey": None, "country": "Italy"}
        assert ex._has_null_params(params) is False


# =============================================================================
# _make_cache_key
# =============================================================================

class TestMakeCacheKey:

    def test_deterministico(self):
        """Stessi params → stessa chiave."""
        k1 = EnrichmentExecutor._make_cache_key({"a": "1", "b": "2"})
        k2 = EnrichmentExecutor._make_cache_key({"a": "1", "b": "2"})
        assert k1 == k2

    def test_ordine_indipendente(self):
        """Mutation target: ordine chiavi non conta."""
        k1 = EnrichmentExecutor._make_cache_key({"a": "1", "b": "2"})
        k2 = EnrichmentExecutor._make_cache_key({"b": "2", "a": "1"})
        assert k1 == k2

    def test_params_diversi_chiavi_diverse(self):
        k1 = EnrichmentExecutor._make_cache_key({"country": "Italy"})
        k2 = EnrichmentExecutor._make_cache_key({"country": "France"})
        assert k1 != k2

    def test_lunghezza_md5(self):
        k = EnrichmentExecutor._make_cache_key({"x": "y"})
        assert len(k) == 32

    def test_params_vuoti(self):
        k = EnrichmentExecutor._make_cache_key({})
        assert len(k) == 32


# =============================================================================
# _flatten_dict
# =============================================================================

class TestFlattenDict:

    def test_scalari_invariati(self):
        ex = make_executor()
        result = ex._flatten_dict({"name": "Italy", "pop": 60000000})
        assert result["name"] == "Italy"
        assert result["pop"] == 60000000

    def test_float_intero_convertito(self):
        """Mutation target: 114.0 → 114 (int)."""
        ex = make_executor()
        result = ex._flatten_dict({"val": 114367654.0})
        assert result["val"] == 114367654
        assert isinstance(result["val"], int)

    def test_float_reale_invariato(self):
        ex = make_executor()
        result = ex._flatten_dict({"val": 3.14})
        assert abs(result["val"] - 3.14) < 1e-9

    def test_dict_a_json_string(self):
        ex = make_executor()
        result = ex._flatten_dict({"meta": {"key": "val"}})
        parsed = json.loads(result["meta"])
        assert parsed["key"] == "val"

    def test_list_a_json_string(self):
        ex = make_executor()
        result = ex._flatten_dict({"tags": ["a", "b", "c"]})
        parsed = json.loads(result["tags"])
        assert parsed == ["a", "b", "c"]


# =============================================================================
# _infer_scalar_type
# =============================================================================

class TestInferScalarType:

    def test_bool(self):
        ex = make_executor()
        assert ex._infer_scalar_type(True) == "bool"
        assert ex._infer_scalar_type(False) == "bool"

    def test_int(self):
        ex = make_executor()
        assert ex._infer_scalar_type(42) == "int"

    def test_float(self):
        ex = make_executor()
        assert ex._infer_scalar_type(3.14) == "real"

    def test_string(self):
        ex = make_executor()
        assert ex._infer_scalar_type("hello") == "str"

    def test_geojson_point(self):
        ex = make_executor()
        result = ex._infer_scalar_type({"type": "Point", "coordinates": [12.4, 41.9]})
        assert "geometry" in result or result == "geometry:point"

    def test_dict_non_geometry(self):
        ex = make_executor()
        assert ex._infer_scalar_type({"key": "val"}) == "str"


# =============================================================================
# _group_by_params e _count_unique_combinations
# =============================================================================

class TestGroupByParams:

    def test_righe_uguali_stesso_gruppo(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": ["Italy", "Italy", "France"]})
        groups = ex._group_by_params(df)
        assert len(groups) == 2
        # Il gruppo Italy ha 2 indici
        italy_group = [v for v in groups.values() if len(v) == 2]
        assert len(italy_group) == 1

    def test_tutte_righe_diverse(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": ["Italy", "France", "Germany"]})
        groups = ex._group_by_params(df)
        assert len(groups) == 3

    def test_count_unique(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": ["Italy", "Italy", "France", "Italy"]})
        assert ex._count_unique_combinations(df) == 2


# =============================================================================
# validate_input
# =============================================================================

class TestValidateInput:

    def test_colonna_mancante_invalid(self):
        ex = make_executor(param_mapping={"country": "COLONNA_XYZ"})
        df = pd.DataFrame({"paese": ["Italy"]})
        result = ex.validate_input(df)
        assert result.valid is False
        assert "COLONNA_XYZ" in result.missing_columns

    def test_tutto_ok(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": ["Italy", "France", "Germany"]})
        result = ex.validate_input(df)
        assert result.valid is True
        assert result.valid_rows == 3
        assert result.invalid_rows == 0

    def test_null_rows_rilevate(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": ["Italy", None, "France", ""]})
        result = ex.validate_input(df)
        assert result.valid is True  # valid ma con warnings
        assert result.invalid_rows == 2
        assert len(result.null_param_rows) == 2

    def test_df_vuoto_valid(self):
        ex = make_executor(param_mapping={"country": "paese"})
        df = pd.DataFrame({"paese": []})
        result = ex.validate_input(df)
        assert result.valid is True
        assert result.total_rows == 0

    def test_nessun_mapping_sempre_valid(self):
        ex = make_executor(param_mapping={})
        df = pd.DataFrame({"paese": ["Italy", None]})
        result = ex.validate_input(df)
        assert result.valid is True
        assert result.invalid_rows == 0
