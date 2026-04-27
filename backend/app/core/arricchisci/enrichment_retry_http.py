"""
Mixin RETRY_HTTP — chiamata API per-row con retry chain + 429 +
exponential backoff per `EnrichmentExecutor`: path sequenziale
e parallelo (sync e async-via-to_thread).

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service.EnrichmentExecutor`
come parte di Phase 5 Batch B3-A.6 (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; mixin pattern
ratificato in B2.1+...+B2.6 + B3-A.2/A.3/A.4/A.5).

Razionale del raggruppamento: 1 metodo che incapsula il "retry HTTP
per-row" pattern, alternativo al batch retry (`_call_batch_api` in
`EnrichmentBatchModeMixin` B3-A.4). Body distinto bit-per-bit
preservato:

  - `_call_api_with_retry`     — chiamata API per riga singola con
                                  retry chain, 429+Retry-After
                                  handling, exponential backoff.
                                  Include path params URL substitution
                                  (`{name}` regex), ArcGIS token query
                                  injection per `auth.type=arcgis_token`,
                                  DEBUG anti-flood log per job grandi.

Scope decisione: SOLO `_call_api_with_retry`
=============================================
I 3 auth helper (`_prepare_headers`, `_get_oauth2_token`,
`_get_oauth2_client_credentials_token`) restano in `EnrichmentExecutor`
core. Verificato pre-extraction:
  - `_prepare_headers` chiamato da `__init__:226` per pre-build
    `self._headers`, NON dal retry path (che usa `self._headers`
    gia' pronto).
  - `_get_oauth2_token` / `_get_oauth2_client_credentials_token`
    sono 3-line stub di delega a `app.shared.api_utils`, NON
    chiamati direttamente da `_call_api_with_retry` (che gestisce
    inline `auth.type=arcgis_token` via `get_arcgis_token` lazy).

Cluster auth utility (3 stub di delega) considerato a se' come
candidato future batch o resta in core (decisione architetturale).

Differenza vs batch retry (B3-A.4 `_call_batch_api`)
=====================================================
Pattern di retry simile (max_retries + 429 + retry_on_status +
exponential backoff) ma body distinto bit-per-bit:

| Aspetto                | `_call_api_with_retry` (B3-A.6) | `_call_batch_api` (B3-A.4) |
|------------------------|----------------------------------|-----------------------------|
| Index reporting        | `row_index`                      | `batch_indices[0]`          |
| Path params URL subst  | si (regex `{name}`)              | no                          |
| ArcGIS token injection | si (`call_params['token']=tok`)  | no                          |
| DEBUG anti-flood log   | si                               | no                          |
| Output extraction      | `self._extract_output`           | `self._extract_batch_output`|
| Payload                | params direct                    | `_build_batch_payload`      |

Consolidamento futuro candidato (Phase 0/5 nuova): comune retry
machinery in helper standalone. NON in scope B3-A.6.

Pattern: Mixin class via MRO
============================
`EnrichmentRetryHttpMixin` viene ereditato da `EnrichmentExecutor`:

    class EnrichmentExecutor(
        EnrichmentTemplateParamsMixin,
        EnrichmentResponseProcessingMixin,
        EnrichmentBatchModeMixin,
        EnrichmentValidationPreviewMixin,
        EnrichmentRetryHttpMixin,
    ):
        ...

`_call_api_with_retry` resta accessibile come
`EnrichmentExecutor._call_api_with_retry` via MRO.
`inspect.getsource(EnrichmentExecutor._call_api_with_retry)` legge
il source DA QUESTO FILE.

Coupling cross-mixin
====================
- `self._extract_output(data)` → `EnrichmentResponseProcessingMixin`
  (B3-A.3). MRO.

Coupling esterno (instance attrs)
==================================
- `self.options.{max_retries, request_timeout, connect_timeout,
  retry_on_status, retry_backoff_factor, respect_retry_after,
  disable_ssl_verify}` — instance attrs di `__init__`.
- `self.url`, `self.method`, `self.auth`, `self._headers`,
  `self._stats_lock`, `self._response_times` — instance attrs.

Module-level deps
==================
- `time` (top-level): timing + sleep.
- `re` (lazy in body): path params regex.
- `urllib.parse.quote` (lazy in body): URL encoding path params.
- `requests` (top-level): Timeout + RequestException exceptions.
- `get_shared_session`, `sanitize_url_for_log`, `sanitize_for_log`
  (top-level da `app.shared.api_utils`): HTTP session shared +
  log redaction.
- `get_arcgis_token` (lazy in body da `app.shared.api_utils`):
  ArcGIS token caching with auto-renewal.
- `RowError`, `RowStatus` (models).

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A.6):
  - 1 instance-level rebind:
    `tests2/arricchisci/unit/test_enrichment_executor_sync_vs_async_parity.py:95`
    fa `executor._call_api_with_retry = fn`. Pattern instance-level
    attribute set: shadow il class lookup MRO, funziona indipendentemente
    dalla posizione fisica del metodo. ✓
  - 0 `patch.object(EnrichmentExecutor, "_call_api_with_retry", ...)`.
  - 0 `inspect.getsource(EnrichmentExecutor._call_api_with_retry)`
    o `inspect.getsource(enrichment_service)`.
  - 0 AST scan su `enrichment_service.py` per pattern in questo metodo.
  - 0 `monkeypatch.setattr` su attributi del modulo.

Estrazione safe per costruzione. Nessuna pre-modernization richiesta.

Logger naming contract
======================
`_call_api_with_retry:956` emette 1 `logger.debug` con sanitize_url +
sanitize params (anti-flood per job grandi: ~3300 righe a INFO satura
journald). Mixin definisce `logger` esplicito canonico
`get_logger("app.core.arricchisci.enrichment_service")` (parita' B2 +
B3-A.2/A.3/A.4; singleton stdlib per nome → record indistinguibili).

Behavior-preserving
===================
NESSUN cambio architetturale in B3-A.6:
  - Retry chain: `max_retries+1 attempt` loop bit-per-bit.
  - 429 + Retry-After: `time.sleep(retry_after)` se
    `respect_retry_after` settato.
  - Exponential backoff: `(retry_backoff_factor ** attempt)`.
  - Path params URL substitution: regex `{name}` + `urllib.parse.quote`
    (safe chars `-._~:@!$&\'()*+,=;/`).
  - ArcGIS token injection: `call_params["token"]=tok` (NOT
    Authorization header).
  - DEBUG anti-flood log: bit-per-bit.
  - SSL verify: `not getattr(self.options, 'disable_ssl_verify', False)`.
  - Empty `with self._stats_lock: pass` block (rate limit hits placeholder).

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`enrichment_service.py` al momento di B3-A.6. Nessuna modifica di
logica, comment, log payload, retry timing, status code dispatch,
ArcGIS token cache behavior, sanitize_*_for_log redaction.
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
