"""
Mixin SOURCE_LOADING + SSRF per `ApiConnectionService`.

Origine
=======
Estratto da `app.core.arricchisci.api_connector.ApiConnectionService`
come parte di Phase 5 Batch B3-B.4b (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-B; pattern mixin
ratificato in B2 carica/writer + B3-A arricchisci/enrichment + B3-B.2b
api_connection_management + B3-B.3 api_connection_support).

Pre-extraction — B3-B.4a sblocca il test contract
==================================================
Pre-B3-B.4a `test_api_connector_source_loading_core.py` patchava
`patch("app.core.arricchisci.api_connector.filter_visible_columns", ...)`
per intercettare il `filter_visible_columns(df, node_columns)` chiamato
da `_load_file_source` (e `_load_source_project`). Patch source-location-
bound: spostando i metodi in mixin, il `filter_visible_columns` letto
dal mixin (un'altra binding `from app.utils.dataframe import
filter_visible_columns`) NON sarebbe stato visto dal patch su
api_connector → 2 test rosso.

B3-B.4a ha modernizzato la patch verso il source canonico
`patch("app.utils.dataframe.filter_visible_columns", ...)`. Identita'
simbolica preservata: `app.utils.dataframe.filter_visible_columns is
app.core.arricchisci.api_connector.filter_visible_columns` (re-import
top-level). Post-B3-B.4a l'extraction qui non rompe alcun contratto.

Razionale del raggruppamento: 7 metodi che gestiscono tutto il boundary
"input source / network egress" di `ApiConnectionService`:

**SOURCE_LOADING / TENANCY (6 metodi)**:
  - `_assert_source_project_in_tenant`   — fail-closed pre-check
                                           cross-tenant prima di
                                           `_recompute_upstream_project`.
                                           Solleva `ValueError("Accesso
                                           negato: ...")` su mismatch
                                           o progetto inesistente.
  - `_recompute_upstream_project`        — istanzia OrchestratorService
                                           standalone e ri-esegue la
                                           pipeline upstream. Pattern
                                           standalone (no import da
                                           main.py) per worker Celery.
  - `_filesystem_fallback_load_project`  — Tentativo 3 last-resort di
                                           `_load_source_project`.
                                           [COMPAT-RESIDUE] candidato
                                           rimozione futura post-
                                           osservazione log prod.
  - `_load_source_project`               — entry point project_source.
                                           3 tentativi: blob DB →
                                           recompute upstream → fallback
                                           filesystem. Cross-tenant
                                           fail-closed via SourceLoader.
  - `_load_file_source`                  — entry point file_source.
                                           Visibility check + preserve
                                           GeoDataFrame (CRS) via
                                           SourceLoader. Preview limit
                                           opzionale.
  - `_load_static_values`                — entry point staticValues.
                                           Template variables ({{NOW}},
                                           {{TODAY}}) processate dentro
                                           il loader. Tenant binding
                                           semantico assente.

**SSRF (1 metodo, staticmethod)**:
  - `_validate_outbound_url`             — guard outbound HTTP per
                                           `_execute_rest_file`. Blocca
                                           schema non http/https,
                                           hostname mancante, IP
                                           privati/loopback/link-local
                                           (incluso AWS IMDS 169.254.*)
                                           /multicast/reserved/
                                           unspecified. DNS resolve al
                                           momento della validazione
                                           (TOCTOU vs DNS rebinding
                                           accettato per threat model
                                           "admin compromesso").

Pattern: Mixin class via MRO
============================
`ApiConnectionSourceLoadingMixin` viene ereditato da
`ApiConnectionService` in coda alla MRO:

    class ApiConnectionService(
        ApiConnectionManagementMixin,        # B3-B.2b
        ApiConnectionSupportMixin,           # B3-B.3
        ApiConnectionSourceLoadingMixin,     # B3-B.4b
    ):
        ...

I 7 metodi restano accessibili come `ApiConnectionService.<method>`
via MRO. `inspect.getsource(ApiConnectionService.<method>)` legge il
source DA QUESTO FILE.

Cross-cluster forward refs:
- `_validate_outbound_url` chiamato da `ApiConnectionService.
  _execute_rest_file` (core, non ancora estratto) → MRO trasparente.
- `_load_source_project` / `_load_file_source` / `_load_static_values`
  chiamati da `executor.py:170,173,176` (instance-level) → MRO
  trasparente.

Intra-cluster:
- `_load_source_project` → `_assert_source_project_in_tenant` (×1),
  `_recompute_upstream_project` (×1), `_filesystem_fallback_load_project`
  (×1). Tutti nello stesso mixin.

State refs (`self.<attr>`) — letti da `_init_execution_context` (core):
- `self.results_dir` (in `_load_source_project`) — settato in `__init__`
  di `ApiConnectionService`. Accesso via MRO trasparente.
- `getattr(self, '_current_group_id', None)` (in `_load_*` x3) —
  settato da `_init_execution_context` (core).
- `getattr(self, '_preview_limit', None)` (in `_load_file_source`) —
  settato da `_init_execution_context` (core).

Coupling esterno
================
- `app.utils.dataframe.filter_visible_columns` (top-level) — usato in
  `_load_source_project` e `_load_file_source`. Source canonico
  importato direttamente per evitare alias re-import (B3-B.4a
  modernization patches verso questo path).
- `app.utils.dataframe.find_latest_output_file` (top-level) — usato in
  `_filesystem_fallback_load_project`.
- `app.core.arricchisci._api_connection_helpers._nd` (B3-B.1) — usato
  in `_load_static_values`.
- `app.config.settings` (top-level) — usato in
  `_recompute_upstream_project` per `settings.DATA_DIR`.
- Lazy imports preservati bit-per-bit:
  - `_assert_source_project_in_tenant`: `from app.db import
    config_db_service as _cfg_db`.
  - `_recompute_upstream_project`: orchestrator stack (7 import lazy
    per circular import avoidance worker Celery).
  - `_filesystem_fallback_load_project`: `from app.utils.dataframe
    import load_dataframe_from_file`.
  - `_load_source_project`: `from app.core.artifacts import
    SourceLoader, SourceLoadingError, SourceRef` + `from
    app.utils.dataframe import load_dataframe_from_file`.
  - `_load_file_source`: `from app.core.artifacts import SourceLoader,
    SourceLoadingError, SourceRef`.
  - `_load_static_values`: `from app.core.artifacts import
    SourceLoader, SourceRef`.
  - `_validate_outbound_url`: `import ipaddress as _ip`, `import
    socket as _sock`, `from urllib.parse import urlparse as _urlparse`.

Coupling test contract
======================
Verificato pre-extraction (preflight B3-B.4 + cleanup B3-B.4a):
  - 0 `inspect.getsource(ApiConnectionService.<method>)` o
    `inspect.getsource(api_connector)` su questi 7 simboli.
  - 0 AST scan su `api_connector.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr(api_connector_module, "logger", ...)`.
  - **`patch("app.core.arricchisci.api_connector.filter_visible_columns")`**
    in `test_api_connector_source_loading_core.py` → **modernizzato in
    B3-B.4a** verso source canonico `app.utils.dataframe.
    filter_visible_columns`. Post-B3-B.4a estrazione safe per
    costruzione.
  - `monkeypatch.setattr(api_connector_mod.db_svc, ...)` in
    `test_init_execution_context_fail_closed.py:88,115,142` → patch
    su `db_svc` (NON su `_assert_source_project_in_tenant` che usa
    `_cfg_db = config_db_service`, modulo distinto). Safe.
  - 14 patch.object/monkeypatch.setattr instance-level (svc.<method>)
    sui 7 target → MRO trasparente. ✓
  - Riferimenti a `_load_*` in `executor.py:170,173,176` (instance-
    level) → MRO trasparente. ✓

Logger naming contract
======================
6 callsite di log distribuiti: `_assert_source_project_in_tenant` error
([SECURITY] cross-tenant + kwarg `op="load_source_project"` per
StructuredLogger), `_recompute_upstream_project` info,
`_filesystem_fallback_load_project` warning ([COMPAT-RESIDUE]),
`_load_source_project` info, `_load_static_values` warning,
`_validate_outbound_url` debug. Mixin definisce `logger` esplicito
canonico `get_logger("app.core.arricchisci.api_connector")`
(StructuredLogger wrapper, supporta kwarg `op=`; singleton stdlib
per nome → record indistinguibili dai consumer che attaccano handler /
filtrano per nome canonico, identico al pattern B3-B.2b/B3-B.3).

Behavior-preserving
===================
NESSUN cambio architetturale in B3-B.4b:
  - SOURCE_LOADING: invariati flusso 3-tentativi, fail-closed
    cross-tenant, preview_limit, filter_visible_columns post-load,
    propagazione `SourceLoadingError → ValueError`, marker
    `[COMPAT-RESIDUE]` su filesystem fallback.
  - SSRF: invariati blocchi (loopback/private/link-local/multicast/
    reserved/unspecified), DNS resolve, schema check, candidates
    deduplication via set, body bit-per-bit incluso `@staticmethod`
    decorator.
  - [COMPAT-RESIDUE] di `_filesystem_fallback_load_project` NON
    rimosso in B3-B.4b: cleanup posticipato a scan dedicato post-
    osservazione log prod.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`api_connector.py` al momento di B3-B.4b. Nessuna modifica di
logica, comment, log payload, ordine di operazione, error message,
import path, lazy import position, decorator preservato.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.config import settings
from app.core.arricchisci._api_connection_helpers import _nd
from app.utils.dataframe import (
    filter_visible_columns,
    find_latest_output_file,
)
from app.utils.logger import get_logger

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b/B3-B.3). StructuredLogger
# wrapper supporta kwarg `op=` usato da `_assert_source_project_in_tenant`.
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionSourceLoadingMixin:
    """Mixin per `ApiConnectionService`: SOURCE_LOADING / TENANCY
    (project_source, file_source, staticValues con cross-tenant
    fail-closed) + SSRF (outbound URL guard).
    """

    # =========================================================================
    # SOURCE_LOADING / TENANCY
    # Wrapper sopra `app.core.artifacts.SourceLoader`. I wrapper di
    # api_connector aggiungono solo cio' che e' specifico al modulo:
    #   - `_load_source_project`: recompute upstream + fallback filesystem
    #   - `_load_file_source`: preview_limit
    #   - tutti: `filter_visible_columns` sul DataFrame ritornato
    # =========================================================================

    def _assert_source_project_in_tenant(
        self, source_project_id: str, executing_group_id: str
    ) -> None:
        """Verifica fail-closed che `source_project_id` appartenga al
        gruppo `executing_group_id`. Solleva `ValueError("Accesso negato:
        ...")` su mismatch o progetto inesistente.

        Questo pre-check e' necessario PRIMA del recompute upstream:
        con `force_recalculate=True` (user-controlled via
        `EnrichAsyncRequest`) il Tentativo 1 con check cross-tenant via
        `SourceLoader` viene saltato, e senza questo pre-check
        l'esecuzione del pipeline di un altro tenant verrebbe triggerata
        prima del block.
        """
        from app.db import config_db_service as _cfg_db
        src_proj = _cfg_db.get_project(source_project_id)
        if not src_proj or src_proj.get("group_id") != executing_group_id:
            logger.error(
                f"[SECURITY] Cross-tenant recompute blocked: "
                f"source_project_id={source_project_id} non appartiene a "
                f"group={executing_group_id}",
                op="load_source_project",
            )
            raise ValueError(
                f"Accesso negato: il progetto sorgente "
                f"'{source_project_id}' non appartiene al tuo gruppo."
            )

    def _recompute_upstream_project(
        self, source_project_id: str, force_recalculate: bool
    ) -> None:
        """Istanzia un OrchestratorService standalone e ri-esegue la
        pipeline di `source_project_id`. Solleva `ValueError` su
        `ImportError` o `success=False`.

        Pattern standalone (no `from main import orchestrator`) perche'
        `main.py` non e' nel path del worker Celery. Il caller fa
        un'altro `loader.load` post-recompute per leggere il risultato
        appena salvato.
        """
        logger.info(f"Riesecuzione progetto upstream: {source_project_id}")
        try:
            from app.core.orchestrator.service import OrchestratorService
            from app.services.project_service import ProjectService
            from app.services.connection_service import ConnectionService
            from app.core.estrai.query_generator import QueryGenerator
            from app.db.query_executor import QueryExecutor
            from app.core.features.features_service import FeaturesService
            from app.utils.dataframe import save_dataframe as _save_df

            _proj_svc = ProjectService()
            _conn_svc = ConnectionService()
            _generator = QueryGenerator()
            _executor = QueryExecutor(
                results_dir=settings.DATA_DIR / "temp",
                save_function=_save_df,
            )
            _features = FeaturesService(save_function=_save_df)
            _orchestrator = OrchestratorService(
                project_service=_proj_svc,
                executor=_executor,
                generator=_generator,
                connection_service=_conn_svc,
                features_service=_features,
            )
            upstream_result = _orchestrator.execute_pipeline(
                source_project_id, force_recalculate=force_recalculate
            )
        except ImportError as _ie:
            raise ValueError(
                f"Impossibile importare orchestrator per ricalcolo: {_ie}"
            ) from _ie

        if not upstream_result.get('success'):
            raise ValueError(
                f"Errore progetto sorgente: {upstream_result.get('message')}"
            )

    def _filesystem_fallback_load_project(
        self, source_project_id: str, output_dir
    ) -> pd.DataFrame:
        """Carica l'ultimo output di `source_project_id` dal filesystem.

        Tentativo 3 (last resort) di `_load_source_project`: usato quando
        ne' il blob DB ne' il recompute upstream hanno prodotto un df.
        Tipico di progetti pre-R#1 che avevano output solo su disco,
        mai migrato al DB blob. Oggi `save_result_blob` cancella sempre
        il file temp dopo il save, quindi il percorso "file su disco
        ma blob DB assente" non dovrebbe piu' accadere per progetti
        nuovi. Il fallback resta come compat residuo — se non scatta
        mai per un periodo di osservazione puo' essere rimosso.

        Emette `logger.warning` con tag `[COMPAT-RESIDUE]` quando lo
        scatta: grep-ami i log di prod per verificare se e' ancora vivo.
        """
        from app.utils.dataframe import load_dataframe_from_file

        if output_dir is None:
            raise ValueError(f"Nessun output per progetto '{source_project_id}'")
        output_file = find_latest_output_file(output_dir)
        if not output_file:
            raise ValueError(f"Nessun output per progetto '{source_project_id}'")

        logger.warning(
            f"[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project "
            f"scattato per project_id={source_project_id} file={output_file} — "
            f"questo fallback e' pensato per progetti pre-R#1 non migrati al "
            f"DB blob. Se scatta regolarmente in prod, indagare."
        )
        return load_dataframe_from_file(output_file)

    def _load_source_project(self, node_data: Dict, force_recalculate: bool = False) -> pd.DataFrame:
        """Carica dati dal progetto sorgente via SourceLoader.

        Cross-tenant fail-closed delegato al loader (che a sua volta
        delega ad ArtifactResolver). Recompute upstream + fallback
        filesystem restano qui perche' sono specifici di api_connector
        (manipola usa orchestrator_callback).
        """
        from app.core.artifacts import (
            SourceLoader,
            SourceLoadingError,
            SourceRef,
        )
        from app.utils.dataframe import load_dataframe_from_file

        # RB.#19 Step A: timing log totale del source project load.
        # Aggrega tutti i sub-step (DB lookup, S3/blob fetch, temp write,
        # parse) gia' loggati individualmente downstream.
        import time as _time_mod
        _t0 = _time_mod.monotonic()

        source_project_id = node_data.get('source_project_id')
        if not source_project_id:
            raise ValueError("source_project_id non specificato")

        executing_group_id = getattr(self, '_current_group_id', None)
        if not executing_group_id:
            raise ValueError(
                "Contesto di sicurezza non determinabile (group_id mancante)"
            )

        ref = SourceRef.from_node_data('project_source', node_data)
        loader = SourceLoader()

        df = None
        # Tentativo 1: latest output dal blob DB (skip se force_recalculate)
        if not force_recalculate:
            try:
                df = loader.load(ref, expected_group_id=executing_group_id)
            except SourceLoadingError as e:
                # I caller di api_connector si aspettano ValueError;
                # propaghiamo il messaggio originale del loader cosi'
                # che la distinzione fra cross-tenant, progetto
                # inesistente ed errore DB resti leggibile lato caller.
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        # Path su disco usato sia per il check "directory esistente"
        # sia per il fallback filesystem. Calcolato solo se serve.
        output_dir = (
            self.results_dir / source_project_id
            if self.results_dir is not None
            else None
        )

        # Tentativo 2: recompute upstream (orchestrator standalone)
        if df is None and (
            force_recalculate or output_dir is None or not output_dir.exists()
        ):
            self._assert_source_project_in_tenant(
                source_project_id, executing_group_id
            )
            self._recompute_upstream_project(source_project_id, force_recalculate)

            # Re-tenta load via SourceLoader dopo recompute.
            try:
                df = loader.load(ref, expected_group_id=executing_group_id)
            except SourceLoadingError as e:
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        # Tentativo 3: fallback filesystem (retention DB scaduta ecc.)
        if df is None:
            df = self._filesystem_fallback_load_project(
                source_project_id, output_dir
            )

        # Post-processing: filtro colonne visibili
        node_columns = node_data.get('columns', [])
        df = filter_visible_columns(df, node_columns)

        duration_ms = round((_time_mod.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] load_source_project op=load_total "
            f"project_id={source_project_id} rows={len(df)} cols={len(df.columns)} "
            f"force_recalculate={force_recalculate} duration_ms={duration_ms}"
        )
        logger.info(f"Caricato progetto {source_project_id}: {len(df)} righe")
        return df

    def _load_file_source(self, node_data: Dict) -> pd.DataFrame:
        """Carica dati da file source via SourceLoader.

        Visibility check + preserve GeoDataFrame (per non perdere il
        CRS lungo le trasformazioni downstream) sono garantiti dal
        loader unico.
        """
        from app.core.artifacts import (
            SourceLoader,
            SourceLoadingError,
            SourceRef,
        )

        # RB.#19 Step A: timing log totale del file source load (analogo
        # a _load_source_project). Sub-step (DB blob fetch, temp write,
        # parse) gia' loggati downstream individualmente.
        import time as _time_mod
        _t0 = _time_mod.monotonic()

        file_source_id = node_data.get('file_source_id')
        if not file_source_id:
            raise ValueError("file_source_id non specificato")

        executing_group_id = getattr(self, '_current_group_id', None)
        if not executing_group_id:
            raise ValueError(
                "Contesto di sicurezza non determinabile (group_id mancante)"
            )

        ref = SourceRef.from_node_data('file_source', node_data)
        try:
            df = SourceLoader().load(ref, expected_group_id=executing_group_id)
        except SourceLoadingError as e:
            # I caller di api_connector si aspettano ValueError; il
            # messaggio originale del loader viene propagato come-is.
            raise ValueError(str(e)) from e

        # Tronca per preview (opzionale)
        _plimit = getattr(self, '_preview_limit', None)
        if _plimit and isinstance(_plimit, int) and _plimit > 0:
            df = df.head(_plimit)

        # Post-processing: filtro colonne visibili
        node_columns = node_data.get('columns', [])
        df = filter_visible_columns(df, node_columns)

        duration_ms = round((_time_mod.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] load_file_source op=load_total "
            f"file_source_id={file_source_id} rows={len(df)} cols={len(df.columns)} "
            f"duration_ms={duration_ms}"
        )
        return df

    def _load_static_values(self, node_data: Dict) -> pd.DataFrame:
        """Carica dati da nodo StaticValues via SourceLoader.

        Le variabili template ({{NOW}}, {{TODAY}}, ecc.) vengono
        processate dentro il loader.
        """
        from app.core.artifacts import SourceLoader, SourceRef

        fields = _nd(node_data, 'fields') or []
        if not fields:
            logger.warning(
                "Nodo StaticValues senza campi - restituisco DataFrame con 1 riga vuota"
            )

        ref = SourceRef.from_node_data('staticValues', node_data)
        # Static branch non tocca DB ne' file: il group_id e' richiesto
        # solo dalla guardia del loader. Passiamo il group_id corrente
        # se disponibile, oppure un placeholder costante (lo static
        # non ha tenant binding semantico, e' processing puro di
        # template variables).
        executing_group_id = getattr(self, '_current_group_id', None) or "_static_"
        return SourceLoader().load(ref, expected_group_id=executing_group_id)

    # =========================================================================
    # SSRF — outbound URL guard per `_execute_rest_file`
    # =========================================================================

    @staticmethod
    def _validate_outbound_url(url: str) -> Optional[str]:
        """Shim REST_FILE strict-no-opt-in: SSRF guard per uscite
        outbound del flusso REST_FILE.

        PR-#35 (P1-C SSRF single-source design): delega alla primitive
        condivisa `app.shared.api_utils._check_outbound_address` con
        `allow_loopback_opt_in=False`. REST_FILE NON accetta loopback
        nemmeno con env `PLENORA_ALLOW_LOOPBACK_OUTBOUND` settata: la
        coerenza col threat model REST_FILE (file processor via HTTP
        invocato dal canvas) richiede che resti strict anche in dev/test.

        Signature `(url: str) -> Optional[str]` invariata pre-/post-fix
        per preservare:
          - call site `api_connection_rest_file.py:512`
            (`ssrf_error = self._validate_outbound_url(url)`)
          - test che fanno `monkeypatch.setattr(svc, "_validate_outbound_url",
            lambda url: None)` su istanza

        Ritorna None se URL safe, stringa con il motivo altrimenti.
        """
        from app.shared.api_utils import _check_outbound_address
        return _check_outbound_address(url, allow_loopback_opt_in=False)
