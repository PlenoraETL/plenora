"""
Contract test per il clamp del header `Retry-After` (HTTP 429).

Bug originale (plenora-bug-etl-sources P1):
  `enrichment_retry_http.py:128` faceva
      `retry_after = int(response.headers.get("Retry-After", 60))`
  e poi `time.sleep(retry_after)` senza cap. Un server che risponde
  `Retry-After: 86400` (24h) bloccava un worker Celery per l'intera
  giornata, esaurendo capacita' del pool.

Post-fix: helper modulo `_clamp_retry_after` (funzione pura, Go-friendly),
configurato via `EnrichmentOptions.max_retry_after_seconds` (default 300s).
"""

from __future__ import annotations

import pytest

from app.core.arricchisci.retry_policy import (
    RETRY_AFTER_FALLBACK_SECONDS,
    clamp_retry_after,
)
from app.core.arricchisci.models import EnrichmentOptions


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.security,
    pytest.mark.regression,
]


class TestClampRetryAfter:
    """Table-driven: ogni riga e' una coppia (input header, output atteso)."""

    @pytest.mark.parametrize("raw_header,max_seconds,expected", [
        # Server hostile: 24 ore → clampato a max_seconds
        ("86400", 300, 300),
        # Server hostile: 1 ora → clampato
        ("3600", 300, 300),
        # Valore legittimo entro il cap → passa
        ("45", 300, 45),
        ("0", 300, 0),
        # Valore al limite del cap → passa
        ("300", 300, 300),
        # Header mancante → fallback (clampato anche lui se < max)
        (None, 300, RETRY_AFTER_FALLBACK_SECONDS),
        (None, 30, 30),  # fallback 60 ma cap 30 → 30
        # Whitespace tollerato
        (" 42 ", 300, 42),
        # Negativo (server bug) → 0
        ("-10", 300, 0),
    ])
    def test_clamp_returns_expected(self, raw_header, max_seconds, expected):
        assert clamp_retry_after(raw_header, max_seconds=max_seconds) == expected

    @pytest.mark.parametrize("malformed_header", [
        "",                # vuoto
        "abc",             # non-numerico
        "Wed, 21 Oct 2015 07:28:00 GMT",  # HTTP-date (non supportato, degrada a fallback)
        "1.5",             # float (non int)
        "not-a-number",
    ])
    def test_malformed_header_falls_back_to_default(self, malformed_header):
        # Fallback `_RETRY_AFTER_FALLBACK_SECONDS` clampato al max
        result = clamp_retry_after(malformed_header, max_seconds=300)
        assert result == RETRY_AFTER_FALLBACK_SECONDS

    def test_negative_max_seconds_treated_as_zero(self):
        """Difesa contro misconfig: max_retry_after_seconds=-1 → 0 sleep,
        non bypass del cap.
        """
        assert clamp_retry_after("42", max_seconds=-5) == 0

    def test_returns_int(self):
        """time.sleep richiede numero, ritornare int e' contratto."""
        result = clamp_retry_after("42", max_seconds=300)
        assert isinstance(result, int)


class TestEnrichmentOptionsDefault:
    """Lock del default `max_retry_after_seconds=300`. Cambiare il default
    senza aggiornare questo test e' un segnale di review obbligata.
    """

    def test_default_is_300_seconds(self):
        opts = EnrichmentOptions()
        assert opts.max_retry_after_seconds == 300

    def test_field_is_overridable(self):
        opts = EnrichmentOptions(max_retry_after_seconds=60)
        assert opts.max_retry_after_seconds == 60
