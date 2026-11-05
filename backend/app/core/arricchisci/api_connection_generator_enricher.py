"""
Mixin API GENERATOR + API ENRICHER + JSON PATH UTIL per

`ApiConnectionService`.
"""

from __future__ import annotations

from app.shared.execution.api_mapping import mapping_destination

import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests

from app.core.arricchisci.async_poller import AsyncPoller
from app.core.arricchisci.request_preparation import prepare_generator_request, prepare_generator_pagination
from app.core.dataframes.tabular import (
    dataframe_first_row_dict,
    dataframe_from_records,
    dataframe_from_rows,
)
from app.core.arricchisci.http_executor import (
    RequestSpec,
    allow_redirects_from_config,
    body_type_from_config,
    execute_http_request,
    raw_body_from_config,
    timeout_from_options,
)
from app.observability.log_sanitization import sanitize_for_log
from app.shared.resources.json_path import extract_json_value as _shared_extract_json_value
from app.utils.logger import get_logger
from app.core.arricchisci.response_parser import (
    ResponseParser,
    get_preset_config,
    parse_response_body,
)
from app.core.arricchisci.success_evaluator import (
    evaluate_success,
    success_config_from_api_config,
)
from app.core.arricchisci.types import JsonObject, JsonRows
from app.core.artifacts.static_parameters import process_static_fields
from app.shared.execution.runtime_bound_fields import RUNTIME_STATIC_PARAMETERS
from app.core.arricchisci.url_path_params import substitute_path_params

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b/B3-B.3/B3-B.4b/B3-B.5b
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionGeneratorEnricherMixin:
    """Mixin per `ApiConnectionService`: API GENERATOR (pagination +
    DataFrame production from JSON response) + API ENRICHER (row-by-row
    enrichment via API call) + JSON PATH UTIL (`_extract_json_value`
    delegate condiviso fra i due flussi).
    """

    # API GENERATOR
    # Pipeline: build_base_params → paginate → call_endpoint(path subst →
    # SSRF guard → execute → parse_response). Pagination strategies:
    # '' (no-pag), 'offset', 'cursor', 'link', 'page-number'.

    def _paginate_generator_results(
        self,
        *,
        call_fn,
        pagination: JsonObject,
        base_params: JsonObject,
        base_url: str | None = None,
        options: JsonObject | None = None,
    ) -> JsonRows:
        """Esegue la paginazione del generator dispatchando su `pagination['type']`.

        - `''` (vuoto): singola chiamata, ritorna tutto.
        - `'offset'`: incrementa `offset_param` di `page_size` ogni
          pagina, ferma su empty / `len < page_size` / `>= max_rows`.
        - `'cursor'`: estrae `cursor_path` dal body e lo passa come
          `cursor_param` alla pagina successiva.
        - `'link'`: segue URL `next` dal body (`link_path`) o
          dall'header `Link` RFC 5988. Dopo la prima pagina svuota
          `base_params` (l'URL e' gia' completo).
        - `'page-number'`: incrementa un parametro pagina numerico.
        - tipo sconosciuto: warn + singola chiamata fallback.

        `MAX_PAGES = max_rows // page_size + 1` come safety cap globale.
        """
        pagination_type = pagination.get('type', '')

        if not pagination_type:
            rows, _, _ = call_fn(base_params)
            logger.info(f"Generator completato: {len(rows)} righe (no paginazione)")
            return rows or []

        retry_cfg = {**dict(options or {}), **dict(pagination or {})}
        raw_page_max_retries = retry_cfg.get(
            "retry_max",
            retry_cfg.get("max_retries", 3),
        )
        if raw_page_max_retries in (None, ""):
            raw_page_max_retries = 3
        page_max_retries = max(
            0,
            int(raw_page_max_retries),
        )
        page_retry_backoff = float(retry_cfg.get("retry_backoff_factor", 0) or 0)
        retry_on_status = {
            int(status)
            for status in retry_cfg.get(
                "retry_on_status",
                [429, 500, 502, 503, 504],
            )
        }

        def _http_status_from_error(exc: ValueError) -> int | None:
            match = re.search(r"HTTP\s+(\d{3})", str(exc))
            return int(match.group(1)) if match else None

        def _call_page(call_params, call_url=None):
            for attempt in range(page_max_retries + 1):
                try:
                    return call_fn(call_params, call_url=call_url)
                except ValueError as exc:
                    status_code = _http_status_from_error(exc)
                    if (
                        status_code not in retry_on_status
                        or attempt >= page_max_retries
                    ):
                        raise
                    logger.warning(
                        "Generator pagination retry "
                        f"{attempt + 1}/{page_max_retries} dopo "
                        f"HTTP {status_code}"
                    )
                    if page_retry_backoff > 0:
                        time.sleep(page_retry_backoff ** attempt)
                except (TimeoutError, requests.RequestException) as exc:
                    if attempt >= page_max_retries:
                        raise
                    logger.warning(
                        "Generator pagination retry "
                        f"{attempt + 1}/{page_max_retries} dopo "
                        f"{type(exc).__name__}"
                    )
                    if page_retry_backoff > 0:
                        time.sleep(page_retry_backoff ** attempt)

        page_size, max_rows = prepare_generator_pagination(pagination)
        all_rows: JsonRows = []
        page_num = 0
        MAX_PAGES = max(1, max_rows // max(1, page_size)) + 1

        if pagination_type == 'offset':
            offset_param = pagination.get('offset_param', 'offset')
            limit_param = pagination.get('limit_param', 'limit')
            start = int(pagination.get('start_page', 0))

            for page_num in range(MAX_PAGES):
                call_params = {
                    **base_params,
                    offset_param: start + page_num * page_size,
                    limit_param: page_size,
                }
                rows, _, _ = _call_page(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator offset pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                if len(rows) < page_size:
                    break  # ultima pagina parziale

        elif pagination_type == 'cursor':
            cursor_param = pagination.get('cursor_param', 'cursor')
            cursor_path = pagination.get('cursor_path', 'next_cursor')
            cursor_value = None

            for page_num in range(MAX_PAGES):
                call_params = {**base_params}
                if cursor_value is not None and cursor_value != "":
                    call_params[cursor_param] = cursor_value
                rows, data, _ = _call_page(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator cursor pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                cursor_value = self._extract_json_value(data, cursor_path)
                if cursor_value is None or cursor_value == "":
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break

        elif pagination_type == 'link':
            link_path = pagination.get('link_path', 'next')
            next_url = None
            link_base_params = base_params  # mutiamo la copy locale

            for page_num in range(MAX_PAGES):
                current_request_url = next_url or base_url
                rows, data, resp = _call_page(link_base_params, call_url=next_url)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator link pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                next_url = self._extract_json_value(data, link_path)
                if not next_url:
                    link_header = resp.headers.get('Link', '')
                    match = re.search(r'<([^>]+)>;\s*rel=["\']next["\']', link_header)
                    next_url = match.group(1) if match else None
                if not next_url:
                    break
                raw_response_url = getattr(
                    getattr(resp, "raw_response", None),
                    "url",
                    None,
                )
                if not isinstance(raw_response_url, str):
                    raw_response_url = None
                current_page_url = (
                    raw_response_url
                    or current_request_url
                    or base_url
                    or ""
                )
                next_url = urljoin(current_page_url, str(next_url))
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                # Dopo prima pagina, non rimandare base_params (URL gia' completo
                link_base_params = {}

        elif pagination_type in ('page', 'page_number', 'page-number'):
            page_param = pagination.get('page_param', 'page')
            page_size_param = pagination.get(
                'page_size_param',
                pagination.get('limit_param', 'page_size'),
            )
            start = int(pagination.get('start_page', 1))

            for page_num in range(MAX_PAGES):
                call_params = {
                    **base_params,
                    page_param: start + page_num,
                    page_size_param: page_size,
                }
                rows, _, _ = _call_page(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator page-number pag {start + page_num}: "
                    f"+{len(rows)} righe (tot {len(all_rows)})"
                )
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                if len(rows) < page_size:
                    break

        else:
            logger.warning(
                f"Tipo paginazione sconosciuto: '{pagination_type}', "
                f"eseguo singola chiamata"
            )
            rows, _, _ = _call_page(base_params)
            all_rows = rows or []

        logger.info(
            f"Generator completato: {len(all_rows)} righe totali in "
            f"{page_num+1} pagine"
        )
        return all_rows

    @staticmethod
    def _substitute_url_path_params(url: str, params: JsonObject) -> tuple:
        """Sostituisce i segnaposto ``{name}`` del URL: regola unica di
        ``url_path_params`` (valore codificato per intero, ``.``/``..``/vuoto
        rifiutati). Ritorna ``(updated_url, query_params)``.

        Usato da `_execute_api_generator` e `test_api_connection`.
        """
        return substitute_path_params(url, params)

    def _call_generator_endpoint(
        self,
        *,
        url: str,
        method: str,
        headers: JsonObject,
        call_params: JsonObject,
        call_url: Optional[str],
        options: JsonObject,
        response_preset: str,
        response_config: JsonObject,
        request_config: JsonObject,
        async_config: JsonObject,
        response_body_config: JsonObject,
        success_config: JsonObject,
        output_mapping: JsonRows,
    ) -> tuple:
        """Esegue una singola chiamata del generator e parsa la response.

        Path param substitution → SSRF guard → GET/POST → status check
        → dispatch parser (preset > response_config no-wildcard >
        output_mapping). Ritorna `(rows, raw_data, response)`.

        Solleva `ValueError` su HTTP error.
        """
        target_url = call_url or url
        target_url, query_params = self._substitute_url_path_params(
            target_url, call_params
        )

        logger.info(f"Generator API call: {method} {target_url}")
        request_spec = RequestSpec(
            url=target_url,
            method=method,
            params=query_params,
            json_body=call_params,
            raw_body=raw_body_from_config(request_config),
            body_type=body_type_from_config(request_config),
            headers=headers,
            timeout=timeout_from_options(options, default_read_timeout=60),
            allow_redirects=allow_redirects_from_config(options, request_config),
            context="API generator",
        )

        if async_config.get("enabled"):
            poll_result = AsyncPoller(execute=execute_http_request).run(
                request_spec,
                async_config,
                base_url=target_url,
            )
            resp = poll_result.response
            data = poll_result.data
        else:
            resp = execute_http_request(request_spec)
            if resp.status_code != 200:
                err = resp.text[:500] if resp.text else 'nessuna risposta'
                raise ValueError(f"API error: HTTP {resp.status_code} — {err}")
            data = parse_response_body(resp, response_body_config)
        evaluation = evaluate_success(data, success_config)
        if not evaluation.ok:
            raise ValueError(
                "API application error: "
                f"{evaluation.error_message or 'success_when non soddisfatto'}"
            )

        if response_preset:
            preset_cfg = get_preset_config(response_preset)
            if preset_cfg:
                return ResponseParser(preset_cfg).parse(data), data, resp

        # `response_config` viene usato solo se ha mappings configurati
        # e senza wildcard `[*]` — i path con wildcard sono gestiti da
        # `_extract_rows_from_response` (specializzato per gli array
        # generati dal wizard
        rc_mappings = response_config.get('mappings', []) if response_config else []
        has_wildcard = any('[*]' in mapping_destination(m, phase='source', default='') for m in rc_mappings)
        if response_config and rc_mappings and not has_wildcard:
            return ResponseParser(response_config).parse(data), data, resp

        return self._extract_rows_from_response(data, output_mapping), data, resp

    def _build_generator_base_params(
        self, api_config: JsonObject, input_df: Optional[Any]
    ) -> JsonObject:
        """Costruisce i parametri base per `_execute_api_generator`.

        Strategie:
        - Se `input_df` non e' vuoto: prima riga → dict di parametri
          stringificati (ignora None/blank).
        - Altrimenti: `static_parameters` con template processing
          (`{{NOW}}`, `{{TODAY}}`, ecc.) per i valori string.

        Aggiunge `api_key` come query param se l'auth e' di tipo
        `api_key` con `location='query'`.
        """
        auth = api_config.get('auth', {}) or {}
        if input_df is not None and not input_df.empty:
            row = dataframe_first_row_dict(input_df)
            base_params = {
                k: str(v).strip()
                for k, v in row.items()
                if v is not None and str(v).strip()
            }
            logger.info(
                f"Generator usando parametri da input: "
                f"{sanitize_for_log(base_params, auth)}"
            )
        else:
            base_params = {}
            static_params = api_config.get('static_parameters', {}) or {}
            for k, v in static_params.items():
                if isinstance(v, str):
                    fields = [{'name': k, 'value': v}]
                    processed = process_static_fields(fields)
                    base_params[k] = processed.get(k, v)
                else:
                    base_params[k] = v
            logger.info(
                f"Generator usando static_parameters (processed): "
                f"{sanitize_for_log(base_params, auth)}"
            )

        # Valori scritti dagli input runtime nel nodo (runtime_bound_fields):
        # sempre inviati, senza elaborare i segnaposto {{...}}.
        base_params.update(api_config.get(RUNTIME_STATIC_PARAMETERS) or {})

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            base_params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        return base_params

    def _execute_api_generator(self, api_config: JsonObject, input_df: Any = None) -> Any:
        """
        Esegue API di tipo 'generator' — genera un DataFrame dalla risposta.

        Supporta paginazione automatica con quattro strategie:
            - offset: aggiunge offset/limit ad ogni chiamata fino a risultati vuoti o max_rows
            - cursor: usa un token/cursor dalla risposta per la chiamata successiva
            - link: segue un URL 'next' nel body o nell'header Link della risposta
            - page-number: incrementa un parametro numerico di pagina

        Configurazione paginazione (in api_config['pagination']):
            type: 'offset' | 'cursor' | 'link' | 'page-number' (default: nessuna)
            page_size: righe per pagina (default: 100)
            max_rows: limite totale righe (default: 10000)
            offset_param: nome parametro offset (default: 'offset')
            limit_param: nome parametro limit/page_size (default: 'limit')
            cursor_param: nome parametro cursor (default: 'cursor')
            cursor_path: path JSON del cursor nella risposta (default: 'next_cursor')
            link_path: path JSON URL prossima pagina (default: 'next')
            page_param: nome parametro pagina per page-number (default: 'page')
            page_size_param: nome parametro page size per page-number (default: 'page_size')
            start_page: indice prima pagina (offset default 0, page-number default 1)

        Strategie di parsing risposta (in ordine di priorità):
            1. response_preset: preset predefinito (italiameteo_observations, geojson_features)
            2. response_config: configurazione custom per strutture JSON annidate
            3. output_mapping: mapping semplice path→colonna (retrocompatibilità)
        """
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        output_mapping = api_config.get('output_mapping', [])
        response_config = api_config.get('response_config', {})
        response_preset = api_config.get('response_preset', '')
        pagination = api_config.get('pagination', {})
        options = api_config.get('options', {}) or {}
        request_config = api_config.get('request', {}) or {}
        async_config = api_config.get('async', {}) or {}
        success_config = success_config_from_api_config(api_config)

        prepare_generator_request(api_config)

        headers = self._prepare_headers(auth, custom_headers)
        base_params = self._build_generator_base_params(api_config, input_df)

        def _call_and_parse(call_params, call_url=None):
            return self._call_generator_endpoint(
                url=url,
                method=method,
                headers=headers,
                call_params=call_params,
                call_url=call_url,
                options=options,
                response_preset=response_preset,
                response_config=response_config,
                request_config=request_config,
                async_config=async_config,
                response_body_config=api_config,
                success_config=success_config,
                output_mapping=output_mapping,
            )

        all_rows = self._paginate_generator_results(
            call_fn=_call_and_parse,
            pagination=pagination,
            base_params=base_params,
            base_url=url,
            options=options,
        )
        if not all_rows and output_mapping:
            columns = []
            seen = set()
            for mapping in output_mapping:
                column = mapping_destination(mapping)
                if column and column not in seen:
                    columns.append(column)
                    seen.add(column)
            if columns:
                return dataframe_from_rows([], columns)
        return dataframe_from_records(all_rows)

    # API ENRICHER
    # Pipeline: per riga del df → build params → path subst → SSRF guard →
    # GET/POST → parse → estrai → scrivi colonne. Skip-on-row-error
    # resilience pattern

    def _extract_rows_from_response(
        self,
        data: Any,
        output_mapping: JsonRows,
    ) -> JsonRows:
        """
        Estrae righe dalla response API usando output_mapping semplice.
        
        Per strutture annidate complesse, usare response_config o response_preset.
        Gestisce path con wildcard [*] tipo data[*].id generati dal wizard.
        """
        rows = []

        # Path con wildcard `[*]` (es. `data[*].id`): estrai il root
        # array e itera con path relativo all'item
        wildcard_paths = [mapping_destination(m, phase='source', default='') for m in output_mapping if '[*]' in mapping_destination(m, phase='source', default='')]
        if wildcard_paths:
            # Trova il prefisso comune prima di [*] (es. "data"
            first_path = wildcard_paths[0]
            array_root = first_path.split('[*]')[0].rstrip('.')  # es. "data"
            # Naviga all'array root
            array_data = self._extract_json_value(data, array_root) if array_root else data
            if not isinstance(array_data, list):
                array_data = [array_data] if array_data is not None else []
            for item in array_data:
                row = {}
                for m in output_mapping:
                    path = mapping_destination(m, phase='source', default='')
                    col = mapping_destination(m, phase='columns', default=path)
                    if '[*]' in path:
                        # path relativo all'item (dopo [*
                        rel = path.split('[*]', 1)[1].lstrip('.')
                        row[col] = self._extract_json_value(item, rel) if rel else item
                    else:
                        row[col] = self._extract_json_value(data, path)
                rows.append(row)
            return rows

        # GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            for feature in data.get('features', []):
                row = {}
                for mapping in output_mapping:
                    path = mapping_destination(mapping, phase='source', default='')
                    col_name = mapping_destination(mapping, phase='columns', default=path)
                    if path.startswith('features['):
                        parts = path.split('.', 1)
                        path = parts[1] if len(parts) > 1 else ''
                    row[col_name] = self._extract_json_value(feature, path)
                rows.append(row)

        # Array — controlla se i path sono assoluti sulla lista (es. [0].name.common
        # In questo caso produce UNA sola riga estraendo i valori dalla lista intera
        elif isinstance(data, list):
            all_absolute = output_mapping and all(
                re.match(r'^\[\d+\]', mapping_destination(m, phase='source', default='')) for m in output_mapping
            )
            if all_absolute:
                row = {}
                for m in output_mapping:
                    col = mapping_destination(m, phase='columns')
                    row[col] = self._extract_json_value(data, mapping_destination(m, phase='source', default=''))
                rows.append(row)
            elif not output_mapping:
                # No mapping: expose list items as rows.
                for item in data:
                    rows.append(dict(item) if isinstance(item, dict) else {"value": item})
            else:
                for item in data:
                    row = {mapping_destination(m, phase='columns'): self._extract_json_value(item, mapping_destination(m, phase='source', default=''))
                           for m in output_mapping}
                    rows.append(row)

        # Oggetto singolo con array "data" (auto-detect
        elif isinstance(data, dict) and 'data' in data and isinstance(data.get('data'), list):
            logger.info("Auto-detect: usando array 'data' come root")
            for item in data['data']:
                if output_mapping:
                    row = {
                        mapping_destination(m, phase='columns'): self._extract_json_value(
                            item, mapping_destination(m, phase='source', default=''),
                        )
                        for m in output_mapping
                    }
                else:
                    # No mapping: expose dict keys as columns.
                    row = dict(item) if isinstance(item, dict) else {"value": item}
                rows.append(row)

        # Oggetto singolo
        elif isinstance(data, dict):
            row = {mapping_destination(m, phase='columns'): self._extract_json_value(data, mapping_destination(m, phase='source', default=''))
                   for m in output_mapping}
            rows.append(row)

        return rows

    # JSON PATH UTIL — delegate condiviso fra Generator e Enricher

    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)
