"""
Mixin API GENERATOR + API ENRICHER + JSON PATH UTIL per

`ApiConnectionService`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from app.shared.api_utils import (
    extract_json_value as _shared_extract_json_value,
    sanitize_for_log,
)
from app.utils.logger import get_logger
from app.core.arricchisci.response_parser import ResponseParser, get_preset_config
from app.core.artifacts.static_parameters import process_static_fields

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
    # SSRF guard → GET/POST → parse_response). Pagination 4 strategies
    # '' (no-pag), 'offset', 'cursor', 'link'

    def _paginate_generator_results(
        self, *, call_fn, pagination: Dict, base_params: Dict
    ) -> List[Dict]:
        """Esegue la paginazione del generator dispatchando su `pagination['type']`.

        - `''` (vuoto): singola chiamata, ritorna tutto.
        - `'offset'`: incrementa `offset_param` di `page_size` ogni
          pagina, ferma su empty / `len < page_size` / `>= max_rows`.
        - `'cursor'`: estrae `cursor_path` dal body e lo passa come
          `cursor_param` alla pagina successiva.
        - `'link'`: segue URL `next` dal body (`link_path`) o
          dall'header `Link` RFC 5988. Dopo la prima pagina svuota
          `base_params` (l'URL e' gia' completo).
        - tipo sconosciuto: warn + singola chiamata fallback.

        `MAX_PAGES = max_rows // page_size + 1` come safety cap globale.
        """
        pagination_type = pagination.get('type', '')

        if not pagination_type:
            rows, _, _ = call_fn(base_params)
            logger.info(f"Generator completato: {len(rows)} righe (no paginazione)")
            return rows or []

        page_size = int(pagination.get('page_size', 100))
        max_rows = int(pagination.get('max_rows', 10000))
        all_rows: List[Dict] = []
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
                rows, _, _ = call_fn(call_params)
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
                if cursor_value:
                    call_params[cursor_param] = cursor_value
                rows, data, _ = call_fn(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(
                    f"Generator cursor pag {page_num+1}: +{len(rows)} righe "
                    f"(tot {len(all_rows)})"
                )
                cursor_value = self._extract_json_value(data, cursor_path)
                if not cursor_value:
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break

        elif pagination_type == 'link':
            link_path = pagination.get('link_path', 'next')
            next_url = None
            link_base_params = base_params  # mutiamo la copy locale

            for page_num in range(MAX_PAGES):
                rows, data, resp = call_fn(link_base_params, call_url=next_url)
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
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                # Dopo prima pagina, non rimandare base_params (URL gia' completo
                link_base_params = {}

        else:
            logger.warning(
                f"Tipo paginazione sconosciuto: '{pagination_type}', "
                f"eseguo singola chiamata"
            )
            rows, _, _ = call_fn(base_params)
            all_rows = rows or []

        logger.info(
            f"Generator completato: {len(all_rows)} righe totali in "
            f"{page_num+1} pagine"
        )
        return all_rows

    @staticmethod
    def _substitute_url_path_params(
        url: str, params: Dict, *, safe: str = "-._~:@!$&'()*+,=;/"
    ) -> tuple:
        """Sostituisce placeholder `{name}` nel URL con i valori da `params`.

        Ritorna `(updated_url, query_params)` dove `query_params` contiene
        solo le chiavi che NON erano placeholder nel URL (vanno in
        querystring). I valori sostituiti nel path sono URL-encoded.

        `safe` controlla quali caratteri NON vengono percent-encodati:
        - default `"-._~:@!$&'*+,=;/"`: permissivo, preserva slash/virgola
          (usato da `_execute_api_enricher` e `test_api_connection` —
          alcuni endpoint si aspettano `lat,lon` non encoded nel path).
        - `""`: stretto, encoda tutto (usato da `_execute_api_generator`).

        Usato da `_execute_api_generator`, `_execute_api_enricher` e
        `test_api_connection`.
        """
        target_url = url
        query_params: Dict = {}
        for pname, pvalue in params.items():
            placeholder = f'{{{pname}}}'
            if placeholder in target_url:
                encoded = requests.utils.quote(str(pvalue), safe=safe)
                target_url = target_url.replace(placeholder, encoded)
            else:
                query_params[pname] = pvalue
        return target_url, query_params

    def _call_generator_endpoint(
        self,
        *,
        url: str,
        method: str,
        headers: Dict,
        call_params: Dict,
        call_url: Optional[str],
        response_preset: str,
        response_config: Dict,
        output_mapping: List[Dict],
    ) -> tuple:
        """Esegue una singola chiamata del generator e parsa la response.

        Path param substitution → SSRF guard → GET/POST → status check
        → dispatch parser (preset > response_config no-wildcard >
        output_mapping). Ritorna `(rows, raw_data, response)`.

        Solleva `ValueError` su HTTP error.
        """
        from app.shared.api_utils import (
            get_shared_session,
            validate_external_url,
        )

        target_url = call_url or url
        # Generator: encoding stretto (safe='') — i path param vengono
        # encoded interamente, niente eccezioni per slash/virgola
        target_url, query_params = self._substitute_url_path_params(
            target_url, call_params, safe=''
        )

        validate_external_url(target_url)

        logger.info(f"Generator API call: {method} {target_url}")
        session = get_shared_session()
        if method == 'GET':
            resp = session.get(
                target_url, params=query_params, headers=headers, timeout=60
            )
        else:
            resp = session.post(
                target_url, json=call_params, headers=headers, timeout=60
            )

        if resp.status_code != 200:
            err = resp.text[:500] if resp.text else 'nessuna risposta'
            raise ValueError(f"API error: HTTP {resp.status_code} — {err}")

        data = resp.json()

        if response_preset:
            preset_cfg = get_preset_config(response_preset)
            if preset_cfg:
                return ResponseParser(preset_cfg).parse(data), data, resp

        # `response_config` viene usato solo se ha mappings configurati
        # e senza wildcard `[*]` — i path con wildcard sono gestiti da
        # `_extract_rows_from_response` (specializzato per gli array
        # generati dal wizard
        rc_mappings = response_config.get('mappings', []) if response_config else []
        has_wildcard = any('[*]' in m.get('path', '') for m in rc_mappings)
        if response_config and rc_mappings and not has_wildcard:
            return ResponseParser(response_config).parse(data), data, resp

        return self._extract_rows_from_response(data, output_mapping), data, resp

    def _build_generator_base_params(
        self, api_config: Dict, input_df: Optional[pd.DataFrame]
    ) -> Dict:
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
            row = input_df.iloc[0].to_dict()
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

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            base_params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        return base_params

    def _execute_api_generator(self, api_config: Dict, input_df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Esegue API di tipo 'generator' — genera un DataFrame dalla risposta.

        Supporta paginazione automatica con tre strategie:
            - offset: aggiunge offset/limit ad ogni chiamata fino a risultati vuoti o max_rows
            - cursor: usa un token/cursor dalla risposta per la chiamata successiva
            - link: segue un URL 'next' nel body o nell'header Link della risposta

        Configurazione paginazione (in api_config['pagination']):
            type: 'offset' | 'cursor' | 'link' (default: nessuna)
            page_size: righe per pagina (default: 100)
            max_rows: limite totale righe (default: 10000)
            offset_param: nome parametro offset (default: 'offset')
            limit_param: nome parametro limit/page_size (default: 'limit')
            cursor_param: nome parametro cursor (default: 'cursor')
            cursor_path: path JSON del cursor nella risposta (default: 'next_cursor')
            link_path: path JSON URL prossima pagina (default: 'next')
            start_page: indice prima pagina (offset) (default: 0)

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

        if not url:
            raise ValueError("URL API non configurato")

        headers = self._prepare_headers(auth, custom_headers)
        base_params = self._build_generator_base_params(api_config, input_df)

        def _call_and_parse(call_params, call_url=None):
            return self._call_generator_endpoint(
                url=url,
                method=method,
                headers=headers,
                call_params=call_params,
                call_url=call_url,
                response_preset=response_preset,
                response_config=response_config,
                output_mapping=output_mapping,
            )

        all_rows = self._paginate_generator_results(
            call_fn=_call_and_parse,
            pagination=pagination,
            base_params=base_params,
        )
        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    # API ENRICHER
    # Pipeline: per riga del df → build params → path subst → SSRF guard →
    # GET/POST → parse → estrai → scrivi colonne. Skip-on-row-error
    # resilience pattern

    def _extract_rows_from_response(self, data: Any, output_mapping: List[Dict]) -> List[Dict]:
        """
        Estrae righe dalla response API usando output_mapping semplice.
        
        Per strutture annidate complesse, usare response_config o response_preset.
        Gestisce path con wildcard [*] tipo data[*].id generati dal wizard.
        """
        rows = []

        # Path con wildcard `[*]` (es. `data[*].id`): estrai il root
        # array e itera con path relativo all'item
        wildcard_paths = [m.get('path', '') for m in output_mapping if '[*]' in m.get('path', '')]
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
                    path = m.get('path', '')
                    col = m.get('column', path)
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
                    path = mapping.get('path', '')
                    col_name = mapping.get('column', path)
                    if path.startswith('features['):
                        parts = path.split('.', 1)
                        path = parts[1] if len(parts) > 1 else ''
                    row[col_name] = self._extract_json_value(feature, path)
                rows.append(row)

        # Array — controlla se i path sono assoluti sulla lista (es. [0].name.common
        # In questo caso produce UNA sola riga estraendo i valori dalla lista intera
        elif isinstance(data, list):
            all_absolute = output_mapping and all(
                re.match(r'^\[\d+\]', m.get('path', '')) for m in output_mapping
            )
            if all_absolute:
                row = {}
                for m in output_mapping:
                    col = m.get('column', m.get('path'))
                    row[col] = self._extract_json_value(data, m.get('path', ''))
                rows.append(row)
            else:
                for item in data:
                    row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', ''))
                           for m in output_mapping}
                    rows.append(row)

        # Oggetto singolo con array "data" (auto-detect
        elif isinstance(data, dict) and 'data' in data and isinstance(data.get('data'), list):
            logger.info("Auto-detect: usando array 'data' come root")
            for item in data['data']:
                row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', ''))
                       for m in output_mapping}
                rows.append(row)

        # Oggetto singolo
        elif isinstance(data, dict):
            row = {m.get('column', m.get('path')): self._extract_json_value(data, m.get('path', ''))
                   for m in output_mapping}
            rows.append(row)

        return rows

    def _enrich_one_row(
        self,
        *,
        row,
        api_config: Dict,
        headers: Dict,
        param_mapping: Dict,
        output_mapping: List[Dict],
        method: str,
        url: str,
        idx,
    ) -> Optional[Dict]:
        """Esegue l'enrichment per una singola riga del DataFrame.

        Costruisce parametri (static + mappati da colonne + api_key in
        query), sostituisce path params nell'URL, fa la chiamata HTTP,
        estrae i valori per le colonne output.

        Ritorna `dict[col_name → value]` su success (200), `None` su
        errore HTTP (lascia il caller incrementare l'error counter senza
        propagare l'eccezione). Le eccezioni network/parse risalgono al
        caller (che le cattura nel try/except del loop).
        """
        from app.shared.api_utils import (
            get_shared_session,
            validate_external_url,
        )

        auth = api_config.get('auth', {}) or {}
        params = dict(api_config.get('static_parameters', {}) or {})

        for api_param, df_column in param_mapping.items():
            if df_column and df_column in row:
                value = row[df_column]
                params[api_param] = str(value) if pd.notna(value) else None

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        call_url, call_params = self._substitute_url_path_params(url, params)

        validate_external_url(call_url)
        session = get_shared_session()
        if method == 'GET':
            response = session.get(
                call_url, params=call_params, headers=headers, timeout=30
            )
        else:
            response = session.post(
                call_url, json=call_params, headers=headers, timeout=30
            )

        if response.status_code != 200:
            logger.warning(
                f"Riga {idx}: HTTP {response.status_code} - "
                f"{response.text[:200]}"
            )
            return None

        data = response.json()
        extracted: Dict[str, Any] = {}
        for mapping in output_mapping:
            path = mapping.get('path', '')
            col_name = mapping.get('column', path)
            extracted[col_name] = self._extract_json_value(data, path)
        return extracted

    def _execute_api_enricher(self, df: pd.DataFrame, api_config: Dict, param_mapping: Dict) -> pd.DataFrame:
        """
        Esegue API di tipo 'enricher' — arricchisce ogni riga del DataFrame.
        
        Per ogni riga: costruisce parametri (fissi + mappati da colonne),
        chiama l'API, estrae i campi dalla risposta JSON, e li scrive
        nelle colonne output. Rispetta il rate limiting tra le chiamate.
        
        Questa è la versione semplificata (sincrona, senza deduplicazione).
        Per l'esecuzione avanzata (parallela, con cache, batch) vedere
        enrichment_service.py / EnrichmentExecutor.
        """
        import time

        result_df = df.copy()

        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        output_mapping = api_config.get('output_mapping', [])

        if not url:
            raise ValueError("URL API non configurato")

        # Prepara colonne output
        for mapping in output_mapping:
            col_name = mapping.get('column', mapping.get('path'))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        # Token OAuth2 gestito via cache con expires_at (vedi api_utils.get_cached_oauth2_token
        # Il refresh avviene automaticamente solo quando il token sta per scadere — non serve
        # piu il vecchio pattern "rinnova ogni N righe" basato su un counter arbitrario
        _is_oauth2 = auth.get('type') in ('oauth2_password', 'oauth2_client_credentials')

        def _get_fresh_headers():
            return self._prepare_headers(auth, custom_headers)

        headers = _get_fresh_headers()

        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0

        success_count = 0
        error_count = 0

        for idx, row in df.iterrows():
            try:
                if _is_oauth2:
                    headers = _get_fresh_headers()

                extracted_values = self._enrich_one_row(
                    row=row,
                    api_config=api_config,
                    headers=headers,
                    param_mapping=param_mapping,
                    output_mapping=output_mapping,
                    method=method,
                    url=url,
                    idx=idx,
                )
                if extracted_values is not None:
                    for col_name, value in extracted_values.items():
                        result_df.at[idx, col_name] = value
                    success_count += 1
                else:
                    error_count += 1

                if delay > 0:
                    time.sleep(delay)

            except Exception as _row_err:
                # skip-on-row-error per design (vedi docstring
                # `_enrich_one_row`): il loop continua sul batch
                # Log a `debug` per evitare flood su dataset grandi
                # il riassunto finale `info` mostra il counter aggregato
                logger.debug(
                    f"Riga {idx}: eccezione skippata "
                    f"({type(_row_err).__name__}: {_row_err})"
                )
                error_count += 1

            if (idx + 1) % 100 == 0:
                logger.info(f"Progresso: {idx + 1}/{len(df)} (OK: {success_count}, ERR: {error_count})")

        logger.info(f"Completato: {success_count}/{len(df)} righe, {error_count} errori")
        return result_df

    # JSON PATH UTIL — delegate condiviso fra Generator e Enricher

    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)
