"""
Source-bound guards for the cross-platform Docker installers.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASH_INSTALLER = _REPO_ROOT / "scripts" / "install-docker.sh"
_PS_INSTALLER = _REPO_ROOT / "scripts" / "install-docker.ps1"
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"
_SINGLE_SERVER_DOC = _REPO_ROOT / "docs" / "ops" / "install-docker-single-server.md"
_MULTI_SERVER_DOC = _REPO_ROOT / "docs" / "ops" / "install-docker-multi-server.md"


@pytest.fixture(scope="module")
def bash_text() -> str:
    return _BASH_INSTALLER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps_text() -> str:
    return _PS_INSTALLER.read_text(encoding="utf-8")


def test_installers_exist():
    assert _BASH_INSTALLER.is_file()
    assert _PS_INSTALLER.is_file()


@pytest.mark.parametrize(
    "path",
    (_BASH_INSTALLER, _PS_INSTALLER),
)
def test_installers_are_ascii(path: Path):
    path.read_text(encoding="ascii")


def test_bash_installer_uses_lf_line_endings():
    raw = _BASH_INSTALLER.read_bytes()
    assert b"\r\n" not in raw
    assert b"\r" not in raw


@pytest.mark.parametrize(
    "marker",
    (
        "--check",
        "--dry-run",
        "--install-deps",
        "--frontend-api-url",
        "--require-sqlserver-odbc",
        ".env.docker.example",
        "PLENORA_ENCRYPTION_KEY",
        "JWT_SECRET_KEY",
        "run_docker compose config",
        "run_docker compose build backend frontend",
        "run_docker compose up -d --wait backend",
        "run_docker compose up -d --wait frontend",
        "import psycopg2, pymysql, oracledb, pyodbc",
        "pyodbc.drivers()",
        "cleanroom.ping",
    ),
)
def test_bash_installer_contains_single_server_contract(
    bash_text: str,
    marker: str,
):
    assert marker in bash_text


@pytest.mark.parametrize(
    "marker",
    (
        "Check",
        "DryRun",
        "InstallDeps",
        "FrontendApiUrl",
        "RequireSqlServerOdbc",
        ".env.docker.example",
        "PLENORA_ENCRYPTION_KEY",
        "JWT_SECRET_KEY",
        '"compose", "config"',
        '"compose", "build", "backend", "frontend"',
        '"compose", "up", "-d", "--wait", "backend"',
        '"compose", "up", "-d", "--wait", "frontend"',
        "import psycopg2, pymysql, oracledb, pyodbc",
        "pyodbc.drivers()",
        "cleanroom.ping",
    ),
)
def test_powershell_installer_contains_single_server_contract(
    ps_text: str,
    marker: str,
):
    assert marker in ps_text


def _strip_bash_comments_and_heredocs(source: str) -> str:
    out: list[str] = []
    in_heredoc = False
    heredoc_terminator: str | None = None
    for line in source.splitlines():
        stripped = line.lstrip()
        if in_heredoc:
            if heredoc_terminator is not None and stripped == heredoc_terminator:
                in_heredoc = False
                heredoc_terminator = None
            continue
        if "<<'" in line or '<<"' in line:
            tag_start = line.rfind("<<")
            quote = line[tag_start + 2]
            tag = line[tag_start + 3 :].rstrip().rstrip(quote)
            in_heredoc = True
            heredoc_terminator = tag
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_bash_installer_does_not_remove_compose_state(bash_text: str):
    code = _strip_bash_comments_and_heredocs(bash_text)
    banned = (
        "docker compose down",
        "docker compose rm",
        "docker volume rm",
        "docker system prune",
        "rm -rf",
        "rm .env.docker",
        "rm -f .env.docker",
    )
    offenders = [pattern for pattern in banned if pattern in code]
    assert not offenders


def test_powershell_installer_does_not_remove_compose_state(ps_text: str):
    banned = (
        "compose\", \"down",
        "compose\", \"rm",
        "volume\", \"rm",
        "system\", \"prune",
        "Remove-Item",
        ".env.docker\" -Force",
    )
    offenders = [pattern for pattern in banned if pattern in ps_text]
    assert not offenders


@pytest.mark.parametrize(
    ("path", "markers"),
    (
        (
            _INSTALL_DOCKER,
            (
                "scripts/install-docker.sh",
                "scripts/install-docker.ps1",
                "--install-deps",
                "-InstallDeps",
                "--require-sqlserver-odbc",
                "-RequireSqlServerOdbc",
            ),
        ),
        (
            _SINGLE_SERVER_DOC,
            (
                "scripts/install-docker.sh --check",
                "scripts/install-docker.sh",
                "scripts/install-docker.ps1 -Check",
                "scripts/install-docker.ps1",
                "PostgreSQL, MySQL/MariaDB, Oracle, and SQL Server",
            ),
        ),
        (
            _MULTI_SERVER_DOC,
            (
                "scripts/install-docker.sh --install-deps --check",
                "scripts/install-docker.ps1 -InstallDeps -Check",
                "Do not run the full single-server installer",
            ),
        ),
    ),
)
def test_docs_reference_cross_platform_installers(
    path: Path,
    markers: tuple[str, ...],
):
    text = path.read_text(encoding="utf-8")
    for marker in markers:
        assert marker in text
