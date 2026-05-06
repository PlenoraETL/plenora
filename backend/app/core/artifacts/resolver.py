"""ArtifactResolver: dispatch unico per materializzare un Ref in un
file su disco / DataFrame inline / blob raw.

Core-level: nessuna dipendenza da `CurrentUser` o dal layer HTTP.
Lavora con `project_id` / `group_id` espliciti, fail-closed sui
check di tenant binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger

from app.core.artifacts.types import (
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    MaterializedArtifact,
    RunArtifactRef,
    RunBlob,
)
# BE-GO-READY-RESOURCE-ENGINE-019: top-level import so the
# `default_registry` symbol is patchable on this module via
# `monkeypatch.setattr(resolver_mod, "default_registry", ...)`.
# The registry singleton is itself lazy (constructed on first
# call by `default_registry()` -- not at import time), so this
# import does not eagerly build the loader graph; it merely
# brings the function reference into the resolver's namespace.
from app.core.resources import ResourceRef, default_registry
from app.shared.exceptions import MemoryLimitExceededError

logger = get_logger(__name__)


class ArtifactResolver:
    """Risolve Ref a MaterializedArtifact.

    Niente "latest by project" implicito: chi vuole l'ultimo output
    deve usare esplicitamente `materialize_latest(LatestProjectArtifactRef)`.

    Cross-tenant fail-closed:
    - `materialize_run` rifiuta result_id appartenenti a un project_id
      diverso da quello del Ref.
    - `materialize_latest` rifiuta progetti fuori dal `expected_group_id`
      passato (con bypass `is_superadmin=True` per i ruoli amministratori).
    """

    def materialize_run(self, ref: RunArtifactRef) -> MaterializedArtifact:
        """Risolve un RunArtifactRef seguendo l'ordine di preferenza:
        inline_df → result_id (con verify) → output_path (validato).

        Solleva ArtifactResolutionError su ogni fallimento. Non torna
        None: o materializza, o fallisce hard.
        """
        if ref.inline_df is not None:
            return MaterializedArtifact(
                df=ref.inline_df,
                format=None,
                is_temporary=False,
            )

        if ref.result_id:
            return self._materialize_from_result_id(
                result_id=ref.result_id,
                expected_project_id=ref.project_id,
            )

        if ref.output_path:
            return self._materialize_from_output_path(
                output_path=ref.output_path,
                project_id=ref.project_id,
            )

        # difensivo: __post_init__ del Ref dovrebbe averlo gia' bloccato
        raise ArtifactResolutionError(
            f"RunArtifactRef per project_id={ref.project_id} senza "
            "artefatto risolvibile"
        )

    def materialize_latest(
        self,
        ref: LatestProjectArtifactRef,
        *,
        expected_group_id: Optional[str] = None,
        is_superadmin: bool = False,
    ) -> Optional[MaterializedArtifact]:
        """Risolve il latest output di un progetto cross-run.

        Tenant binding:
        - Se `expected_group_id` e' fornito, verifica che il progetto
          appartenga a quel gruppo. Cross-tenant → ArtifactResolutionError.
        - `is_superadmin=True` bypassa il check di gruppo: i superadmin
          hanno accesso totale a tutti i progetti, allineato al policy
          di `assert_project_visible`/`assert_resource_visible` usato
          dai router HTTP. Senza questo bypass il loader era piu'
          restrittivo dell'access control upstream e bloccava i casi
          legittimi di accesso cross-group da parte dei superadmin.
        - Senza `expected_group_id` il check e' saltato (uso interno
          del resolver da contesti con tenant binding gia' validato).

        NB: a differenza di `file_sources`, la tabella `projects` non
        e' "shareable" via allowed_groups (vedi `_validate_table` in
        `app/shared/sharing.py`). L'unico bypass legittimo del check
        owner-only e' quindi il ruolo superadmin.

        Ritorna `None` se il progetto esiste ma non ha alcun risultato
        (semantica compatibile con `get_result_as_tempfile`).

        BE-GO-READY-RESOURCE-ENGINE-021 (R-019.1 closure): il blob
        fetch + tempfile materialization e' delegato a
        ``default_registry().get(ref, mode='path')`` come in
        ``_materialize_from_result_id`` (019). Tenant binding:
        ``expected_group_id`` per ResourceRef arriva da
        ``row['group_id']`` (la riga `latest` per definizione e' del
        gruppo corretto -- garantito o dal SQL filter di
        ``get_latest_result(group_id=...)`` o, in superadmin,
        dall'audit-bypass del loader). ``is_superadmin=True`` viene
        passato al ResourceRef per simmetria con il check di gruppo
        upstream (cross-group reads permessi all'admin sono
        registrati nello structured log del loader).

        Loader fail-modes:
        - ``FileNotFoundError`` -> ``return None`` (semantica
          legacy "blob non disponibile" preservata).
        - ``MemoryLimitExceededError`` -> propaga al boundary 413.
        - altri fault -> wrap in ``ArtifactResolutionError(lettura
          latest blob ... fallita)`` con ``[GESTITO]`` log + chain.
        """
        from app.db.config.projects import get_project as _get_project
        from app.db.results_db_service import get_latest_result as _get_latest_result

        if expected_group_id is not None:
            project = _get_project(ref.project_id)
            if not project:
                raise ArtifactResolutionError(
                    f"Progetto {ref.project_id} non trovato"
                )
            actual_group = project.get("group_id")
            if not is_superadmin and actual_group != expected_group_id:
                raise ArtifactResolutionError(
                    f"Cross-tenant: progetto {ref.project_id} appartiene a "
                    f"group={actual_group}, atteso group={expected_group_id}"
                )

        row = _get_latest_result(ref.project_id)
        if not row:
            return None

        # ResourceRef.expected_group_id is REQUIRED non-empty. The
        # latest-row's `group_id` IS the resource tenant binding;
        # use it for the loader's SQL filter. is_superadmin is
        # threaded through so the project-level cross-group bypass
        # (already enforced upstream via the project_id project
        # check above) keeps its symmetric audit log entry on the
        # loader side.
        result_row_group_id = row.get("group_id")
        if not result_row_group_id:
            raise ArtifactResolutionError(
                f"latest result for project_id '{ref.project_id}' "
                "without group_id (data corruption or pre-tenant-"
                "binding row)"
            )

        result_id = row["id"]
        result_ref = ResourceRef(
            kind="result_blob",
            result_id=result_id,
            expected_group_id=result_row_group_id,
            # Pass project_id as the row-level project filter
            # (RESOURCE-ENGINE-007). `get_latest_result` already
            # selected by project_id, but the second filter at
            # the SQL boundary closes any race window between
            # the latest-row lookup and the blob load.
            expected_project_id=ref.project_id,
            is_superadmin=bool(is_superadmin),
        )

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            resource = default_registry().get(result_ref, mode="path")
        except FileNotFoundError:
            # Legacy semantics: missing / unavailable blob -> None
            # (NOT ArtifactResolutionError). The router/UI treats
            # None as "no latest result available"; raising would
            # 500 the request.
            return None
        except MemoryLimitExceededError:
            # Preserve the typed exception so FastAPI maps it to the
            # memory-limit 413 envelope. Wrapping here would turn an
            # operator cap into a generic artifact failure.
            raise
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lettura latest blob "
                "project_id='%s' result_id='%s' fallita: %s",
                ref.project_id, result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lettura blob latest project_id "
                f"'{ref.project_id}' fallita: {e}"
            ) from e

        fmt = row.get("format") or "csv"
        return MaterializedArtifact(
            path=resource.path,
            format=fmt,
            is_temporary=True,
        )

    def fetch_run_blob(
        self, *, result_id: str, expected_project_id: str
    ) -> RunBlob:
        """Recupera il blob raw di un result_id verificando l'ownership.

        Pensato per download HTTP che vogliono mandare il blob in
        response senza tempfile. Stessa cross-tenant policy di
        materialize_run: result_id appartenente ad altro project_id
        solleva ArtifactResolutionError.

        BE-GO-READY-RESOURCE-ENGINE-022-FETCH-RUN-BLOB-REGISTRY-MIGRATION
        (R-019.2 closure):
          La materializzazione bytes passa attraverso
          `default_registry().get(ResourceRef(kind="result_blob", ...),
          mode="bytes")` invece di `db_service.get_result_data`.
          Same row metadata lookup come prima (per filename / format /
          project_id verify); solo il fetch del payload BYTEA / object
          storage e' migrato. Mirror del pattern gia' applicato a
          `_materialize_from_result_id` da RESOURCE-ENGINE-019, ma con
          `mode="bytes"` (no tempfile) — il caller HTTP fa
          `StreamingResponse(blob.data)` direttamente.
        """
        from app.db.results_db_service import get_result as _get_result

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            row = _get_result(result_id)
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lookup result_id '%s' fallito: %s",
                result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lookup result_id '{result_id}' fallito: {e}"
            ) from e

        if not row:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non trovato"
            )
        if row.get("project_id") != expected_project_id:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non appartiene al "
                f"project_id '{expected_project_id}'"
            )

        # ResourceRef requires a non-empty `expected_group_id`.
        # The row's `group_id` IS the resource's tenant binding;
        # mirror del defensive check in _materialize_from_result_id.
        group_id = row.get("group_id")
        if not group_id:
            raise ArtifactResolutionError(
                f"result_id '{result_id}': row senza group_id "
                "(data corruption or pre-tenant-binding row)"
            )

        ref = ResourceRef(
            kind="result_blob",
            result_id=result_id,
            expected_group_id=group_id,
            # Pass project_id again so the loader's row-level
            # project filter (RESOURCE-ENGINE-007) gives a second
            # check at the SQL boundary. Tautological vs the
            # resolver-level check above but cheap.
            expected_project_id=expected_project_id,
        )

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            resource = default_registry().get(ref, mode="bytes")
        except FileNotFoundError as e:
            # Loader fail-closed: row missing OR cross-tenant OR
            # blob unavailable. Translate to the canonical Italian
            # message so existing operator log greps keep working.
            raise ArtifactResolutionError(
                f"result_id '{result_id}' senza blob dati"
            ) from e
        except MemoryLimitExceededError:
            # Preserve the typed exception for the FastAPI 413 handler.
            # The HTTP download path is still a resource read and must not
            # degrade an oversize signal into a generic artifact error.
            raise
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lettura blob result_id '%s' fallita: %s",
                result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lettura blob result_id '{result_id}' fallita: {e}"
            ) from e

        # In bytes mode the loader returns the bytes inline. A None
        # `data` means "row exists but blob unavailable" (es. object
        # storage download returned None). Per il download HTTP
        # questa condizione deve essere un errore tipato come prima
        # del migration: pre-022 ricadeva sul fail "senza blob dati".
        if not resource.data:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' senza blob dati"
            )

        return RunBlob(
            data=resource.data,
            filename=row.get("filename") or f"{result_id}",
            format=row.get("format") or "csv",
            project_id=row["project_id"],
            result_id=row["id"],
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _materialize_from_result_id(
        self, *, result_id: str, expected_project_id: str
    ) -> MaterializedArtifact:
        """Resolve a `result_id` via the Resource Engine path mode.

        BE-GO-READY-RESOURCE-ENGINE-019 (R-PILOT-017.A closure):
        the blob fetch + tempfile materialization is delegated to
        ``default_registry().get(ref, mode='path')`` -- the FIRST
        real production caller of the registry surface. Pre-019
        the path was a two-step ``db_service.get_result_data`` +
        ``self._write_temp_blob`` shim; post-019 the loader owns
        SQL preflight (013), bounded read (010), safe-prefixed
        tempfile materialization (006), and the cap envelope
        contract in one place. The resolver continues to own the
        upstream row lookup (for `format` / `group_id` metadata)
        and the cross-tenant project_id check (which produces the
        Italian operator error string the rest of the system
        depends on).
        """
        from app.db.results_db_service import get_result as _get_result

        # ResourceRef + default_registry are top-level imports on
        # this module (see header). That makes the symbols
        # patchable for tests AND keeps a single canonical lookup
        # point: production calls go through the same module-level
        # `default_registry` reference the tests monkeypatch.

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            row = _get_result(result_id)
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lookup result_id '%s' fallito: %s",
                result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lookup result_id '{result_id}' fallito: {e}"
            ) from e

        if not row:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non trovato"
            )

        # Cross-tenant binding fail-closed: niente fallback "latest by
        # project" che produrrebbe race cross-task fra due esecuzioni
        # parallele dello stesso progetto.
        if row.get("project_id") != expected_project_id:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non appartiene al "
                f"project_id '{expected_project_id}'"
            )

        # ResourceRef requires a non-empty `expected_group_id`.
        # The row's `group_id` IS the resource's tenant binding:
        # the loader uses it for the SQL filter (already verified
        # by the row lookup above; passing it through preserves
        # the typed contract and gives defense-in-depth in the
        # event of a row-mutation race between lookup and load).
        group_id = row.get("group_id")
        if not group_id:
            raise ArtifactResolutionError(
                f"result_id '{result_id}': row senza group_id "
                "(data corruption or pre-tenant-binding row)"
            )

        ref = ResourceRef(
            kind="result_blob",
            result_id=result_id,
            expected_group_id=group_id,
            # Pass the project_id again so the loader's row-level
            # project filter (RESOURCE-ENGINE-007) gives a second
            # check at the SQL boundary. Tautological vs the
            # resolver-level check above, but cheap and keeps the
            # typed contract honest.
            expected_project_id=expected_project_id,
        )

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            resource = default_registry().get(ref, mode="path")
        except FileNotFoundError as e:
            # Loader fail-closed: row missing OR cross-tenant OR
            # blob unavailable. Resolver translates to its own
            # typed exception with the canonical Italian message
            # so existing operator dashboards / log greps keep
            # working bit-for-bit.
            raise ArtifactResolutionError(
                f"result_id '{result_id}' senza blob dati"
            ) from e
        except MemoryLimitExceededError:
            # Preserve the typed exception for the FastAPI 413 handler.
            raise
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lettura blob result_id '%s' fallita: %s",
                result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lettura blob result_id '{result_id}' fallita: {e}"
            ) from e

        # Format is row metadata; the loader is byte-level only and
        # does not learn the format. The resolver attaches it to
        # the MaterializedArtifact for downstream readers
        # (load_dataframe_from_file dispatches on format/extension).
        fmt = row.get("format") or "csv"
        return MaterializedArtifact(
            path=resource.path,
            format=fmt,
            is_temporary=True,
        )

    def _materialize_from_output_path(
        self, *, output_path: str, project_id: str
    ) -> MaterializedArtifact:
        from app.config import settings
        from app.shared.path_policy import assert_path_within

        if not output_path:
            raise ArtifactResolutionError("output_path vuoto")

        # Whitelist di root validi per output_path:
        # - RESULTS_DIR/{project_id}: artifact persistito post-pipeline
        # - DATA_DIR/temp e TEMP_DIR: tempfile fresh prima del transfer
        #   nel DB blob (window in cui il path e' legittimo ma non
        #   ancora dentro RESULTS_DIR)
        # Path arbitrari dal client vengono sempre rifiutati: l'unico
        # produttore di output_path e' il backend stesso.
        # intent: resource guard (build path whitelist) / outcome: fail-closed
        # esplicito se la lista finale e' vuota. Una lista parziale (≥1 root)
        # e' ammessa — gli altri root sono tracciabili via warning log, cosi'
        # che config/FS issue siano diagnosticabili post-hoc senza confonderle
        # con path client malicious al livello `assert_path_within` a valle.
        trusted_roots = []
        try:
            results_root = (Path(settings.RESULTS_DIR).resolve() / project_id).resolve()
            trusted_roots.append(results_root)
        except Exception as e:
            logger.warning(
                f"trusted_roots build: RESULTS_DIR resolve fallito per project_id={project_id}: {e}",
                exc_info=True,
            )
        for attr in ("TEMP_DIR", "DATA_DIR"):
            try:
                base = getattr(settings, attr, None)
                if not base:
                    continue  # setting non impostato: skip legittimo, non un fallimento
                base = Path(base).resolve()
                if attr == "DATA_DIR":
                    trusted_roots.append((base / "temp").resolve())
                else:
                    trusted_roots.append(base)
            except Exception as e:
                logger.warning(
                    f"trusted_roots build: attr={attr} resolve fallito: {e}",
                    exc_info=True,
                )

        # Gate fail-closed: lista vuota = config rotta o FS unmount, non un
        # path client malicious. Raise esplicito con messaggio diagnostico
        # che distingue questo caso dal "fuori dai root" a valle.
        if not trusted_roots:
            raise ArtifactResolutionError(
                "trusted_roots build fallita: nessun root valido disponibile "
                "(verifica config RESULTS_DIR/DATA_DIR/TEMP_DIR)"
            )

        # Validazione delegata a `assert_path_within`. Convertiamo
        # `ValueError` in `ArtifactResolutionError` con messaggi
        # specifici del resolver per uniformare il tipo eccezione.
        try:
            resolved = assert_path_within(
                output_path, trusted_roots, must_exist=True
            )
        except ValueError as e:
            msg = str(e)
            if "UNC" in msg:
                raise ArtifactResolutionError(
                    f"output_path UNC non consentito: {output_path}"
                ) from e
            if "non esiste" in msg:
                raise ArtifactResolutionError(
                    f"output_path non esiste: {output_path}"
                ) from e
            if "fuori dai trusted roots" in msg:
                raise ArtifactResolutionError(
                    f"output_path '{output_path}' fuori dai root consentiti "
                    f"(RESULTS_DIR/{project_id}, DATA_DIR/temp, TEMP_DIR)"
                ) from e
            raise ArtifactResolutionError(
                f"output_path non valido: {e}"
            ) from e

        if not resolved.is_file():
            raise ArtifactResolutionError(
                f"output_path non esiste: {output_path}"
            )

        fmt = resolved.suffix.lstrip(".").lower() or None
        return MaterializedArtifact(
            path=resolved,
            format=fmt,
            is_temporary=False,
        )
