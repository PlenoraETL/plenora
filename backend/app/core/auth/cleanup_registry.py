"""Wrapper compatibile: il registry del group cleanup vive in
`app.core.lifecycle.group_cleanup` (Epic E roadmap accoppiamento).

`auth` non importa piu' i cleanup verticali: il meccanismo e' nel
lifecycle neutro, la composizione dei default e' al composition root
(`app/runtime/group_cleanup_wiring.py`). Questo modulo preserva la
superficie storica per chiamanti e test:

- `GROUP_CLEANUP_HANDLERS`: attributo di modulo (lazy via PEP 562,
  monkeypatchabile come prima);
- `run_group_cleanup_handlers(...)`: senza `handlers` espliciti consulta
  `GROUP_CLEANUP_HANDLERS` (quindi rispetta i monkeypatch);
- re-export di tipi e helper (`GroupCleanupHandler`, report, ...).

Percorso di rimozione: quando i chiamanti migrano a
`app.core.lifecycle.group_cleanup` + wiring, questo wrapper si tombstona.
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence

from app.core.lifecycle.group_cleanup import (  # noqa: F401  re-export compatibile
    GROUP_CLEANUP_REPORT_KEYS,
    CleanupLogger,
    GroupCleanupHandler,
    GroupCleanupReport,
    build_empty_group_cleanup_report,
)
from app.core.lifecycle.group_cleanup import (
    get_group_cleanup_handlers as _get_registered_handlers,
)
from app.core.lifecycle.group_cleanup import (
    run_group_cleanup_handlers as _run_handlers,
)


def _ensure_default_handlers() -> None:
    """Registrazione lazy dei default dal composition root.

    Lazy (e non a import-time) perche' `app.runtime` trascina l'intero
    stack servizi: i contesti che importano solo `auth.cleanup_registry`
    (unit test, tooling) non devono pagarlo finche' non eseguono o
    ispezionano davvero il cascade cleanup.
    """
    from app.runtime.group_cleanup_wiring import (
        register_default_group_cleanup_handlers,
    )

    register_default_group_cleanup_handlers()


def __getattr__(name: str):
    # PEP 562: `GROUP_CLEANUP_HANDLERS` resta un attributo di modulo
    # monkeypatchabile; il primo accesso materializza i default.
    if name == "GROUP_CLEANUP_HANDLERS":
        _ensure_default_handlers()
        return _get_registered_handlers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_group_cleanup_handlers() -> Sequence[GroupCleanupHandler]:
    # globals() prima di __getattr__: un monkeypatch.setattr sul modulo
    # crea un attributo reale che deve vincere sui default registrati.
    handlers = globals().get("GROUP_CLEANUP_HANDLERS")
    if handlers is not None:
        return handlers
    _ensure_default_handlers()
    return _get_registered_handlers()


def run_group_cleanup_handlers(
    group_id: str,
    report: MutableMapping[str, int],
    log: CleanupLogger,
    handlers: Sequence[GroupCleanupHandler] | None = None,
) -> None:
    _run_handlers(
        group_id,
        report,
        log,
        handlers=tuple(handlers if handlers is not None else get_group_cleanup_handlers()),
    )


def delete_intermediate_runs_for_group_project(project_id: str) -> int:
    """Backward-compatible export for older auth cleanup imports."""
    from app.core.projects.cleanup import (
        delete_intermediate_runs_for_group_project as delete_project_runs,
    )

    return delete_project_runs(project_id)


__all__ = [
    "GROUP_CLEANUP_HANDLERS",
    "GROUP_CLEANUP_REPORT_KEYS",
    "GroupCleanupHandler",
    "build_empty_group_cleanup_report",
    "delete_intermediate_runs_for_group_project",
    "get_group_cleanup_handlers",
    "run_group_cleanup_handlers",
]
