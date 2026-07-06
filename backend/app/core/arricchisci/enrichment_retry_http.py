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

from app.core.arricchisci.async_poller import (
    AsyncPollError,
    AsyncPoller,
    validate_async_config,
)
from app.core.arricchisci.http_executor import (
    RequestSpec,
    allow_redirects_from_config,
    body_type_from_config,
    execute_http_request,
    raw_body_from_config,
    validate_request_spec_config,
)
from app.core.arricchisci.retry_policy import clamp_retry_after
from app.core.arricchisci.success_evaluator import (
    evaluate_success,
    success_config_from_api_config,
)
from app.core.security.outbound_url import validate_external_url
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

        call_url = self.url
        call_params_base = dict(params)
        for pname in re.findall(r'\{(\w+)\}', call_url):
            if pname in call_params_base:
                encoded = _quote(str(call_params_base.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                call_url = call_url.replace(f'{{{pname}}}', encoded)

        try:
            validate_external_url(call_url)
        except ValueError as e:
            return None, RowError(
                row_index=row_index,
                status=RowStatus.ERROR,
                error_message=str(e),
                error_type="request_error",
                retry_count=0,
            )

        ssl_verify = resolve_tls_verify(
            not getattr(self.options, 'disable_ssl_verify', False),
            context="API enricher per-row",
        )
        connector_config = getattr(self, "api_config", {}) or {}
        request_cfg = connector_config.get("request", {}) or {}
        options_cfg = connector_config.get("options", {}) or {}
        async_config = connector_config.get("async", {}) or {}
        success_config = success_config_from_api_config(connector_config)
        body_type = body_type_from_config(request_cfg)

        try:
            validate_request_spec_config(self.method, body_type)
            validate_async_config(async_config)
        except ValueError as e:
            return None, RowError(
                row_index=row_index,
                status=RowStatus.ERROR,
                error_message=str(e),
                error_type="request_config_error",
                retry_count=0,
            )

        for attempt in range(self.options.max_retries + 1):
            try:
                call_params = dict(call_params_base)

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

                request_spec = RequestSpec(
                    url=call_url,
                    method=self.method,
                    params=call_params,
                    json_body=call_params,
                    raw_body=raw_body_from_config(request_cfg),
                    body_type=body_type,
                    headers=self._headers,
                    timeout=(self.options.connect_timeout, self.options.request_timeout),
                    verify=ssl_verify,
                    allow_redirects=allow_redirects_from_config(
                        options_cfg,
                        request_cfg,
                    ),
                    validate_url=False,
                    context="API enricher per-row",
                )

                data = None
                async_error_message = None
                if async_config.get("enabled"):
                    try:
                        poll_result = AsyncPoller(
                            execute=execute_http_request,
                            before_poll_request=getattr(
                                getattr(self, "_rate_limiter", None),
                                "acquire",
                                None,
                            ),
                        ).run(
                            request_spec,
                            async_config,
                            base_url=call_url,
                        )
                        response = poll_result.response
                        data = poll_result.data
                    except AsyncPollError as e:
                        if e.response is None:
                            raise
                        if e.stage != "submit":
                            return None, RowError(
                                row_index=row_index,
                                status=RowStatus.ERROR,
                                error_message=str(e),
                                error_type=(
                                    f"async_{e.stage}_error"
                                    if e.stage
                                    else "async_error"
                                ),
                                http_status=e.response.status_code,
                                retry_count=attempt,
                            )
                        response = e.response
                        async_error_message = str(e)
                    except TimeoutError as e:
                        return None, RowError(
                            row_index=row_index,
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="timeout",
                            retry_count=attempt,
                        )
                    except ValueError as e:
                        return None, RowError(
                            row_index=row_index,
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="async_error",
                            retry_count=attempt,
                        )
                else:
                    response = execute_http_request(request_spec)

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
                    if data is None:
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
                    evaluation = evaluate_success(data, success_config)
                    if not evaluation.ok:
                        return None, RowError(
                            row_index=row_index,
                            status=RowStatus.ERROR,
                            error_message=(
                                evaluation.error_message
                                or "Errore applicativo nella risposta API"
                            ),
                            error_type="application_error",
                            http_status=200,
                            retry_count=attempt,
                        )
                    return self._extract_output(data), None

                # Rate limited - rispetta Retry-After
                if response.status_code == 429:
                    with self._stats_lock:
                        self._rate_limit_hits_observed += 1
                    if self.options.respect_retry_after:
                        retry_after = clamp_retry_after(
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
                    error_message=(
                        async_error_message
                        or f"HTTP {response.status_code}: {response.text[:200]}"
                    ),
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

            except requests.RequestException as e:
                last_error = RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="request_error",
                    retry_count=attempt
                )
            except (ValueError, KeyError, TypeError) as e:
                return None, RowError(
                    row_index=row_index,
                    status=RowStatus.ERROR,
                    error_message=str(e),
                    error_type="response_processing_error",
                    retry_count=attempt,
                )

            # Exponential backoff tra retry
            if attempt < self.options.max_retries:
                wait_time = (self.options.retry_backoff_factor ** attempt)
                time.sleep(wait_time)

        # Max retry raggiunto
        if last_error:
            last_error.status = RowStatus.RETRY_EXHAUSTED
        return None, last_error
