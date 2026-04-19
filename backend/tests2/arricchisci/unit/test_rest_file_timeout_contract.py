"""
test_rest_file_timeout_contract.py

Verifica che `_parse_rest_file_config` NON usi mai il fallback legacy
`options.timeout` (pensato per connettori generici: default 30s) per
rest_file. Il timeout canonico è `request.timeout`; se mancante, il
default è 600s (10min), adeguato a processi server che possono
richiedere minuti (es. imdf.plenora.it/api/process).
"""
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def connector():
    from app.core.arricchisci.api_connector import ApiConnectionService
    svc = ApiConnectionService.__new__(ApiConnectionService)
    return svc


def _base_cfg(**overrides):
    cfg = {
        "endpoint": {"url": "https://imdf.plenora.it/api/process", "method": "POST"},
        "auth": {},
        "request": {
            "body_type": "multipart",
            "file_fields": [],
            "static_fields": [],
        },
        "response": {"format": "binary_file", "output_extension": ".zip"},
    }
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


def test_request_timeout_explicit_wins(connector):
    cfg = _base_cfg()
    cfg["request"]["timeout"] = 1200
    parsed, err = connector._parse_rest_file_config(cfg)
    assert err is None
    assert parsed["timeout"] == 1200


def test_request_timeout_missing_uses_600_not_options_timeout(connector):
    """Bug regression: quando request.timeout è assente, il vecchio
    codice cadeva sul fallback options.timeout (default 30s per i
    connettori generici), causando 'Timeout dopo 30s' su chiamate a
    servizi lenti come imdf.plenora.it/api/process. Il fallback deve
    essere 600s, NON options.timeout."""
    cfg = _base_cfg()
    cfg["request"].pop("timeout", None)
    cfg["options"] = {"timeout": 30}  # legacy/shared-field valore alto
    parsed, err = connector._parse_rest_file_config(cfg)
    assert err is None
    assert parsed["timeout"] == 600, (
        f"Atteso fallback 600s, trovato {parsed['timeout']}s "
        f"(probabile uso del fallback legacy options.timeout=30)"
    )


def test_request_timeout_zero_falls_back_to_default(connector):
    cfg = _base_cfg()
    cfg["request"]["timeout"] = 0  # disabilitato/invalido
    parsed, err = connector._parse_rest_file_config(cfg)
    assert err is None
    assert parsed["timeout"] == 600
