"""SourceLoader: dispatch unico file/project/static per il caricamento
delle sorgenti dati di una pipeline.

Core-level: nessuna dipendenza da `CurrentUser` o dal layer HTTP.
Accetta `expected_group_id` keyword-only **required**: ogni branch e'
fail-closed.

Il check cross-tenant per il branch `file` e' **enforced dal loader
stesso** (non delegato al caller): il caller passa un `group_id`
corretto e il loader chiama `is_resource_visible` prima di qualsiasi
materializzazione del blob.
"""

from __future__ import annotations

from typing import Any, Optional

from app.utils.logger import get_logger

from app.core.artifacts.resolver import ArtifactResolver
from app.core.artifacts.types import (
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    SourceLoadingError,
    SourceRef,
)

logger = get_logger(__name__)


class SourceLoader:
    """Carica una `SourceRef` in DataFrame (o GeoDataFrame se applicabile).

    Tre branch:
    - **project**: delega a `ArtifactResolver.materialize_latest`, con
      cross-tenant check fail-closed. Ritorna `None` se il progetto
      non ha ancora alcun risultato (il caller decide se ricalcolare
      upstream o trattarlo come errore).
    - **file**: visibility check via `is_resource_visible` enforced
      qui dentro (non dal caller); se non visibile solleva
      `SourceLoadingError`. Poi `file_source_service.load_dataframe`
      e preferenza al GeoDataFrame quando presente per preservare il CRS.
    - **static**: `process_static_fields` su lista di campi raw, ritorna
      `pd.DataFrame([...])` di una riga.
    """

    def __init__(self, artifact_resolver: Optional[ArtifactResolver] = None) -> None:
        self._resolver = artifact_resolver or ArtifactResolver()

    def load(
        self,
        ref: SourceRef,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Any:
        """Carica `ref` applicando il check cross-tenant fail-closed.

        `is_superadmin=True` bypassa il check di visibility: usato per
        utenti con ruolo superadmin che hanno accesso totale alle
        risorse di tutti i gruppi. `expected_group_id` e' comunque
        obbligatorio per defense-in-depth (anche superadmin deve avere
        un gruppo di appartenenza, anche solo per audit/log).

        Il check di visibility include automaticamente il caso "shared"
        (risorsa condivisa via `allowed_groups`/share table) — non solo
        l'ownership stretta.
        """
        if not expected_group_id:
            raise ValueError(
                "SourceLoader.load: expected_group_id obbligatorio "
                "(defense-in-depth, niente bypass silente)"
            )

        kind = ref.kind()
        if kind == "static":
            return self._load_static(ref.static_fields)
        if kind == "file":
            return self._load_file(
                ref.file_source_id,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
            )
        if kind == "project":
            return self._load_project(
                ref.project,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
            )
        # difensivo: SourceRef.__post_init__ esclude il caso
        raise ValueError(f"SourceLoader.load: kind sconosciuto {kind!r}")

    # ------------------------------------------------------------------
    # branch: static
    # ------------------------------------------------------------------

    def _load_static(self, fields: Optional[tuple]) -> Any:
        import pandas as pd

        if not fields:
            return pd.DataFrame([{}])

        from app.utils.template_variables import process_static_fields
        processed = process_static_fields(list(fields))
        return pd.DataFrame([processed])

    # ------------------------------------------------------------------
    # branch: file
    # ------------------------------------------------------------------

    def _load_file(
        self,
        file_source_id: str,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Any:
        # Visibility check (owned/shared/superadmin) eseguito qui dentro
        # prima di qualsiasi accesso al blob: il contratto del loader e'
        # fail-closed senza delegare al caller.
        from app.shared.sharing import is_resource_visible

        try:
            visible = is_resource_visible(
                "file_sources",
                file_source_id,
                expected_group_id,
                is_superadmin=is_superadmin,
            )
        except Exception as e:
            logger.error(
                f"[SECURITY] is_resource_visible esplode per "
                f"file_source={file_source_id} group={expected_group_id}: {e}",
                op="source_load",
            )
            raise SourceLoadingError(
                f"Verifica visibilita' file_source '{file_source_id}' fallita"
            ) from e

        if not visible:
            logger.error(
                f"[SECURITY] Cross-tenant access blocked: file_source "
                f"{file_source_id} non visibile al gruppo {expected_group_id}",
                op="source_load",
            )
            raise SourceLoadingError(
                f"Accesso negato: il file sorgente '{file_source_id}' "
                f"non appartiene al tuo gruppo"
            )

        from app.services.file_service import (
            file_source_service, FileSourceService,
        )
        try:
            # N1: propaga expected_group_id al service per il fail-closed
            # interno. Se is_superadmin, usa il sentinel di bypass — altrimenti
            # il group_id gia' validato sopra.
            _svc_group = (
                FileSourceService.SUPERADMIN_BYPASS
                if is_superadmin
                else expected_group_id
            )
            df, gdf = file_source_service.load_dataframe(
                file_source_id, expected_group_id=_svc_group,
            )
        except Exception as e:
            logger.error(
                f"[GESTITO] file_source {file_source_id} non materializzabile: {e}",
                op="source_load",
            )
            raise SourceLoadingError(
                f"Impossibile caricare il file sorgente '{file_source_id}'"
            ) from e

        # Preferenza al GeoDataFrame quando presente: preserva il CRS
        # lungo le trasformazioni downstream (un DataFrame piatto
        # perderebbe `geometry.crs` al primo cast).
        return gdf if gdf is not None else df

    # ------------------------------------------------------------------
    # branch: project
    # ------------------------------------------------------------------

    def resolve_project_path(
        self,
        project_ref: LatestProjectArtifactRef,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Optional[str]:
        """Variante di `_load_project` che ritorna il path su disco
        invece del DataFrame caricato.

        Pensato per consumer che leggono il file in modo custom (es.
        file processor rest_file che invia il file a un servizio
        HTTP). Stesso cross-tenant fail-closed del `load`.

        NB: il path ritornato puo' essere un tempfile materializzato
        dal blob DB e il caller e' responsabile del cleanup. Per
        cleanup automatico usare `_load_project` oppure
        `materialize_latest` direttamente in un `with` block.
        """
        if not expected_group_id:
            raise ValueError(
                "SourceLoader.resolve_project_path: expected_group_id "
                "obbligatorio (defense-in-depth, niente bypass silente)"
            )

        try:
            materialized = self._resolver.materialize_latest(
                project_ref,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
            )
        except ArtifactResolutionError as e:
            logger.error(
                f"[SECURITY] resolve_project_path: {project_ref.project_id} "
                f"non risolvibile per group={expected_group_id}: {e}",
                op="source_load",
            )
            raise SourceLoadingError(str(e)) from e

        if materialized is None:
            return None
        return str(materialized.path) if materialized.path is not None else None

    def _load_project(
        self,
        project_ref: LatestProjectArtifactRef,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Any:
        from app.utils.dataframe import load_dataframe_from_file

        try:
            materialized = self._resolver.materialize_latest(
                project_ref,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
            )
        except ArtifactResolutionError as e:
            # Cross-tenant block o progetto inesistente: uniformiamo
            # come `SourceLoadingError` per il caller HTTP (→ 403/404).
            logger.error(
                f"[SECURITY] project_source {project_ref.project_id} non "
                f"risolvibile per group={expected_group_id}: {e}",
                op="source_load",
            )
            raise SourceLoadingError(str(e)) from e

        if materialized is None:
            # Nessun blob disponibile per il progetto: ritorna None
            # invece di sollevare. Il caller decide se ricalcolare
            # upstream o trattarlo come errore.
            return None

        with materialized as m:
            if m.df is not None:
                return m.df
            return load_dataframe_from_file(m.path)

    # ------------------------------------------------------------------
    # load_with_metadata: typed envelope path (BE-GO-READY-ARCH-058-W1)
    # ------------------------------------------------------------------

    def load_with_metadata(
        self,
        ref: SourceRef,
        *,
        expected_group_id: str,
        columns_metadata: Optional[list] = None,
        is_superadmin: bool = False,
    ) -> "LoadedResource":
        """Load ``ref`` and return a typed ``LoadedResource`` envelope.

        Delegates to ``load()`` for the actual data fetch (all existing
        cross-tenant and visibility guarantees apply). When
        ``columns_metadata`` is supplied, constructs a ``ResourceMetadata``
        envelope from it at load time - producers no longer need to
        reconstruct it downstream from legacy ``node_columns`` dicts.

        Args:
            ref: source reference (file / project / static).
            expected_group_id: tenant binding, forwarded verbatim to
                ``load()``.
            columns_metadata: optional ``[{"name": str, "type": str}]``
                list. When present, ``LoadedResource.metadata`` is
                populated. When absent, ``metadata`` is ``None``.
            is_superadmin: bypass visibility check, forwarded to
                ``load()``.

        Returns:
            ``LoadedResource(data=..., metadata=...)`` - data is the
            same object ``load()`` would have returned; metadata is a
            ``ResourceMetadata`` built from ``columns_metadata`` or
            ``None``.
        """
        from app.shared.resources.metadata import LoadedResource, ResourceMetadata

        data = self.load(
            ref,
            expected_group_id=expected_group_id,
            is_superadmin=is_superadmin,
        )

        metadata: Optional[ResourceMetadata] = None
        if columns_metadata is not None:
            _kind_map = {"file": "file", "project": "project", "static": "file"}
            source_kind = _kind_map.get(ref.kind(), "file")
            metadata = ResourceMetadata.from_columns_list(
                columns_metadata,
                source_kind=source_kind,
            )

        return LoadedResource(data=data, metadata=metadata)
