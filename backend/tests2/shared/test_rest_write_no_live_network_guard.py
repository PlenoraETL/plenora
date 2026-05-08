from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REST_TEST_ROOTS = (
    REPO_ROOT / "backend" / "tests2" / "features" / "unit",
    REPO_ROOT / "backend" / "tests2" / "carica" / "unit",
)

FORBIDDEN_IMPORT_MODULES = {
    "requests",
    "httpx",
    "urllib.request",
}

FORBIDDEN_CALLS = {
    "requests.request",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.Session",
    "httpx.Client",
    "httpx.AsyncClient",
    "httpx.request",
    "httpx.get",
    "httpx.post",
    "urllib.request.urlopen",
}


def _rest_sink_or_writer_tests() -> list[Path]:
    files: list[Path] = []
    for root in REST_TEST_ROOTS:
        if not root.exists():
            continue
        for path in root.glob("test_*rest*.py"):
            if "rest_sink" in path.name or "rest_write" in path.name:
                files.append(path)
    return sorted(files)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def test_rest_sink_and_writer_tests_do_not_instantiate_live_network_clients() -> None:
    offenders: list[str] = []

    for path in _rest_sink_or_writer_tests():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_IMPORT_MODULES:
                        offenders.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FORBIDDEN_IMPORT_MODULES:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} imports from {module}")
            elif isinstance(node, ast.Call):
                call_name = _call_name(node.func)
                if call_name in FORBIDDEN_CALLS:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} calls {call_name}")

    assert offenders == []

