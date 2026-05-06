"""Source-bound guards for the Docker validation pack.

BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK.

Two contracts locked here:

  1. ``scripts/docker-validate.sh`` exists, advertises FIRST
     SCAFFOLD / NOT YET VALIDATED status, runs the eight
     canonical commands of the validation pack, and is
     **non-destructive** — it must NEVER call:
       - ``docker compose down`` (removes containers);
       - ``docker compose down -v`` (DESTRUCTIVE: removes
         volumes);
       - ``rm`` against ``.env.docker``;
       - ``docker volume rm`` against any ``plenora-*``
         volume;
       - ``docker system prune`` (any variant).

  2. ``docs/ops/install-docker.md`` carries the
     "Validation pack for the Linux PC test" section with
     the eight numbered expected-output blocks and the
     "FIRST SCAFFOLD, NOT YET VALIDATED" status line.

Source-bound: the test reads the script + doc as text and
asserts substring presence / absence. We do NOT execute the
script (no Docker on CI / Windows dev hosts). Same posture as
``test_docker_scaffold_guard.py``.

Outside scope:
  - the Dockerfile / docker-compose.yml / .env.docker.example
    contracts (locked by ``test_docker_scaffold_guard.py``);
  - the dry-run pack disclaimer (locked by
    ``test_install_marco_dry_run_doc_guard.py``);
  - the canonical 8-item smoke harness (locked by
    ``test_install_smoke_harness_doc_guard.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "docker-validate.sh"
_INSTALL_DOCKER = _REPO_ROOT / "docs" / "ops" / "install-docker.md"


# =====================================================================
# scripts/docker-validate.sh: existence + canonical contract
# =====================================================================


def test_validate_script_exists():
    assert _SCRIPT.is_file(), (
        "scripts/docker-validate.sh must exist (INSTALL-012). "
        "It is the convenience runner Marco invokes for the "
        "Linux PC validation pack."
    )


def test_validate_script_advertises_first_scaffold_status():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "FIRST SCAFFOLD" in text, (
        "scripts/docker-validate.sh header must advertise the "
        "FIRST SCAFFOLD status. Without it operators may treat "
        "the script as a validated runner."
    )
    assert "NOT YET VALIDATED" in text, (
        "scripts/docker-validate.sh must keep the 'NOT YET "
        "VALIDATED' qualifier so a successful run is not "
        "interpreted as a Linux validation pass."
    )
    assert "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK" in text, (
        "scripts/docker-validate.sh must reference the "
        "BE-GO-READY-INSTALL-012 stream tag for traceability."
    )


def test_validate_script_supports_dry_run_and_help():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "--dry-run" in text, (
        "scripts/docker-validate.sh must accept `--dry-run` so "
        "the eight commands can be previewed without executing "
        "them. Required for safe first-look on a real host."
    )
    assert "--help" in text, (
        "scripts/docker-validate.sh must accept `--help`."
    )


def test_validate_script_runs_eight_canonical_commands():
    text = _SCRIPT.read_text(encoding="utf-8")
    expected_commands = (
        "docker compose config",
        "docker compose build backend",
        "docker compose up -d postgres redis",
        "docker compose up -d --wait backend",
        "http://127.0.0.1:5004/health",
        "http://127.0.0.1:5004/ready",
        "docker compose up -d worker beat",
        "cleanroom.ping",
        "LOAD_FILE_MAX_BYTES",
        "docker compose stop",
    )
    for cmd in expected_commands:
        assert cmd in text, (
            f"scripts/docker-validate.sh must include the "
            f"canonical validation command/marker {cmd!r}. "
            "Each one corresponds to a numbered step of the "
            "validation pack in install-docker.md; missing one "
            "means the script and doc disagree."
        )


def _strip_shell_comments_and_heredocs(source: str) -> str:
    """Remove shell ``#``-prefixed lines and heredoc bodies so the
    non-destructive scan does not false-positive on phrases that
    appear only in the script header / usage docstring.

    The wrapper script's header explicitly enumerates the banned
    invocations as a contract reminder ("never runs `docker
    compose down`, `down -v`, ..."). Those mentions are
    documentation, not invocations, so we drop them before
    asserting.
    """
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
        # Shell here-doc: `... <<'TAG'` or `... <<TAG`. Cheap
        # detection — sufficient because our script only uses
        # one heredoc shape (single-quoted USAGE).
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


def test_validate_script_is_non_destructive():
    """Hard non-destructive contract. Adding any of the banned
    invocations re-introduces a footgun and breaks the contract
    documented in the script header.

    The scan ignores comments and heredoc bodies so the script
    header's enumeration of banned phrases (which is itself the
    contract reminder) does not false-positive.
    """
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
    offenders = [p for p in banned_patterns if p in code]
    assert not offenders, (
        f"scripts/docker-validate.sh must remain non-destructive. "
        f"Banned invocations found: {offenders}. The teardown "
        "step is `docker compose stop` (preserves volumes). "
        "Volume / container removal and any operation that "
        "deletes or overwrites .env.docker is OUT OF SCOPE for "
        "this script — operators run those manually if they "
        "really want a destructive reset."
    )


def test_validate_script_does_not_clobber_env_docker():
    """The script must NOT regenerate or overwrite the
    `.env.docker` secrets file. Step 2 of install-docker.md is
    the only place `.env.docker` is created; the validation
    pack consumes it, never produces it."""
    text = _SCRIPT.read_text(encoding="utf-8")
    # Anything that writes to .env.docker is suspicious. The
    # banned-patterns test covers `rm`, `>`, etc; this test
    # adds the positive assertion that the script reads the
    # file (pre-flight existence check) without touching it.
    assert "ERROR: .env.docker not found" in text, (
        "scripts/docker-validate.sh must perform a pre-flight "
        "existence check on .env.docker (and exit 2 with a "
        "pointer to install-docker.md Step 2 if missing) — "
        "not silently regenerate the file."
    )


# =====================================================================
# install-docker.md: validation pack section
# =====================================================================


def test_install_docker_doc_has_validation_pack_section():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "Validation pack for the Linux PC test" in text, (
        "install-docker.md must include a 'Validation pack for "
        "the Linux PC test' section (INSTALL-012). Without it "
        "Marco has no canonical copy/paste sequence to run on "
        "his Linux PC."
    )
    assert "BE-GO-READY-INSTALL-012-DOCKER-LINUX-VALIDATION-PACK" in text, (
        "install-docker.md validation pack section must "
        "reference the BE-GO-READY-INSTALL-012 stream tag."
    )
    assert "FIRST SCAFFOLD, NOT YET VALIDATED" in text, (
        "install-docker.md validation pack section must keep "
        "the 'FIRST SCAFFOLD, NOT YET VALIDATED' status header. "
        "Removing it lets the doc be mistaken for a validated "
        "path."
    )


def test_install_docker_doc_references_validation_script():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    assert "scripts/docker-validate.sh" in text, (
        "install-docker.md must reference the convenience "
        "wrapper `scripts/docker-validate.sh`. The doc and the "
        "script are paired artifacts; missing the pointer means "
        "operators duplicate the eight commands by hand."
    )
    assert "--dry-run" in text, (
        "install-docker.md validation pack section must show "
        "the `--dry-run` invocation as the recommended first "
        "look before executing the eight commands."
    )


def test_install_docker_doc_lists_expected_outputs_for_each_step():
    """Every numbered validation step must have an expected
    output / failure paste guidance attached. The shape (a
    numbered list 1..8 with concrete shapes) is the contract;
    we assert each numbered marker is present alongside an
    expected-output cue."""
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # The numbered markdown list `1. **`docker compose ...`**`
    # uses bold backtick'd commands.
    expected_markers = (
        "1. **`docker compose config`**",
        "2. **`docker compose build backend`**",
        "3. **`docker compose up -d --wait backend`**",
        "4. **`curl -fsS http://127.0.0.1:5004/health`**",
        "5. **`docker compose up -d worker beat`**",
        "6. **`cleanroom.ping`**",
        "7. **memory caps visibility**",
        "8. **`docker compose stop`**",
    )
    for marker in expected_markers:
        assert marker in text, (
            f"install-docker.md validation pack must include "
            f"numbered expected-output entry {marker!r}. Each "
            "numbered entry pins the shape Marco must paste "
            "back; dropping one creates an unverifiable step."
        )


def test_install_docker_doc_documents_paste_back_artifacts():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # Marco's report-back must include host metadata so the
    # validation report has a complete fingerprint.
    for marker in (
        "uname -a",
        "docker --version",
        "docker compose version",
        "/etc/os-release",
    ):
        assert marker in text, (
            f"install-docker.md validation pack must request "
            f"host metadata {marker!r} as part of the paste-"
            "back artifacts. Without the host fingerprint the "
            "validation report is not reproducible."
        )


def test_install_docker_doc_documents_script_non_destructive_contract():
    text = _INSTALL_DOCKER.read_text(encoding="utf-8")
    # The doc must call out the script's non-destructive
    # contract so operators know what the wrapper avoids.
    assert "non-destructive" in text.lower(), (
        "install-docker.md validation pack section must "
        "describe the script's non-destructive contract."
    )
    assert "docker compose down" in text, (
        "install-docker.md must explicitly call out that "
        "`docker compose down` (and its `-v` variant) are NOT "
        "run by the validation pack."
    )
