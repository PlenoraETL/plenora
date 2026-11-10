"""
Mixin SOURCE_LOADING + SSRF per `ApiConnectionService`.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from __future__ import annotations

import time as _time_mod

from typing import Any, Optional

from app.core.artifacts import SourceLoader, SourceLoadingError, SourceRef
from app.core.arricchisci._api_connection_helpers import _nd
from app.core.arricchisci.execution_context import (
    ArricchisciExecutionContext,
)
from app.core.arricchisci.types import JsonObject
from app.core.arricchisci.project_access import load_project_access_context
from app.core.dataframes.frame_io import filter_visible_columns
from app.core.output_contract import (
    ensure_dataframe_matches_contract,
    node_columns_from_contract,
)
from app.core.projects.service import ProjectService
from app.core.projects.source_freshness import get_project_source_freshness
from app.core.projects.upstream_identity import source_identity_need
from app.shared.execution.runtime_identity import guard_upstream_recompute, route_upstream_source
from app.shared.security.outbound_url import check_outbound_address
from app.utils.logger import get_logger


def _preview_limit_from_ctx(
    ctx: Optional[ArricchisciExecutionContext],
) -> Optional[int]:
    """helper strict per
    `preview_limit`.

    Ritorna `ctx.preview_limit` se il caller passa un context tipato,
    altrimenti `None`.

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
    altrimenti `None`.

    `_load_static_values` non ha tenant binding semantico: processa solo
    template variables come `{{NOW}}` e `{{TODAY}}`. La lettura dal typed
    boundary evita stale-state coupling al singleton.
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
    # `_load_source_project`: recompute upstream + loader unico
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
        src_project_ctx = load_project_access_context(source_project_id)
        if not src_project_ctx or src_project_ctx.group_id != executing_group_id:
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

    def _upstream_blob_is_stale(
        self,
        source_project_id: str,
        *,
        expected_group_id: str | None = None,
    ) -> bool:
        """True quando il canvas dell'upstream e' stato aggiornato dopo
        l'ultimo blob risultato.

        Implica che il blob non riflette piu' lo schema corrente
        (es. output_mapping rinominato, transform aggiunto). Usato da
        `_load_source_project` per scattare il recompute aggressivo
        anche senza ``force_recalculate`` dall'utente — evita che
        MANIPOLA leggesse colonne fantasma dell'ARR upstream.
        """
        try:
            freshness = get_project_source_freshness(
                source_project_id,
                expected_group_id=expected_group_id,
            )
            return bool(freshness and freshness.stale)
        except Exception as e:
            # Failure mode safe: niente staleness, flusso normale prosegue
            logger.warning(
                f"upstream_blob_is_stale check fallito per "
                f"{source_project_id}: {e}"
            )
            return False

    def _recompute_upstream_project(
        self,
        source_project_id: str,
        force_recalculate: bool,
        *,
        test_run: bool = False,
        source_name: Optional[str] = None,
    ) -> None:
        """Istanzia un OrchestratorService standalone e ri-esegue la
        pipeline di `source_project_id`. Solleva `ValueError` su
        `success=False`; gli errori di import interni restano fail-loud.

        Pattern standalone (no `from main import orchestrator`) perche'
        `main.py` non e' nel path del worker Celery. Il caller fa
        un'altro `loader.load` post-recompute per leggere il risultato
        appena salvato.
        """
        # L'unica porta del ricalcolo a monte: una prova si ferma qui, prima
        # di ogni scrittura (runtime_identity, regola madre).
        guard_upstream_recompute(
            test_run=test_run, source_name=source_name or source_project_id,
        )
        logger.info(f"Riesecuzione progetto upstream: {source_project_id}")
        from app.runtime.services import build_orchestrator_stack

        _stack = build_orchestrator_stack()
        upstream_result = _stack.orchestrator.execute_pipeline(
            source_project_id, force_recalculate=force_recalculate
        )

        if not upstream_result.get('success'):
            raise ValueError(
                f"Errore progetto sorgente: {upstream_result.get('message')}"
            )

    def _load_source_project(
        self,
        node_data: JsonObject,
        force_recalculate: bool = False,
        *,
        ctx: ArricchisciExecutionContext,
    ) -> Any:
        """Carica dati dal progetto sorgente via SourceLoader.

        Cross-tenant fail-closed delegato al loader (che a sua volta
        delega ad ArtifactResolver). Il recompute upstream resta qui
        perche' e' specifico di api_connector.

        `ctx` e' un keyword-only **required** parameter (no default). Il
        `group_id` viene letto ESCLUSIVAMENTE da `ctx.group_id` via
        `_group_id_from_ctx(ctx)`; se `ctx.group_id is None`
        (project non trovato) il loader fail-closes con
        `ValueError("Contesto di sicurezza non determinabile...")`.
        """
        # Timing log totale del source project load: aggrega sub-step gia'
        # loggati downstream.
        _t0 = _time_mod.monotonic()

        source_project_id = node_data.get('source_project_id')
        if not source_project_id:
            raise ValueError("source_project_id non specificato")

        executing_group_id = _group_id_from_ctx(ctx)
        if not executing_group_id:
            raise ValueError(
                "Contesto di sicurezza non determinabile (group_id mancante)"
            )

        # La porta unica (runtime_identity U1, U5): un progetto a monte che
        # ha bisogno di «Utente che esegue» blocca (Arricchisci non ha il
        # ricalcolo isolato): mai il suo risultato salvato non filtrato.
        route_upstream_source(
            source_project_id=source_project_id,
            need=source_identity_need(source_project_id, group_id=executing_group_id),
            isolated_upstreams=None,
        )
        ref = SourceRef.from_node_data('project_source', node_data)
        loader = SourceLoader()
        # Esecuzione di prova: nessun ricalcolo a monte (force ignorato;
        # artefatto mancante o scaduto -> guard_upstream_recompute).
        test_run = bool(getattr(ctx, "test_run", False))
        if test_run:
            force_recalculate = False
        source_name: Optional[str] = None

        df = None
        # Se il canvas upstream e' piu' recente dell'ultimo blob risultato,
        # il blob e' stale: forza recompute per non propagare dati obsoleti.
        upstream_stale = self._upstream_blob_is_stale(
            source_project_id,
            expected_group_id=executing_group_id,
        )
        if upstream_stale:
            freshness = get_project_source_freshness(
                source_project_id,
                expected_group_id=executing_group_id,
            )
            source_name = freshness.source_project_name if freshness else None
            if freshness and not force_recalculate and not test_run:
                raise ValueError(freshness.message)
            logger.info(
                f"Upstream blob stale per {source_project_id}: "
                f"recompute forzato (canvas modificato dopo l'ultimo run)"
            )

        # Tentativo 1: latest output dal blob DB (skip se
        # force_recalculate o se l'upstream e' stale)
        if not force_recalculate and not upstream_stale:
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

        # Tentativo 2: recompute upstream quando richiesto, quando lo
        # schema e' stale, o quando non esiste ancora un latest artifact.
        if df is None:
            self._assert_source_project_in_tenant(
                source_project_id, executing_group_id
            )
            # Stale upstream: ricalcolo aggressivo per evitare cache locale
            # sul blob obsoleto.
            self._recompute_upstream_project(
                source_project_id,
                force_recalculate=force_recalculate or upstream_stale,
                test_run=test_run,
                source_name=source_name,
            )

            # Re-tenta load via SourceLoader dopo recompute
            try:
                df = loader.load(ref, expected_group_id=executing_group_id)
            except SourceLoadingError as e:
                raise ValueError(
                    f"Accesso negato: progetto sorgente "
                    f"'{source_project_id}': {e}"
                ) from e

        if df is None:
            raise ValueError(f"Nessun output per progetto '{source_project_id}'")

        output_contract = ProjectService().get_project_output_contract(
            source_project_id
        )
        if not output_contract:
            if not node_data.get("columns"):
                return df
            raise ValueError(
                f"output_contract mancante per progetto sorgente "
                f"{source_project_id}"
            )
        node_columns = node_columns_from_contract(
            output_contract,
            previous_node_columns=node_data.get('columns', []),
        )
        ensure_dataframe_matches_contract(
            dataframe_columns=df.columns,
            node_columns=node_columns,
            source_project_id=source_project_id,
            dataframe=df,
        )
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
        node_data: JsonObject,
        *,
        ctx: ArricchisciExecutionContext,
    ) -> Any:
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
        `ctx.preview_limit` via `_preview_limit_from_ctx(ctx)`. Quando
        `ctx.preview_limit is None`, il preview_limit e' considerato
        non-richiesto (no truncation).
        """
        # Timing log totale del file source load; sub-step gia' loggati
        # downstream individualmente.
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

        # Tronca per preview (opzionale): `preview_limit` e `group_id` vengono
        # letti SOLO dal `ctx` typed boundary.
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
        node_data: JsonObject,
        *,
        ctx: Optional[ArricchisciExecutionContext] = None,
    ) -> Any:
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
        questa funzione usa il helper strict `_group_id_from_ctx(ctx)`
        che ritorna esclusivamente `ctx.group_id` (o `None`); il
        sentinel `"_static_"` viene applicato come placeholder
        quando il context non fornisce un group_id.
        """
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

        Delega alla primitive condivisa
        `app.core.security.outbound_url.check_outbound_address` con
        `allow_loopback_opt_in=False`. REST_FILE NON accetta loopback
        nemmeno con env `PLENORA_ALLOW_LOOPBACK_OUTBOUND` settata: la
        coerenza col threat model REST_FILE (file processor via HTTP
        invocato dal canvas) richiede che resti strict anche in dev/test.

        Signature `(url: str) -> Optional[str]` stabile per preservare:
          - call site `api_connection_rest_file.py:512`
            (`ssrf_error = self._validate_outbound_url(url)`)
          - test che fanno `monkeypatch.setattr(svc, "_validate_outbound_url",
            lambda url: None)` su istanza

        Ritorna None se URL safe, stringa con il motivo altrimenti.
        """
        return check_outbound_address(url, allow_loopback_opt_in=False)
