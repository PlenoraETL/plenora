from pathlib import Path
import sys

import pytest


def _ensure_backend_on_path() -> None:
    here = Path(__file__).resolve()
    backend = here.parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


_ensure_backend_on_path()


def _kill_stray_test_subprocesses() -> None:
    """Kill stray celery/uvicorn spawnati da test runs precedenti.

    Quando pytest viene ucciso da --timeout o Ctrl+C a meta' test, i
    subprocess figli (celery worker, uvicorn backend) spesso sopravvivono
    perche' la teardown del fixture module-scoped non gira. Il celery stray
    si riattacca al Redis condiviso e ruba i task del nuovo worker di test
    — DuplicateNodenameWarning — causando flake indistinguibili dai bug
    applicativi.

    Heuristica conservativa: killa solo processi il cui command-line
    matcha ESATTAMENTE quello che spawniamo nei test (celery/uvicorn con
    app specifica). Non tocca dev server su porte arbitrarie."""
    import sys as _sys
    if _sys.platform != "win32":
        return
    import subprocess as _sp
    try:
        out = _sp.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return

    patterns = (
        "celery -A workers.celery_app worker",
        "uvicorn app.main:app",
        "uvicorn tests2.functional.fakes",
    )
    to_kill: list[str] = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        cmdline = parts[1] if len(parts) >= 3 else ""
        pid = parts[-1].strip()
        if not pid.isdigit():
            continue
        if any(p in cmdline for p in patterns):
            to_kill.append(pid)
    for pid in to_kill:
        try:
            _sp.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def _cleanup_stray_test_subprocesses_at_session_start():
    """Pulisce stray prima della session, non tocca al teardown (gli altri
    fixture module-scoped hanno i loro stop_process)."""
    _kill_stray_test_subprocesses()
    yield


@pytest.fixture
def backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


def _iter_managed_processes(obj):
    if obj is None:
        return
    if hasattr(obj, "process") and hasattr(obj, "name"):
        yield obj
        return
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_managed_processes(value)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            yield from _iter_managed_processes(value)


@pytest.fixture(autouse=True)
def _dump_managed_process_output_on_failure(request):
    yield
    rep = getattr(request.node, "rep_call", None)
    if not rep or not rep.failed:
        return

    seen = set()
    for value in getattr(request.node, "funcargs", {}).values():
        for managed in _iter_managed_processes(value):
            pid = id(managed)
            if pid in seen:
                continue
            seen.add(pid)
            captured = "".join(getattr(managed, "captured_output", [])[-400:])
            if not captured.strip():
                continue
            print(
                f"\n===== captured output: {getattr(managed, 'name', 'process')} =====\n"
                f"{captured}"
                f"\n===== end captured output: {getattr(managed, 'name', 'process')} =====\n"
            )
