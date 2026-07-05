"""
Mixin RETRY_HTTP — chiamata API per-row con retry chain + 429 +

exponential backoff per `EnrichmentExecutor`: path sequenziale
"""

from __future__ import annotations

from urllib.parse import quote as _quote
import re

import time
from typing import Optional, Tuple

import requests

from app.core.arricchisci.http_executor import RequestSpec, execute_http_request
from app.core.arricchisci.types import JsonObject
from app.observability.log_sanitization import (
    sanitize_for_log,
    sanitize_url_for_log,
)
from app.shared.security.tls_policy import resolve_tls_verify
from app.shared.connections.oauth import get_arcgis_token
from app.utils.logger import get_logger

from app.core.arricchisci.models import RowError, RowStatus

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`). Singleton stdlib per
# nome → record indistinguibili dai consumer che attaccano handler /
# filtrano per nome canonico
logger = get_logger("app.core.arricchisci.enrichment_service")


_RETRY_AFTER_FALLBACK_SECONDS = 60
"""Default usato quando il server non manda Retry-After con 429."""


def _clamp_retry_after(raw_header: Optional[str], *, max_seconds: int) -> int:
    """Parsa il valore dell'header Retry-After (HTTP 429) e lo clampa.

    Funzione pura (no I/O, no stato) — testabile in isolamento e mappabile
    direttamente in Go come `func clampRetryAfter(raw string, max int) int`.

    Threat: un server malevolo o malfunzionante puo' rispondere
    `Retry-After: 86400` (24 ore) e parcheggiare un worker Celery per
    l'intera giornata, esaurendo la capacita' del pool. Il clamp limita
    il blast radius a `max_seconds`.

    Args:
        raw_header: valore raw dell'header. Se None/vuoto/non-int, ritorna
            il fallback `_RETRY_AFTER_FALLBACK_SECONDS` clampato.
            NOTA: RFC 7231 ammette anche date-string HTTP-date come valore;
            qui supportiamo solo il formato delta-seconds (interi), che e'
            quello usato dal 99% dei servizi REST moderni.
        max_seconds: cap superiore (tipicamente
            `EnrichmentOptions.max_retry_after_seconds`).

    Returns:
        Numero intero di secondi nell'intervallo [0, max_seconds].
    """
    if max_seconds < 0:
        max_seconds = 0
    if raw_header is None:
        candidate = _RETRY_AFTER_FALLBACK_SECONDS
    else:
        try:
            candidate = int(str(raw_header).strip())
        except (TypeError, ValueError):
            # Formato HTTP-date o malformato: degrada al fallback. Non
            # spendiamo cicli a parsare le date (raro nei servizi REST,
            # e il caller di fallback e' comunque ragionevole).
            candidate = _RETRY_AFTER_FALLBACK_SECONDS
    if candidate < 0:
        candidate = 0
    return min(candidate, max_seconds)


class EnrichmentRetryHttpMixin:
    """Mixin per `EnrichmentExecutor`: HTTP per-row retry chain con
    429+Retry-After + exponential backoff + path params URL substitution
    + ArcGIS token injection + DEBUG anti-flood log.
    """

    def _call_api_with_retry(
        self,
        params: JsonObject,
        row_index: int
    ) -> Tuple[Optional[JsonObject], Optional[RowError]]:
        """
        Chiama API con retry e exponential backoff.

        Returns:
            Tuple (risultato, errore) - uno dei due è None
        """
        last_error = None

        for attempt in range(self.options.max_retries + 1):
            try:
                # disable_ssl_verify e' user-config (EnrichmentOptions); il
                # gate `resolve_tls_verify` lo forza a True in prod/staging
                # (signal MITM-prevention) e lo permette solo con opt-in env
                # esplicito (`PLENORA_ALLOW_TLS_BYPASS=1`) o ENVIRONMENT=dev.
                ssl_verify = resolve_tls_verify(
                    not getattr(self.options, 'disable_ssl_verify', False),
                    context="API enricher per-row",
                )

                # Sostituisci path params nell'URL ({ip}, {coordinates}, ecc
                call_url = self.url
                call_params = dict(params)
                for pname in re.findall(r'\{(\w+)\}', call_url):
                    if pname in call_params:
                        encoded = _quote(str(call_params.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                        call_url = call_url.replace(f'{{{pname}}}', encoded)

                # Iniezione token ArcGIS in query string (auth_type=arcgis_token
                # Il token va come ?token=xxx, NON come Authorization header
                # La cache su (token_url, username) gestisce l'auto-renewal
                if self.auth.get("type") == "arcgis_token":
                    arcgis_tok = get_arcgis_token(self.auth)
                    if arcgis_tok:
                        call_params["token"] = arcgis_tok

                # DEBUG level: per un job di 300 righe × 11 nodi questo produce
                # ~3300 righe di log. A INFO satura journald e rallenta ~400ms per run
                # Chi deve fare troubleshooting abilita DEBUG sul logger
                logger.debug(
                    f"API call: {self.method} {sanitize_url_for_log(call_url)} | "
                    f"params={sanitize_for_log(call_params, self.auth)}"
                )

                response = execute_http_request(RequestSpec(
                    url=call_url,
                    method=self.method,
                    params=call_params,
                    json_body=call_params,
                    headers=self._headers,
                    timeout=(self.options.connect_timeout, self.options.request_timeout),
                    verify=ssl_verify,
                    allow_redirects=False,
                    context="API enricher per-row",
                ))

                elapsed_ms = response.elapsed_ms
                with self._stats_lock:
                    # deque con maxlen=1000: auto-trunca i piu' vecchi
                    self._response_times.append(elapsed_ms)


                # Successo
                if response.status_code == 200:
                    # Body 200 non JSON (es. captive portal/proxy) finisce in
                    # RowError sulla singola riga, non in crash del batch.
                    # `error_type="invalid_json_response"` e il batch
                    # prosegue sulle altre.
                    try:
                        data = response.json()
                    except (ValueError, requests.exceptions.JSONDecodeError) as e:
                        return None, RowError(
                            row_index=row_index,
                            status=RowStatus.ERROR,
                            error_message=(
                                f"Risposta HTTP 200 non e' JSON valido: "
                                f"{type(e).__name__}"
                            ),
                            error_type="invalid_json_response",
                            http_status=200,
                            retry_count=attempt,
                        )
                    return self._extract_output(data), None

                # Rate limited - rispetta Retry-After
                if response.status_code == 429:
                    with self._stats_lock:
                        self._rate_limit_hits_observed += 1
                    if self.options.respect_retry_after:
                        retry_after = _clamp_retry_after(
                            response.headers.get("Retry-After"),
                            max_seconds=self.options.max_retry_after_seconds,
                        )
                        time.sleep(retry_after)

                    last_error = RowError(
                        row_index=row_index,
                        status=RowStatus.ERROR,
                        error_message="Rate limited (429)",
                        error_type="rate_limit",
                        http_status=429,
                        retry_count=attempt
                    )
                    continue

                # Altri errori retriable
                if response.status_code in self.options.retry_on_status:
                    last_error = RowError(
                        row_index=row_index,
                        status=RowStatus.ERROR,
                        error_message=f"HTTP {response.status_code}",
                        error_type=f"http_{response.status_code}",
                        http_status=response.status_code,
                        retry_count=attempt
                    )

                    # Exponential backoff
                    if attempt < self.options.max_retries:
                        wait_time = (self.options.retry_backoff_factor ** attempt)
                        time.sleep(wait_time)
                    continue

                # Errore non retriable
                return None, RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    error_type=f"http_{response.status_code}",
                    http_status=response.status_code,
                    retry_count=attempt
                )

            except requests.Timeout:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=f"Timeout dopo {self.options.request_timeout}s",
                    error_type="timeout",
                    retry_count=attempt
                )

            except ValueError as e:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="request_error",
                    retry_count=attempt
                )

            except requests.RequestException as e:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="request_error",
                    retry_count=attempt
                )

            # Exponential backoff tra retry
            if attempt < self.options.max_retries:
                wait_time = (self.options.retry_backoff_factor ** attempt)
                time.sleep(wait_time)

        # Max retry raggiunto
        if last_error:
            last_error.status = RowStatus.RETRY_EXHAUSTED
        return None, last_error
