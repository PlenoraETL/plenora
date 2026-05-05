"""Runtime composition boundary.

BE-GO-READY-BOOTSTRAP-ENGINE-011-RUNTIME-CONTAINER-FIRST-SLICE.

Holds the typed factory + container for the coherent slice of shared
services currently constructed at module-load time in `app/main.py`
(project + connection + estrai + executor + features + orchestrator).

This is a FIRST SLICE. Routers, exception handlers, middleware,
health checks and `init_*` glue stay where they are. The factory
emits a typed `RuntimeServices` dataclass; consumers that need the
old module-global names re-bind from the container in `main.py`
(preserved for monkeypatch surface).
"""

from app.runtime.services import RuntimeServices, build_runtime_services

__all__ = ["RuntimeServices", "build_runtime_services"]
