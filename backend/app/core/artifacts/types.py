"""
Tipi del modello unificato artifact / source.

Tutti i Ref sono frozen dataclass: niente mutazione dopo costruzione,
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.shared.execution.executor_protocol import ExecutionResult


class ArtifactResolutionError(Exception):
    """Errore fatale durante la risoluzione di un artefatto.

    Sollevato da ArtifactResolver quando:
    - il result_id non esiste
    - il result_id appartiene a un altro project_id (cross-tenant)
    - il blob e' assente
    - l'output_path e' fuori dalla directory consentita per il progetto
    - nessuno dei riferimenti dentro il Ref e' valido
    """


class SourceLoadingError(Exception):
    """Errore fatale durante il caricamento di una sorgente.

    Sollevato da SourceLoader quando:
    - il file_source non e' visibile al gruppo atteso (cross-tenant block)
    - il file_source non puo' essere materializzato dal blob DB
    - una sorgente progetto non puo' essere risolta dal resolver
    - il SourceRef e' malformato (factory `from_node_data` solleva
      direttamente ValueError, questa eccezione vive solo in fase di load)

    Centralizzata in `types.py` per evitare di importare FastAPI nel
    core: i router HTTP catturano e traducono in HTTPException.
    """


@dataclass(frozen=True)
class RunArtifactRef:
    """Riferimento a un output specifico di UNA pipeline run.

    Almeno uno fra `inline_df` / `result_id` / `output_path` deve
    essere valorizzato; con piu' valori l'ordine di preferenza per
    la materializzazione e' quello sopra.

    `inline_df` e' in-process: non passa per il DB, non e' serializzabile,
    valido solo nello stesso processo che ha eseguito la pipeline.
    """

    project_id: str
    result_id: Optional[str] = None
    output_path: Optional[str] = None
    inline_df: Any = None

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("RunArtifactRef.project_id mancante")
        if (
            self.inline_df is None
            and not self.result_id
            and not self.output_path
        ):
            raise ValueError(
                "RunArtifactRef senza artefatto specifico: "
                "nessuno fra inline_df/result_id/output_path"
            )


class PipelineArtifact(dict):
    """Contratto strutturato dell'output di una pipeline run.

    Subclass di `dict`: i consumer possono leggere come dict legacy
    (`result["success"]`, `**result`, `result["carica"] = ...`) e
    contemporaneamente accedere alle proprieta' tipizzate
    (`artifact.project_id`, `artifact.artifact_kind`,
    `artifact.to_run_artifact_ref`). La forma dict resta necessaria
    per la serializzazione cross-process via Celery.

    `artifact_kind` (proprieta' derivata):
    - "inline" → c'e' un DataFrame in process (path MANIPOLA inline)
    - "blob" → c'e' un result_id, blob persistito nel DB results
    - "file" → c'e' un output_path su disco (RESULTS_DIR o temp)
    - "none" → nessun artefatto (pipeline fallita o senza output)
    """

    _ALLOWED_KINDS = ("inline", "blob", "file", "none")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self.get("project_id"):
            raise ValueError("PipelineArtifact.project_id mancante")

    # proprieta' tipizzate sopra il dict

    @property
    def project_id(self) -> str:
        return self["project_id"]

    @property
    def success(self) -> bool:
        return bool(self.get("success", False))

    @property
    def message(self) -> Optional[str]:
        return self.get("message")

    @property
    def run_id(self) -> Optional[str]:
        return self.get("run_id") or None

    @property
    def result_id(self) -> Optional[str]:
        return self.get("result_id") or None

    @property
    def output_path(self) -> Optional[str]:
        return self.get("output_path") or None

    @property
    def filename(self) -> Optional[str]:
        return self.get("filename") or None

    @property
    def format(self) -> Optional[str]:
        return self.get("format") or None

    @property
    def rows_count(self) -> Optional[int]:
        return self.get("rows_count")

    @property
    def inline_df(self) -> Any:
        return self.get("dataframe")

    @property
    def artifact_kind(self) -> str:
        if self.inline_df is not None:
            return "inline"
        if self.result_id:
            return "blob"
        if self.output_path:
            return "file"
        return "none"

    # factory + conversione

    @classmethod
    def from_executor_result(
        cls, result: Any, *, project_id: str
    ) -> "PipelineArtifact":
        """Costruisce un PipelineArtifact dal dict ritornato dagli
        executor (estrai/manipola/arricchisci/features_service).

        Il `project_id` keyword e' la fonte autorevole del tenant
        binding per il risultato: se il payload ne contiene gia' uno
        diverso (rewrap stale, scambio di artefatti cross-progetto)
        solleva `ValueError` fail-closed invece di propagare la
        discrepanza in `to_run_artifact_ref`.

        Input non-dict produce un artefatto "none" senza sollevare.
        """
        if not project_id:
            raise ValueError("from_executor_result: project_id obbligatorio")

        if isinstance(result, (PipelineArtifact, dict)):
            merged = dict(result)
        else:
            return cls({
                "project_id": project_id,
                "success": False,
                "message": "result non strutturato",
            })

        existing = merged.get("project_id")
        if existing and existing != project_id:
            raise ValueError(
                f"from_executor_result: project_id mismatch "
                f"(payload={existing!r}, atteso={project_id!r})"
            )
        merged["project_id"] = project_id
        return cls(merged)

    @classmethod
    def from_execution_result(
        cls,
        exec_result: "ExecutionResult",
        *,
        project_id: str,
    ) -> "PipelineArtifact":
        """Typed factory: constructs a PipelineArtifact from an ExecutionResult.

        Preserves the typed payload boundary between executor and artifact.
        The legacy dict-compatible shape is reconstructed locally from typed
        fields instead of calling ``exec_result.to_dict`` at this internal
        boundary; explicit dict conversion remains available for API/task
        edges that need a serializable mapping.
        """
        merged = {"success": exec_result.success}
        if exec_result.message is not None:
            merged["message"] = exec_result.message
        if exec_result.error is not None:
            merged["error"] = exec_result.error
        merged.update(exec_result.payload)
        return cls.from_executor_result(merged, project_id=project_id)

    @classmethod
    def failure(
        cls, *, project_id: str, message: str, **extra: Any
    ) -> "PipelineArtifact":
        """Helper per costruire un PipelineArtifact di fallimento
        senza mai rischiare di passare un project_id vuoto."""
        return cls({
            "project_id": project_id,
            "success": False,
            "message": message,
            **extra,
        })

    def to_run_artifact_ref(self) -> "RunArtifactRef":
        """Conversione a RunArtifactRef per ArtifactResolver.

        Solleva ValueError (via __post_init__ del Ref) se l'artefatto
        non e' risolvibile (artifact_kind == "none").
        """
        return RunArtifactRef(
            project_id=self.project_id,
            result_id=self.result_id,
            output_path=self.output_path,
            inline_df=self.inline_df,
        )


@dataclass(frozen=True)
class SourceRef:
    """Riferimento a una sorgente di dati per un nodo input.

    Esattamente UNA sorgente esplicita per volta — tre campi mutuamente
    esclusivi:
    - `project`: ultimo output di un progetto upstream (cross-run)
    - `file_source_id`: file caricato dall'utente, materializzato dal blob DB
    - `static_fields`: lista di campi `{name, value, field_type?}` per nodi
      StaticValues (template variables come {{NOW}} risolte in `SourceLoader`)

    Validazione fail-closed in `__post_init__`: combinazioni multiple
    o nessuna sorgente sollevano `ValueError`. Niente fallback impliciti.

    NB: gli static_fields sono passati come `tuple` (immutable) per
    rendere il dataclass frozen-friendly. La factory
    `from_node_data` accetta liste e converte automaticamente.
    """

    project: Optional["LatestProjectArtifactRef"] = None
    file_source_id: Optional[str] = None
    static_fields: Optional[tuple] = None

    def __post_init__(self) -> None:
        active = sum([
            self.project is not None,
            self.file_source_id is not None,
            self.static_fields is not None,
        ])
        if active == 0:
            raise ValueError("SourceRef vuoto: nessuna sorgente specificata")
        if active > 1:
            raise ValueError("SourceRef ambiguo: piu' sorgenti specificate")

    def kind(self) -> str:
        if self.project is not None:
            return "project"
        if self.file_source_id is not None:
            return "file"
        return "static"

    @classmethod
    def from_node_data(cls, node_type: str, node_data: dict) -> "SourceRef":
        """Costruisce un SourceRef da una coppia (node_type, node_data)
        come arrivano dal canvas.

        Centralizza l'estrazione dei campi cosi' i call site
        (manipola, api_connector) non duplicano la logica di parsing.
        """
        if node_type == "project_source":
            project_id = node_data.get("source_project_id")
            if not project_id:
                raise ValueError(
                    "from_node_data: project_source senza source_project_id"
                )
            return cls(project=LatestProjectArtifactRef(project_id=project_id))

        if node_type == "file_source":
            file_id = node_data.get("file_source_id")
            if not file_id:
                raise ValueError(
                    "from_node_data: file_source senza file_source_id"
                )
            return cls(file_source_id=file_id)

        if node_type in ("staticValues", "static_values"):
            fields = node_data.get("fields") or []
            return cls(static_fields=tuple(fields))

        raise ValueError(
            f"from_node_data: node_type sconosciuto {node_type!r}"
        )


@dataclass(frozen=True)
class LatestProjectArtifactRef:
    """Ultimo output noto di un progetto, cross-run.

    Da usare SOLO per il caso "progetto upstream" (manipola consuma
    output di estrai, features/arricchisci consumano l'ultimo output
    di un progetto). Non usare come fallback dopo `execute_pipeline`
    sullo stesso project: due esecuzioni parallele si scambierebbero
    l'output (race cross-task).
    """

    project_id: str

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("LatestProjectArtifactRef.project_id mancante")


@dataclass(frozen=True)
class RunBlob:
    """Blob raw + metadata di un run artifact.

    Usato dai download HTTP che vogliono mandare il blob direttamente
    nella response, senza materializzarlo su disco. Filename arriva
    gia' sanitizzato dal DB ma il caller dovrebbe comunque rimuovere
    CR/LF prima di metterlo in Content-Disposition.
    """

    data: bytes
    filename: str
    format: str
    project_id: str
    result_id: str


@dataclass
class MaterializedArtifact:
    """Risultato della risoluzione di un Ref.

    Almeno uno fra `data` (bytes in memoria), `path` (file su disco)
    e `df` (DataFrame in memoria) e' valorizzato. Mode preferito post-
    Phase5: `data` (bytes-first, niente tempfile bounce). `path` resta
    per i path-only adapter (REST connector, library che richiedono un
    path filesystem). `df` per gli inline-df Ref.

    Se `is_temporary` e' True, `cleanup` rimuove il `path` (solo
    relevant in modalita' path). In modalita' bytes il cleanup e'
    no-op: i bytes sono in RAM, garbage-collected dal runtime.
    Context manager safe in entrambe le modalita'.
    """

    path: Optional[Path] = None
    df: Any = None
    data: Optional[bytes] = None
    format: Optional[str] = None
    is_temporary: bool = False
    _cleaned: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.path is None and self.df is None and self.data is None:
            raise ValueError(
                "MaterializedArtifact vuoto: serve path, df, oppure data"
            )

    def cleanup(self) -> None:
        if self._cleaned or not self.is_temporary or self.path is None:
            return
        try:
            from app.core.io.local_cleanup import rmtree_safe
            p = Path(self.path)
            if p.exists():
                rmtree_safe(p)
        except Exception:
            # cleanup best-effort: il caller non deve fallire per questo
            pass
        finally:
            self._cleaned = True

    def __enter__(self) -> "MaterializedArtifact":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = tb
        self.cleanup()
