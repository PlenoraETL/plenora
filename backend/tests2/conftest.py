from pathlib import Path
import sys

import pytest


def _ensure_backend_on_path() -> None:
    here = Path(__file__).resolve()
    backend = here.parent.parent
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


_ensure_backend_on_path()


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
