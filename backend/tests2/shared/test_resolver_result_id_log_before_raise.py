"""Contract test per log-before-raise nei broad catch di
``ArtifactResolver._materialize_from_result_id`` (Batch 8 C3).

Blinda il fix §3.2 S8 / §6.2 F6.6 (audit storage/provider 2026-04-22):
entrambi i broad catch (``db_service.get_result`` e
``db_service.get_result_data``) emettono ``logger.error`` con tag
``[GESTITO]`` e ``exc_info=True`` **prima** di rilanciare
``ArtifactResolutionError(...) from e``.

Motivazione:
- Il pattern ``raise DomainError(...) from e`` preserva la chain del
  traceback, ma la visibilità server-side dipende dal caller: se nessuno
  logga, l'errore originale viene "silenziato" in stacktrace senza mai
  passare dal log store. Il pattern già consolidato in
  ``source_loader.py`` (best-in-class del cluster) è
  ``logger.error([SECURITY]/[GESTITO], exc_info=True)`` **prima** del raise.

Invariants bloccati:
- ``ArtifactResolutionError`` viene ancora sollevata (fail-closed).
- ``__cause__`` è l'eccezione originale (chain preservata).
- Il messaggio di ``ArtifactResolutionError`` è **invariato** rispetto
  al pre-C3 (UX/contract del caller invariato).
- Un record ERROR con tag ``[GESTITO]`` è emesso server-side con
  ``exc_info=True`` (stacktrace catturato).

Regressione protetta:
- Rimozione del ``logger.error(exc_info=True)`` riporta la silenziosità
  pre-fix → warning a review.
- Rimozione del ``from e`` o cambio della classe eccezione rompe il test
  (fail-closed deve restare ``ArtifactResolutionError`` con chain).
"""

from __future__ import annotations

import logging

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_RESOLVER_LOGGER_NAME = "app.core.artifacts.resolver"


