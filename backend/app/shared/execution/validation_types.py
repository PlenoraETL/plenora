"""Vocabolario condiviso della validazione canvas.

Vive in ``shared/execution`` (NON in ``projects/``): protocol+registry,
l'orchestratore ``projects/canvas_validation`` e i 4 motori vi dipendono tutti —
nessuno deve dipendere da ``projects/``. È la stessa direzione di
``ColumnSchema`` (già qui).

``Issue`` è l'unità tipizzata prodotta dai validatori; ``ValidationReport`` è
l'aggregato che l'engine restituisce. Severità → effetto:
``error`` blocca (422), ``warning`` passa, ``info`` è una proposta di migrazione.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from app.shared.contracts.payloads import JsonObject

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Issue:
    node_id: str
    severity: Severity
    code: str           # es. "column.missing" | "lineage.cycle" | "mapping.dup_dest"
    message: str
    suggestion: Optional[JsonObject] = None

    def to_dict(self) -> JsonObject:
        out: JsonObject = {
            "node_id": self.node_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.suggestion is not None:
            out["suggestion"] = self.suggestion
        return out


@dataclass(frozen=True)
class ValidationReport:
    issues: Tuple[Issue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def by_severity(self, severity: Severity) -> Tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == severity)

    def for_node(self, node_id: str) -> Tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.node_id == node_id)

    def to_dict(self) -> JsonObject:
        return {
            "has_errors": self.has_errors,
            "issues": [i.to_dict() for i in self.issues],
        }


__all__ = ["Severity", "Issue", "ValidationReport"]
