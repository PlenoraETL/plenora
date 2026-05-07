"""Contract test — integrazione bulk insert in `_execute_batch`.

Verifica che:
  - Per `operation == "insert"` su Postgres/MySQL, il bulk path
    viene tentato per primo
  - Su `BulkInsertNotSupportedError` (geometry, server-side disable, ecc.)
    avviene fallback automatico ad executemany
  - Per `operation == "update"` o `"upsert"`, il bulk path NON viene
    chiamato (incompatibile)
  - Env `CARICA_BULK_INSERT=off` disabilita anche su INSERT
  - Per dialect Oracle/MSSQL/SQLite, niente bulk (executemany diretto)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.bulk_writers import BulkInsertNotSupportedError
from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _col(src: str, **kw) -> ColumnMapping:
    return ColumnMapping(source_column=src, dest_column=src, **kw)


def _make_writer(db_type: str = "postgres"):
    """Crea un DatabaseWriter senza eseguire __init__ completo (evita
    engine SQLAlchemy reale). Settiamo solo gli attributi necessari
    per `_execute_batch`."""
    from app.core.carica.database_writer import DatabaseWriter
    w = DatabaseWriter.__new__(DatabaseWriter)
    w.db_type = db_type
    config = MagicMock()
    config.schema_name = "public"
    config.table_name = "users"
    w.config = config
    return w


@pytest.fixture
def df():
    return pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})


@pytest.fixture
def cols():
    return [_col("id"), _col("name")]


@pytest.fixture
def conn():
    return MagicMock()


# Bulk INSERT path attivo


def test_insert_postgres_falls_back_to_executemany(monkeypatch, df, cols, conn):
    """Post-rimozione COPY FROM STDIN (2026-04-23, kickoff §9.6): il
    dispatcher raise `BulkInsertNotSupportedError(reason=
    "dialect_not_supported")` anche per PG; `_execute_batch` cattura e
    fa fallback a executemany (conn.execute). Test anti-regressione
    della rimozione: se qualcuno riaggiunge la branch postgres al
    dispatcher, questo test fallisce (`conn.execute` non verrebbe
    chiamato)."""
    from app.core.carica.bulk_writers import BulkInsertNotSupportedError
    monkeypatch.delenv("CARICA_BULK_INSERT", raising=False)  # default auto
    writer = _make_writer("postgres")

    with patch(
        "app.core.carica.bulk_writers.bulk_insert",
        side_effect=BulkInsertNotSupportedError(
            "Bulk insert non implementato per dialect 'postgres'"
        ),
    ) as mock_bulk:
        n = writer._execute_batch(conn, df, cols, "INSERT INTO ...", "insert")

    # Bulk tentato ma rifiutato dal dispatcher (PG tombstoned)
    mock_bulk.assert_called_once()
    kwargs = mock_bulk.call_args.kwargs
    assert kwargs["db_type"] == "postgres"
    assert kwargs["schema"] == "public"
    assert kwargs["table"] == "users"
    # Fallback a executemany: conn.execute chiamato
    conn.execute.assert_called_once()
    # Il fallback ritorna n == len(params_list) == 3
    assert n == 3


def test_insert_mysql_falls_back_to_executemany(monkeypatch, df, cols, conn):
    """Post-rimozione LOAD DATA (2026-04-23, kickoff.md §9.5): il dispatcher
    bulk raise `BulkInsertNotSupportedError(reason="dialect_not_supported")`
    per mysql/mariadb; `_execute_batch` cattura e fa fallback a
    executemany (conn.execute). Test anti-regressione della rimozione."""
    from app.core.carica.bulk_writers import BulkInsertNotSupportedError
    writer = _make_writer("mysql")
    with patch(
        "app.core.carica.bulk_writers.bulk_insert",
        side_effect=BulkInsertNotSupportedError(
            "Bulk insert non implementato per dialect 'mysql'"
        ),
    ) as mock_bulk:
        n = writer._execute_batch(conn, df, cols, "INSERT INTO ...", "insert")
    # Bulk tentato ma rifiutato dal dispatcher (mysql tombstoned)
    mock_bulk.assert_called_once()
    # Fallback a executemany: conn.execute chiamato
    conn.execute.assert_called_once()
    # Il fallback ritorna n == len(params_list) == 3
    assert n == 3


# Fallback automatico


def test_insert_falls_back_to_executemany_on_not_supported(
    monkeypatch, df, cols, conn,
):
    """Se bulk path solleva BulkInsertNotSupportedError, executemany kicks in."""
    writer = _make_writer("postgres")

    with patch(
        "app.core.carica.bulk_writers.bulk_insert",
        side_effect=BulkInsertNotSupportedError("geometry"),
    ):
        # _build_params richiede attributi del writer; usiamo mock
        with patch.object(
            writer, "_build_params", return_value={"id": 1, "name": "a"}
        ):
            n = writer._execute_batch(conn, df, cols, "INSERT...", "insert")

    assert n == 3
    conn.execute.assert_called_once()  # executemany kicked in


def test_insert_falls_back_silently_on_geometry(monkeypatch, df, conn):
    """Geometry colonne → bulk_insert solleva NotSupported → fallback
    silenzioso ad executemany. Niente warning rumoroso (e' il caso
    atteso per catene RFI/imdf)."""
    writer = _make_writer("postgres")
    geo_cols = [_col("id"), _col("geom", is_geometry=True)]
    df_geo = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})

    with patch.object(
        writer, "_build_params", return_value={"id": 1, "geom": "POINT(0 0)"}
    ):
        n = writer._execute_batch(conn, df_geo, geo_cols, "INSERT...", "insert")

    assert n == 1
    conn.execute.assert_called_once()


# Operation diverse da INSERT: niente bulk


@pytest.mark.parametrize("op", ["update", "upsert"])
def test_non_insert_operations_skip_bulk_path(op, df, cols, conn):
    """Update/upsert non hanno equivalente in COPY/LOAD DATA → executemany direct."""
    writer = _make_writer("postgres")
    with patch(
        "app.core.carica.bulk_writers.bulk_insert"
    ) as mock_bulk:
        with patch.object(writer, "_build_params", return_value={"id": 1}):
            writer._execute_batch(conn, df, cols, "UPDATE...", op)
    mock_bulk.assert_not_called()
    conn.execute.assert_called_once()


# Feature flag


def test_env_off_disables_bulk_even_on_insert(monkeypatch, df, cols, conn):
    monkeypatch.setenv("CARICA_BULK_INSERT", "off")
    writer = _make_writer("postgres")

    with patch(
        "app.core.carica.bulk_writers.bulk_insert"
    ) as mock_bulk:
        with patch.object(writer, "_build_params", return_value={"id": 1}):
            writer._execute_batch(conn, df, cols, "INSERT...", "insert")

    mock_bulk.assert_not_called()
    conn.execute.assert_called_once()


# Dialect non supportati


@pytest.mark.parametrize("dialect", ["oracle", "mssql", "sqlite"])
def test_unsupported_dialect_falls_back_to_executemany(
    dialect, df, cols, conn,
):
    """Su Oracle/MSSQL/SQLite il dispatcher solleva NotSupported subito
    → executemany. Niente errore visibile per l'utente."""
    writer = _make_writer(dialect)
    with patch.object(writer, "_build_params", return_value={"id": 1}):
        n = writer._execute_batch(conn, df, cols, "INSERT...", "insert")
    assert n == 3
    conn.execute.assert_called_once()