class _RecordCollector(logging.Handler):
    """Collector locale: il ``StructuredLogger`` ha ``propagate=False``
    quindi ``caplog`` (sul root) non riceve i record — agganciamo
    direttamente al logger di modulo."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_resolver_logs():
    logger = logging.getLogger(_RESOLVER_LOGGER_NAME)
    handler = _RecordCollector()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _ensure_database_url_for_module_import(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fake:fake@localhost:5432/fake",
    )
    yield


def _assert_gestito_error_with_exc_info(
    handler: _RecordCollector, result_id: str, expected_phrase: str,
):
    """Helper: verifica che il collector abbia almeno un record ERROR
    con tag ``[GESTITO]``, il result_id per correlazione, la frase
    di contesto, e ``exc_info`` popolato (exc_info=True passato)."""
    error_records = [
        r for r in handler.records if r.levelno == logging.ERROR
    ]
    assert error_records, (
        "Atteso almeno un log ERROR server-side sul broad catch."
    )
    tagged = [r for r in error_records if "[GESTITO]" in r.getMessage()]
    assert tagged, (
        "Il log ERROR deve usare il prefisso '[GESTITO]' per coerenza "
        "taxonomy cluster storage/provider."
    )
    # exc_info=True → record.exc_info è tupla (type, value, tb) non-None
    assert any(r.exc_info for r in tagged), (
        "logger.error DEVE essere chiamato con exc_info=True per "
        "preservare lo stacktrace server-side (audit §3.2 S8)."
    )
    messages = " || ".join(r.getMessage() for r in tagged)
    assert result_id in messages, (
        f"Il log DEVE includere il result_id per correlazione: "
        f"ottenuto {messages!r}"
    )
    assert expected_phrase in messages, (
        f"Il log DEVE contenere la frase di contesto {expected_phrase!r}: "
        f"ottenuto {messages!r}"
    )


# ─── 1) get_result (lookup row) broad catch ───────────────────────────────

def test_materialize_from_result_id_logs_error_before_raise_on_lookup_failure(
    monkeypatch, capture_resolver_logs,
):
    """``db_service.get_result`` solleva → log ERROR ``[GESTITO]`` con
    ``exc_info=True`` PRIMA del raise di ``ArtifactResolutionError``.
    """
    from app.core.artifacts.resolver import ArtifactResolver
    from app.core.artifacts.types import ArtifactResolutionError
    import app.db.db_service as db_service

    class _UnderlyingDbError(RuntimeError):
        """Simula un errore dalla libreria PG (psycopg2 / SQLAlchemy / ...)."""

    def _raising_get_result(result_id):
        raise _UnderlyingDbError(
            f"connection lost while fetching result {result_id}"
        )

    monkeypatch.setattr(db_service, "get_result", _raising_get_result)

    resolver = ArtifactResolver()

    with pytest.raises(ArtifactResolutionError) as excinfo:
        resolver._materialize_from_result_id(
            result_id="res-123",
            expected_project_id="proj-abc",
        )

    # Contract 1: messaggio del DomainError invariato (UX/caller contract)
    assert "lookup result_id 'res-123' fallito" in str(excinfo.value), (
        f"Il messaggio di ArtifactResolutionError NON deve essere cambiato "
        f"dal fix C3: ottenuto {str(excinfo.value)!r}"
    )

    # Contract 2: chain preservata (from e)
    assert excinfo.value.__cause__ is not None, (
        "`raise ArtifactResolutionError(...) from e` DEVE essere preservato "
        "→ __cause__ non None. Rimuovere `from e` romperebbe la chain."
    )
    assert isinstance(excinfo.value.__cause__, _UnderlyingDbError), (
        f"Il __cause__ DEVE essere l'eccezione originale della libreria, "
        f"ottenuto {type(excinfo.value.__cause__).__name__}"
    )

    # Contract 3: log ERROR [GESTITO] con exc_info prima del raise
    _assert_gestito_error_with_exc_info(
        capture_resolver_logs,
        result_id="res-123",
        expected_phrase="lookup result_id",
    )


# ─── 2) get_result_data (blob read) broad catch ───────────────────────────

def test_materialize_from_result_id_logs_error_before_raise_on_blob_read_failure(
    monkeypatch, capture_resolver_logs,
):
    """BE-GO-READY-RESOURCE-ENGINE-019: il blob fetch ora delega a
    ``default_registry().get(ref, mode='path')``. Il broad-except
    nel resolver cattura eccezioni NON-FileNotFoundError dal
    registry (fault della libreria PG / driver / I/O di basso
    livello) e le traduce in ``ArtifactResolutionError`` con il
    messaggio canonical "lettura blob ... fallita" + log ERROR
    [GESTITO] + exc_info=True PRIMA del raise. Path richiede row
    valida + tenant match: mockiamo ``get_result`` per ritornare
    una row compatibile (incl. ``group_id`` non-empty richiesto
    dall'engine) cosi' il primo catch non intercetta il test.
    """
    from app.core.artifacts.resolver import ArtifactResolver
    from app.core.artifacts.types import ArtifactResolutionError
    import app.core.artifacts.resolver as resolver_mod
    import app.db.db_service as db_service
    from types import SimpleNamespace

    class _UnderlyingBlobError(IOError):
        """Simula un errore di lettura blob (I/O, timeout, corruzione)."""

    def _ok_get_result(result_id):
        return {
            "id": result_id,
            "project_id": "proj-abc",
            "group_id": "grp-1",
            "format": "csv",
        }

    monkeypatch.setattr(db_service, "get_result", _ok_get_result)

    # 019: l'errore di lettura blob arriva dal registry, NON da
    # `db_service.get_result_data`. Mock il registry per simulare
    # un fault del loader (non-FileNotFoundError -> broad except).
    def _raising_get(ref, *, mode):  # noqa: ARG001
        raise _UnderlyingBlobError(
            f"blob corruption detected on {ref.result_id}"
        )

    monkeypatch.setattr(
        resolver_mod,
        "default_registry",
        lambda: SimpleNamespace(get=_raising_get),
    )

    resolver = ArtifactResolver()

    with pytest.raises(ArtifactResolutionError) as excinfo:
        resolver._materialize_from_result_id(
            result_id="res-456",
            expected_project_id="proj-abc",
        )

    # Contract 1: messaggio invariato
    assert "lettura blob result_id 'res-456' fallita" in str(excinfo.value), (
        f"Il messaggio di ArtifactResolutionError NON deve essere cambiato, "
        f"ottenuto {str(excinfo.value)!r}"
    )

    # Contract 2: chain preservata
    assert excinfo.value.__cause__ is not None
    assert isinstance(excinfo.value.__cause__, _UnderlyingBlobError)

    # Contract 3: log [GESTITO] + exc_info
    _assert_gestito_error_with_exc_info(
        capture_resolver_logs,
        result_id="res-456",
        expected_phrase="lettura blob result_id",
    )


# ─── 3) Happy path e branch tenant-mismatch non-toccati dal fix ──────────

def test_materialize_from_result_id_happy_path_unaffected_by_log_fix(
    monkeypatch, capture_resolver_logs,
):
    """BE-GO-READY-RESOURCE-ENGINE-019: se ``get_result`` ritorna
    una row OK (incl. ``group_id``) e il registry ritorna un
    Resource path-mode, il path di successo NON deve emettere
    alcun log ``[GESTITO]`` — il fix C3 tocca solo i due broad
    catch.
    """
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from app.core.artifacts.resolver import ArtifactResolver
    import app.core.artifacts.resolver as resolver_mod
    import app.db.db_service as db_service
    from app.core.resources import Resource

    monkeypatch.setattr(
        db_service, "get_result",
        lambda rid: {
            "id": rid,
            "project_id": "proj-x",
            "group_id": "grp-1",
            "format": "csv",
        },
    )

    # 019: il registry materializza il blob in un tempfile (path
    # mode) col prefix canonico `plenora-result-blob-`. Il
    # resolver wrappa il path in MaterializedArtifact e si occupa
    # del lifecycle.
    def _ok_get(ref, *, mode):  # noqa: ARG001
        tf = tempfile.NamedTemporaryFile(
            delete=False,
            prefix="plenora-result-blob-",
            suffix=".csv",
        )
        try:
            tf.write(b"col1,col2\n1,2\n")
        finally:
            tf.close()
        return Resource(
            kind="result_blob",
            size_bytes=12,
            path=Path(tf.name),
            is_temporary=True,
        )

    monkeypatch.setattr(
        resolver_mod,
        "default_registry",
        lambda: SimpleNamespace(get=_ok_get),
    )

    resolver = ArtifactResolver()
    artifact = resolver._materialize_from_result_id(
        result_id="res-ok",
        expected_project_id="proj-x",
    )

    assert artifact is not None
    # Cleanup tempfile se materializzato a disco
    try:
        if artifact.path and artifact.path.exists():
            artifact.path.unlink()
    except (AttributeError, OSError):
        pass

    # Nessun log ERROR [GESTITO] nel path di successo
    gestito = [
        r for r in capture_resolver_logs.records
        if r.levelno == logging.ERROR and "[GESTITO]" in r.getMessage()
    ]
    assert gestito == [], (
        f"Il happy path NON deve emettere log ERROR [GESTITO]: "
        f"trovati {[r.getMessage() for r in gestito]}"
    )


def test_materialize_from_result_id_tenant_mismatch_does_not_log_gestito(
    monkeypatch, capture_resolver_logs,
):
    """Difensivo: cross-tenant fail-closed (result row esiste ma
    ``project_id`` mismatch) solleva ``ArtifactResolutionError`` senza
    passare dai due broad catch → nessun log ``[GESTITO]`` emesso.

    Questo blinda che il fix C3 non abbia allargato troppo la logger
    coverage a branch non-catch (es. spostare il logger all'inizio della
    funzione e loggare ogni raise, incluso quello cross-tenant).
    """
    from app.core.artifacts.resolver import ArtifactResolver
    from app.core.artifacts.types import ArtifactResolutionError
    import app.db.db_service as db_service

    monkeypatch.setattr(
        db_service, "get_result",
        lambda rid: {"id": rid, "project_id": "OTHER-tenant", "format": "csv"},
    )

    resolver = ArtifactResolver()

    with pytest.raises(ArtifactResolutionError) as excinfo:
        resolver._materialize_from_result_id(
            result_id="res-mismatch",
            expected_project_id="proj-expected",
        )

    assert "non appartiene al" in str(excinfo.value)

    gestito = [
        r for r in capture_resolver_logs.records
        if r.levelno == logging.ERROR and "[GESTITO]" in r.getMessage()
    ]
    assert gestito == [], (
        f"Il branch cross-tenant NON deve emettere log [GESTITO] "
        f"(non è un broad catch): trovati {[r.getMessage() for r in gestito]}"
    )
