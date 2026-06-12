"""
Guardia D5 (Epic D roadmap accoppiamento): ratchet sulle istanziazioni
dirette di servizi condivisi fuori dal composition root.

Censisce via AST le costruzioni `ProjectService()`, `ConnectionService()`,
`QueryExecutor()`, `DataSourceService()` sotto `app/core`. La baseline puo'
solo scendere: ogni nuova costruzione diretta deve invece passare da
`app/runtime/services.py` (iniezione o factory) o da un binding del
router registry.

Siti sanzionati (non contano come debito):
- `app/runtime/**` (composition root);
- i fallback compat dichiarati (accessor `_get_*` dei router gia' migrati
  e l'adapter `projects.access_context.ProjectMetadataAccess`, che
  costruiscono per-chiamata SOLO quando il wiring non e' avvenuto).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_CORE = _BACKEND_ROOT / "app" / "core"

_SERVICE_CLASSES = frozenset(
    {
        "ProjectService",
        "ConnectionService",
        "QueryExecutor",
        "DataSourceService",
    }
)

# Fallback compat censiti: file -> costruzioni ammesse (accessor wired-first).
_SANCTIONED_FALLBACKS: dict[str, int] = {
    "app/core/estrai/router.py": 3,        # _get_* post ESTRAI_BINDING (PR4)
    "app/core/orchestrator/router.py": 1,  # _project_service() post PR4
    "app/core/carica/dispatch.py": 3,      # _get_* post CARICA_DISPATCH_BINDING
    "app/core/projects/access_context.py": 1,  # adapter Epic C (per-call by design)
    # Sede del singleton canonico `datasource_service` (strategia "wrap,
    # don't move": RuntimeServices lo espone per identita', la costruzione
    # resta qui — vedi docstring RuntimeServices).
    "app/core/datasources/service.py": 1,
}

# Debito storico censito (2026-06-12, post PR2/PR4/Epic E). Ridurre, mai
# aumentare: ogni riga a zero va rimossa dal censimento nello stesso PR.
_DIRECT_CONSTRUCTION_BASELINE: dict[str, int] = {
    "app/core/arricchisci/api_connection_source_loading.py": 1,
    "app/core/arricchisci/enrichment_run_mode.py": 2,
    "app/core/carica/executor.py": 1,
    "app/core/manipola/project_source_runtime.py": 1,
    "app/core/scheduler/execution_manager.py": 2,
    "app/core/scheduler/schedule_service.py": 1,
}


def _count_constructions(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in _SERVICE_CLASSES:
            count += 1
    return count


def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(_APP_CORE.rglob("*.py")):
        rel = path.relative_to(_BACKEND_ROOT).as_posix()
        count = _count_constructions(path)
        allowed = _SANCTIONED_FALLBACKS.get(rel, 0)
        residual = count - allowed
        if residual > 0:
            counts[rel] = residual
    return counts


def test_no_new_direct_service_constructions() -> None:
    """Nessun file puo' superare il proprio debito censito; file nuovi
    partono a zero."""
    current = _scan()
    violations = {
        rel: (count, _DIRECT_CONSTRUCTION_BASELINE.get(rel, 0))
        for rel, count in current.items()
        if count > _DIRECT_CONSTRUCTION_BASELINE.get(rel, 0)
    }
    assert violations == {}, (
        "Nuove istanziazioni dirette di servizi fuori dal composition root "
        f"(file -> (attuale, baseline)): {violations}. Iniettare via "
        "runtime/services.py o router registry; vedi Epic D roadmap."
    )


def test_direct_construction_baseline_is_not_stale() -> None:
    """Igiene: righe della baseline scese a zero vanno consolidate."""
    current = _scan()
    stale = sorted(
        rel for rel in _DIRECT_CONSTRUCTION_BASELINE if rel not in current
    )
    assert stale == [], (
        f"Questi file non hanno piu' costruzioni dirette: {stale}. "
        "Rimuoverli dalla baseline per consolidare il progresso."
    )


def test_sanctioned_fallbacks_exist_and_are_bounded() -> None:
    """I fallback sanzionati restano nel perimetro dichiarato."""
    for rel, allowed in _SANCTIONED_FALLBACKS.items():
        path = _BACKEND_ROOT / rel
        assert path.is_file(), f"{rel} censito ma inesistente"
        count = _count_constructions(path)
        assert count <= allowed, (
            f"{rel}: {count} costruzioni > {allowed} sanzionate — le nuove "
            "vanno iniettate, non aggiunte al fallback"
        )