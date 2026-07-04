"""
Pilot-exit-criteria lock for the Resource Engine.

BE-GO-READY-RESOURCE-ENGINE-017-PILOT-EXIT-CRITERIA.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESOURCES_PKG = _REPO_ROOT / "backend" / "app" / "core" / "resources"
_APP_DIR = _REPO_ROOT / "backend" / "app"


# ResourceKind literal cardinality


def test_resource_kind_literal_has_pilot_graduation_entries():
    """Post-BE-GO-READY-RESOURCE-ENGINE-020 the pilot has
    GRADUATED with the second real loader (`file_source_blob`).
    The Literal now ships two entries; adding a 3rd kind requires
    extending ``BE_RESOURCE_ENGINE_AUDIT.md`` with a named
    production caller AND bumping this assertion in the same PR."""
    from app.core.resources.types import ResourceKind

    kinds = tuple(sorted(get_args(ResourceKind)))
    assert kinds == ("file_source_blob", "result_blob"), (
        "ResourceKind Literal must currently have exactly the two "
        f"entries 'result_blob' (RESOURCE-ENGINE-002) and "
        f"'file_source_blob' (RESOURCE-ENGINE-020 graduation). "
        f"Got: {kinds}. To add a 3rd kind, FIRST extend "
        "BE_RESOURCE_ENGINE_AUDIT.md with the named production "
        "caller AND update this lock test to the new cardinality."
    )


def test_default_registry_singleton_has_graduation_loaders():
    """The default registry pre-populates the two graduation
    loaders. A third registration is paired with the
    `ResourceKind` Literal extension above and the audit
    documentation."""
    from app.core.resources.registry import build_default_registry

    reg = build_default_registry()
    # `_loaders` is a dict[ResourceKind, ResourceLoader]; we read
    # it to assert cardinality. Tests below in the strategy guard
    # cover the loader contract per kind.
    loaders = getattr(reg, "_loaders", None)
    assert loaders is not None, (
        "Default registry must expose `_loaders` dict for "
        "introspection by source-bound tests."
    )
    assert len(loaders) == 2, (
        f"Default registry must hold exactly two loaders post-"
        f"graduation. Got: {sorted(loaders.keys())}. Adding a "
        "loader requires the matching `ResourceKind` Literal "
        "entry, the audit production-caller documentation, and "
        "an update to this lock test."
    )
    assert "result_blob" in loaders, (
        "Default registry must register ResultBlobLoader under "
        f"the canonical kind 'result_blob'. Got: {sorted(loaders.keys())}."
    )
    assert "file_source_blob" in loaders, (
        "Default registry must register FileSourceBlobLoader "
        "under the canonical kind 'file_source_blob' "
        f"(RESOURCE-ENGINE-020). Got: {sorted(loaders.keys())}."
    )


# NB: i 3 test "Audit documentation contract" che leggevano
# .agent-temp/BE_RESOURCE_ENGINE_AUDIT.md sono stati rimossi (2026-07-04):
# la directory .agent-temp era scratch mai tracciata da git, il file non
# esiste su nessun checkout. Il pilot e' comunque GRADUATO (vedi lock
# sulla cardinalita' sopra), quindi i criteri di exit sono consumati.


# Public surface (no private-by-stealth retreat)


def test_default_registry_remains_publicly_exported():
    """`default_registry` and `build_default_registry` must
    remain on the package's public surface. Privatizing them
    silently (renaming to `_default_registry`) would not retire
    the pilot but would hide the abstraction from new callers,
    which is the worst of both worlds.

    The KILL path REMOVES these names and the registry module;
    it does NOT prefix them with `_`.
    """
    import app.core.resources as pkg

    public = set(pkg.__all__)
    for name in ("default_registry", "build_default_registry"):
        assert name in public, (
            f"`{name}` must remain in `app.core.resources.__all__` "
            "while the pilot is active. Privatizing the registry "
            "API is not a valid pilot-exit move - either the pilot "
            "graduates (KEEP) or the module is removed (KILL); "
            "there is no middle state."
        )


def test_no_new_kind_in_loaders_directory_without_registration():
    """Defensive lock: if a future contributor adds a new
    `app/core/resources/loaders/<x>.py` without registering it in
    the default registry, this test fails. The error message
    explains the pilot-exit gate.

    A new loader file MUST be paired with:
      1) a new entry in the `ResourceKind` Literal (locked above);
      2) registration in `build_default_registry()`;
      3) a row in the BE_RESOURCE_ENGINE_AUDIT.md production-
         callers table.

    Any of those missing means the pilot is growing speculative
    abstractions without consumer pressure."""
    loaders_dir = _RESOURCES_PKG / "loaders"
    loader_files = sorted(
        p.name for p in loaders_dir.glob("*.py")
        if p.name not in {"__init__.py"} and not p.name.startswith("_")
    )
    assert loader_files == [
        "file_source_blob.py",
        "result_blob.py",
    ], (
        f"Loader directory must currently contain exactly "
        f"`file_source_blob.py` (RESOURCE-ENGINE-020) and "
        f"`result_blob.py` (RESOURCE-ENGINE-002). "
        f"Got: {loader_files}. Adding a new loader file requires: "
        "the matching `ResourceKind` Literal entry, registration "
        "in `build_default_registry()`, an audit production-caller "
        "row, and an update to this lock test IN THE SAME PR."
    )


def test_loader_store_compat_reexports_stay_tombstoned():
    """DB store boundaries are canonical under resources/stores.

    The old loaders/*_store.py compatibility paths have been removed and
    must not regain pg_connection access or local query functions.
    """
    wrappers = ("file_source_blob_store.py", "result_blob_store.py")
    reintroduced = [
        filename
        for filename in wrappers
        if (_RESOURCES_PKG / "loaders" / filename).exists()
    ]
    assert reintroduced == []

    result_loader = (_RESOURCES_PKG / "loaders" / "result_blob.py").read_text(encoding="utf-8")
    file_loader = (_RESOURCES_PKG / "loaders" / "file_source_blob.py").read_text(encoding="utf-8")
    assert "app.core.resources.stores.result_blob" in result_loader
    assert "app.core.resources.stores.file_source_blob" in file_loader


# KEEP-3 positive lock: registry has at least one production caller
#
# BE-GO-READY-RESOURCE-ENGINE-019 wired the FIRST real production
# caller of `default_registry().get(...)` --
# `ArtifactResolver._materialize_from_result_id` in
# `app/core/artifacts/resolver.py`. That closes residual
# `R-PILOT-017.A` "registry has zero real callers" and satisfies
# KEEP-3 of the pilot exit criteria.
#
# This positive lock prevents the resolver migration from being
# silently reverted (which would re-open the speculative-
# architecture risk). The grep is deliberately permissive: any
# production module under `app/` (excluding the engine itself
# AND the `app/core/intermediate/artifact_reader.py` orchestrator
# which only uses the bounded_reader helper, not the registry)
# that imports `default_registry` from the engine satisfies the
# lock.


def test_registry_has_at_least_one_production_caller():
    """KEEP-3 positive lock: AT LEAST ONE production module under
    `app/` (excluding `app/core/resources/` itself) must exercise
    the registry's public surface (`default_registry`). After
    BE-GO-READY-RESOURCE-ENGINE-019 closed `R-PILOT-017.A`, the
    registry MUST never drift back to zero callers without an
    explicit pilot kill.

    A failure here means: the resolver migration was reverted,
    OR every production caller switched back to internal loader
    function imports. Either rewires the speculative-architecture
    risk the audit's PILOT-EXIT-CRITERIA section was written to
    prevent. Restore at least one production caller, OR trigger
    the KILL path (audit § 17.4)."""
    callers: list[str] = []
    skip_dirs = (("core", "resources"),)
    for path in _APP_DIR.rglob("*.py"):
        rel_parts = path.relative_to(_APP_DIR).parts
        if any(
            len(skip) <= len(rel_parts) and rel_parts[: len(skip)] == skip
            for skip in skip_dirs
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Match either a top-level/lazy `from ... import default_registry`
        # OR a literal `default_registry(` call site. Either form
        # exercises the registry's public surface.
        if "default_registry" in text:
            callers.append(
                str(path.relative_to(_APP_DIR)).replace("\\", "/")
            )

    assert callers, (
        "Pilot exit criterion KEEP-3 regression: NO production "
        "module under `app/` (outside `app/core/resources/`) "
        "references `default_registry`. After "
        "BE-GO-READY-RESOURCE-ENGINE-019 (ArtifactResolver "
        "migration) this lock must hold; a failure means the "
        "resolver migration was reverted or the registry surface "
        "was bypassed. Restore at least one production caller "
        "(see audit § 17.6 W-A) or trigger the KILL path "
        "(audit § 17.4)."
    )


def test_resource_ref_used_by_at_least_one_production_caller():
    """KEEP-3 (companion lock): `ResourceRef` is the typed entry
    contract; at least one production caller must construct one
    to exercise the engine. Same surface check as the
    `default_registry` lock above, narrowed to the type
    construction site (a future loader hook that imports only
    the registry without constructing a ref would still trip
    this lock and force the audit to be updated)."""
    callers: list[str] = []
    skip_dirs = (("core", "resources"),)
    for path in _APP_DIR.rglob("*.py"):
        rel_parts = path.relative_to(_APP_DIR).parts
        if any(
            len(skip) <= len(rel_parts) and rel_parts[: len(skip)] == skip
            for skip in skip_dirs
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "ResourceRef(" in text or "ResourceRef\n" in text:
            callers.append(
                str(path.relative_to(_APP_DIR)).replace("\\", "/")
            )

    assert callers, (
        "Pilot exit criterion KEEP-3 (ResourceRef construction): "
        "no production module under `app/` (outside "
        "`app/core/resources/`) constructs a ResourceRef. Closing "
        "R-PILOT-017.A requires at least one production caller "
        "exercising the typed entry contract; restore the "
        "ArtifactResolver migration or trigger the KILL path."
    )
