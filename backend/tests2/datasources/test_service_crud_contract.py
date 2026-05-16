"""
Contract test per CRUD + tenant isolation di `DataSourceService`.

Invariants blindati (da ``backend/docs/architecture/invariants.md``):
"""

from __future__ import annotations

import json
import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    """``app.db.pg_connection`` at-import-time richiede ``DATABASE_URL``.

    Placeholder sintetico: nessuna query reale viene eseguita (tutte le
    funzioni DB sono monkeypatchate nei singoli test).
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fake:fake@localhost:5432/fake",
    )
    yield


def _make_row(ds_id: str, group_id: str, name: str = "test ds",
              ds_type: str = "local") -> dict:
    """Costruisce una row DB finta come la ritornerebbe ``fetchall``.

    ``config`` è una string JSON (formato DB). Senza campi sensibili:
    così ``_decrypt_dict_fields`` non esegue logica Fernet (allowlist
    skippa campi assenti — già blindato nei test crypto).
    """
    config = {
        "id": ds_id,
        "name": name,
        "type": ds_type,
        "root_path": "/tmp",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    return {
        "id": ds_id,
        "group_id": group_id,
        "name": name,
        "ds_type": ds_type,
        "config": json.dumps(config),
        "created_by": "user-x",
    }


def test_datasource_service_uses_domain_db_not_config_leaf() -> None:
    source_path = _BACKEND_ROOT / "app" / "core" / "datasources" / "service.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "app.db.config.datasources" not in imported_modules
    assert "app.db.pg_connection" not in imported_modules
    assert "app.core.datasources" in imported_modules


def test_datasource_row_has_no_legacy_dict_serializer() -> None:
    from app.core.datasources import db as datasource_db

    assert not hasattr(datasource_db.DataSourceRow, "to_legacy_dict")


def test_list_datasources_filters_by_group_id_uses_where_clause(monkeypatch):
    """Blinda §4.1 tenant isolation: con ``group_id`` non-None la query SQL
    include ``WHERE group_id = %s`` e i params passano l'id del tenant.

    Regressione: la rimozione del filtro (o il passaggio a ``WHERE TRUE``)
    esporrebbe a un tenant i datasource di ogni altro gruppo — silent,
    nessun errore, solo lista gonfia.
    """
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    captured = []

    def fake_fetchall(query, params=None):
        captured.append((query, params))
        return []

    monkeypatch.setattr(datasource_db, "fetchall", fake_fetchall)

    service = DataSourceService()
    service.list_datasources(group_id="tenant-a")

    assert len(captured) == 1, (
        f"Attesa 1 chiamata a fetchall, ottenute {len(captured)}"
    )
    query, params = captured[0]
    assert "WHERE group_id" in query, (
        f"La query DEVE includere WHERE group_id per tenant isolation. "
        f"Ottenuto: {query!r}"
    )
    assert params == ("tenant-a",), (
        f"Il params DEVE essere ('tenant-a',) per binding SQL sicuro, "
        f"ottenuto {params!r}"
    )


def test_list_datasources_no_where_clause_when_group_id_is_none(monkeypatch):
    """Blinda il path superadmin: ``group_id=None`` → query SENZA WHERE.

    Semantica: ``None`` significa "chi chiama ha già autorizzato l'accesso
    full-tenant" (superadmin o route con ``allowed_groups`` pre-filtrato).
    Il service non deve inserire autonomamente filtri: rimane responsabilità
    del caller (router/auth layer) proteggere la chiamata.
    """
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    captured = []

    def fake_fetchall(query, params=None):
        captured.append((query, params))
        return []

    monkeypatch.setattr(datasource_db, "fetchall", fake_fetchall)

    service = DataSourceService()
    service.list_datasources(group_id=None)

    assert len(captured) == 1
    query, params = captured[0]
    assert "WHERE group_id" not in query, (
        f"Senza group_id NON deve esserci filtro WHERE group_id, "
        f"query: {query!r}"
    )
    # L'implementazione chiama fetchall senza tuple di params in questo path.
    assert params is None, (
        f"Senza group_id i params DEVONO essere None, ottenuto {params!r}"
    )


def test_list_datasources_uses_single_fetchall_no_per_row_requery(monkeypatch):
    """Blinda il fix N+1 (service.py:52-68): ``list_datasources`` DEVE usare
    1 sola ``fetchall`` e parse in memoria — MAI ``get_datasource(id)`` per-row.

    Regressione: il pattern N+1 era presente prima del refactor
    2026-04-17/18. Un merge futuro che riportasse un
    ``get_datasource(row.id)`` per ogni riga (magari per uniformare il
    decrypt path) distruggerebbe la garanzia
    di latenza O(1) query per M datasource.

    Strategia: counter su ``fetchall`` (==1) e su
    ``datasource_db.get_datasource`` (==0).
    Il counter 0 su get_datasource è il segnale discriminante: se sale,
    c'è un path di re-fetch che non deve esserci.
    """
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    rows = [_make_row(f"ds-{i}", "tenant-a", f"ds{i}") for i in range(5)]

    fetchall_count = [0]
    get_datasource_count = [0]

    def fake_fetchall(query, params=None):
        fetchall_count[0] += 1
        return rows

    def fake_get_datasource(ds_id):
        get_datasource_count[0] += 1
        return rows[0]  # non dovrebbe mai essere chiamato

    monkeypatch.setattr(datasource_db, "fetchall", fake_fetchall)
    monkeypatch.setattr(datasource_db, "get_datasource", fake_get_datasource)

    service = DataSourceService()
    result = service.list_datasources(group_id="tenant-a")

    assert len(result.datasources) == 5
    assert fetchall_count[0] == 1, (
        f"list_datasources DEVE fare 1 sola fetchall. Ottenuto "
        f"{fetchall_count[0]} — probabile regressione N+1."
    )
    assert get_datasource_count[0] == 0, (
        f"list_datasources NON DEVE chiamare get_datasource per row. "
        f"Ottenuto {get_datasource_count[0]} chiamate — pattern N+1."
    )


def test_delete_datasource_returns_false_for_nonexistent_no_db_delete(monkeypatch):
    """Blinda idempotenza delete: id inesistente → ``False`` SENZA side-effect.

    Il check ``if not datasource_db.get_datasource(ds_id): return False`` è
    il punto di idempotenza. Rimuoverlo farebbe partire DELETE SQL spurie che
    toccherebbero 0 row ma:
    - gonfierebbero le metriche ``rowcount = 0`` nei log
    - confonderebbero eventuali trigger audit (delete event per id ignoto)
    - nasconderebbero bug upstream (l'UI che chiama delete su id stale)

    Il contract fail-closed: se non esiste, ritorna False e nessuna
    operazione DB viene eseguita.
    """
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    delete_called_with = []
    monkeypatch.setattr(datasource_db, "get_datasource", lambda ds_id: None)
    monkeypatch.setattr(
        datasource_db,
        "delete_datasource",
        lambda ds_id: delete_called_with.append(ds_id),
    )

    service = DataSourceService()
    result = service.delete_datasource("ds-not-exist")

    assert result is False, (
        f"delete su id inesistente DEVE ritornare False, ottenuto {result!r}"
    )
    assert delete_called_with == [], (
        f"datasource_db.delete_datasource NON DEVE essere chiamato se get_datasource è None. "
        f"Chiamate osservate: {delete_called_with}"
    )


def test_create_datasource_generates_id_with_canonical_pattern(monkeypatch):
    """Blinda il contract pubblico di ``_generate_id``: ID segue il pattern
    ``ds_<name_safe>_<YYYYMMDD>_<HHMMSS>_<uuid[:8]>``.

    Questo ID finisce nelle URL REST (``/api/datasources/{ds_id}``), nelle
    metriche, nei log strutturati. Un refactor che ne cambiasse la forma
    (es. UUID puro) romperebbe:
    - le URL bookmark-ate dai client
    - i grep negli stack di log che cercano il prefisso ``ds_``
    - i parser che estraggono il timestamp per analisi storica

    Regressione: regression fix deve aggiornare anche il router e il frontend.
    Il test blinda il formato lato service.
    """
    from app.core.datasources.service import DataSourceService
    from app.core.datasources.models import DataSourceCreate
    import app.core.datasources.db as datasource_db

    saved = []
    monkeypatch.setattr(datasource_db, "save_datasource", lambda **kw: saved.append(kw))

    service = DataSourceService()
    data = DataSourceCreate(name="My Test DS", type="local", root_path="/tmp")
    result = service.create_datasource(
        data, group_id="tenant-x", created_by="user-1",
    )

    # ds_<name>_<date>_<time>_<uuid8>: il nome può contenere '_' per via della
    # normalizzazione (space → underscore), quindi ragiono sui token dalla coda.
    assert result.id.startswith("ds_"), (
        f"ID deve iniziare con 'ds_', ottenuto {result.id!r}"
    )
    parts = result.id.split("_")
    assert len(parts) >= 5, (
        f"ID deve avere almeno 5 token (ds, name>=1, date, time, uuid): {result.id!r}"
    )
    uuid_token = parts[-1]
    time_token = parts[-2]
    date_token = parts[-3]
    assert len(uuid_token) == 8, (
        f"Ultimo token deve essere uuid[:8] (8 char), ottenuto {uuid_token!r}"
    )
    assert time_token.isdigit() and len(time_token) == 6, (
        f"Penultimo token deve essere HHMMSS (6 cifre), ottenuto {time_token!r}"
    )
    assert date_token.isdigit() and len(date_token) == 8, (
        f"Terzultimo token deve essere YYYYMMDD (8 cifre), ottenuto {date_token!r}"
    )

    # Side-effect: save_datasource è stato chiamato con l'id generato
    assert len(saved) == 1
    assert saved[0]["ds_id"] == result.id
    assert saved[0]["group_id"] == "tenant-x"
    assert saved[0]["created_by"] == "user-1"


def test_datasource_scan_state_db_boundary_uses_datasources_row(monkeypatch):
    """scan_state deve passare dal DB domain-owned, non dal JSON locale."""
    import app.core.datasources.db as datasource_db

    fetch_calls = []
    execute_calls = []

    def fake_fetchone(query, params=()):
        fetch_calls.append((query, params))
        return {
            "id": "ds-db",
            "scan_state": {
                "strategy": "modified_date",
                "snapshot": [],
                "processed": [],
            },
        }

    class _Result:
        rowcount = 1

    def fake_execute(query, params=()):
        execute_calls.append((query, params))
        return _Result()

    monkeypatch.setattr(datasource_db, "fetchone", fake_fetchone)
    monkeypatch.setattr(datasource_db, "execute", fake_execute)

    row = datasource_db.get_scan_state("ds-db")
    saved = datasource_db.save_scan_state("ds-db", {"processed": []})

    assert row is not None
    assert row.scan_state is not None
    assert row.scan_state["processed"] == []
    assert saved is True
    assert fetch_calls == [
        ("SELECT id, scan_state FROM datasources WHERE id = %s", ("ds-db",))
    ]
    assert execute_calls
    assert "UPDATE datasources SET scan_state = %s" in execute_calls[0][0]


def test_datasource_service_has_no_scan_state_filesystem_fallback():
    """scan_state runtime deve restare DB-only in ottica multi-pod."""
    from pathlib import Path

    source = Path("backend/app/core/datasources/service.py").read_text(encoding="utf-8")

    assert "datasources_scan" not in source
    assert "_scan_dir" not in source
    assert "_scan_state_path" not in source
    assert "_load_legacy_scan_state" not in source


def test_get_scan_state_ignores_corrupted_legacy_file_and_returns_empty(tmp_path, monkeypatch):
    """Il runtime DB-only ignora qualunque file legacy locale corrotto."""
    from app.core.datasources.service import DataSourceService
    from app.core.datasources.models import ScanState
    import app.core.datasources.db as datasource_db

    service = DataSourceService()
    monkeypatch.setattr(
        datasource_db,
        "get_scan_state",
        lambda ds_id: datasource_db.DataSourceScanStateRow(
            id=ds_id,
            scan_state=None,
        ),
    )
    save_calls = []
    monkeypatch.setattr(
        datasource_db,
        "save_scan_state",
        lambda ds_id, state: save_calls.append((ds_id, state)) or True,
    )

    ds_id = "ds-corrupt-test"
    scan_path = tmp_path / f"{ds_id}.json"
    scan_path.write_text("{this is not valid json", encoding="utf-8")
    assert scan_path.exists(), "setup: file corrotto deve esistere"

    state = service.get_scan_state(ds_id)

    assert isinstance(state, ScanState)
    assert state.last_check is None, (
        "ScanState ritornato DEVE essere empty (no fallback su contenuto corrotto)"
    )
    assert state.processed == []

    assert scan_path.exists(), (
        "Il fallback filesystem e' rimosso: il file legacy non deve essere toccato."
    )
    broken_files = list(tmp_path.glob(f"{ds_id}.json.broken.*"))
    assert len(broken_files) == 0, (
        f"Non devono essere creati file .broken.<ts>, trovati: "
        f"{[f.name for f in broken_files]}"
    )
    assert save_calls == [], "Il JSON legacy corrotto non deve essere migrato nel DB"


def test_get_scan_state_returns_empty_when_file_absent(tmp_path, monkeypatch):
    """Edge case base: path inesistente → ``ScanState`` vuoto, no file create.

    Complementare al test precedente: senza questo, un refactor che per errore
    creasse un file placeholder (``path.touch()``) sembrerebbe benigno ma
    introdurrebbe I/O spurio (disk write su ogni read di scheduler). Blindato.
    """
    from app.core.datasources.service import DataSourceService
    from app.core.datasources.models import ScanState
    import app.core.datasources.db as datasource_db

    service = DataSourceService()
    monkeypatch.setattr(
        datasource_db,
        "get_scan_state",
        lambda ds_id: datasource_db.DataSourceScanStateRow(
            id=ds_id,
            scan_state=None,
        ),
    )
    save_calls = []
    monkeypatch.setattr(
        datasource_db,
        "save_scan_state",
        lambda ds_id, state: save_calls.append((ds_id, state)) or True,
    )

    state = service.get_scan_state("ds-never-existed")

    assert isinstance(state, ScanState)
    assert state.last_check is None
    assert state.processed == []
    assert state.snapshot == []
    # Nessun file deve essere stato creato come side-effect
    assert list(tmp_path.iterdir()) == [], (
        f"Nessun file deve essere creato per scan_state inesistente. "
        f"Trovati: {[p.name for p in tmp_path.iterdir()]}"
    )
    assert save_calls == []


def test_get_scan_state_prefers_db_state_over_legacy_file(tmp_path, monkeypatch):
    """DB canonico: se scan_state esiste su Postgres, il file legacy è ignorato."""
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    service = DataSourceService()
    ds_id = "ds-db-wins"
    (tmp_path / f"{ds_id}.json").write_text(
        json.dumps({
            "processed": [
                {
                    "name": "legacy.csv",
                    "processed_at": "2026-01-01T00:00:00+00:00",
                    "pipeline_id": "p-old",
                }
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        datasource_db,
        "get_scan_state",
        lambda _ds_id: datasource_db.DataSourceScanStateRow(
            id=_ds_id,
            scan_state={
                "last_check": "2026-01-02T00:00:00+00:00",
                "strategy": "modified_date",
                "snapshot": [],
                "processed": [
                    {
                        "name": "db.csv",
                        "processed_at": "2026-01-02T00:00:00+00:00",
                        "pipeline_id": "p-db",
                    }
                ],
            },
        ),
    )

    state = service.get_scan_state(ds_id)

    assert [entry.name for entry in state.processed] == ["db.csv"]


def test_get_scan_state_ignores_valid_legacy_file_when_db_state_null(tmp_path, monkeypatch):
    """Fallback legacy rimosso: file valido non viene letto ne' migrato."""
    from app.core.datasources.service import DataSourceService
    import app.core.datasources.db as datasource_db

    service = DataSourceService()
    ds_id = "ds-legacy"
    (tmp_path / f"{ds_id}.json").write_text(
        json.dumps({
            "last_check": "2026-01-03T00:00:00+00:00",
            "strategy": "modified_date",
            "snapshot": [],
            "processed": [
                {
                    "name": "legacy.csv",
                    "processed_at": "2026-01-03T00:00:00+00:00",
                    "pipeline_id": "p-legacy",
                }
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        datasource_db,
        "get_scan_state",
        lambda _ds_id: datasource_db.DataSourceScanStateRow(
            id=_ds_id,
            scan_state=None,
        ),
    )
    saved = []
    monkeypatch.setattr(
        datasource_db,
        "save_scan_state",
        lambda _ds_id, state: saved.append((_ds_id, state)) or True,
    )

    state = service.get_scan_state(ds_id)

    assert state.processed == []
    assert saved == []


def test_save_scan_state_writes_db_not_legacy_file(tmp_path, monkeypatch):
    """save_scan_state non deve più creare il JSON locale come source of truth."""
    from app.core.datasources.service import DataSourceService
    from app.core.datasources.models import ScanState
    import app.core.datasources.db as datasource_db

    service = DataSourceService()
    saved = []
    monkeypatch.setattr(
        datasource_db,
        "save_scan_state",
        lambda ds_id, state: saved.append((ds_id, state)) or True,
    )

    service.save_scan_state("ds-db", ScanState())

    assert saved == [
        (
            "ds-db",
            {
                "last_check": None,
                "strategy": "modified_date",
                "snapshot": [],
                "processed": [],
            },
        )
    ]
    assert list(tmp_path.iterdir()) == []
