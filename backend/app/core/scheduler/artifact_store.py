"""ScheduleArtifactStore: archiviazione output run schedulate.

Responsabilita' singola: copiare i file output di un progetto in una
cartella timestamped sotto `RESULTS_DIR/{project_id}/runs/...`,
preservando lo storico delle esecuzioni schedulate (l'orchestrator
sovrascrive sempre lo stesso path `RESULTS_DIR/{project_id}/`).

Vive come classe dedicata (non come metodo di `ExecutionManager`) per:
- testabilita' isolata (niente mock di mezzo execution manager)
- DI esplicito su `results_dir` (no monkey-patch di settings)

`ExecutionManager` istanzia `self._artifact_store` nel costruttore e
i caller interni chiamano direttamente `self._artifact_store.archive(...)`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


class ScheduleArtifactStore:
    """Archivia gli output di una run schedulata in cartelle timestamped.

    Layout su disco:
        results/{project_id}/                          ← l'orchestrator scrive qui
        results/{project_id}/runs/{schedule_short}/{YYYYMMDD_HHMMSS}/  ← noi copiamo qui
    """

    def __init__(self, results_dir: Optional[Path] = None) -> None:
        if results_dir is None:
            from app.config import settings
            self._results_dir = Path(settings.RESULTS_DIR)
        else:
            self._results_dir = Path(results_dir)

    def archive(
        self,
        project_id: str,
        schedule_id: str,
        output_file: str = "",
    ) -> Optional[str]:
        """Copia i file output in una cartella timestamped.

        Args:
            project_id: ID del progetto. La directory sorgente e'
                `{results_dir}/{project_id}`.
            schedule_id: ID dello schedule. Viene usato il prefix
                rimosso e troncato a 12 char per il nome cartella.
            output_file: se valorizzato, archivia SOLO quel file (se
                esiste). Se vuoto, archivia tutti i file nella root
                della directory progetto, escludendo le sottocartelle
                (es. `runs/` ricorsivo).

        Returns:
            Path della cartella archiviata (relativo a `results_dir`),
            oppure None se la directory sorgente non esiste, non ci
            sono file da archiviare, o l'archiviazione fallisce.
            None NON deve mai bloccare la pipeline: l'archive e' un
            best-effort, una pipeline che gira ma non archivia il
            backup e' meglio di una pipeline che fallisce per un
            problema di filesystem.
        """
        try:
            project_output_dir = self._results_dir / project_id
            if not project_output_dir.exists():
                logger.warning(f"Cartella output non trovata: {project_output_dir}")
                return None

            files_to_copy = self._select_files_to_copy(
                project_output_dir, output_file
            )
            if not files_to_copy:
                logger.warning(f"Nessun file da archiviare in {project_output_dir}")
                return None

            archive_dir = self._build_archive_dir(project_output_dir, schedule_id)
            archive_dir.mkdir(parents=True, exist_ok=True)

            copied = self._copy_files(files_to_copy, archive_dir)

            relative = str(archive_dir.relative_to(self._results_dir))
            logger.info(
                f"Output archiviato: {relative} "
                f"({len(copied)} file: {', '.join(copied)})"
            )
            return relative

        except Exception as e:
            logger.warning(f"Errore archiviazione output: {e}", exc_info=True)
            return None

    # internals

    def _select_files_to_copy(
        self, project_output_dir: Path, output_file: str
    ) -> List[Path]:
        if output_file and (project_output_dir / output_file).is_file():
            return [project_output_dir / output_file]
        # Copia tutti i file nella root (escludi sottocartelle come runs/)
        return [f for f in project_output_dir.iterdir() if f.is_file()]

    def _build_archive_dir(self, project_output_dir: Path, schedule_id: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        schedule_short = schedule_id.replace("schedule_", "")[:12]
        return project_output_dir / "runs" / schedule_short / timestamp

    def _copy_files(self, files: List[Path], dest_dir: Path) -> List[str]:
        import shutil

        copied: List[str] = []
        for src_file in files:
            dest = dest_dir / src_file.name
            shutil.copy2(str(src_file), str(dest))
            copied.append(src_file.name)
        return copied