# Empty batch shortcut


def test_empty_batch_returns_zero_no_bulk_call(cols, conn):
    writer = _make_writer("postgres")
    empty_df = pd.DataFrame({"id": [], "name": []})
    with patch(
        "app.core.carica.bulk_writers.bulk_insert"
    ) as mock_bulk:
        n = writer._execute_batch(conn, empty_df, cols, "INSERT...", "insert")
    assert n == 0
    mock_bulk.assert_not_called()
    conn.execute.assert_not_called()


# CRITICAL: target_table override (stage+swap correctness)
# Pre-fix il bulk path passava SEMPRE config.table_name. Nei flow
# stage+swap (replace_atomic, stage_swap_replace), insert_sql punta a
# `<target>__stg` ma il bulk caricava direttamente sul `<target>`,
# rompendo l'atomicita' (i dati nuovi finivano sul target finale prima
# del rename, esponendo stato incoerente).
#
# Test che codificano l'override esplicito.


def test_bulk_uses_target_table_override_when_provided(df, cols, conn):
    """Caller passa target_table=stg → bulk scrive su stg, non config.table_name."""
    writer = _make_writer("postgres")
    writer.config.table_name = "users"
    writer.config.schema_name = "public"

    with patch(
        "app.core.carica.bulk_writers.bulk_insert", return_value=3
    ) as mock_bulk:
        writer._execute_batch(
            conn, df, cols,
            'INSERT INTO "public"."users__stg" ...',
            "insert",
            target_table="users__stg",
        )

    mock_bulk.assert_called_once()
    kwargs = mock_bulk.call_args.kwargs
    assert kwargs["table"] == "users__stg", (
        "BUG REGRESSION: bulk path ha ignorato target_table=users__stg "
        "e ha scritto sulla tabella di config (users). Stage+swap rotto."
    )
    assert kwargs["schema"] == "public"


