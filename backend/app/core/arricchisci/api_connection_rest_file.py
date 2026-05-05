"""
Mixin REST_FILE PIPELINE + FILE_SOURCE RESOLUTION per `ApiConnectionService`.

Origine
=======
Estratto da `app.core.arricchisci.api_connector.ApiConnectionService`
come parte di Phase 5 Batch B3-B.5b (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B; pattern mixin
ratificato in B2 carica/writer + B3-A arricchisci/enrichment + B3-B.2b
api_connection_management + B3-B.3 api_connection_support + B3-B.4b
api_connection_source_loading).

Pre-extraction — B3-B.5a sblocca il test contract
==================================================
Pre-B3-B.5a 3 test patchavano
`patch("app.core.arricchisci.api_connector.requests.post", ...)` per
intercettare le chiamate HTTP di `_execute_rest_file`. Patch source-
location-bound: spostando il metodo in mixin, il `requests.post` letto
dal mixin (lookup attraverso `import requests` lazy della funzione)
sarebbe risolto con la stessa funzione (`requests.post is
requests.api.post`), MA per chiarezza canonica B3-B.5a ha modernizzato
i 3 patch verso `patch("requests.post", ...)` (source canonico), che
intercetta il binding sul module `requests` stesso — futuro-proof per
qualsiasi consumer.

Post-B3-B.5a l'extraction qui non rompe alcun contratto.

Behavior-equivalent rewrite — 8 forward-ref dichiarati
=======================================================
**Pre-B3-B.5b** 2 metodi (`_build_rest_file_multipart` e
`_parse_rest_file_config`) usavano la sintassi esplicita
`ApiConnectionService._rest_file_error(...)` invece di
`self._rest_file_error(...)` (8 occorrenze totali: linee 680, 697,
711, 719 in `_build_rest_file_multipart` + linee 776, 780, 788, 796
in `_parse_rest_file_config`).

Il forward-ref alla classe `ApiConnectionService` non e' risolvibile
nel module namespace di questo mixin (importazione circolare:
`api_connector.py` importa il mixin a module-load, prima che
`ApiConnectionService` sia definita). Le tre alternative valutate nel
mini-scan B3-B.5:
  - (a) riscrivere `ApiConnectionService._rest_file_error(...)` →
        `self._rest_file_error(...)` (8 siti) — APPROVATA
  - (b) late binding via setattr da api_connector.py — scartata
        (hackish, fragile)
  - (c) lazy import per metodo — scartata (NON bit-per-bit + overhead)

**B3-B.5b applica opzione (a)**: behavior-equivalent rewrite. Per uno
staticmethod, `ApiConnectionService._rest_file_error(...)` e
`self._rest_file_error(...)` sono RUNTIME-IDENTICI (Python descriptor
protocol: staticmethod non ha self bound, accesso via instance ritorna
la stessa funzione). Il rewrite e':
  - 100% behavior-preserving (zero cambio semantico runtime).
  - MRO-correct: `self._rest_file_error` risolve via MRO al mixin
    corrente (o a un'eventuale override futura), evitando hardcoding
    della classe top-level.
  - documentato qui esplicitamente come unica deviazione dal
    bit-per-bit.

Razionale del raggruppamento: 10 metodi che insieme implementano il
"Processore File REST" (rest_file) — pipeline file-to-file via HTTP
multipart con cross-tenant fail-closed.

**REST_FILE PIPELINE (6 metodi)**:
  - `_parse_http_error_body`           — staticmethod, parse JSON con
                                         fallback `text[:500]`. Usato
                                         da `_execute_rest_file` su
                                         response 4xx/5xx.
  - `_download_binary_response`        — staticmethod, streaming
                                         download body con dual-timeout
                                         (connect via `requests.timeout`
                                         + total via wall-clock
                                         `_time.time()`). Chiude la
                                         response in finally. Ritorna
                                         `(total_bytes, timed_out)`.
  - `_build_rest_file_multipart`       — assembla `(files, data, error)`
                                         dict per `requests.post(
                                         multipart)`. Tracking
                                         `opened_handles` per cleanup
                                         deterministico nel caller.
                                         Branch `node_input` /
                                         `extra_input` / static_fields.
  - `_parse_rest_file_config`          — valida config rest_file:
                                         endpoint URL + method=POST +
                                         SSRF guard via
                                         `self._validate_outbound_url`
                                         (B3-B.4b mixin) + format
                                         binary_file + timeout default
                                         600s.
  - `_rest_file_error`                 — staticmethod, taxonomy F5
                                         primo batch: `internal`,
                                         `file_source_not_found`,
                                         `file_source_forbidden`,
                                         `remote_processor_error`.
  - `_execute_rest_file`               — orchestratore principale.
                                         Pipeline: parse_config →
                                         prepare_headers → resolve_input
                                         → build_multipart →
                                         requests.post(stream) →
                                         download_binary → cleanup
                                         tmp_exec_dir + opened_handles.
                                         Catch narrow #20/#21 +
                                         safety-net #22c.

**FILE_SOURCE RESOLUTION (4 metodi)**:
  - `_assert_file_source_group_access` — defense-in-depth cross-tenant
                                         check via
                                         `is_resource_visible`. Fail-
                                         closed se `_current_group_id`
                                         missing.
  - `_resolve_file_source_id_path`     — materializza blob DB → cache
                                         filesystem (atomico via
                                         `.tmp + os.replace`).
                                         `_assert_file_source_group_access`
                                         pre-check obbligatorio.
                                         `FileSourceResolutionError`
                                         su DB/FS errors (Fix-A).
  - `_resolve_file_source_path`        — risolve edge in input al nodo:
                                         file_source via id_path,
                                         project_source via SourceLoader
                                         (cross-tenant fail-closed).
                                         `[SECURITY]` log.
  - `_resolve_input_path_for_file_processor` — variante che gestisce
                                         anche upstream api_enrich (df
                                         materializzato in-memory FS-5)
                                         e upstream rest_file
                                         (`is_file_result=True` reuse
                                         output_path).

Pattern: Mixin class via MRO
============================
`ApiConnectionRestFileMixin` viene ereditato da `ApiConnectionService`
in coda alla MRO:

    class ApiConnectionService(
        ApiConnectionManagementMixin,        # B3-B.2b
        ApiConnectionSupportMixin,           # B3-B.3
        ApiConnectionSourceLoadingMixin,     # B3-B.4b
        ApiConnectionRestFileMixin,          # B3-B.5b
    ):
        ...

I 10 metodi restano accessibili come `ApiConnectionService.<method>`
via MRO. `inspect.getsource(ApiConnectionService.<method>)` legge il
source DA QUESTO FILE.

Cross-cluster forward refs (risolti via MRO):
- `self._validate_outbound_url(url)` → `ApiConnectionSourceLoadingMixin`
  (B3-B.4b). MRO trasparente.
- `self._prepare_headers(auth, custom_headers)` → core
  `ApiConnectionService` (AUTH/UTILITY non estratto). MRO trasparente.

Intra-cluster:
- `_execute_rest_file` chiama: `_parse_rest_file_config`,
  `_build_rest_file_multipart`, `_download_binary_response`,
  `_parse_http_error_body`, `_resolve_input_path_for_file_processor`,
  `_rest_file_error` (×11). Tutti nel mixin.
- `_build_rest_file_multipart` chiama: `_resolve_file_source_id_path`,
  `self._rest_file_error` (×4 — post-B3-B.5b rewrite).
- `_parse_rest_file_config` chiama: `self._validate_outbound_url`
  (cross-cluster), `self._rest_file_error` (×4 — post-B3-B.5b
  rewrite).
- `_resolve_input_path_for_file_processor` chiama:
  `_resolve_file_source_path`.
- `_resolve_file_source_path` chiama: `_resolve_file_source_id_path`.
- `_resolve_file_source_id_path` chiama:
  `_assert_file_source_group_access`.

State refs (`self.<attr>`) — letti da `_init_execution_context` (core):
- `getattr(self, '_current_group_id', None)` (in
  `_assert_file_source_group_access`, `_resolve_file_source_path`).
  Settato da `_init_execution_context` (core). MRO trasparente.

Coupling esterno
================
Top-level (mixin module-level):
- `app.config.settings` per `_resolve_file_source_id_path`
  (`settings.get_file_source_dir`).
- `app.core.arricchisci._api_connection_helpers._nd` (B3-B.1) per
  `_resolve_file_source_path`.
- `app.shared.exceptions.FileSourceResolutionError` per
  `_resolve_file_source_id_path` + `_execute_rest_file`.
- `app.utils.logger.get_logger` per logger canonico.
- `db_svc` (alias `app.db.db_service`) per
  `_resolve_file_source_id_path`.
- `psycopg2` per catch DB error in `_resolve_file_source_id_path`.
- `pathlib.Path`, `io`, typing.

Lazy imports preservati bit-per-bit:
- `_download_binary_response`: `import time as _time`.
- `_execute_rest_file`: `import time`, `import tempfile`, `import
  requests`, `from app.core.artifacts import SourceLoadingError`,
  `import shutil as _shutil_cleanup` (in finally).
- `_assert_file_source_group_access`: `from app.shared.sharing import
  is_resource_visible`.
- `_resolve_file_source_id_path`: `import os as _os`, `from app.config
  import settings` (lazy redundant, ma preservato bit-per-bit).
- `_resolve_file_source_path`: `from app.core.artifacts import
  LatestProjectArtifactRef, SourceLoader, SourceLoadingError`.
- `_resolve_input_path_for_file_processor`: `from app.utils.dataframe
  import save_dataframe_to_bytes`.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-B.5 + cleanup B3-B.5a):
  - 0 `inspect.getsource(ApiConnectionService.<method>)` o
    `inspect.getsource(api_connector)` su questi 10 simboli.
  - 0 AST scan su `api_connector.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr(api_connector_module, "logger", ...)`.
  - **`patch("app.core.arricchisci.api_connector.requests.post", ...)`**
    in `test_api_connector_rest_file_error_taxonomy.py:163,196` +
    `test_temp_lifecycle_core.py:326` → **modernizzato in B3-B.5a**
    verso source canonico `patch("requests.post", ...)`.
  - `patch("app.core.arricchisci.api_connector._shared_prepare_headers", ...)`
    in `test_temp_lifecycle_core.py:325` → patch sul re-import
    api_connector. `_prepare_headers` resta in core (NON estratto in
    B3-B.5b), quindi il body legge `_shared_prepare_headers` dal
    namespace api_connector — patch funzionante.
  - 30+ `patch.object(svc, "_method", ...)` instance-level sui 10
    target → MRO trasparente.

Logger naming contract
======================
22 callsite di log distribuiti: `_execute_rest_file` 8 (info, warning,
error con `[CRASH]` marker), `_resolve_input_path_for_file_processor`
4 (info + warning + error), `_assert_file_source_group_access` 2
(`[SECURITY]` error + warning), `_resolve_file_source_id_path` 2
(debug + debug), `_resolve_file_source_path` 2 (`[SECURITY]` error
×2), `_build_rest_file_multipart` 2 (warning), `_parse_rest_file_config`
1 (warning), `_download_binary_response` 1 (debug). Mixin definisce
`logger` esplicito canonico
`get_logger("app.core.arricchisci.api_connector")` (StructuredLogger
wrapper, singleton stdlib per nome → record indistinguibili dai
consumer che attaccano handler / filtrano per nome canonico, identico
al pattern B3-B.2b/B3-B.3/B3-B.4b).

Behavior-preserving (eccetto 8 forward-ref dichiarati)
=======================================================
NESSUN cambio architetturale in B3-B.5b oltre alle 8 linee dichiarate
sopra:
  - REST_FILE PIPELINE: invariati lifecycle (parse → prepare → resolve
    → build → POST → download → cleanup), dual-timeout watchdog,
    materialize in-memory FS-5 (BytesIO), opened_handles tracking,
    catch narrow #20/#21 + safety-net #22c, error taxonomy F5 primo
    batch.
  - FILE_SOURCE RESOLUTION: invariati cross-tenant fail-closed,
    `is_resource_visible` defense-in-depth, atomic cache write
    (`.tmp + os.replace`), `FileSourceResolutionError` (Fix-A),
    SourceLoader project_source path resolution.
  - `_filesystem_fallback_load_project` [COMPAT-RESIDUE]: gia'
    estratto in B3-B.4b mixin, NON in scope B3-B.5b.
  - Broad-catch `_resolve_file_source_id_path` (vedi
    `app/shared/exceptions.py:322`): NON in scope B3-B.5b. Trade-off
    documentato.
  - Catch #22c safety-net `_execute_rest_file`: NON in scope B3-B.5b.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`api_connector.py` al momento di B3-B.5b, ECCETTO gli 8 forward-ref
dichiarati sopra (`ApiConnectionService._rest_file_error` →
`self._rest_file_error`). Behavior 100% identico.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import psycopg2

import app.db.db_service as db_svc
from app.config import settings
from app.core.arricchisci._api_connection_helpers import _nd
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
)
from app.shared.exceptions import FileSourceResolutionError
from app.utils.logger import get_logger


def _resolve_rest_file_group_id(
    self_obj,
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """BE-GO-READY-TRANSFORMATION-ENGINE-019: speculare a
    `_resolve_group_id` in `api_connection_source_loading.py`.

    Ritorna `ctx.group_id` se Phase 2 (typed context fornito),
    altrimenti `getattr(self_obj, "_current_group_id", None)`
    (KEEP_COMPAT Phase 1).
    """
    if ctx is not None:
        return ctx.group_id
    return getattr(self_obj, "_current_group_id", None)


def _extract_file_source_group_id(
    self_obj,
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """BE-GO-READY-TRANSFORMATION-ENGINE-024: variante ctx-strict
    per la catena di risoluzione `file_source` (chiamata da
    `_assert_file_source_group_access` → `_resolve_file_source_id_path`).

    Differenza vs `_resolve_rest_file_group_id`:
      - **Ctx fornito**: ritorna `ctx.group_id` esclusivamente.
        Se il caller passa `ctx`, lo stato singleton legacy NON
        viene mai consultato — anche se `ctx.group_id is None`
        (intent: ctx vince sempre, fail-closed esplicito a valle).
      - **Ctx assente**: KEEP_COMPAT — fallback su
        `getattr(self_obj, "_current_group_id", None)` per i
        callsite legacy non ancora migrati e per il lock test
        `test_resolve_file_source_id_path_fails_closed_without_security_context`
        che NON setta il ctx.

    Razionale: la catena `file_source` (visibility check + blob
    materialization) e' un boundary di security ad alto rischio
    cross-tenant. Quando il caller dichiara esplicitamente il
    context tipato, qualsiasi stato singleton stale (race window
    da execution precedente, leak fra worker pickup) DEVE essere
    ignorato — altrimenti un `ctx.group_id=None` esplicito
    cadrebbe in fallback su `_current_group_id` di un'altra run,
    aprendo un canale cross-tenant.
    """
    if ctx is not None:
        return ctx.group_id
    return getattr(self_obj, "_current_group_id", None)


def _extract_project_source_group_id(
    self_obj,
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """BE-GO-READY-TRANSFORMATION-ENGINE-026: variante ctx-strict
    per il branch `project_source` di `_resolve_file_source_path`
    (chiama `SourceLoader.resolve_project_path` con
    `expected_group_id`).

    Stesso contract di `_extract_file_source_group_id`
    (TE-024) per simmetria e per chiudere il residual TE24-R1:
      - **Ctx fornito**: ritorna `ctx.group_id` esclusivamente.
        Anche con `ctx.group_id is None`, NON cadiamo sul
        singleton legacy — la run dichiarata fail-closed a
        valle.
      - **Ctx assente**: KEEP_COMPAT — fallback su
        `getattr(self_obj, "_current_group_id", None)` per i
        legacy callsite non ancora migrati. Nessun lock test
        attualmente esercita il fallback `ctx=None +
        _current_group_id=None` per il branch project_source,
        ma il fallback resta per simmetria col helper TE-024
        e per non rompere chiamate dirette esterne (es.
        `enrichment_task.py`).

    Razionale: `SourceLoader.resolve_project_path` accetta
    `expected_group_id` come tenant binding fail-closed.
    Passare un singleton stale qui significherebbe che un
    canvas cross-tenant con `project_source` referenziando un
    progetto di un altro tenant verrebbe risolto attraverso
    il group_id sbagliato — il loader bloccherebbe comunque
    (defense in depth), ma il messaggio di errore + il log
    `[SECURITY]` riferirebbe il group sbagliato. Ctx-strict
    rende il fallimento attribuibile alla run corretta.
    """
    if ctx is not None:
        return ctx.group_id
    return getattr(self_obj, "_current_group_id", None)

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b/B3-B.3/B3-B.4b).
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionRestFileMixin:
    """Mixin per `ApiConnectionService`: REST_FILE PIPELINE (file-to-file
    via HTTP multipart) + FILE_SOURCE RESOLUTION (materializzazione
    file_source dal DB blob, cross-tenant fail-closed, edge-based input
    path resolution).
    """

    # =========================================================================
    # REST_FILE PIPELINE
    # Pipeline: parse_config → prepare_headers → resolve_input → build_multipart
    # → requests.post(stream=True) → download_binary (dual-timeout) → cleanup.
    # Error taxonomy via `_rest_file_error` (F5 primo batch, 2026-04-24).
    # =========================================================================

    @staticmethod
    def _parse_http_error_body(response) -> str:
        """Estrae il messaggio d'errore dal body di una risposta HTTP.

        Prova prima JSON (preferendo `detail` → `error` → `message` →
        l'intero dict se nessuno di questi e' presente). Se il body non
        e' JSON, fallback a `text[:500]`.
        """
        try:
            err_body = response.json()
            if isinstance(err_body, dict):
                return (
                    err_body.get('detail')
                    or err_body.get('error')
                    or err_body.get('message')
                    or err_body
                )
            return err_body
        except Exception:
            return response.text[:500] if response.text else ''

    @staticmethod
    def _download_binary_response(
        response, output_path: Path, start_time: float, timeout: int
    ) -> tuple:
        """Streaming download del body su file con watchdog wall-clock.

        Il parametro `timeout` di `requests.post` copre solo connect/
        first-byte, NON il download del body. Per un server che inizia
        a rispondere ma poi rallenta drammaticamente, il task rischia
        di durare molto oltre il timeout apparente. Il watchdog
        interrompe il download se il totale supera `timeout` secondi
        dall'avvio della chiamata (`start_time`).

        Ritorna `(total_bytes, download_timed_out)`. Chiude sempre
        la response in un `finally` interno (`response.close` best-effort).
        """
        import time as _time

        total_bytes = 0
        body_deadline = start_time + timeout
        download_timed_out = False
        try:
            with open(output_path, 'wb') as out_fh:
                for chunk in response.iter_content(chunk_size=65536):
                    if _time.time() > body_deadline:
                        download_timed_out = True
                        break
                    if chunk:
                        out_fh.write(chunk)
                        total_bytes += len(chunk)
        finally:
            try:
                response.close()
            except Exception as _close_err:
                logger.debug(
                    f"_download_binary_response: response.close() best-effort "
                    f"fallito: {_close_err}"
                )

        return total_bytes, download_timed_out

    def _build_rest_file_multipart(
        self,
        *,
        file_fields: List[Dict],
        static_fields: List[Dict],
        input_path: Optional[str],
        extra_files_cfg: Dict,
        opened_handles: List,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> tuple:
        """Costruisce i dict `files` e `data` per `requests.post(multipart)`.

        Ritorna `(files, data, error_or_None)`.

        Regole:
        - `file_fields` con `source='node_input'` leggono `input_path`
          (risolto dall'edge in ingresso al nodo); se required e assente
          → error, altrimenti skip silenzioso.
        - `file_fields` con `source='extra_input'` leggono da
          `extra_files_cfg[name]` (dict con `file_source_id` o id nudo)
          via `_resolve_file_source_id_path`. Stessa logica required/skip.
        - `static_fields` con `content_type` sono inviati come file
          in-memory (per JSON inline tipo styles/mapping); altrimenti
          come form-data semplice.

        `opened_handles` e' appended in-place: il caller e' responsabile
        del close nel finally, indipendentemente da success/error.
        """
        files: Dict = {}
        data: Dict = {}

        for ff in file_fields:
            name = ff.get('name')
            source = ff.get('source', 'node_input')
            required = bool(ff.get('required', False))
            if not name:
                continue

            if source == 'node_input':
                if not input_path:
                    if required:
                        # B3-B.5b rewrite #1/8: forward-ref classe → self
                        return files, data, self._rest_file_error(
                            "internal",
                            f"Campo file '{name}' richiesto ma nessun file di input collegato al nodo",
                        )
                    continue
                # FS-5: input_path puo' essere str (path su disco) OPPURE
                # tuple(bytes, filename) dal materialize in-memory.
                if isinstance(input_path, tuple):
                    data_bytes, filename = input_path
                    fh = io.BytesIO(data_bytes)
                    # Nessun opened_handle: BytesIO viene GC'd al return,
                    # non serve close esplicito (diversamente da file handle).
                    files[name] = (filename, fh, 'application/octet-stream')
                else:
                    ip = Path(input_path)
                    if not ip.exists():
                        if required:
                            # B3-B.5b rewrite #2/8: forward-ref classe → self
                            return files, data, self._rest_file_error(
                                "file_source_not_found",
                                f"File di input {ip} non trovato",
                            )
                        continue
                    fh = open(ip, 'rb')
                    opened_handles.append(fh)
                    files[name] = (ip.name, fh, 'application/octet-stream')

            elif source == 'extra_input':
                entry = extra_files_cfg.get(name)
                fs_id = entry.get('file_source_id') if isinstance(entry, dict) else entry
                if not fs_id:
                    if required:
                        # B3-B.5b rewrite #3/8: forward-ref classe → self
                        return files, data, self._rest_file_error(
                            "internal",
                            f"Campo file extra '{name}' richiesto ma non configurato sul nodo",
                        )
                    continue
                extra_path = self._resolve_file_source_id_path(fs_id, ctx=ctx)
                if not extra_path:
                    if required:
                        # B3-B.5b rewrite #4/8: forward-ref classe → self
                        return files, data, self._rest_file_error(
                            "file_source_not_found",
                            f"Campo file extra '{name}': file_source '{fs_id}' non trovato "
                            f"(verifica che il file esista ancora nel catalogo e sia nel tuo gruppo)",
                        )
                    logger.warning(
                        f"rest_file extra_input '{name}': file_source {fs_id} "
                        f"non risolvibile, saltato"
                    )
                    continue
                ep = Path(extra_path)
                fh = open(ep, 'rb')
                opened_handles.append(fh)
                files[name] = (ep.name, fh, 'application/octet-stream')

            else:
                logger.warning(
                    f"rest_file: source '{source}' non gestita per campo '{name}', saltato"
                )

        for sf in static_fields:
            name = sf.get('name')
            value = sf.get('value', '')
            ct = sf.get('content_type') or None
            if not name:
                continue
            if ct:
                payload = (
                    value
                    if isinstance(value, (bytes, bytearray))
                    else str(value).encode('utf-8')
                )
                files[name] = (name, payload, ct)
            else:
                data[name] = '' if value is None else str(value)

        return files, data, None

    def _parse_rest_file_config(
        self, api_config: Dict
    ) -> tuple:
        """Valida e normalizza la config di un rest_file.

        Ritorna `(parsed_dict, None)` se OK, `(None, error_dict)` se
        invalida. `error_dict` ha il formato `{success: False, error: ...}`
        atteso dai caller di `_execute_rest_file`.

        Controlli:
        - `endpoint.url` obbligatorio
        - `endpoint.method` deve essere POST (unico supportato per rest_file)
        - `_validate_outbound_url(url)` per SSRF guard
        - `response.format` deve essere `binary_file` (unico supportato)
        """
        endpoint = api_config.get('endpoint', {}) or {}
        url = endpoint.get('url')
        method = (endpoint.get('method') or 'POST').upper()
        if not url:
            # B3-B.5b rewrite #5/8: forward-ref classe → self
            return None, self._rest_file_error(
                "internal", "Endpoint URL mancante nella configurazione",
            )
        if method != 'POST':
            # B3-B.5b rewrite #6/8: forward-ref classe → self
            return None, self._rest_file_error(
                "internal",
                f"Metodo {method} non supportato per rest_file (usare POST)",
            )

        ssrf_error = self._validate_outbound_url(url)
        if ssrf_error:
            logger.warning(f"rest_file SSRF block: {url} -> {ssrf_error}")
            # B3-B.5b rewrite #7/8: forward-ref classe → self
            return None, self._rest_file_error(
                "remote_processor_error",
                f"URL non consentito: {ssrf_error}",
            )

        response_cfg = api_config.get('response', {}) or {}
        response_format = response_cfg.get('format', 'binary_file')
        if response_format != 'binary_file':
            # B3-B.5b rewrite #8/8: forward-ref classe → self
            return None, self._rest_file_error(
                "internal",
                f"response.format '{response_format}' non supportato (usare 'binary_file')",
            )
        output_ext = response_cfg.get('output_extension') or '.bin'
        if not output_ext.startswith('.'):
            output_ext = '.' + output_ext

        request_cfg = api_config.get('request', {}) or {}
        # IMPORTANTE: NON fare fallback su `options.timeout` qui. Quel
        # campo è il timeout "richieste API generiche" (default 30s nel
        # form frontend) e per rest_file è fuorviante: un'operazione
        # file-to-file può durare minuti (es. imdf.plenora.it/api/process
        # che genera IMDF da un DXF). Il canonico è `request.timeout`;
        # se assente, default 600s (10 min).
        timeout = int(request_cfg.get('timeout') or 600)

        return {
            'url': url,
            'method': method,
            'file_fields': request_cfg.get('file_fields') or [],
            'static_fields': request_cfg.get('static_fields') or [],
            'timeout': timeout,
            'output_ext': output_ext,
        }, None

    @staticmethod
    def _rest_file_error(error_kind: str, message: str, *, user_error: bool = True) -> Dict:
        """Return dict standard per errori _execute_rest_file con taxonomy
        `error_kind` (F5 primo batch, 2026-04-24).

        error_kind validi:
          - 'file_source_not_found'   — file_source_id referenziato non esiste in DB
          - 'file_source_forbidden'   — cross-tenant access denied
          - 'remote_processor_error'  — HTTP 4xx/5xx, timeout, connessione upstream
          - 'internal'                — bug o catch-all inatteso (user_error=False)
        """
        return {
            "success": False,
            "user_error": user_error,
            "error_kind": error_kind,
            "error": message,
        }

    def _execute_rest_file(self, node_data: Dict, api_config: Dict,
                           nodes: List[Dict], edges: List[Dict],
                           node_id: str, project_id: str,
                           node_results: Optional[Dict] = None,
                           *,
                           ctx: Optional[ArricchisciExecutionContext] = None) -> Dict:
        """
        Esegue un connector REST di tipo "Processore File" (connection_type='rest_file').

        Pattern: invia un file (o piu') in multipart/form-data a un servizio HTTP
        remoto e riceve un file binario come risposta. A differenza di
        enricher/generator che lavorano su DataFrame riga per riga, questo pattern
        lavora file->file via HTTP.

        Config attesa in ``api_config``:
            endpoint: { url, method }                        # POST
            auth: {...}                                      # usa _prepare_headers
            request:
              body_type: "multipart"                         # fisso per ora
              file_fields: [ { name, source, required } ]    # source='node_input'
              static_fields: [ { name, value, content_type } ]
              timeout: 600                                   # override opzionale
            response:
              format: "binary_file"                          # fisso per ora
              output_extension: ".zip"                       # estensione file output

        Il campo file con ``source='node_input'`` riceve il file dal file_source
        collegato in input al nodo del canvas (``_resolve_file_source_path``).

        I campi ``static_fields`` con ``content_type`` sono inviati come file
        in-memory (utile per JSON inline tipo ``styles`` e ``mapping``). Senza
        content_type sono inviati come form-data semplice.

        Ritorna un dict con ``is_file_result=True``, ``output_path``,
        ``metadata`` cosi' il caller del canvas arricchisci lo tratta come
        un generico risultato file.
        """
        import time
        import tempfile
        import requests

        start_time = time.time()

        parsed, err = self._parse_rest_file_config(api_config)
        if err is not None:
            return err
        url = parsed['url']
        method = parsed['method']
        file_fields = parsed['file_fields']
        static_fields = parsed['static_fields']
        timeout = parsed['timeout']
        output_ext = parsed['output_ext']

        # Fix-D narrow #20: clausola ristretta alle classi domain-safe
        # sollevate da `_prepare_headers`:
        #  - KeyError: campo auth mancante (es. api_key, client_secret)
        #  - ValueError: formato auth invalido / auth_type non riconosciuto
        #  - requests.RequestException: fallimento del fetch OAuth2 token
        # Eccezioni fuori tupla (bug interno) fluiscono al safety-net
        # outer di _execute_rest_file (catch #22c, ancora broad in
        # questo commit).
        try:
            headers = self._prepare_headers(api_config.get('auth', {}) or {}, {})
        except (KeyError, ValueError, requests.RequestException) as e:
            return self._rest_file_error(
                "internal",
                f"Preparazione auth fallita: {e}",
            )

        # Formato di materializzazione per input da upstream DataFrame.
        # Default xlsx; override via node_data.materialize_format o
        # api_config.request.materialize_format.
        _req_cfg = api_config.get('request', {}) or {}
        materialize_format = (
            node_data.get('materialize_format')
            or _req_cfg.get('materialize_format')
            or 'xlsx'
        )
        # Fix-D narrow #21: clausola ristretta alle classi domain-safe
        # prodotte dalla catena di risoluzione input:
        #  - ValueError: messaggi domain gia' safe ("Accesso negato:",
        #    "Contesto di sicurezza...", "Errore progetto sorgente...")
        #  - SourceLoadingError: errore dal SourceLoader cross-tenant
        #    (tecnicamente gia' wrappato in ValueError da
        #    _resolve_file_source_path, incluso come belt-and-suspenders)
        #  - FileSourceResolutionError: da Fix-A (lookup DB o scrittura
        #    cache fallita), messaggio safe-by-construction
        # Eccezioni fuori tupla fluiscono al safety-net outer (#22c).
        from app.core.artifacts import SourceLoadingError
        try:
            input_path, _owns_materialized_input = self._resolve_input_path_for_file_processor(
                nodes, edges, node_id,
                node_results=node_results,
                materialize_format=materialize_format,
                ctx=ctx,
            )
        except (ValueError, SourceLoadingError, FileSourceResolutionError) as e:
            msg = str(e)
            if "Accesso negato" in msg or "non appartiene al tuo gruppo" in msg:
                return self._rest_file_error("file_source_forbidden", msg)
            return self._rest_file_error(
                "internal",
                f"Risoluzione input fallita: {msg}",
            )

        # `extra_files` e' il dict {field_name: file_source_id} salvato sul
        # nodo api_enrich quando l'utente carica inline file aggiuntivi
        # (es. Excel di arricchimento per il servizio IMDF). Separato dal
        # file principale che arriva via edge.
        extra_files_cfg = node_data.get('extra_files') or {}
        opened_handles: List = []

        # Pre-dichiarazione per il finally: se l'eccezione capita prima
        # del mkdtemp queste variabili devono comunque esistere.
        tmp_exec_dir: Optional[Path] = None
        _cleanup_tmp = True

        try:
            files, data, build_err = self._build_rest_file_multipart(
                file_fields=file_fields,
                static_fields=static_fields,
                input_path=input_path,
                extra_files_cfg=extra_files_cfg,
                opened_handles=opened_handles,
                ctx=ctx,
            )
            if build_err is not None:
                return build_err

            # ── 5. Directory temp output ──────────────────────────────────
            # tmp_exec_dir viene cleanup dal `finally` se _cleanup_tmp
            # resta True. Sul ramo success lo settiamo a False perche'
            # restituiamo un output_path al suo interno e il chiamante
            # e' responsabile del cleanup downstream.
            tmp_exec_dir = Path(tempfile.mkdtemp(prefix="plenora_exec_"))
            _cleanup_tmp = True
            output_filename = f"output{output_ext}"
            output_path = tmp_exec_dir / output_filename

            logger.info(
                f"REST File Processor: POST {url} "
                f"files={list(files.keys())} data={list(data.keys())} timeout={timeout}s"
            )

            # ── 6. Chiamata HTTP ──────────────────────────────────────────
            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
                stream=True,
            )

            if response.status_code >= 400:
                return self._rest_file_error(
                    "remote_processor_error",
                    f"HTTP {response.status_code}: {self._parse_http_error_body(response)}",
                )

            total_bytes, download_timed_out = self._download_binary_response(
                response, output_path, start_time, timeout
            )

            if download_timed_out:
                return self._rest_file_error(
                    "remote_processor_error",
                    f"Timeout download response dopo {timeout}s "
                    f"(ricevuti {total_bytes // 1024} KB parziali) da {url}",
                )

            if total_bytes == 0:
                return self._rest_file_error(
                    "remote_processor_error",
                    "Risposta vuota dal servizio remoto",
                )

            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(f"REST File Processor: output {output_path} ({total_bytes // 1024} KB, {elapsed_ms:.0f}ms)")

            _cleanup_tmp = False  # ramo success: caller gestisce tmp_exec_dir
            return {
                "is_file_result": True,
                "success": True,
                "output_path": str(output_path),
                "execution_time_ms": round(elapsed_ms, 1),
                "metadata": {
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "content_length": total_bytes,
                },
            }

        except requests.Timeout:
            return self._rest_file_error(
                "remote_processor_error",
                f"Timeout dopo {timeout}s chiamando {url}",
            )
        except requests.RequestException as e:
            return self._rest_file_error(
                "remote_processor_error",
                f"Errore HTTP: {e}",
            )
        except ValueError as e:
            # F5 primo batch (2026-04-24): un ValueError con prefisso
            # "Accesso negato" arriva da `_assert_file_source_group_access`
            # (chiamato da `_resolve_file_source_id_path` in
            # `_build_rest_file_multipart` per extra_input). Senza questo
            # catch specifico cadrebbe nel safety-net sotto e risulterebbe
            # "internal" opaco — ma e' un denial cross-tenant semantico.
            msg = str(e)
            if "Accesso negato" in msg or "non appartiene al tuo gruppo" in msg:
                return self._rest_file_error("file_source_forbidden", msg)
            # Altri ValueError imprevisti fluiscono al safety-net
            logger.error(f"[CRASH] rest_file ValueError inatteso: {e}", exc_info=True)
            return self._rest_file_error(
                "internal",
                "Errore interno nel processore file remoto",
                user_error=False,
            )
        except Exception as e:
            # Fix-D safety-net #22c: copre eccezioni inattese dentro il
            # main try (build multipart, mkdtemp, download response,
            # HTTP path oltre Timeout/RequestException). Messaggio safe
            # generico per il FE; il dettaglio tecnico resta nel log
            # (logger.error + exc_info=True).
            # NOTA: i catch narrow #20 (auth prep) e #21 (input resolve)
            # sono FUORI da questo try. Eccezioni inattese in quei due
            # step propagano fuori dalla funzione e vengono raccolte
            # dal boundary top-level di `execute_enrichment` (Fix-C,
            # commit a10774f) con messaggio "Errore interno durante
            # l'arricchimento". Un'eventuale estensione della safety-net
            # locale a #20/#21 e' debito separato (vedi checkpoint).
            logger.error(f"[CRASH] rest_file: {e}", exc_info=True)
            return self._rest_file_error(
                "internal",
                "Errore interno nel processore file remoto",
                user_error=False,
            )
        finally:
            for fh in opened_handles:
                try:
                    fh.close()
                except Exception as _fh_err:
                    logger.debug(
                        f"rest_file: fh.close() best-effort fallito: {_fh_err}"
                    )
            # Cleanup tmp_exec_dir su tutti i rami errore: senza questo
            # ogni exception lascerebbe una dir `plenora_exec_*` su disco,
            # con leak progressivo nei worker Celery long-running.
            try:
                if _cleanup_tmp and tmp_exec_dir is not None and tmp_exec_dir.exists():
                    import shutil as _shutil_cleanup
                    _shutil_cleanup.rmtree(tmp_exec_dir, ignore_errors=True)
            except Exception as _rm_err:
                logger.debug(
                    f"rest_file: cleanup tmp_exec_dir best-effort fallito: {_rm_err}"
                )
            # Cleanup del file temporaneo creato per materializzare un
            # DataFrame upstream (branch api_enrich → rest_file). Solo se
            # e' stato creato qui; non tocchiamo path provenienti da
            # file_source o da un altro file-processor a monte.
            # FS-5: per il branch in-memory (input_path e' tuple bytes+filename)
            # _owns_materialized_input e' sempre False. Guardia contro
            # un unlink su tuple (che fallirebbe) in caso di future evoluzioni.
            try:
                if _owns_materialized_input and input_path and not isinstance(input_path, tuple):
                    Path(input_path).unlink(missing_ok=True)
            except Exception as _unlink_err:
                logger.debug(
                    f"rest_file: unlink materialized input best-effort "
                    f"fallito: {_unlink_err}"
                )

    # =========================================================================
    # FILE_SOURCE RESOLUTION
    # Materializzazione file_source dal DB blob (cache filesystem atomica),
    # cross-tenant fail-closed, edge-based input path resolution per
    # file-processor nodes.
    # =========================================================================

    def _assert_file_source_group_access(
        self,
        file_source_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> None:
        """Defense-in-depth: verifica che `file_source_id` sia visibile al
        gruppo del progetto corrente. Fail-closed se il contesto non e' noto.

        BE-GO-READY-TRANSFORMATION-ENGINE-019: accetta `ctx` come
        kwarg opzionale.

        BE-GO-READY-TRANSFORMATION-ENGINE-024: la catena di
        risoluzione `file_source` usa `_extract_file_source_group_id`
        (ctx-strict): se `ctx` e' fornito, `ctx.group_id` vince in
        modo assoluto e lo stato singleton legacy NON e' consultato
        — anche se `ctx.group_id is None` (in quel caso fail-closed
        esplicito sotto). Solo quando `ctx is None` (legacy
        callsite) si applica il KEEP_COMPAT fallback su
        `_current_group_id`.

        Raises:
            ValueError con 403-like message se cross-tenant.
        """
        current_group_id = _extract_file_source_group_id(self, ctx)
        if not current_group_id:
            # Se non conosciamo il gruppo corrente meglio rifiutare che
            # rischiare un leak (schedule/task context mancante e' gia' un
            # segnale sospetto di per se').
            raise ValueError(
                "Contesto di sicurezza non determinabile per file_source"
            )
        try:
            from app.shared.sharing import is_resource_visible
            if not is_resource_visible(
                "file_sources", file_source_id, current_group_id,
                is_superadmin=False,
            ):
                logger.error(
                    f"[SECURITY] Cross-tenant access blocked: file_source "
                    f"{file_source_id} non visibile al gruppo {current_group_id}",
                )
                raise ValueError(
                    f"Accesso negato: il file sorgente '{file_source_id}' "
                    f"non appartiene al tuo gruppo"
                )
        except ValueError:
            raise
        except Exception as _sec_err:
            logger.warning(
                f"Cross-tenant check file_source fallito: {_sec_err}"
            )
            raise ValueError(
                f"Errore verifica accesso file_source '{file_source_id}'"
            ) from _sec_err

    def _resolve_file_source_id_path(
        self,
        file_source_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> Optional[str]:
        """Materializza il blob di un file_source su filesystem come cache.

        DB e' sempre source of truth. Questa funzione estrae il blob dal
        DB in una cache locale (`{FILES_DIR}/{file_source_id}/{filename}`)
        perche' fiona/GDAL lavorano con path fisici. Il file in cache
        puo' essere eliminato in qualsiasi momento — viene ricreato al
        prossimo accesso.

        Flusso:
          1. Check cache: se il file esiste in cache con il nome atteso
             (da DB metadata), usalo
          2. Altrimenti estrai il blob dal DB, scrivi in modo atomico
             (.tmp + os.replace) per evitare race tra worker concorrenti

        SECURITY: cross-tenant group check prima di qualsiasi lookup.
        Senza questo, un canvas con `file_source_id` di un altro tenant
        verrebbe materializzato su disco / estratto dal blob.

        Restituisce None se il file_source non esiste o e' vuoto.
        """
        if not file_source_id:
            return None

        self._assert_file_source_group_access(file_source_id, ctx=ctx)

        import os as _os
        from app.config import settings

        # Fix-A: il broad `except Exception -> warning + return None`
        # outer e' eliminato. Errori tecnici (DB / FS) sono ora
        # classificati e sollevati come `FileSourceResolutionError`
        # con chain preservata. `return None` resta solo sui rami
        # dati legittimi (metadata assente / blob vuoto).
        try:
            row_meta = db_svc.get_file_source(file_source_id, include_data=False)
        except psycopg2.Error as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="lookup",
                reason="errore lettura metadata file_source dal DB",
                original_error=e,
            ) from e

        if not row_meta:
            return None
        expected_name = (
            row_meta.get("original_filename")
            or (row_meta.get("metadata") or {}).get("original_filename")
            or "input.bin"
        )

        source_dir = settings.get_file_source_dir(file_source_id)

        # Cache hit: se il file gia' materializzato esiste con il
        # nome atteso, riuso (evita I/O ripetuto tra task che usano
        # lo stesso file_source nello stesso nodo).
        target = source_dir / expected_name
        if target.is_file():
            return str(target.resolve())

        # Cache miss: estrai blob da DB e scrivi atomico
        try:
            row_full = db_svc.get_file_source(file_source_id, include_data=True)
        except psycopg2.Error as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="lookup",
                reason="errore lettura blob file_source dal DB",
                original_error=e,
            ) from e

        if not row_full or not row_full.get("data"):
            return None
        raw = row_full["data"]

        try:
            source_dir.mkdir(parents=True, exist_ok=True)
            out_path = source_dir / expected_name
            tmp_path = source_dir / f".{expected_name}.{_os.getpid()}.tmp"
            try:
                tmp_path.write_bytes(raw if isinstance(raw, bytes) else bytes(raw))
                _os.replace(str(tmp_path), str(out_path))
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception as _tmp_err:
                        logger.debug(
                            f"_resolve_file_source_id_path: unlink tmp_path "
                            f"best-effort fallito: {_tmp_err}"
                        )
        except OSError as e:
            raise FileSourceResolutionError(
                file_source_id=file_source_id,
                cause="materialization",
                reason="scrittura cache file_source su filesystem fallita",
                original_error=e,
            ) from e

        logger.debug(
            f"file_source {file_source_id}: blob materializzato in cache -> {out_path}"
        )
        return str(out_path.resolve())

    def _resolve_file_source_path(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        target_node_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> Optional[str]:
        """
        Risolve il path originale del file dal nodo file_source collegato.

        Cerca il nodo sorgente collegato via edge al target_node_id.
        Se è un file_source, trova il file originale nel file system.
        Se è un project_source, trova l'ultimo output del progetto.

        BE-GO-READY-TRANSFORMATION-ENGINE-019: accetta `ctx` come
        kwarg opzionale; il `group_id` viene letto da `ctx` se
        fornito (Phase 2), altrimenti fallback a singleton state
        (KEEP_COMPAT).
        """
        # Trova edge in ingresso
        input_edge = next((e for e in edges if e['target'] == target_node_id), None)
        if not input_edge:
            return None

        source_node = next((n for n in nodes if n['id'] == input_edge['source']), None)
        if not source_node:
            return None

        if source_node.get('type') == 'file_source':
            file_source_id = _nd(source_node, 'file_source_id')
            return self._resolve_file_source_id_path(file_source_id, ctx=ctx)

        elif source_node.get('type') == 'project_source':
            source_project_id = _nd(source_node, 'source_project_id')
            if not source_project_id:
                return None

            # Tenant binding fail-closed via SourceLoader: tutto il
            # source loading di api_connector passa per il loader unico,
            # niente accesso diretto ad ArtifactResolver. Senza
            # group_id rifiutiamo esplicitamente: meglio errore
            # visibile che leak silente.
            #
            # BE-GO-READY-TRANSFORMATION-ENGINE-026: ctx-strict per
            # il branch project_source. Quando `ctx` e' fornito,
            # `ctx.group_id` vince sempre — anche se None — e lo
            # stato singleton legacy NON viene mai consultato. Chiude
            # il residual TE24-R1 (`_resolve_file_source_path`
            # project_source path ancora su `_resolve_rest_file_group_id`).
            executing_group_id = _extract_project_source_group_id(self, ctx)
            if not executing_group_id:
                logger.error(
                    f"[SECURITY] _resolve_file_source_path: group_id "
                    f"corrente sconosciuto, rifiuto risoluzione "
                    f"project_source {source_project_id} (fail-closed)"
                )
                raise ValueError(
                    "Contesto di sicurezza non determinabile (group_id mancante)"
                )

            from app.core.artifacts import (
                LatestProjectArtifactRef,
                SourceLoader,
                SourceLoadingError,
            )
            try:
                return SourceLoader().resolve_project_path(
                    LatestProjectArtifactRef(project_id=source_project_id),
                    expected_group_id=executing_group_id,
                )
            except SourceLoadingError as e:
                # Cross-tenant block o progetto inesistente: hard fail
                # con messaggio che preserva il dettaglio originale del
                # loader, coerente con _load_source_project.
                logger.error(
                    f"[SECURITY] _resolve_file_source_path cross-tenant: "
                    f"{source_project_id} per group={executing_group_id}: {e}"
                )
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        return None

    def _resolve_input_path_for_file_processor(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        target_node_id: str,
        node_results: Optional[Dict] = None,
        materialize_format: str = "xlsx",
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> tuple:
        """Risolve il path di input per un nodo file-processor (rest_file)
        accettando come predecessore anche un api_enrich o un altro file-processor.

        Estende `_resolve_file_source_path` con due branch aggiuntivi quando
        `node_results` è disponibile:

        1. Upstream è un altro file-processor (`is_file_result=True`): riusa
           direttamente il suo `output_path`, così rest_file→rest_file
           funziona senza duplicare file su disco.
        2. Upstream è un api_enrich che ha prodotto un pandas.DataFrame:
           serializza il DataFrame in un file temporaneo (formato scelto dal
           nodo, default xlsx) e ritorna quel path. Il caller è responsabile
           del cleanup.

        Ritorna `(input_ref, owns_disk_file)` dove `input_ref` e' una delle seguenti:
          - `str` (path): file su disco pre-esistente (file_source / upstream
            file-processor output). NON owned: il caller non deve cancellarlo.
          - `tuple(bytes, filename)`: DataFrame upstream serializzato
            in-memory (no disk touch). No cleanup necessario (GC naturale).
          - `None`: nessun input risolvibile (il caller gestisce required/skip).

        `owns_disk_file`: True solo in casi legacy dove un tempfile e'
        stato creato e va cancellato. Dopo FS-5 (in-memory refactor del
        materialize), il branch DataFrame ritorna sempre bytes/False —
        owns=True resta solo per path backward-compat non usati oggi.

        Se non c'è un upstream utilizzabile via node_results, fa fallback al
        comportamento classico di `_resolve_file_source_path` (file_source
        / project_source), con `owns_disk_file=False`.
        """
        if node_results is not None:
            input_edge = next((e for e in edges if e.get('target') == target_node_id), None)
            if input_edge is not None:
                source_id = input_edge.get('source')
                source_node = next((n for n in nodes if n.get('id') == source_id), None)
                upstream_type = source_node.get('type') if source_node else None
                upstream_result = node_results.get(source_id)

                # Branch 1: upstream e' un altro file-processor (rest_file)
                # il cui risultato e' gia' un file su disco.
                if isinstance(upstream_result, dict) and upstream_result.get('is_file_result'):
                    out_path = upstream_result.get('output_path')
                    if out_path and Path(out_path).exists():
                        logger.info(
                            f"file-processor input: riuso output file del nodo upstream "
                            f"{source_id} ({out_path})"
                        )
                        return str(out_path), False

                # Branch 2: upstream ha prodotto un DataFrame (api_enrich o simili).
                # FS-5: serializzazione in-memory → bytes. Nessun tempfile.
                # Il caller (`_build_rest_file_multipart`) wrappa i bytes in
                # io.BytesIO per requests multipart — requests accetta ogni
                # file-like object che implementa read().
                if isinstance(upstream_result, pd.DataFrame) and upstream_type != 'file_source':
                    from app.utils.dataframe import save_dataframe_to_bytes
                    fmt = (materialize_format or 'xlsx').lower()
                    supported = {'xlsx', 'csv', 'json', 'parquet'}
                    if fmt not in supported:
                        logger.warning(
                            f"file-processor input: materialize_format {fmt!r} non supportato "
                            f"in-memory (supportati: {supported}), uso xlsx"
                        )
                        fmt = 'xlsx'
                    try:
                        data_bytes = save_dataframe_to_bytes(upstream_result, fmt)
                    except Exception as e:
                        logger.error(
                            f"file-processor input: materializzazione DataFrame upstream "
                            f"fallita per nodo {target_node_id} (fmt={fmt}): {e}"
                        )
                        raise
                    filename = f"input.{fmt}"
                    logger.info(
                        f"file-processor input: DataFrame upstream {source_id} "
                        f"materializzato in-memory (fmt={fmt}, {len(upstream_result)} righe, "
                        f"{len(data_bytes)} bytes)"
                    )
                    return (data_bytes, filename), False

        # Fallback: comportamento classico (file_source / project_source)
        return self._resolve_file_source_path(
            nodes, edges, target_node_id, ctx=ctx,
        ), False
