"""
Source-bound guards for the Docker API smoke runner.

BE-CLEANROOM-072-W4-DOCKER-API-SMOKE-SKELETON.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "docker-api-smoke.sh"


def test_docker_api_smoke_script_exists():
    assert _SCRIPT.is_file(), (
        "scripts/docker-api-smoke.sh must exist as the Docker-native API "
        "acceptance runner skeleton. It targets an already running backend "
        "instead of booting or mutating Docker compose resources."
    )


def test_docker_api_smoke_script_locks_base_url_env_default():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "PLENORA_ACCEPTANCE_BASE_URL" in text, (
        "scripts/docker-api-smoke.sh must read PLENORA_ACCEPTANCE_BASE_URL "
        "so operators can target a non-default already running backend."
    )
    assert "http://127.0.0.1:5004" in text, (
        "scripts/docker-api-smoke.sh must default to the Docker-published "
        "backend URL http://127.0.0.1:5004."
    )
    assert 'BASE_URL="${BASE_URL%/}"' in text, (
        "scripts/docker-api-smoke.sh must normalize a trailing slash from "
        "PLENORA_ACCEPTANCE_BASE_URL before appending endpoint paths."
    )


def test_docker_api_smoke_script_checks_health_and_ready_only_for_now():
    text = _SCRIPT.read_text(encoding="utf-8")
    for endpoint in ('run_get_check "/health"', 'run_get_check "/ready"'):
        assert endpoint in text, (
            f"scripts/docker-api-smoke.sh must include implemented safe "
            f"check {endpoint!r}. These checks require no unknown "
            "credentials and can run against an already booted backend."
        )
    assert "docker compose up" not in _strip_shell_comments_and_heredocs(text), (
        "scripts/docker-api-smoke.sh must target an already running backend. "
        "Boot orchestration belongs in scripts/docker-validate.sh."
    )


def test_docker_api_smoke_script_polls_with_bounded_timeouts():
    text = _SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "PLENORA_ACCEPTANCE_TIMEOUT_SECONDS",
        "PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS",
        "PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS",
        'curl -sS --max-time "$CURL_MAX_TIME_SECONDS"',
        "Polling GET ${url} for up to ${TIMEOUT_SECONDS}s",
        "did not return 2xx",
    ):
        assert marker in text, (
            f"scripts/docker-api-smoke.sh must keep bounded polling marker "
            f"{marker!r}. Docker CI should tolerate normal backend boot time "
            "without becoming an unbounded or flaky probe."
        )


def test_docker_api_smoke_script_has_infrastructure_only_success_mode():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "--infrastructure-only" in text, (
        "scripts/docker-api-smoke.sh must expose --infrastructure-only so "
        "the currently implemented /health and /ready checks can be used "
        "without pretending the full auth/project/upload/execute smoke is done."
    )
    assert "Infrastructure-only Docker API smoke checks passed." in text, (
        "scripts/docker-api-smoke.sh must print a narrow success message "
        "that names infrastructure-only scope."
    )


def test_docker_api_smoke_script_documents_login_logout_skip_reason():
    text = _SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "Skipping login/logout smoke",
        "compose does not seed deterministic user credentials",
        "Creating a user here would mutate application data",
        "POST /api/auth/logout",
    ):
        assert marker in text, (
            f"scripts/docker-api-smoke.sh must document skipped login/logout "
            f"reason marker {marker!r}. Full auth smoke should only be added "
            "after Docker compose has deterministic credentials/bootstrap."
        )


def test_docker_api_smoke_script_does_not_fake_full_acceptance_success():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "exit 3" in text, (
        "Full scripts/docker-api-smoke.sh mode must exit with a distinct "
        "pending-code until real auth/project/upload/execute checks exist."
    )
    assert "TODO: Docker API smoke full flow is intentionally pending." in text, (
        "Full scripts/docker-api-smoke.sh mode must stop with a clear TODO "
        "instead of silently reporting acceptance success."
    )
    pending_markers = (
        "seed isolated admin/group",
        "POST /api/auth/login",
        "project create/list/delete",
        "tiny CSV upload/list/delete",
        "project execute/poll/download",
    )
    for marker in pending_markers:
        assert marker in text, (
            f"scripts/docker-api-smoke.sh pending stop must mention "
            f"{marker!r} so the next worker knows what remains before "
            "full Docker API acceptance can be claimed."
        )
    fake_success_markers = (
        "Full Docker API smoke passed",
        "auth smoke passed",
        "project smoke passed",
        "upload smoke passed",
        "execute smoke passed",
    )
    offenders = [marker for marker in fake_success_markers if marker in text]
    assert not offenders, (
        "scripts/docker-api-smoke.sh must not contain full-flow success "
        f"messages before those checks are implemented. Offenders: {offenders}"
    )


def test_docker_api_smoke_script_is_non_destructive():
    code = _strip_shell_comments_and_heredocs(
        _SCRIPT.read_text(encoding="utf-8")
    )
    banned_patterns = (
        "docker compose down",
        "docker compose rm",
        "docker volume rm",
        "docker system prune",
        "rm -rf",
        "rm .env.docker",
        "rm -f .env.docker",
        "> .env.docker",
    )
    offenders = [pattern for pattern in banned_patterns if pattern in code]
    assert not offenders, (
        "scripts/docker-api-smoke.sh must remain non-destructive while it "
        "targets an already running backend. Banned invocations found: "
        f"{offenders}"
    )


def test_docker_api_smoke_script_uses_lf_line_endings():
    raw = _SCRIPT.read_bytes()
    assert b"\r" not in raw, (
        "scripts/docker-api-smoke.sh must use LF line endings only so the "
        "bash shebang works on Linux hosts."
    )


def _strip_shell_comments_and_heredocs(source: str) -> str:
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
            tag = line[tag_start + 3:].rstrip().rstrip(quote)
            in_heredoc = True
            heredoc_terminator = tag
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)
