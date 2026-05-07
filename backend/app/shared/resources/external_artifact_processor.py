"""
Shared protocol per i processor di artifact esterni HTTP file-to-file.

Source-of-truth portable per il pattern "spedisco un blob (file/bytes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ArtifactSource:
    """Input per un processor esterno: blob bytes + filename +
    content-type opzionale.

    Wrapper tipato sopra il pattern attuale di
    `_build_rest_file_multipart` che oggi accetta `List[Dict]` di
    `file_fields` con `name`/`data`/`filename`/`content_type`. Il
    Protocol futuro accetta un singolo `ArtifactSource` (single-file
    upload) — l'eventuale multi-file come `tuple[ArtifactSource, ...]`.
    """
    data: bytes
    filename: str
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class ProcessorConfig:
    """Configurazione invocazione del processor esterno.

    `endpoint_url`: URL completo del processor (es.
        `https://imdf.plenora.it/api/process`).
    `method`: metodo HTTP (default `POST`; alcune API usano `PUT`).
    `timeout_seconds`: timeout connect+first-byte. Il body download
        richiede un dual-timeout watchdog separato (responsabilita'
        dell'impl concrete).
    `multipart_field_name`: nome del campo multipart che contiene il
        file (es. `"file"`, `"input"`, `"upload"`).
    `extra_form_fields`: campi multipart aggiuntivi non-file (es.
        opzioni di processing).
    `extra_headers`: header HTTP aggiuntivi (auth e' gia' applicata
        dall'impl).
    """
    endpoint_url: str
    method: str = "POST"
    timeout_seconds: float = 60.0
    multipart_field_name: str = "file"
    extra_form_fields: Mapping[str, str] = field(default_factory=dict)
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactResult:
    """Output del processor esterno: blob trasformato + metadata
    response.

    `data`: bytes del payload trasformato (es. ZIP IMDF).
    `filename`: nome di output suggerito (Content-Disposition o
        derivato).
    `content_type`: MIME type del response.
    `status_code`: HTTP status (sempre 2xx in success path).
    `headers`: mappa case-insensitive degli header response (subset
        rilevante, no full header dump).
    """
    data: bytes
    filename: str
    content_type: str
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class ExternalArtifactProcessor(Protocol):
    """Generic HTTP file-to-file processor.

    Implementazioni concrete (future):
    - `HttpRestFileProcessor` — promote di `_execute_rest_file` come
      classe stand-alone testabile (REST-002 follow-up).
    - capability service-specific processors (es. `AIAssistProcessor`,
      `SatelliteImageProcessor`) quando emergono come dominio.

    Il Protocol cattura solo l'interfaccia contract (process). Auth,
    retry, dual-timeout, streaming download, error taxonomy sono
    responsabilita' dell'impl concrete.
    """

    def process(
        self, source: ArtifactSource, config: ProcessorConfig,
    ) -> ArtifactResult:
        """Esegue il processing remoto. Solleva su HTTP error /
        timeout / payload non valido (impl-defined exception
        taxonomy)."""
        ...


__all__ = [
    "ArtifactSource",
    "ProcessorConfig",
    "ArtifactResult",
    "ExternalArtifactProcessor",
]
