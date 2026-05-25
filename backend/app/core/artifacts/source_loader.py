"""
SourceLoader: dispatch unico file/project/static per il caricamento

delle sorgenti dati di una pipeline.
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
from app.core.artifacts.static_parameters import process_static_fields
from app.core.auth.visibility import is_resource_visible
from app.core.dataframes.frame_io import load_dataframe_from_artifact
from app.shared.resources.metadata import LoadedResource, ResourceMetadata

logger = get_logger(__name__)


def _source_kind_for_ref(ref: SourceRef) -> str:
    """Map a ``SourceRef`` to the ``SourceKind`` vocabulary used in ``ResourceMetadata``.

    Extracted so callers and tests can verify the mapping independently of
    the full ``load_with_metadata`` call.

    - ``"file"`` -> ``"file"``
    - ``"project"`` -> ``"project"``
    - ``"static"`` -> ``"static"`` (single-row key-value DataFrame; typically
      no canvas column definitions are available, so metadata will be ``None``
      in practice unless the caller explicitly supplies resource columns).
    - any future kind falls back to ``"file"`` (safe default until extended).
    """
    kind = ref.kind()
    if kind == "project":
        return "project"
    if kind == "static":
        return "static"
    return "file"


class SourceLoader:
    """Carica una `SourceRef` in una risorsa frame-like.

    Tre branch:
    - **project**: delega a `ArtifactResolver.materialize_latest`, con
      cross-tenant check fail-closed. Ritorna `None` se il progetto
      non ha ancora alcun risultato (il caller decide se ricalcolare
      upstream o trattarlo come errore).
    - **file**: visibility check via `is_resource_visible` enforced
      qui dentro (non dal caller); se non visibile solleva
      `SourceLoadingError`. Poi `file_source_service.load_dataframe`
      e preferenza alla risorsa geospaziale quando presente per preservare il CRS.
    - **static**: static-parameter contract su lista di campi raw, ritorna
      una riga costruita tramite helper `app.core.dataframes`.
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

    # branch: static

    def _load_static(self, fields: Optional[tuple]) -> Any:
        from app.core.dataframes.frame_io import dataframe_from_records

        if not fields:
            return dataframe_from_records([{}])

        processed = process_static_fields(list(fields))
        return dataframe_from_records([processed])

    # branch: file

    def _load_file(
        self,
        file_source_id: str,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Any:
        # Visibility check (owned/shared/superadmin) eseguito qui dentro
        # prima di qualsiasi accesso al blob: il contratto del loader e'
        # fail-closed senza delegare al caller
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

        from app.core.files.service import (
            file_source_service, FileSourceService,
        )
        try:
            # N1: propaga expected_group_id al service per il fail-closed
            # interno. Se is_superadmin, usa il sentinel di bypass — altrimenti
            # il group_id gia' validato sopra
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

        # Preferenza alla risorsa geospaziale quando presente: preserva il CRS
        # lungo le trasformazioni downstream; la meccanica concreta resta
        # confinata negli helper `app.core.dataframes`.
        return gdf if gdf is not None else df

    # branch: project

    def resolve_project_path(
        self,
        project_ref: LatestProjectArtifactRef,
        *,
        expected_group_id: str,
        is_superadmin: bool = False,
    ) -> Optional[str]:
        """Variante di `_load_project` che ritorna il path su disco
        invece della risorsa frame-like caricata.

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
            # mode="path": resolve_project_path e' chiamato dai path-only
            # adapter (REST connector) che hanno BISOGNO di un path
            # filesystem. Non default bytes.
            materialized = self._resolver.materialize_latest(
                project_ref,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
                mode="path",
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
        try:
            # mode="bytes": Post-Phase5 il loader bytes-first va via
            # `MaterializedArtifact.data` -> `load_dataframe_from_artifact(
            # data=..., format_hint=...)`. Niente tempfile bounce.
            materialized = self._resolver.materialize_latest(
                project_ref,
                expected_group_id=expected_group_id,
                is_superadmin=is_superadmin,
                mode="bytes",
            )
        except ArtifactResolutionError as e:
            # Cross-tenant block o progetto inesistente: uniformiamo
            # come `SourceLoadingError` per il caller HTTP (→ 403/404
            logger.error(
                f"[SECURITY] project_source {project_ref.project_id} non "
                f"risolvibile per group={expected_group_id}: {e}",
                op="source_load",
            )
            raise SourceLoadingError(str(e)) from e

        if materialized is None:
            # Nessun blob disponibile per il progetto: ritorna None
            # invece di sollevare. Il caller decide se ricalcolare
            # upstream o trattarlo come errore
            return None

        with materialized as m:
            if m.df is not None:
                return m.df
            # Post-Phase5: resolver default mode="bytes". Path fallback
            # solo per i ref legacy che ancora ritornano path (e.g.
            # output_path branch di RunArtifactRef).
            if m.data is not None:
                return load_dataframe_from_artifact(
                    data=m.data, format_hint=m.format,
                )
            return load_dataframe_from_artifact(
                path=m.path, format_hint=m.format,
            )


    def load_with_metadata(
        self,
        ref: SourceRef,
        *,
        expected_group_id: str,
        resource_columns: Optional[list] = None,
        is_superadmin: bool = False,
    ) -> "LoadedResource":
        """Load ``ref`` and return a typed ``LoadedResource`` envelope.

        Delegates to ``load`` for the actual data fetch (all existing
        cross-tenant and visibility guarantees apply). When
        ``resource_columns`` is supplied, constructs a ``ResourceMetadata``
        envelope from it at load time - producers no longer need to
        reconstruct it downstream from legacy ``node_columns`` dicts.

        Args:
            ref: source reference (file / project / static).
            expected_group_id: tenant binding, forwarded verbatim to
                ``load``.
            resource_columns: optional ``[{"name": str, "type": str}]``
                list. When present, ``LoadedResource.metadata`` is
                populated. When absent, ``metadata`` is ``None``.
                For static-source refs this is typically ``None`` because
                static values carry their schema in ``static_fields``,
                not in canvas column definitions.
            is_superadmin: bypass visibility check, forwarded to
                ``load``.

        Returns:
            ``LoadedResource(data=..., metadata=...)`` - data is the
            same object ``load`` would have returned; metadata is a
            ``ResourceMetadata`` built from ``resource_columns`` or
            ``None``.
        """
        data = self.load(
            ref,
            expected_group_id=expected_group_id,
            is_superadmin=is_superadmin,
        )

        metadata: Optional[ResourceMetadata] = None
        if resource_columns is not None:
            metadata = ResourceMetadata.from_columns_list(
                resource_columns,
                source_kind=_source_kind_for_ref(ref),
            )

        return LoadedResource(data=data, metadata=metadata)
