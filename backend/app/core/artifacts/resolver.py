"""ArtifactResolver: dispatch unico per materializzare un Ref in un
file su disco / DataFrame inline / blob raw.

Core-level: nessuna dipendenza da `CurrentUser` o dal layer HTTP.
Lavora con `project_id` / `group_id` espliciti, fail-closed sui
check di tenant binding.
"""

from __future__ import annotations

import os
import tempfile
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
        """
        import app.db.db_service as db_service

        if expected_group_id is not None:
            project = db_service.get_project(ref.project_id)
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

        row = db_service.get_latest_result(ref.project_id)
        if not row:
            return None

        blob = db_service.get_result_data(row["id"])
        if not blob:
            return None

        fmt = row.get("format") or "csv"
        tmp_path = self._write_temp_blob(blob, fmt)
        return MaterializedArtifact(
            path=tmp_path,
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
        """
        import app.db.db_service as db_service

        row = db_service.get_result(result_id)
        if not row:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non trovato"
            )
        if row.get("project_id") != expected_project_id:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' non appartiene al "
                f"project_id '{expected_project_id}'"
            )

        data = db_service.get_result_data(row["id"])
        if not data:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' senza blob dati"
            )

        return RunBlob(
            data=data,
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
        import app.db.db_service as db_service

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            row = db_service.get_result(result_id)
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

        # intent: boundary / outcome: fail-closed (broad→domain con chain preservata)
        try:
            blob = db_service.get_result_data(result_id)
        except Exception as e:
            logger.error(
                "[GESTITO] resolver lettura blob result_id '%s' fallita: %s",
                result_id, e,
                exc_info=True,
            )
            raise ArtifactResolutionError(
                f"lettura blob result_id '{result_id}' fallita: {e}"
            ) from e

        if not blob:
            raise ArtifactResolutionError(
                f"result_id '{result_id}' senza blob dati"
            )

        fmt = row.get("format") or "csv"
        tmp_path = self._write_temp_blob(blob, fmt)
        return MaterializedArtifact(
            path=tmp_path,
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
        trusted_roots = []
        try:
            results_root = (Path(settings.RESULTS_DIR).resolve() / project_id).resolve()
            trusted_roots.append(results_root)
        except Exception:
            pass
        for attr in ("TEMP_DIR", "DATA_DIR"):
            try:
                base = getattr(settings, attr, None)
                if not base:
                    continue
                base = Path(base).resolve()
                if attr == "DATA_DIR":
                    trusted_roots.append((base / "temp").resolve())
                else:
                    trusted_roots.append(base)
            except Exception:
                continue

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

    def _write_temp_blob(self, blob: bytes, fmt: str) -> Path:
        """Materializza un blob in un tempfile e ritorna il path.

        I tempfile vengono creati dentro `settings.TEMP_DIR` (se
        configurata) per coerenza con il resto del backend
        (`file_service` ecc.) — non sparpagliati nel temp di sistema.
        Fallback al default Python `tempfile` solo se TEMP_DIR non
        e' valorizzata (es. test environment minimale).

        Cleanup: errore durante `tf.write(blob)` (disk full, permission)
        triggera un unlink esplicito prima di re-raise. Sul path
        success il caller wrappa il Path ritornato in
        `MaterializedArtifact(is_temporary=True)`, il cui context
        manager garantisce il cleanup downstream.
        """
        import os as _os

        suffix = f".{fmt}" if fmt else ""
        tempfile_kwargs = {"delete": False, "suffix": suffix}
        # intent: optional feature / outcome: fail-open (TEMP_DIR config best-effort)
        try:
            from app.config import settings as _settings
            _tmp_dir = getattr(_settings, "TEMP_DIR", None)
            if _tmp_dir:
                tempfile_kwargs["dir"] = str(_tmp_dir)
        except Exception as e:
            logger.debug(
                "_write_temp_blob: TEMP_DIR config skipped, uso default tempfile: %s: %s",
                type(e).__name__, e,
            )

        tf = tempfile.NamedTemporaryFile(**tempfile_kwargs)
        path = Path(tf.name)
        try:
            tf.write(blob)
        # intent: resource guard / outcome: fail-closed (cleanup + re-raise)
        except Exception as e:
            logger.debug(
                "_write_temp_blob: tf.write fallita per %r, avvio cleanup: %s: %s",
                str(path), type(e).__name__, e,
            )
            # intent: optional feature / outcome: fail-open (close best-effort)
            try:
                tf.close()
            except Exception as _close_err:
                logger.debug(
                    "_write_temp_blob: tf.close cleanup skipped: %s: %s",
                    type(_close_err).__name__, _close_err,
                )
            # intent: optional feature / outcome: fail-open (unlink best-effort)
            try:
                if path.exists():
                    _os.unlink(path)
            except Exception as _unlink_err:
                logger.debug(
                    "_write_temp_blob: unlink cleanup skipped per %r: %s: %s",
                    str(path), type(_unlink_err).__name__, _unlink_err,
                )
            raise
        else:
            tf.close()
            return path
