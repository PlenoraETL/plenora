"""Source-bound guards for the Docker API smoke runner."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "docker-api-smoke.sh"


def _script_text() -> str:
    assert _SCRIPT.is_file()
    return _SCRIPT.read_text(encoding="utf-8")


def test_docker_api_smoke_script_keeps_targeting_existing_backend():
    text = _script_text()
    for marker in (
        "PLENORA_ACCEPTANCE_BASE_URL",
        "http://127.0.0.1:5004",
        'BASE_URL="${BASE_URL%/}"',
        'run_get_check "/health"',
        'run_get_check "/ready"',
        "--infrastructure-only",
        "Infrastructure-only Docker API smoke checks passed.",
        'curl_json "POST" "/api/auth/login"',
        'curl_json "GET" "/api/auth/me"',
        'curl_json "POST" "/api/projects"',
        'curl_json "GET" "/api/projects"',
        'curl_json "DELETE" "/api/projects/${PROJECT_ID}"',
        'curl_json "POST" "/api/auth/logout"',
        "Full Docker API smoke passed.",
    ):
        assert marker in text

    assert "docker compose up" not in _strip_shell_comments_and_heredocs(text), (
        "docker-api-smoke.sh must target an already running backend."
    )


def test_docker_api_smoke_script_keeps_full_flow_ordered_after_infra_checks():
    text = _script_main_block()
    ordered_markers = (
        'run_get_check "/health"',
        'run_get_check "/ready"',
        "if [ \"$INFRASTRUCTURE_ONLY\" -eq 1 ]; then",
        "require_full_flow_credentials",
        "login_smoke_user",
        "check_auth_me",
        "create_smoke_project",
        "list_smoke_projects",
        "delete_smoke_project",
        "logout_smoke_user",
        'echo "Full Docker API smoke passed."',
    )
    positions = [text.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions), (
        "docker-api-smoke.sh must keep the full acceptance flow ordered as "
        "health -> ready -> credentials -> login -> me -> create -> list -> "
        "delete -> logout."
    )


def test_docker_api_smoke_script_polls_with_bounded_timeouts():
    text = _script_text()
    for marker in (
        "PLENORA_ACCEPTANCE_TIMEOUT_SECONDS",
        "PLENORA_ACCEPTANCE_POLL_INTERVAL_SECONDS",
        "PLENORA_ACCEPTANCE_CURL_MAX_TIME_SECONDS",
        'curl -sS --max-time "$CURL_MAX_TIME_SECONDS"',
        "Polling GET ${url} for up to ${TIMEOUT_SECONDS}s",
        "did not return 2xx",
    ):
        assert marker in text


def test_docker_api_smoke_script_requires_seeded_credentials():
    text = _script_text()
    for marker in (
        "PLENORA_SMOKE_USERNAME",
        "PLENORA_SMOKE_PASSWORD",
        "python -m app.core.auth.smoke_seed",
        "require_full_flow_credentials",
    ):
        assert marker in text


def test_docker_api_smoke_script_allows_infrastructure_only_without_credentials():
    text = _script_main_block()
    infra_exit = text.index("Infrastructure-only Docker API smoke checks passed.")
    credential_gate = text.index("require_full_flow_credentials")
    login_call = text.index("login_smoke_user")
    assert infra_exit < credential_gate < login_call


def test_docker_api_smoke_script_serializes_json_payloads_safely():
    text = _script_text()
    for marker in ("login_payload_json", "project_payload_json", "json.dumps"):
        assert marker in text
    assert '\\"username\\":\\"${SMOKE_USERNAME}\\"' not in text, (
        "docker-api-smoke.sh must not interpolate env credentials into JSON."
    )


def test_docker_api_smoke_script_does_not_leave_full_acceptance_pending():
    text = _script_text()
    offenders = [
        marker
        for marker in (
            "exit 3",
            "intentionally pending",
            "Skipping login/logout smoke",
            "does not seed deterministic user credentials",
        )
        if marker in text
    ]
    assert not offenders


def test_docker_api_smoke_script_is_non_destructive():
    code = _strip_shell_comments_and_heredocs(_script_text())
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
        f"docker-api-smoke.sh must remain non-destructive. Offenders: {offenders}"
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


def _script_main_block() -> str:
    text = _script_text()
    return text[text.index('echo "Docker API smoke target: ${BASE_URL}"') :]
