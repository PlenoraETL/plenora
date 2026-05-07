"""
Mixin SOURCE_LOADING + SSRF per `ApiConnectionService`.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.config import settings
from app.core.arricchisci._api_connection_helpers import _nd
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
)
from app.utils.dataframe import (
    filter_visible_columns,
    find_latest_output_file,
)
from app.utils.logger import get_logger


def _preview_limit_from_ctx(
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[int]:
    """helper strict per
    `preview_limit`.

    Ritorna `ctx.preview_limit` se il caller passa un context tipato,
    altrimenti `None`. **Non legge** `_preview_limit` dal singleton
    — il preview-limit pre-set sul singleton e' considerato stale
    state che NON deve influenzare il flusso `_load_file_source`
    quando il caller non ha esplicitamente dichiarato un context.

    Storia (TE25/TE30B): introdotto in -025 a fianco del legacy
    `_resolve_preview_limit` (ctx-or-singleton-fallback). Il legacy
    helper era dead code dopo che -025 ha sostituito l'unico
    callsite production: in -030B e' stato rimosso.

    Decisione di slice (audit -023/-025):
      Il `preview_limit` e' tenant-non-sensitive ma cross-execution
      sensitive: un eventual `svc._preview_limit = 100` di un
      precedente run potrebbe truncare silenziosamente l'output di
      un run successivo che NON aveva intenzione di limitare.
      Spostare la lettura sul typed boundary chiude questa race
      window.
    """
    if ctx is not None:
        return ctx.preview_limit
    return None


def _group_id_from_ctx(
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[str]:
    """helper strict

    Ritorna `ctx.group_id` se il caller passa un context tipato,
    altrimenti `None`. **Non legge** `_current_group_id` dal
    singleton — la "tenant binding via singleton mutation" non e'
    parte del contract di questo helper.

    Storia (TE19/TE23/TE30B): in -019 il legacy `_resolve_group_id`
    (ctx-or-singleton-fallback) era l'helper unico. -023 ha
    introdotto questa variante strict per il branch static. -030B
    ha promosso anche `_load_source_project` e `_load_file_source`
    a usare lo strict helper, completando la migrazione e
    rimuovendo il legacy `_resolve_group_id` come dead code.

    Decisione di slice (audit -021):
      `_load_static_values` non ha tenant binding semantico
      (processing puro di template variables come `{{NOW}}`,
      `{{TODAY}}`). La sua dipendenza pre-singleton era
      un side effect del helper legacy, non del contract della
      funzione. La rimozione del fallback per il branch static
      elimina lo stale-state coupling al singleton (un eventuale
      `svc._current_group_id` pre-set da un'altra esecuzione non
      puo' ne' deve influenzare il static).
    """
    if ctx is not None:
        return ctx.group_id
    return None

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b/B3-B.3). StructuredLogger
# wrapper supporta kwarg `op=` usato da `_assert_source_project_in_tenant`
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionSourceLoadingMixin:
    """Mixin per `ApiConnectionService`: SOURCE_LOADING / TENANCY
    (project_source, file_source, staticValues con cross-tenant
    fail-closed) + SSRF (outbound URL guard).
    """

    # SOURCE_LOADING / TENANCY
    # Wrapper sopra `app.core.artifacts.SourceLoader`. I wrapper di
    # api_connector aggiungono solo cio' che e' specifico al modulo
    # `_load_source_project`: recompute upstream + fallback filesystem
    # `_load_file_source`: preview_limit
    # tutti: `filter_visible_columns` sul DataFrame ritornato

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
        # Lazy to avoid surfacing `pg_connection` at module import time.
        from app.core.projects.db import get_project as _get_project
        src_proj = _get_project(source_project_id)
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
            from app.core.projects.service import ProjectService
            from app.core.connections.service import ConnectionService
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

    def _load_source_project(
        self,
        node_data: Dict,
        force_recalculate: bool = False,
        *,
        ctx: ArricchisciExecutionContext,
    ) -> pd.DataFrame:
        """Carica dati dal progetto sorgente via SourceLoader.

        Cross-tenant fail-closed delegato al loader (che a sua volta
        delega ad ArtifactResolver). Recompute upstream + fallback
        filesystem restano qui perche' sono specifici di api_connector
        (manipola usa orchestrator_callback).

         `ctx` e' ora un
        keyword-only **required** parameter (no default). Tutti i 3
        production callsite (`canvas_orchestrator`, `executor.preview`,
        `enrichment_task`) e tutti i test callsite passano `ctx=ctx`
        esplicito post-TE-019/-020/-030A/-030B/-031. Il `group_id`
        viene letto ESCLUSIVAMENTE da `ctx.group_id` via
        `_group_id_from_ctx(ctx)`; se `ctx.group_id is None`
        (project non trovato) il loader fail-closes con
        `ValueError("Contesto di sicurezza non determinabile...")`.
        """
        from app.core.artifacts import (
            SourceLoader,
            SourceLoadingError,
            SourceRef,
        )
        from app.utils.dataframe import load_dataframe_from_file

        # RB.#19 Step A: timing log totale del source project load
        # Aggrega tutti i sub-step (DB lookup, S3/blob fetch, temp write
        # parse) gia' loggati individualmente downstream
        import time as _time_mod
        _t0 = _time_mod.monotonic()

        source_project_id = node_data.get('source_project_id')
        if not source_project_id:
            raise ValueError("source_project_id non specificato")

        executing_group_id = _group_id_from_ctx(ctx)
        if not executing_group_id:
            raise ValueError(
                "Contesto di sicurezza non determinabile (group_id mancante)"
            )

        ref = SourceRef.from_node_data('project_source', node_data)
        loader = SourceLoader()

        df = None
        # Tentativo 1: latest output dal blob DB (skip se force_recalculate
        if not force_recalculate:
            try:
                df = loader.load(ref, expected_group_id=executing_group_id)
            except SourceLoadingError as e:
                # I caller di api_connector si aspettano ValueError
                # propaghiamo il messaggio originale del loader cosi'
                # che la distinzione fra cross-tenant, progetto
                # inesistente ed errore DB resti leggibile lato caller
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        # Path su disco usato sia per il check "directory esistente"
        # sia per il fallback filesystem. Calcolato solo se serve
        output_dir = (
            self.results_dir / source_project_id
            if self.results_dir is not None
            else None
        )

        # Tentativo 2: recompute upstream (orchestrator standalone
        if df is None and (
            force_recalculate or output_dir is None or not output_dir.exists()
        ):
            self._assert_source_project_in_tenant(
                source_project_id, executing_group_id
            )
            self._recompute_upstream_project(source_project_id, force_recalculate)

            # Re-tenta load via SourceLoader dopo recompute
            try:
                df = loader.load(ref, expected_group_id=executing_group_id)
            except SourceLoadingError as e:
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        # Tentativo 3: fallback filesystem (retention DB scaduta ecc
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

    def _load_file_source(
        self,
        node_data: Dict,
        *,
        ctx: ArricchisciExecutionContext,
    ) -> pd.DataFrame:
        """Carica dati da file source via SourceLoader.

        Visibility check + preserve GeoDataFrame (per non perdere il
        CRS lungo le trasformazioni downstream) sono garantiti dal
        loader unico.

         `ctx` e' ora un
        keyword-only **required** parameter (no default). Tutti i 3
        production callsite e tutti i test callsite passano
        `ctx=ctx` esplicito. Il `group_id` viene letto
        ESCLUSIVAMENTE da `ctx.group_id` via
        `_group_id_from_ctx(ctx)`; se `ctx.group_id is None` il
        loader fail-closes con `ValueError("Contesto di sicurezza
        non determinabile...")`.

         (slice migration):
        `preview_limit` viene letto ESCLUSIVAMENTE da
        `ctx.preview_limit` via `_preview_limit_from_ctx(ctx)`. Lo
        `svc._preview_limit` legacy non e' piu' consultato —
        elimina lo stale-state cross-execution leak. Quando
        `ctx.preview_limit is None`, il preview_limit e' considerato
        non-richiesto (no truncation).
        """
        from app.core.artifacts import (
            SourceLoader,
            SourceLoadingError,
            SourceRef,
        )

        # RB.#19 Step A: timing log totale del file source load (analogo
        # a _load_source_project). Sub-step (DB blob fetch, temp write
        # parse) gia' loggati downstream individualmente
        import time as _time_mod
        _t0 = _time_mod.monotonic()

        file_source_id = node_data.get('file_source_id')
        if not file_source_id:
            raise ValueError("file_source_id non specificato")

        executing_group_id = _group_id_from_ctx(ctx)
        if not executing_group_id:
            raise ValueError(
                "Contesto di sicurezza non determinabile (group_id mancante)"
            )

        ref = SourceRef.from_node_data('file_source', node_data)
        try:
            df = SourceLoader().load(ref, expected_group_id=executing_group_id)
        except SourceLoadingError as e:
            # I caller di api_connector si aspettano ValueError; il
            # messaggio originale del loader viene propagato come-is
            raise ValueError(str(e)) from e

        # Tronca per preview (opzionale
        # slice migration
        # sia il `preview_limit` (TE-025) sia il `group_id` (TE-030B
        # vengono ora letti SOLO dal `ctx` (typed boundary). I legacy
        # state `svc._preview_limit` / `svc._current_group_id` non
        # sono piu' consultati — eliminato lo stale-state cross
        # execution leak
        _plimit = _preview_limit_from_ctx(ctx)
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

    def _load_static_values(
        self,
        node_data: Dict,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> pd.DataFrame:
        """Carica dati da nodo StaticValues via SourceLoader.

        Le variabili template ({{NOW}}, {{TODAY}}, ecc.) vengono
        processate dentro il loader.

        **Optional** (a differenza di `_load_source_project` /
        `_load_file_source` migrati a required in -032).

        Rationale:
          1. Lo static branch e' tenant-agnostic per definizione
             (puro processing di template variables); non ha
             semantica cross-tenant da proteggere via ctx.
          2. Quando `ctx is None` o `ctx.group_id is None`, il
             callsite usa il sentinel costante `"_static_"` come
             placeholder per soddisfare la guardia del SourceLoader.
             Non c'e' fail-closed.
          3. Due regression locks dipendono dal contract
             `ctx-Optional` per asserire il sentinel behavior:
             - `tests2/security/unit/test_api_connector_source_loading_core.py::test_load_static_values_works_without_db_or_user`
             - `tests2/arricchisci/unit/test_mixin_ctx_migration_contract.py::test_load_static_values_uses_static_sentinel_when_ctx_none`
             Cambiare la signature a required romperebbe il contract.

         (slice migration):
        questa funzione NON legge piu' `_current_group_id` dal
        singleton. Usa il helper strict `_group_id_from_ctx(ctx)`
        che ritorna esclusivamente `ctx.group_id` (o `None`); il
        sentinel `"_static_"` viene applicato come placeholder
        quando il context non fornisce un group_id.
        """
        from app.core.artifacts import SourceLoader, SourceRef

        fields = _nd(node_data, 'fields') or []
        if not fields:
            logger.warning(
                "Nodo StaticValues senza campi - restituisco DataFrame con 1 riga vuota"
            )

        ref = SourceRef.from_node_data('staticValues', node_data)
        # Static branch non tocca DB ne' file: il group_id e' richiesto
        # solo dalla guardia del loader. Passiamo il group_id dal
        # context tipato se disponibile, oppure un placeholder
        # costante (lo static non ha tenant binding semantico, e'
        # processing puro di template variables
        executing_group_id = _group_id_from_ctx(ctx) or "_static_"
        return SourceLoader().load(ref, expected_group_id=executing_group_id)

    # SSRF — outbound URL guard per `_execute_rest_file`

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