def test_bulk_uses_config_table_when_no_override(df, cols, conn):
    """Backward compat: senza target_table, fallback a config.table_name."""
    writer = _make_writer("postgres")
    writer.config.table_name = "users"
    writer.config.schema_name = "public"

    with patch(
        "app.core.carica.bulk_writers.bulk_insert", return_value=3
    ) as mock_bulk:
        writer._execute_batch(
            conn, df, cols, "INSERT INTO ...", "insert",
        )

    kwargs = mock_bulk.call_args.kwargs
    assert kwargs["table"] == "users"
    assert kwargs["schema"] == "public"


def test_bulk_uses_target_schema_override_when_provided(df, cols, conn):
    """target_schema esplicito ha precedenza su config.schema_name."""
    writer = _make_writer("postgres")
    writer.config.table_name = "users"
    writer.config.schema_name = "public"

    with patch(
        "app.core.carica.bulk_writers.bulk_insert", return_value=3
    ) as mock_bulk:
        writer._execute_batch(
            conn, df, cols, "INSERT INTO ...", "insert",
            target_table="users", target_schema="staging_schema",
        )

    kwargs = mock_bulk.call_args.kwargs
    assert kwargs["schema"] == "staging_schema"


def test_target_schema_empty_string_is_passed_through(df, cols, conn):
    """target_schema='' (no schema) deve essere rispettato (non sostituito
    dal default config.schema_name). Caso uso originario: MySQL senza
    schema esplicito; post-rimozione LOAD DATA (2026-04-23) il test e'
    stato reimpostato su postgres — il contract del dispatcher sulla
    propagazione di target_schema e' dialect-agnostic."""
    writer = _make_writer("postgres")
    writer.config.table_name = "users"
    writer.config.schema_name = "default_db"

    with patch(
        "app.core.carica.bulk_writers.bulk_insert", return_value=3
    ) as mock_bulk:
        writer._execute_batch(
            conn, df, cols, "INSERT INTO ...", "insert",
            target_table="users", target_schema="",
        )

    kwargs = mock_bulk.call_args.kwargs
    assert kwargs["schema"] == ""


# Stage+swap call paths integration: i 3 path che oggi passano
# target_table devono propagarlo correttamente fino al bulk dispatcher


