"""Sessione 7 contract test: P1 residui sparsi (etl-sources + entities).

Bug coperti:
1. manipola/source_loading.py:175 — `log.warning` (era `logger.warning`)
   → logger iniettato ora rispettato
2. arricchisci/api_connection_rest_file.py:579-586 — `requests.post`
   ora delega a `tls_policy.resolve_tls_verify` invece di ignorare
   verify=
3. projects/db.py:341,376,468 — datetime nativo invece di isoformat
   string (allineato a boundary B1 Pydantic)
4. projects/db.py ProjectRow — campo `description` aggiunto + SELECT
5. projects/service.py:105-120 — `description` letta da row invece
   di hardcoded ""
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Path 1: manipola source_loading log.warning

def test_manipola_source_loading_uses_injected_logger():
    """`log.warning` (alias del logger iniettato) invece di `logger.warning`
    (module-level). Anti-regression per test harness con logger catturante."""
    import inspect as _inspect
    from app.core.manipola import source_loading

    src = _inspect.getsource(source_loading.load_file_sources)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Cerca la sezione "file_source_id mancante"
    idx = src_no_comments.find("file_source_id mancante")
    assert idx >= 0
    # Backward search per la chiamata logger
    window = src_no_comments[max(0, idx - 200):idx]
    assert "log.warning" in window, (
        "load_file_sources deve usare `log.warning` (logger iniettato), "
        "NON `logger.warning` (module-level che ignora il logger arg)"
    )


# Path 2: api_connection_rest_file usa resolve_tls_verify

def test_api_connection_rest_file_uses_tls_policy():
    """Il `requests.post` in `_execute_rest_file` deve passare `verify=`
    risolto via `tls_policy.resolve_tls_verify`. Anti-regression
    contro pattern hardcoded verify=False o omissione."""
    from app.core.arricchisci import api_connection_rest_file

    src = inspect.getsource(api_connection_rest_file)

    # Cerca il blocco HTTP del rest_file
    idx = src.find("# ── HTTP call")
    assert idx >= 0
    window = src[idx:idx + 1200]

    assert "resolve_tls_verify" in window, (
        "rest_file POST deve delegare a resolve_tls_verify per coerenza "
        "con il pattern tls_policy centralizzato"
    )
    assert "verify=verify_tls" in window or "verify=" in window, (
        "requests.post deve passare verify= esplicito"
    )


# Path 3: projects/db.py uses datetime nativo

def test_projects_db_uses_datetime_not_isoformat():
    """`create_project_row`, `update_project_row` e
    `update_project_canvas_key_atomic` devono passare `datetime` nativo
    a psycopg2, non `.isoformat()` string. Pre-fix: roundtrip
    string→datetime aggiuntivo via cast Postgres."""
    from app.core.projects import db as projects_db

    for func_name in [
        "create_project_row",
        "update_project_row",
        "update_project_canvas_key_atomic",
    ]:
        # I metodi sono in classe ProjectsDb; cerca via getattr
        # NB: questi metodi sono dentro classi del modulo, vanno cercati
        # nel source del file intero.
        pass

    src = inspect.getsource(projects_db)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: NO `datetime.now(timezone.utc).isoformat()` per
    # i 3 callsite di now interno (create/update/canvas_key_atomic).
    # Il pattern accettabile e' `datetime.now(timezone.utc)` senza .isoformat().
    iso_count = src_no_comments.count("datetime.now(timezone.utc).isoformat()")
    assert iso_count == 0, (
        f"Detected {iso_count} occorrenze residue di "
        f"`datetime.now(timezone.utc).isoformat()` in projects/db.py — "
        f"passa `datetime` nativo, psycopg2 gestisce il cast a "
        f"timestamptz automaticamente"
    )


# Path 4: ProjectRow ha campo description

def test_project_row_has_description_field():
    """ProjectRow deve avere il campo `description: str | None` per
    propagare la description al ProjectMetadata invece di hardcode ''."""
    from app.core.projects.db import ProjectRow

    # dataclass: i fields esposti via __dataclass_fields__
    fields = ProjectRow.__dataclass_fields__
    assert "description" in fields, (
        "ProjectRow deve esporre `description` (era mancante pre-fix, "
        "ProjectMetadata hardcodava '')"
    )


# Path 5: project_row_to_metadata propaga description

def test_project_row_to_metadata_propagates_description():
    """`project_row_to_metadata` deve leggere `row.description`, non
    hardcodare stringa vuota."""
    from app.core.projects.service import ProjectService
    from app.core.projects.db import ProjectRow

    row = ProjectRow(
        id="p-1",
        group_id="g-1",
        name="Test",
        project_type="estrai",
        connection_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        created_by=None,
        description="Una descrizione del progetto",
    )
    metadata = ProjectService.project_row_to_metadata(row)
    assert metadata.description == "Una descrizione del progetto", (
        "ProjectMetadata.description deve venire da row.description "
        "(pre-fix era hardcoded a '')"
    )


def test_project_row_to_metadata_handles_none_description():
    """Edge case: description=None nel DB → ProjectMetadata.description=''.
    No errori Pydantic su tipo str."""
    from app.core.projects.service import ProjectService
    from app.core.projects.db import ProjectRow

    row = ProjectRow(
        id="p-2",
        group_id="g-1",
        name="No desc",
        project_type="estrai",
        connection_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        created_by=None,
        description=None,
    )
    metadata = ProjectService.project_row_to_metadata(row)
    assert metadata.description == ""
