"""
Mixin REST_FILE PIPELINE + FILE_SOURCE RESOLUTION per `ApiConnectionService`.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from __future__ import annotations

import re
import time
import time as _time

import io
from pathlib import Path
from typing import Optional

import requests

from app.core.artifacts import resolver as artifact_resolver_module
from app.core.artifacts import types as artifact_types_module
from app.core.dataframes.values import is_dataframe
from app.core.artifacts import (
    LatestProjectArtifactRef,
    SourceLoader,
    SourceLoadingError,
)
from app.core.dataframes.frame_io import save_dataframe_to_bytes
from app.core.files.source_loading import assert_file_source_group_access
from app.core.files.source_loading import resolve_file_source_id_bytes
# File-source path materialization lives in the files domain boundary.
# This mixin owns ctx extraction and REST-file orchestration.
from app.core.arricchisci._api_connection_helpers import _nd
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
)
from app.core.arricchisci.types import JsonObject, JsonRows
from app.core.files.exceptions import FileSourceResolutionError
from app.core.io.local_cleanup import rmtree_safe
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources import resource_caps as resource_caps_module
from app.core.results import blob_writer as blob_writer_module
from app.shared.security.tls_policy import resolve_tls_verify
from app.utils.logger import get_logger


# Sessione E-3 (2026-05-26): TENTATIVO `_ChunksFileAdapter` file-like
# sopra `dataframe_to_export_chunks` per multipart streaming via
# `requests.post(files=...)`. RIVELATOSI INEFFICACE: `requests` non
# fa streaming dei file parts — `urllib3.encode_multipart_formdata`
# chiama `fp.read()` UNA VOLTA per ogni file, materializzando il
# payload intero in RAM prima di costruire il body multipart (serve
# Content-Length header). Risultato: API "streaming" ma RAM uguale
# al pattern bytes diretto.
#
# F2(a) follow-up (2026-05-26): RIPRISTINATO `save_dataframe_to_bytes`
# (path diretto bytes) come comportamento onesto. Vero multipart
# streaming richiede:
#   - `requests_toolbelt.MultipartEncoder` (lazy-read file-like durante
#     l'invio): pacchetto separato (~50KB dep aggiuntiva)
#   - oppure encoder multipart custom + `requests.post(data=generator,
#     headers={"Content-Type": "multipart/form-data; boundary=..."})`
#     (~80 LOC custom, no nuova dep, ma server con no `Transfer-Encoding:
#     chunked` support possono rompere).
# Scope futuro: implementare l'opzione di streaming reale quando un
# caso d'uso REST-file su DataFrame upstream >500MB diventa bottleneck
# documentato in produzione.


def _extract_file_source_group_id(
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """Variante ctx-strict per la catena di risoluzione `file_source`.

    Ritorna `ctx.group_id` se il context e' presente, altrimenti `None`
    per fail-closed a valle. Non consulta stato singleton del service.

    Razionale: la catena `file_source` (visibility check + blob
    materialization) e' un boundary di security ad alto rischio
    cross-tenant.
    """
    if ctx is None:
        return None
    return ctx.group_id


def _extract_project_source_group_id(
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """Variante ctx-strict per il branch `project_source`.

    Ritorna `ctx.group_id` se il context e' presente, altrimenti `None`
    per fail-closed a valle. Non consulta stato singleton del service.

    Razionale: `SourceLoader.resolve_project_file_bytes` accetta
    `expected_group_id` come tenant binding fail-closed.
    """
    if ctx is None:
        return None
    return ctx.group_id

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b/B3-B.3/B3-B.4b
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionRestFileMixin:
    """Mixin per `ApiConnectionService`: REST_FILE PIPELINE (file-to-file
    via HTTP multipart) + FILE_SOURCE RESOLUTION (materializzazione
    file_source dal DB blob, cross-tenant fail-closed, edge-based input
    path resolution).
    """

    # REST_FILE PIPELINE
    # Pipeline: parse_config → prepare_headers → resolve_input → build_multipart
    # → requests.post(stream=True) → download_binary (dual-timeout) → cleanup
    # Error taxonomy via `_rest_file_error` (F5 primo batch, 2026-04-24

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
    def _download_binary_response_to_bytes(
        response, start_time: float, timeout: int, max_bytes: int
    ) -> tuple:
        """Streaming download del body in memoria con watchdog wall-clock
        e cap esplicito.

        Il parametro `timeout` di `requests.post` copre solo connect/
        first-byte, NON il download del body. Per un server che inizia
        a rispondere ma poi rallenta drammaticamente, il task rischia
        di durare molto oltre il timeout apparente. Il watchdog
        interrompe il download se il totale supera `timeout` secondi
        dall'avvio della chiamata (`start_time`).

        Cap: se l'accumulo dei chunk supera `max_bytes`, solleva
        `MemoryLimitExceededError` (typed signal mappato a 413 dal
        FastAPI boundary). Il cap protegge il worker da output remoti
        unbounded e blocca a monte ogni roundtrip verso storage.

        Ritorna `(data: bytes, total_bytes: int, download_timed_out: bool)`.
        Chiude sempre la response in un `finally` interno
        (`response.close` best-effort).
        """
        total_bytes = 0
        body_deadline = start_time + timeout
        download_timed_out = False
        buf = io.BytesIO()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if _time.time() > body_deadline:
                    download_timed_out = True
                    break
                if not chunk:
                    continue
                if total_bytes + len(chunk) > max_bytes:
                    raise MemoryLimitExceededError(
                        observed_bytes=total_bytes + len(chunk),
                        limit_bytes=max_bytes,
                        path_basename="rest_file_output",
                        stage="rest_file_output_oversize",
                    )
                buf.write(chunk)
                total_bytes += len(chunk)
        finally:
            try:
                response.close()
            except Exception as _close_err:
                logger.debug(
                    f"_download_binary_response_to_bytes: response.close() "
                    f"best-effort fallito: {_close_err}"
                )

        return buf.getvalue(), total_bytes, download_timed_out

    _download_binary_response = _download_binary_response_to_bytes

    def _build_rest_file_multipart(
        self,
        *,
        file_fields: JsonRows,
        static_fields: JsonRows,
        input_path: Optional[str],
        extra_files_cfg: JsonObject,
        opened_handles: list,
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
          via `_resolve_file_source_id_bytes`. Stessa logica required/skip.
        - `static_fields` con `content_type` sono inviati come file
          in-memory (per JSON inline tipo styles/mapping); altrimenti
          come form-data semplice.

        `opened_handles` e' appended in-place: il caller e' responsabile
        del close nel finally, indipendentemente da success/error.
        """
        files: JsonObject = {}
        data: JsonObject = {}

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
                    # Nessun opened_handle: BytesIO viene GC'd al return
                    # non serve close esplicito (diversamente da file handle
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
                extra_resolved = self._resolve_file_source_id_bytes(
                    fs_id, ctx=ctx,
                )
                if not extra_resolved:
                    if required:
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
                extra_bytes, extra_filename = extra_resolved
                files[name] = (
                    extra_filename, extra_bytes, 'application/octet-stream',
                )

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
        self, api_config: JsonObject
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
        # Pre-fix (P2 etl-sources HIGH): `output_extension` proveniva
        # dal canvas user-configurabile e veniva concatenato in
        # `output{output_ext}` senza sanificazione. Un valore tipo
        # `.bin/../../escape` produceva un output_path fuori da
        # tmp_exec_dir. Post-fix: sanifica l'extension a soli
        # `.<alnum>` (es. `.csv`, `.parquet`, `.bin`). Defense-in-depth:
        # tmp_exec_dir e' creata via mkdtemp con prefix random, MA la
        # sanificazione blinda anche caller futuri che bypassano la
        # tmp_exec_dir (es. test che inietta un output_path custom).
        if not re.match(r'^\.[A-Za-z0-9_]{1,16}$', output_ext):
            logger.warning(
                "Output extension non valida '%s': fallback a '.bin' "
                "per evitare path traversal.",
                output_ext,
            )
            output_ext = '.bin'

        request_cfg = api_config.get('request', {}) or {}
        # NOTE: NON fare fallback su `options.timeout` qui. Quel
        # campo è il timeout "richieste API generiche" (default 30s nel
        # form frontend) e per rest_file è fuorviante: un'operazione
        # file-to-file può durare minuti (es. imdf.plenora.it/api/process
        # che genera IMDF da un DXF). Il canonico è `request.timeout`
        # se assente, default 600s (10 min
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
    def _rest_file_error(
        error_kind: str,
        message: str,
        *,
        user_error: bool = True,
    ) -> JsonObject:
        """Return dict standard per errori _execute_rest_file con taxonomy
        `error_kind` (F5 primo batch, 2026-04-24).

        error_kind validi:
          - 'file_source_not_found' — file_source_id referenziato non esiste in DB
          - 'file_source_forbidden' — cross-tenant access denied
          - 'remote_processor_error' — HTTP 4xx/5xx, timeout, connessione upstream
          - 'internal' — bug o catch-all inatteso (user_error=False)
        """
        return {
            "success": False,
            "user_error": user_error,
            "error_kind": error_kind,
            "error": message,
        }

    def _execute_rest_file(self, node_data: JsonObject, api_config: JsonObject,
                           nodes: JsonRows, edges: JsonRows,
                           node_id: str, project_id: str,
                           node_results: Optional[JsonObject] = None,
                           *,
                           ctx: Optional[ArricchisciExecutionContext] = None) -> JsonObject:
        """
        Esegue un connector REST di tipo "Processore File" (connection_type='rest_file').

        Pattern: invia un file (o piu') in multipart/form-data a un servizio HTTP
        remoto e riceve un file binario come risposta. A differenza di
        enricher/generator che lavorano su DataFrame riga per riga, questo pattern
        lavora file->file via HTTP.

        Config attesa in ``api_config``:
            endpoint: { url, method } # POST
            auth: {...} # usa _prepare_headers
            request:
              body_type: "multipart" # fisso per ora
              file_fields: [ { name, source, required } ] # source='node_input'
              static_fields: [ { name, value, content_type } ]
              timeout: 600 # override opzionale
            response:
              format: "binary_file" # fisso per ora
              output_extension: ".zip" # estensione file output

        Il campo file con ``source='node_input'`` riceve il file dal file_source
        collegato in input al nodo del canvas (``_resolve_file_source_path``).

        I campi ``static_fields`` con ``content_type`` sono inviati come file
        in-memory (utile per JSON inline tipo ``styles`` e ``mapping``). Senza
        content_type sono inviati come form-data semplice.

        Ritorna un dict con ``is_file_result=True``, ``result_id``,
        ``filename``, ``format``, ``size_bytes``, ``metadata`` cosi' il
        caller del canvas arricchisci lo tratta come un generico
        risultato file. Post-Phase5: niente piu' ``output_path`` —
        l'output e' persistito direttamente come result_blob e i
        consumer ricaricano i bytes via ``ArtifactResolver.fetch_run_blob``.
        """
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
        # sollevate da `_prepare_headers`
        # KeyError: campo auth mancante (es. api_key, client_secret
        # ValueError: formato auth invalido / auth_type non riconosciuto
        # requests.RequestException: fallimento del fetch OAuth2 token
        # Eccezioni fuori tupla (bug interno) fluiscono al safety-net
        # outer di _execute_rest_file (catch #22c, ancora broad in
        # questo commit
        try:
            headers = self._prepare_headers(api_config.get('auth', {}) or {}, {})
        except (KeyError, ValueError, requests.RequestException) as e:
            return self._rest_file_error(
                "internal",
                f"Preparazione auth fallita: {e}",
            )

        # Formato di materializzazione per input da upstream DataFrame
        # Default xlsx; override via node_data.materialize_format o
        # api_config.request.materialize_format
        _req_cfg = api_config.get('request', {}) or {}
        materialize_format = (
            node_data.get('materialize_format')
            or _req_cfg.get('materialize_format')
            or 'xlsx'
        )
        # Fix-D narrow #21: clausola ristretta alle classi domain-safe
        # prodotte dalla catena di risoluzione input
        # ValueError: messaggi domain gia' safe ("Accesso negato:"
        # "Contesto di sicurezza...", "Errore progetto sorgente..."
        # SourceLoadingError: errore dal SourceLoader cross-tenant
        # tecnicamente gia' wrappato in ValueError da
        # _resolve_file_source_path, incluso come belt-and-suspenders
        # FileSourceResolutionError: da Fix-A (lookup DB o scrittura
        # cache fallita), messaggio safe-by-construction
        # Eccezioni fuori tupla fluiscono al safety-net outer (#22c
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
        # es. Excel di arricchimento per il servizio IMDF). Separato dal
        # file principale che arriva via edge
        extra_files_cfg = node_data.get('extra_files') or {}
        opened_handles: list = []

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

            # Post-Phase5 (REST output bytes-first): niente piu' tmp_exec_dir.
            # Lo stream del body va direttamente in memoria (con cap), poi
            # `save_result_blob_from_bytes` persiste come result_id. Caller
            # downstream legge il blob via `ArtifactResolver.fetch_run_blob`
            # — niente piu' `output_path` cross-process.
            output_filename = f"output{output_ext}"
            fmt = output_ext.lstrip('.') or 'bin'

            logger.info(
                f"REST File Processor: POST {url} "
                f"files={list(files.keys())} data={list(data.keys())} timeout={timeout}s"
            )

            # ── HTTP call ────────────────────────────────────────────────
            # Pre-fix (P1 etl-sources): `requests.post` SENZA `verify=`
            # esplicito → ignorava silenziosamente l'eventuale flag
            # `verify_ssl=False` sull'auth/api_config. Inconsistente con
            # il pattern centralizzato `tls_policy.resolve_tls_verify`
            # usato in `enrichment_retry_http` e `oauth`. Post-fix:
            # delega allo stesso helper env-aware → bypass concesso solo
            # in dev/test/opt-in, bloccato in prod.
            auth_cfg = api_config.get('auth', {}) or {}
            verify_tls = resolve_tls_verify(
                auth_cfg.get('verify_ssl', True),
                context=f"rest_file POST {url}",
            )
            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
                stream=True,
                verify=verify_tls,
            )

            if response.status_code >= 400:
                return self._rest_file_error(
                    "remote_processor_error",
                    f"HTTP {response.status_code}: {self._parse_http_error_body(response)}",
                )

            # Cap: RESOURCE_MAX_BYTES (override via env). Lo stesso cap
            # gia' usato dal Resource Engine per i loader bytes-first.
            max_bytes = resource_caps_module.resolve_resource_max_bytes()
            try:
                output_bytes, total_bytes, download_timed_out = (
                    self._download_binary_response_to_bytes(
                        response, start_time, timeout, max_bytes,
                    )
                )
            except MemoryLimitExceededError as cap_err:
                # Cap superato: rest_file output > RESOURCE_MAX_BYTES.
                # Errore controllato user-visible — il caller orchestrator
                # propaga come failure user_error.
                logger.error(
                    f"[GESTITO] REST File Processor output oversize: "
                    f"observed={cap_err.observed_bytes} cap={cap_err.limit_bytes} url={url}"
                )
                return self._rest_file_error(
                    "output_too_large",
                    f"Output del processore remoto supera il cap "
                    f"RESOURCE_MAX_BYTES ({cap_err.limit_bytes // (1024 * 1024)} MB). "
                    "Per output di queste dimensioni serve un percorso storage "
                    "streaming dedicato (non ancora supportato).",
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
            logger.info(
                f"REST File Processor: output {output_filename} "
                f"({total_bytes // 1024} KB, {elapsed_ms:.0f}ms)"
            )

            # Persistenza immediata come result_blob. Il resolver downstream
            # (`ArtifactResolver.fetch_run_blob`) ricarica i bytes verificando
            # `expected_project_id`. Per rest_file -> rest_file chain
            # questo significa un round-trip DB/MinIO in piu' rispetto al
            # passthrough disco precedente, ma evita disco-leak su worker
            # Celery long-running e uniforma il contratto di scambio
            # inter-node (sempre result_id, mai path).
            result_id = blob_writer_module.save_result_blob_from_bytes(
                project_id=project_id,
                data=output_bytes,
                filename=output_filename,
                fmt=fmt,
                rows_count=0,
                group_id=(ctx.group_id if ctx is not None else None),
            )

            return {
                "is_file_result": True,
                "success": True,
                "result_id": result_id,
                "filename": output_filename,
                "format": fmt,
                "size_bytes": total_bytes,
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
            # chiamato da `_resolve_file_source_id_bytes` in
            # `_build_rest_file_multipart` per extra_input). Senza questo
            # catch specifico cadrebbe nel safety-net sotto e risulterebbe
            # "internal" opaco — ma e' un denial cross-tenant semantico
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
            # main try (build multipart, download response HTTP path
            # oltre Timeout/RequestException). Messaggio safe generico
            # per il FE; il dettaglio tecnico resta nel log logger.error
            # + exc_info=True. Post-Phase5 (REST output bytes-first):
            # niente piu' `mkdtemp` da safety-net qui.
            # NOTA: i catch narrow #20 (auth prep) e #21 (input resolve
            # sono FUORI da questo try. Eccezioni inattese in quei due
            # step propagano fuori dalla funzione e vengono raccolte
            # dal boundary top-level di `execute_enrichment` (Fix-C
            # l'arricchimento". Un'eventuale estensione della safety-net
            # locale a #20/#21 e' debito separato (vedi checkpoint
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
            # Cleanup del file temporaneo creato per materializzare un
            # DataFrame upstream (branch api_enrich → rest_file). Solo se
            # e' stato creato qui; non tocchiamo path provenienti da
            # file_source o da un altro file-processor a monte
            # FS-5: per il branch in-memory (input_path e' tuple bytes+filename
            # _owns_materialized_input e' sempre False. Guardia contro
            # un unlink su tuple (che fallirebbe) in caso di future evoluzioni
            try:
                if _owns_materialized_input and input_path and not isinstance(input_path, tuple):
                    rmtree_safe(input_path, logger=logger)
            except Exception as _unlink_err:
                logger.debug(
                    f"rest_file: unlink materialized input best-effort "
                    f"fallito: {_unlink_err}"
                )

    # FILE_SOURCE RESOLUTION
    # Materializzazione file_source dal DB blob (cache filesystem atomica
    # cross-tenant fail-closed, edge-based input path resolution per
    # file-processor nodes

    def _assert_file_source_group_access(
        self,
        file_source_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> None:
        """Defense-in-depth: verifica che `file_source_id` sia visibile al
        gruppo del progetto corrente. Fail-closed se il contesto non e' noto.

         accetta `ctx` come
        kwarg opzionale.

         la catena di
        risoluzione `file_source` usa `_extract_file_source_group_id`
        (ctx-strict). Se `ctx` e' fornito,
        `ctx.group_id` vince in modo assoluto. Se `ctx is None`
        (o `ctx.group_id is None`), la funzione fail-closed
        esplicito SOTTO con `ValueError("Contesto di sicurezza
        non determinabile")`.

        Raises:
            ValueError con 403-like message se cross-tenant.
        """
        current_group_id = _extract_file_source_group_id(ctx)
        assert_file_source_group_access(file_source_id, current_group_id)

    def _resolve_file_source_id_bytes(
        self,
        file_source_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> Optional[tuple[bytes, str]]:
        """Risolve un file_source_id in `(bytes, filename)` RAM-only.

        Post-Phase5: il blob viene letto direttamente da DB / object
        storage senza materializzare file locali. Il caller
        (`_build_files_for_post`) lo consuma via `requests.post(files=...)`
        come tuple, niente file handle.

        SECURITY: cross-tenant group check prima di qualsiasi lookup.

        Restituisce None se il file_source non esiste o e' vuoto.
        """
        current_group_id = _extract_file_source_group_id(ctx)
        return resolve_file_source_id_bytes(
            file_source_id,
            expected_group_id=current_group_id,
        )

    def _resolve_file_source_path(
        self,
        nodes: JsonRows,
        edges: JsonRows,
        target_node_id: str,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> Optional[tuple[bytes, str]]:
        """Risolve la sorgente file collegata al target node (bytes-first).

        Returns:
            - file_source: `(bytes, filename)` via `resolve_file_source_id_bytes`.
            - project_source: `(bytes, filename)` via
              `SourceLoader.resolve_project_file_bytes`.
            - None se nessuna sorgente collegata.

        Il caller (`_build_files_for_post`) consuma il tuple via il check
        `isinstance(input_path, tuple)` (linea 272) e popola
        `files[name] = (filename, BytesIO(data), 'application/octet-stream')`
        per `requests.post(files=...)`.

        `ctx` e' il solo boundary di sicurezza per il group_id.
        Se il contesto manca, i branch file_source/project_source
        falliscono chiusi.
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
            return self._resolve_file_source_id_bytes(file_source_id, ctx=ctx)

        elif source_node.get('type') == 'project_source':
            source_project_id = _nd(source_node, 'source_project_id')
            if not source_project_id:
                return None

            # Tenant binding fail-closed via SourceLoader: tutto il
            # source loading di api_connector passa per il loader unico
            # niente accesso diretto ad ArtifactResolver. Senza
            # group_id rifiutiamo esplicitamente: meglio errore
            # visibile che leak silente
            # ctx-strict per il branch project_source. Quando `ctx` e'
            # fornito `ctx.group_id` vince sempre, anche se None.
            executing_group_id = _extract_project_source_group_id(ctx)
            if not executing_group_id:
                logger.error(
                    f"[SECURITY] _resolve_file_source_path: group_id "
                    f"corrente sconosciuto, rifiuto risoluzione "
                    f"project_source {source_project_id} (fail-closed)"
                )
                raise ValueError(
                    "Contesto di sicurezza non determinabile (group_id mancante)"
                )

            try:
                # Post-Phase5: bytes-first. Il caller `_build_files_for_post`
                # gia' accetta `tuple(bytes, filename)` come input_path
                # (vedi isinstance check a riga 272).
                return SourceLoader().resolve_project_file_bytes(
                    LatestProjectArtifactRef(project_id=source_project_id),
                    expected_group_id=executing_group_id,
                )
            except SourceLoadingError as e:
                # Cross-tenant block o progetto inesistente: hard fail
                # con messaggio che preserva il dettaglio originale del
                # loader, coerente con _load_source_project
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
        nodes: JsonRows,
        edges: JsonRows,
        target_node_id: str,
        node_results: Optional[JsonObject] = None,
        materialize_format: str = "xlsx",
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> tuple:
        """Risolve l'input per un nodo file-processor (rest_file) accettando
        come predecessore anche un api_enrich o un altro file-processor.

        Estende `_resolve_file_source_path` con due branch aggiuntivi quando
        `node_results` è disponibile:

        1. Upstream è un altro file-processor (`is_file_result=True`):
           ricarica i bytes via `ArtifactResolver.fetch_run_blob` dal
           `result_id` upstream (post-Phase5: niente piu' `output_path`
           cross-process, lo scambio inter-node passa sempre per il
           Resource Engine).
        2. Upstream è un api_enrich che ha prodotto un pandas.DataFrame:
           serializza in-memory (bytes-first), ritorna `(bytes, filename)`.

        Ritorna `(input_ref, owns_disk_file)`. `input_ref` puo' essere:
          - `tuple(bytes, filename)`: caso comune post-Phase5 (rest_file
            upstream, api_enrich DataFrame, file_source/project_source).
          - `str` (path): accettato solo come input difensivo; i branch
            correnti ritornano bytes.
          - `None`: nessun input risolvibile.

        `owns_disk_file`: False sempre post-Phase5 (no tempfile creati qui).
        """
        if node_results is not None:
            input_edge = next((e for e in edges if e.get('target') == target_node_id), None)
            if input_edge is not None:
                source_id = input_edge.get('source')
                source_node = next((n for n in nodes if n.get('id') == source_id), None)
                upstream_type = source_node.get('type') if source_node else None
                upstream_result = node_results.get(source_id)

                # Branch 1: upstream e' un altro file-processor.
                # Post-Phase5: `result_id` e' il riferimento canonico
                # (no piu' `output_path`). Ricarichiamo i bytes via
                # ArtifactResolver con `expected_project_id` per la
                # verifica cross-tenant fail-closed.
                if isinstance(upstream_result, dict) and upstream_result.get('is_file_result'):
                    upstream_result_id = upstream_result.get('result_id')
                    upstream_filename = (
                        upstream_result.get('filename') or 'upstream_output.bin'
                    )
                    if upstream_result_id and ctx is not None and ctx.project_id:
                        try:
                            blob = (
                                artifact_resolver_module.ArtifactResolver()
                                .fetch_run_blob(
                                    result_id=upstream_result_id,
                                    expected_project_id=ctx.project_id,
                                )
                            )
                        except artifact_types_module.ArtifactResolutionError as e:
                            logger.error(
                                f"[GESTITO] file-processor input: fetch_run_blob "
                                f"upstream {source_id} (result_id={upstream_result_id}) "
                                f"fallito: {e}"
                            )
                            raise ValueError(
                                f"Impossibile ricaricare output del nodo upstream "
                                f"{source_id}: {e}"
                            ) from e
                        logger.info(
                            f"file-processor input: bytes upstream {source_id} "
                            f"da result_id={upstream_result_id} "
                            f"({len(blob.data)} bytes, filename={blob.filename})"
                        )
                        return (blob.data, blob.filename or upstream_filename), False

                # Branch 2: upstream ha prodotto un DataFrame (api_enrich o simili
                # FS-5: serializzazione in-memory → bytes. Nessun tempfile
                # Il caller (`_build_rest_file_multipart`) wrappa i bytes in
                # io.BytesIO per requests multipart — requests accetta ogni
                # file-like object che implementa read
                if is_dataframe(upstream_result) and upstream_type != 'file_source':
                    fmt = (materialize_format or 'xlsx').lower()
                    supported = {'xlsx', 'csv', 'json', 'parquet'}
                    if fmt not in supported:
                        logger.warning(
                            f"file-processor input: materialize_format {fmt!r} non supportato "
                            f"in-memory (supportati: {supported}), uso xlsx"
                        )
                        fmt = 'xlsx'
                    try:
                        # F2(a) revert (2026-05-26): bytes diretto onesto.
                        # `requests.post(files=...)` materializza comunque
                        # il payload via urllib3 `_encode_files` upfront —
                        # un file-like "streaming" non riduce il peak.
                        # Vero multipart streaming = scope futuro
                        # (requests_toolbelt o encoder custom, vedi nota
                        # in testa al modulo).
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

        # Branch base: file_source / project_source.
        return self._resolve_file_source_path(
            nodes, edges, target_node_id, ctx=ctx,
        ), False