def test_stage_swap_callsite_propagates_stg_table_to_bulk():
    """Smoke che i 3 metodi shadow/interim passano effettivamente
    `target_table` al bulk path (regression guard via AST scan).

    Contract verificato: ognuno dei 3 metodi caller deve avere almeno
    1 callsite di `_execute_batch(..., target_table=...)`. Indipendente
    dalla posizione fisica del metodo (modulo facade, mixin, sotto-modulo).

    Modernization B2.4a — behavior contract vs source-location contract
    ====================================================================
    Pre-B2.4a il test parsava `Path(.../database_writer.py).read_text()`
    cercando i callsite nel sorgente del MODULO. Questo legava il test
    alla posizione fisica dei 3 metodi: spostarli in mixin (B2.4b
    `ReplaceStrategiesMixin`) avrebbe rotto il test (i callsite non
    sarebbero piu' stati nel source di `database_writer.py`).

    Post-B2.4a: parsa `inspect.getsource(<unbound_method>)` per ciascuno
    dei 3 metodi caller. `inspect.getsource` legge via
    `__code__.co_filename` + line numbers, quindi trova il source
    dovunque il metodo sia fisicamente definito (modulo facade, mixin,
    sotto-modulo). Il contract bit-per-bit "ogni metodo caller deve
    avere ≥1 callsite di `_execute_batch(target_table=...)`" e' preservato.

    Inoltre la modernizzazione e' piu' precisa: prima un eventuale
    spostamento di un callsite in un metodo NON-target sarebbe stato
    accettato dal vecchio test (purche' restassero ≥3 callsite totali
    nel file). Ora il contract e' per-metodo: ogni dei 3 metodi target
    DEVE avere il callsite (anti-regressione piu' stretta).
    """
    import ast
    import inspect

    # Lazy import allineato al pattern degli altri test in questo file
    # (vedi `_make_writer` linea 37).
    from app.core.carica.database_writer import DatabaseWriter

    # I 3 metodi shadow/interim che DEVONO passare target_table al bulk
    # path. Rappresentati come unbound method della classe DatabaseWriter:
    # `inspect.getsource(method)` risolve via __code__.co_filename →
    # funziona dovunque il metodo sia definito (database_writer.py o
    # ReplaceStrategiesMixin in writer_replace_strategies.py post-B2.4b).
    methods = {
        "_replace_atomic": DatabaseWriter._replace_atomic,
        "_truncate_insert_stage_swap": DatabaseWriter._truncate_insert_stage_swap,
        "_truncate_insert_oracle_redef": DatabaseWriter._truncate_insert_oracle_redef,
    }

    method_callsite_summary: dict[str, dict[str, list[int]]] = {}

    for method_name, method in methods.items():
        try:
            method_src = inspect.getsource(method)
        except (OSError, TypeError) as e:
            pytest.fail(
                f"inspect.getsource({method.__qualname__}) ha fallito: {e}. "
                f"Se il metodo e' stato rinominato/spostato/wrappato, "
                f"aggiornare il riferimento nel test (NON rimuovere il "
                f"contract: il callsite di _execute_batch(target_table=...) "
                f"deve restare per il path shadow/interim)."
            )

        # `inspect.getsource(method)` ritorna il source con indentazione
        # di classe (4 spazi). `ast.parse` rifiuta indented code a top
        # level → IndentationError. `textwrap.dedent` NON funziona perche'
        # i metodi (es. `_truncate_insert_oracle_redef`) contengono
        # multiline raw SQL con `BEGIN`/`END;` a colonna 0 — common
        # leading whitespace risulta 0 → nessun dedent.
        # Soluzione: wrap in scaffold sintetico `class _S:\n<src>` →
        # `ast.parse` accetta il metodo indented dentro la classe scope.
        # Walking l'AST trova le `ast.Call` indipendentemente dal wrapping.
        tree = ast.parse("class _S:\n" + method_src)

        with_target: list[int] = []
        without_target: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "_execute_batch":
                continue
            has_target = any(kw.arg == "target_table" for kw in node.keywords)
            if has_target:
                with_target.append(node.lineno)
            else:
                without_target.append(node.lineno)

        method_callsite_summary[method_name] = {
            "with_target": with_target,
            "without_target": without_target,
        }

    # Contract per-metodo: ognuno dei 3 metodi target DEVE avere
    # almeno 1 callsite di `_execute_batch(target_table=...)`.
    methods_missing_callsite = [
        name for name, info in method_callsite_summary.items()
        if not info["with_target"]
    ]
    assert not methods_missing_callsite, (
        f"Aspettato che ognuno dei 3 metodi shadow/interim abbia "
        f"≥1 callsite di `_execute_batch(target_table=...)`, ma "
        f"questi metodi NON ne hanno: {methods_missing_callsite}. "
        f"Summary completo: {method_callsite_summary}. "
        f"Senza target_table il bulk path scrive sulla tabella finale "
        f"invece che sulla shadow/interim, rompendo la semantica di "
        f"stage+swap / replace_atomic / oracle redef."
    )


