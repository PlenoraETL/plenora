"""
ScheduleRunsCatalog: catalog delle esecuzioni archiviate di uno schedule.

Centralizza il lookup/listing delle runs prodotte da `ScheduleArtifactStore`,
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from app.shared.path_policy import assert_path_within
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RunFileInfo:
    """Metadata di un file dentro una run archiviata."""

    name: str
    size_bytes: int


@dataclass(frozen=True)
class RunInfo:
    """Snapshot di una run archiviata.

    `timestamp` e' il nome della cartella (formato `YYYYMMDD_HHMMSS`).
    `path` e' il path assoluto della directory della run.
    `files` e' una tuple immutabile di file presenti nella root della
    run (sottocartelle escluse, coerente con `ScheduleArtifactStore`).
    """

    timestamp: str
    path: Path
    files: Tuple[RunFileInfo, ...] = field(default_factory=tuple)

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


_RUN_TOKEN_PATTERN = r"^[0-9A-Za-z_-]{1,32}$"


class ScheduleRunsCatalog:
    """Catalog read-only delle runs archiviate da ScheduleArtifactStore.

    DI esplicito su `results_dir` per testabilita' senza monkey-patch
    di settings. Default: `settings.RESULTS_DIR`.
    """

    def __init__(self, results_dir: Optional[Path] = None) -> None:
        if results_dir is None:
            from app.config import settings
            self._results_dir = Path(settings.RESULTS_DIR)
        else:
            self._results_dir = Path(results_dir)

    # path helpers

    @staticmethod
    def _schedule_short(schedule_id: str) -> str:
        """Convenzione condivisa con `ScheduleArtifactStore._build_archive_dir`.

        Rimuove il prefisso `schedule_` (se presente) e tronca a 12 char.
        Vive in un solo punto del codebase per evitare drift fra archive
        e catalog."""
        return schedule_id.replace("schedule_", "")[:12]

    def runs_root_for(self, project_id: str, schedule_id: str) -> Path:
        """Path della cartella che contiene tutte le runs di uno schedule.

        NB: ritorna un Path resolved anche se la cartella non esiste —
        e' deliberato, i caller la usano per check di esistenza
        successivi. Non valida se project_id contiene path traversal:
        l'ACL HTTP a monte garantisce che project_id arrivi pulito.
        """
        return (
            self._results_dir / project_id / "runs" / self._schedule_short(schedule_id)
        ).resolve()

    # API: listing

    def list_runs(
        self, project_id: str, schedule_id: str
    ) -> List[RunInfo]:
        """Lista tutte le runs archiviate di uno schedule, dal piu' recente.

        Ritorna lista vuota se la directory delle runs non esiste o
        non contiene sottocartelle. Non solleva — il caller decide
        come rappresentare lo stato vuoto (di solito JSON con
        `runs: []`).
        """
        runs_root = self.runs_root_for(project_id, schedule_id)
        if not runs_root.exists() or not runs_root.is_dir():
            return []

        runs: List[RunInfo] = []
        for run_folder in sorted(runs_root.iterdir(), reverse=True):
            if not run_folder.is_dir():
                continue
            files = tuple(
                RunFileInfo(name=f.name, size_bytes=f.stat().st_size)
                for f in run_folder.iterdir()
                if f.is_file()
            )
            runs.append(
                RunInfo(
                    timestamp=run_folder.name,
                    path=run_folder,
                    files=files,
                )
            )
        return runs

    # API: lookup specifico

    def get_run_dir(
        self, project_id: str, schedule_id: str, run_token: str
    ) -> Path:
        """Risolve il path di una run specifica identificata da `run_token`.

        Steps:
        1. Sanitize regex su `run_token` (no path traversal nei nomi)
        2. Compone candidate dentro `runs_root_for(...)`
        3. Valida via `assert_path_within` (R#4 cleanup, niente
           reimplementazione manuale di `relative_to`)
        4. Verifica che esista come directory

        Solleva:
            ValueError: run_token malformato o fuori da runs_root
            FileNotFoundError: run_dir non esiste o non e' directory
        """
        import re

        if not run_token or not re.match(_RUN_TOKEN_PATTERN, run_token):
            raise ValueError(f"run_token malformato: {run_token!r}")

        runs_root = self.runs_root_for(project_id, schedule_id)
        # Containment check delegato al path_policy: catturiamo
        # ValueError e re-raise con il wording del catalog.
        try:
            run_dir = assert_path_within(runs_root / run_token, [runs_root])
        except ValueError as e:
            raise ValueError(f"run_token non valido: {run_token!r}") from e

        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(
                f"Run '{run_token}' non trovata per schedule '{schedule_id}'"
            )
        return run_dir

    def latest_run_dir(
        self, project_id: str, schedule_id: str
    ) -> Optional[Path]:
        """Path della run piu' recente (per timestamp).

        I nomi delle subdir sono `YYYYMMDD_HHMMSS` quindi l'ordinamento
        lessicografico coincide con quello temporale; fallback a mtime
        per robustezza contro nomi non standard.

        Ritorna None se non ci sono runs archiviate (caller decide
        come tradurre — di solito 404).
        """
        runs_root = self.runs_root_for(project_id, schedule_id)
        if not runs_root.exists() or not runs_root.is_dir():
            return None

        run_dirs = [d for d in runs_root.iterdir() if d.is_dir()]
        if not run_dirs:
            return None

        run_dirs.sort(key=lambda d: (d.name, d.stat().st_mtime), reverse=True)
        return run_dirs[0]

    # API: file lookup dentro una run

    def first_file_in(self, run_dir: Path) -> Optional[Path]:
        """Primo file (non directory) dentro una run.

        I caller download attuali servono il primo file della run (se
        ce n'e' piu' di uno e' uno scenario non previsto, in futuro
        si potrebbe zippare). Centralizziamo la logica per non
        ripeterla.
        """
        if not run_dir.exists() or not run_dir.is_dir():
            return None
        for f in run_dir.iterdir():
            if f.is_file():
                return f
        return None
