"""
Mixin RETRY_HTTP — chiamata API per-row con retry chain + 429 +

exponential backoff per `EnrichmentExecutor`: path sequenziale
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import requests

from app.shared.api_utils import (
    get_shared_session,
    sanitize_for_log,
    sanitize_url_for_log,
)
from app.utils.logger import get_logger

from app.core.arricchisci.models import RowError, RowStatus

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`). Singleton stdlib per
# nome → record indistinguibili dai consumer che attaccano handler /
# filtrano per nome canonico.
logger = get_logger("app.core.arricchisci.enrichment_service")


class EnrichmentRetryHttpMixin:
    """Mixin per `EnrichmentExecutor`: HTTP per-row retry chain con
    429+Retry-After + exponential backoff + path params URL substitution
    + ArcGIS token injection + DEBUG anti-flood log.
    """

    def _call_api_with_retry(
        self,
        params: Dict,
        row_index: int
    ) -> Tuple[Optional[Dict], Optional[RowError]]:
        """
        Chiama API con retry e exponential backoff.

        Returns:
            Tuple (risultato, errore) - uno dei due è None
        """
        last_error = None

        for attempt in range(self.options.max_retries + 1):
            try:
                start_time = time.time()

                ssl_verify = not getattr(self.options, 'disable_ssl_verify', False)

                # Sostituisci path params nell'URL ({ip}, {coordinates}, ecc.)
                import re
                from urllib.parse import quote as _quote
                call_url = self.url
                call_params = dict(params)
                for pname in re.findall(r'\{(\w+)\}', call_url):
                    if pname in call_params:
                        encoded = _quote(str(call_params.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                        call_url = call_url.replace(f'{{{pname}}}', encoded)

                # Iniezione token ArcGIS in query string (auth_type=arcgis_token)
                # Il token va come ?token=xxx, NON come Authorization header.
                # La cache su (token_url, username) gestisce l'auto-renewal.
                if self.auth.get("type") == "arcgis_token":
                    from app.shared.api_utils import get_arcgis_token
                    arcgis_tok = get_arcgis_token(self.auth)
                    if arcgis_tok:
                        call_params["token"] = arcgis_tok

                # DEBUG level: per un job di 300 righe × 11 nodi questo produce
                # ~3300 righe di log. A INFO satura journald e rallenta ~400ms per run.
                # Chi deve fare troubleshooting abilita DEBUG sul logger.
                logger.debug(
                    f"API call: {self.method} {sanitize_url_for_log(call_url)} | "
                    f"params={sanitize_for_log(call_params, self.auth)}"
                )

                _session = get_shared_session()
                if self.method == "GET":
                    response = _session.get(
                        call_url,
                        params=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )
                else:
                    response = _session.post(
                        call_url,
                        json=call_params,
                        headers=self._headers,
                        timeout=(self.options.connect_timeout, self.options.request_timeout),
                        verify=ssl_verify
                    )

                elapsed_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
                    # deque con maxlen=1000: auto-trunca i piu' vecchi
                    self._response_times.append(elapsed_ms)


                # Successo
                if response.status_code == 200:
                    data = response.json()
                    return self._extract_output(data), None

                # Rate limited - rispetta Retry-After
                if response.status_code == 429:
                    if self.options.respect_retry_after:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        time.sleep(retry_after)

                    with self._stats_lock:
                        # Conta rate limit hits se presente nelle stats
                        pass

                    last_error = RowError(
                        row_index=row_index,
                        status=RowStatus.ERROR,
                        error_message=f"Rate limited (429)",
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