# Stage+swap dispatch telemetry (Batch 3.4) — chiude il gap di visibilita'
# sulle invocazioni TRUNCATE_INSERT grandi per dialect
#
# Emit 1 log INFO all'ingresso di _truncate_insert_stage_swap e di
# _truncate_insert_oracle_redef con event="stage_swap.dispatched", path,
# dialect, threshold. Abilita la decisione di rimozione MySQL LOAD DATA:
# se stage+swap mysql non appare nel periodo di osservazione, la
# rimozione del bulk_load_mysql non impatta TRUNCATE_INSERT grandi.


@pytest.fixture
def writer_caplog(caplog):
    """Caplog per il logger di database_writer (propagate=False via 'app')."""
    target = logging.getLogger("app.core.carica.writer")
    target.addHandler(caplog.handler)
    orig_level = target.level
    target.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(orig_level)


def _stage_swap_records(caplog):
    return [
        r for r in caplog.records
        if getattr(r, "event", None) == "stage_swap.dispatched"
    ]


def test_stage_swap_dispatched_emits_for_mysql(writer_caplog):
    """_truncate_insert_stage_swap emette stage_swap.dispatched con
    dialect=mysql e path=stage_swap. Forziamo early exit via raise sulla
    prima chiamata DDL, dopo il log gia' emesso."""
    from app.core.carica.database_writer import DatabaseWriter
    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.db_type = "mysql"
    config = MagicMock()
    config.schema_name = "mydb"
    config.table_name = "users"
    writer.config = config
    # ddl: forza raise subito dopo il log di dispatch
    ddl = MagicMock()
    ddl.generate_drop_if_exists.side_effect = RuntimeError("halt after log")
    writer.ddl = ddl

    df_ = pd.DataFrame({"id": [1, 2, 3]})
    cols_ = [_col("id")]
    result = MagicMock()

    with pytest.raises(RuntimeError, match="halt after log"):
        writer._truncate_insert_stage_swap(df_, cols_, result)

    recs = _stage_swap_records(writer_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.dialect == "mysql"
    assert r.path == "stage_swap"
    assert r.schema == "mydb"
    assert r.table == "users"
    assert r.rows == 3


def test_oracle_redef_dispatched_emits_for_oracle(writer_caplog):
    """_truncate_insert_oracle_redef emette stage_swap.dispatched con
    dialect=oracle e path=oracle_redef."""
    from app.core.carica.database_writer import DatabaseWriter
    writer = DatabaseWriter.__new__(DatabaseWriter)
    writer.db_type = "oracle"
    config = MagicMock()
    config.schema_name = "hr"
    config.table_name = "employees"
    writer.config = config
    # ddl: forza raise subito dopo il log di dispatch
    ddl = MagicMock()
    ddl.generate_insert.side_effect = RuntimeError("halt after log")
    writer.ddl = ddl

    df_ = pd.DataFrame({"id": [1, 2]})
    cols_ = [_col("id")]
    result = MagicMock()

    with pytest.raises(RuntimeError, match="halt after log"):
        writer._truncate_insert_oracle_redef(df_, cols_, result)

    recs = _stage_swap_records(writer_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.dialect == "oracle"
    assert r.path == "oracle_redef"
    # schema e table sono uppercased da _truncate_insert_oracle_redef
    assert r.schema == "HR"
    assert r.table == "EMPLOYEES"
    assert r.rows == 2
