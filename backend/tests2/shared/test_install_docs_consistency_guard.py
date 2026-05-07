"""
Cross-doc consistency lock for install runbooks.

BE-GO-READY-INSTALL-014-DOC-CONSISTENCY-GUARD.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.shared,
    pytest.mark.contract,
    pytest.mark.architecture,
    pytest.mark.docs_guard,
    pytest.mark.cleanroom_excluded,
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCS_OPS = _REPO_ROOT / "docs" / "ops"

_INSTALL_DOCKER = _DOCS_OPS / "install-docker.md"
_INSTALL_MARCO = _DOCS_OPS / "install-marco-dry-run.md"
_INSTALL_NON_DOCKER = _DOCS_OPS / "install-non-docker.md"


# Phrases that described the previous "no Docker artifacts at
# all" state. Re-introducing any of them in the in-scope docs
# would silently rebrand the FIRST SCAFFOLD as missing again.
_STALE_PHRASES: tuple[str, ...] = (
    "NO Dockerfile",
    "NO docker-compose",
    "Docker not implemented",
    "cannot docker compose up",
    # `target shape only` is forbidden anywhere it would imply
    # "Docker is target shape only" (i.e. the scaffold doesn't
    # exist). The Helm/K8s line in install-non-docker.md uses a
    # different phrasing now; we ban the literal everywhere.
    "target shape only",
)


# Truth markers the in-scope docs MUST keep so the consistency
# is positive, not just a negative ban list.
_REQUIRED_TRUTH_MARKERS: tuple[str, ...] = (
    # Either the backquoted Dockerfile/compose mention OR the
    # FIRST SCAFFOLD label must appear; we check the SCAFFOLD
    # label as the load-bearing signal because it is the
    # difference between "scaffold exists" and "target shape".
    "FIRST SCAFFOLD",
    # The validation gap MUST be visible.
    "NOT YET VALIDATED",
)


_IN_SCOPE_DOCS: tuple[Path, ...] = (
    _INSTALL_DOCKER,
    _INSTALL_MARCO,
    _INSTALL_NON_DOCKER,
)


@pytest.fixture(scope="module")
def doc_texts() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for path in _IN_SCOPE_DOCS:
        assert path.is_file(), (
            f"docs/ops/{path.name} must exist; INSTALL-014 "
            "consistency guard depends on the three install runbooks "
            "being present together."
        )
        out[path] = path.read_text(encoding="utf-8")
    return out


def test_in_scope_docs_have_no_stale_phrases(doc_texts):
    """Source-bound ban list. Every in-scope install doc is
    scanned (case-insensitive) for the five stale phrases. A hit
    means the doc has drifted back to the pre-INSTALL-011
    "Docker not implemented" state and must be corrected.

    The Helm/K8s "NOT IMPLEMENTED" callout in install-non-docker.md
    is NOT banned (different wording on purpose); only the literal
    five phrases above are.
    """
    offenders: list[tuple[str, str]] = []
    for path, text in doc_texts.items():
        haystack = text.lower()
        for needle in _STALE_PHRASES:
            if needle.lower() in haystack:
                offenders.append((path.name, needle))

    if offenders:
        details = "\n".join(
            f"  {doc}: {phrase!r}" for (doc, phrase) in offenders
        )
        raise AssertionError(
            "Stale install-doc phrase(s) detected:\n"
            f"{details}\n\n"
            "After BE-GO-READY-INSTALL-011 the repo ships a FIRST "
            "SCAFFOLD `backend/Dockerfile` + `docker-compose.yml` "
            "+ `scripts/docker-validate.sh`. Re-introducing any of "
            "the banned phrases would silently rebrand the scaffold "
            "as missing, contradicting the install-docker.md "
            "header AND the install-marco-dry-run.md disclaimer.\n"
            "If the scaffold is genuinely being torn down, this "
            "guard MUST be updated in the same change with a "
            "paired audit entry in BE_INSTALLATION_AUDIT.md."
        )


def test_in_scope_docs_state_scaffold_exists(doc_texts):
    """Positive lock. Every in-scope doc MUST mention both
    `FIRST SCAFFOLD` and `NOT YET VALIDATED` so an operator who
    skims any of the three pages cannot mistake the scaffold for
    a validated path nor for a missing one."""
    missing: list[tuple[str, str]] = []
    for path, text in doc_texts.items():
        for marker in _REQUIRED_TRUTH_MARKERS:
            if marker not in text:
                missing.append((path.name, marker))

    if missing:
        details = "\n".join(
            f"  {doc}: missing {marker!r}"
            for (doc, marker) in missing
        )
        raise AssertionError(
            "Install doc consistency drift:\n"
            f"{details}\n\n"
            "Each in-scope doc must carry both `FIRST SCAFFOLD` "
            "AND `NOT YET VALIDATED`. The two markers together "
            "are the load-bearing honesty signal: the scaffold "
            "exists (cannot be dropped quietly) AND the Linux "
            "validation gap is visible (cannot be claimed "
            "production-ready)."
        )


def test_in_scope_docs_do_not_claim_docker_production_ready(doc_texts):
    """Negative lock on aspirational claims. The in-scope docs
    MUST NOT say the Docker path is "production-ready",
    "production ready", or "validated end-to-end" without the
    `NOT YET` qualifier in the same sentence. INSTALL-011 only
    landed a SCAFFOLD."""
    offenders: list[tuple[str, str]] = []
    for path, text in doc_texts.items():
        lower = text.lower()
        for needle in ("production-ready", "production ready"):
            if needle in lower:
                offenders.append((path.name, needle))
        # `validated end-to-end` is only allowed alongside
        # `NOT YET` (the disclaimer is the canonical phrasing).
        # Markdown wraps the disclaimer across blockquote line
        # breaks ("not\nyet been validated end-to-end"), so we
        # collapse whitespace before checking the window.
        import re as _re
        normalized = _re.sub(r"\s+", " ", lower)
        idx = 0
        needle = "validated end-to-end"
        while True:
            idx = normalized.find(needle, idx)
            if idx == -1:
                break
            window_start = max(0, idx - 64)
            window = normalized[window_start:idx + len(needle)]
            if "not yet" not in window:
                offenders.append(
                    (path.name, "validated end-to-end (without 'NOT YET')")
                )
            idx += len(needle)

    if offenders:
        details = "\n".join(
            f"  {doc}: {phrase!r}" for (doc, phrase) in offenders
        )
        raise AssertionError(
            "Aspirational Docker claim detected in install docs:\n"
            f"{details}\n\n"
            "INSTALL-011 ships a FIRST SCAFFOLD; calling it "
            "production-ready or validated end-to-end (without "
            "'NOT YET') misleads operators into running it as a "
            "real install path before the Linux validation report "
            "is pasted back."
        )


def test_install_non_docker_remains_go_ready_first_pc_path():
    """The single-machine runbook MUST remain the go-ready
    first-PC path until the Docker validation report lands.
    This positive lock prevents a future doc edit from quietly
    flipping the operator's go-ready path to Docker."""
    text = _INSTALL_NON_DOCKER.read_text(encoding="utf-8")
    # The section 13.1 callout phrases the funnel; the dry-run pack
    # echoes it. We accept either of the canonical lower-cased
    # phrasings because both currently appear in the runbook.
    canonical_phrases = (
        "go-ready first-pc",
        "go-ready first-pc test path",
        "go-ready install path",
    )
    lower = text.lower()
    if not any(p in lower for p in canonical_phrases):
        raise AssertionError(
            "install-non-docker.md must continue to identify "
            "itself as the go-ready first-PC path. None of the "
            f"canonical phrasings {canonical_phrases!r} were "
            "found. Without this funnel, an operator with Docker "
            "installed has no signal that the scaffold is NOT "
            "the validated path."
        )


def test_install_marco_dry_run_funnels_back_to_non_docker():
    """The dry-run pack MUST funnel operators with pre-installed
    Docker back to the single-machine path until the validation
    report exists. The companion guard
    `test_install_marco_dry_run_doc_guard.test_dry_run_doc_keeps_docker_honest`
    asserts the disclaimer block exists; this lock simply asserts
    the funnel sentence so the consistency layer is independent
    of the disclaimer wording."""
    text = _INSTALL_MARCO.read_text(encoding="utf-8")
    # Either of the two canonical phrasings is acceptable; both
    # currently appear (one as the disclaimer's closing sentence,
    # the other as the section 13.1 callout cross-link).
    lower = text.lower()
    assert (
        "the only go-ready path for the first pc test is this one"
        in lower
        or "go-ready first-pc test path remains" in lower
        or "go-ready first-pc path" in lower
    ), (
        "install-marco-dry-run.md must explicitly funnel operators "
        "back to the non-Docker path as the go-ready first-PC "
        "path. The funnel sentence is the operator-facing "
        "consistency signal between the two docs; without it, an "
        "operator who reads only the dry-run pack misses that the "
        "single-machine path is the validated route."
    )
