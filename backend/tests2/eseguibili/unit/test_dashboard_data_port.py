"""Porta unica dei «dati dashboard» di un task (11/11/2026).

Raccolta: ``docs/audits/grafana-dashboard-raccolta-2026-11-11.md`` (C, D).

Test di enumerazione: analizzano ``backend/app`` e ``workers`` (AST) e
falliscono se un percorso NUOVO legge, scrive o cancella nella cartella del
task di un Eseguibile senza essere dichiarato in
``dashboard_data.STORAGE_ACCESS_SITES``, se un percorso nuovo cancella gli
output (``delete_task_outputs``) o legge la copia senza lasciapassare.
Poi il comportamento su uno storage finto: la copia resta al download, sparisce
alla pulizia completa e alla scadenza, non rende mai ambiguo il download.
"""

from __future__ import annotations

import ast
import json
import math
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_STORAGE_MODULE = "app.core.eseguibili.storage"
_STORAGE_FUNCTIONS = frozenset({
    "find_keys", "save_bytes", "save_chunks", "save", "delete_key", "delete_by_task", "open_stream",
    "open_task_stream", "list_domain_keys", "ExecutableOutputWriter", "_key_for",
})


def _source_files():
    for root in (_BACKEND_ROOT / "app", _BACKEND_ROOT / "workers"):
        for path in sorted(root.rglob("*.py")):
            yield path, path.relative_to(_BACKEND_ROOT).as_posix()


def _module_of(rel: str) -> str:
    return rel[:-3].replace("/", ".").removesuffix(".__init__")


def _resolve_from(rel: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = _module_of(rel).split(".")
    if not rel.endswith("__init__.py"):
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _aliases(rel: str, tree: ast.AST, target: str) -> tuple[set[str], set[str]]:
    """(nomi locali del modulo ``target``, nomi locali importati da lui)."""
    module_names: set[str] = set()
    function_names: set[str] = set()
    parent, _, leaf = target.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target and alias.asname:
                    module_names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            source = _resolve_from(rel, node)
            for alias in node.names:
                if source == parent and alias.name == leaf:
                    module_names.add(alias.asname or alias.name)
                elif source == target:
                    function_names.add(alias.asname or alias.name)
    return module_names, function_names


def _calls_in(rel: str, tree: ast.AST, target: str, functions: frozenset[str] | None):
    """(rel, funzione che contiene la chiamata, nome, nodo) per ogni chiamata."""
    module_names, function_names = _aliases(rel, tree, target)
    if not module_names and not function_names:
        return []
    found = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            # Scope = la funzione (o il metodo) di primo livello che contiene la chiamata.
            is_function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            inner = child.name if is_function and scope == "<modulo>" else scope
            if isinstance(child, ast.Call):
                func = child.func
                name = None
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in module_names:
                    name = func.attr
                elif isinstance(func, ast.Name) and func.id in function_names:
                    name = func.id
                if name and (functions is None or name in functions):
                    found.append((rel, scope, name, child))
            visit(child, inner)

    visit(tree, "<modulo>")
    return found


def _scan(target: str, functions: frozenset[str] | None = None, *, skip: tuple[str, ...] = ()):
    hits = []
    for path, rel in _source_files():
        if rel in skip:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits.extend(_calls_in(rel, tree, target, functions))
    return hits


# ── Enumerazione ────────────────────────────────────────────────────────────

def test_every_task_storage_access_is_declared():
    """Ogni chiamante di ``eseguibili.storage`` (fuori dal modulo stesso) e'
    dichiarato con cosa fa della copia della dashboard. Un percorso nuovo di
    lettura/cancellazione dell'output che non passa dalla porta fallisce qui."""
    from app.core.eseguibili.dashboard_data import STORAGE_ACCESS_SITES

    found = {(rel, scope) for rel, scope, _name, _node in
             _scan(_STORAGE_MODULE, _STORAGE_FUNCTIONS, skip=("app/core/eseguibili/storage.py",))}
    assert found == set(STORAGE_ACCESS_SITES), (
        f"non dichiarati: {sorted(found - set(STORAGE_ACCESS_SITES))}; "
        f"dichiarati ma spariti: {sorted(set(STORAGE_ACCESS_SITES) - found)}"
    )


def test_output_deletion_has_exactly_two_paths():
    """Solo due percorsi cancellano gli output di un task: il consumo del
    download (conserva owner e copia) e la pulizia completa (toglie tutto)."""
    calls = _scan("app.core.eseguibili.output_store", frozenset({"delete_task_outputs"}))
    by_site = {}
    for rel, scope, _name, node in calls:
        retain = next((kw.value for kw in node.keywords if kw.arg == "retain_owner"), None)
        by_site[(rel, scope)] = isinstance(retain, ast.Constant) and retain.value is True
    assert by_site == {
        ("app/core/eseguibili/download_response.py", "build_consume_once_response"): True,
        ("app/core/orchestrator/task_cleanup.py", "purge_terminal_task"): False,
    }


def test_dashboard_copy_is_read_only_behind_the_pass():
    """La copia si legge solo dalla regola del lasciapassare
    (``dashboard.read_granted``, dopo ``authorize_read``) e la sua scadenza
    solo per emetterlo; la scrive solo il raccoglitore (``save``, dentro la
    porta) e la pulisce il beat con il suo ciclo dedicato."""
    calls = {(rel, scope, name) for rel, scope, name, _node in _scan(
        "app.core.eseguibili.dashboard_data", frozenset({"read", "current_key", "write", "delete", "purge_expired"}),
        skip=("app/core/eseguibili/dashboard_data.py",),
    )}
    assert calls == {
        ("app/core/eseguibili/dashboard.py", "issue_pass", "current_key"),
        ("app/core/eseguibili/dashboard.py", "read_granted", "read"),
        ("workers/beat_scheduler.py", "_cleanup_dashboard_copies", "purge_expired"),
    }


def test_persistence_is_the_only_writer_with_dashboard_copy():
    """``ExecutableOutputPersistence(dashboard_copy=...)`` riceve il flag solo
    dal contesto del contratto; nessun altro produttore scrive la copia."""
    sites = set()
    for path, rel in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(kw.arg == "dashboard_copy" for kw in node.keywords):
                func = node.func
                sites.add((rel, getattr(func, "attr", getattr(func, "id", ""))))
    assert sites == {
        ("app/core/orchestrator/service.py", "ExecutableOutputPersistence"),
        ("app/core/arricchisci/enrichment_run_mode.py", "ExecutableOutputPersistence"),
        ("app/core/arricchisci/enrichment_run_mode.py", "EseguibileRunMode"),
        ("workers/tasks/enrichment_task.py", "for_task"),
    }


# ── Storage finto ───────────────────────────────────────────────────────────

class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.tags: dict[str, dict[str, str]] = {}

    def upload_bytes(self, data: bytes, key: str) -> None:
        self.objects[key] = bytes(data)

    def set_object_tags(self, key, tags):
        self.tags[key] = dict(tags)

    def list_objects(self, prefix):
        return [{"key": k, "size": len(v), "last_modified": None} for k, v in self.objects.items()
                if k.startswith(prefix)]

    def get_object_stream(self, key):
        return {"body": BytesIO(self.objects[key]), "size": len(self.objects[key])}

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    from app.core.eseguibili import storage

    fake = _FakeStorage()
    monkeypatch.setattr(storage, "_storage", lambda: fake)
    return fake


def _write_copy(rows, *, now=1_000.0, task="task-1"):
    from app.core.eseguibili import dashboard_data

    collector = dashboard_data.DashboardRowsCollector(max_rows=50_000, max_bytes=1 << 20)
    collector.add_rows(["a", "b"], rows)
    return dashboard_data.write(task, group_id="g1", collector=collector, now=now)


def _task_folder(fake_storage, task="task-1"):
    from app.core.eseguibili import output_store

    output_store.save_task_owner(task, "exec_1", "u1", group_id="g1")
    fake_storage.objects[f"tenants/g1/eseguibili-outputs/{task}/report.csv"] = b"a,b\n1,2\n"
    fake_storage.objects[f"tenants/g1/eseguibili-outputs/{task}/__internal__rest-file__n1__x.zip"] = b"zip"


def test_copy_is_an_internal_ephemeral_artifact_never_an_output(fake_storage):
    from app.core.eseguibili import dashboard_data, output_store

    _task_folder(fake_storage)
    key = _write_copy([(1, "x")])
    assert key == "tenants/g1/eseguibili-outputs/task-1/__internal__dashboard__dati__scade-2800.json"
    assert dashboard_data.is_dashboard_data_key(key)
    assert fake_storage.tags[key] == {"plenora-retention": "ephemeral"}
    # Mai «Output ambiguo»: il download vede solo l'output.
    assert output_store.find_output_keys("task-1", group_id="g1") == [
        "tenants/g1/eseguibili-outputs/task-1/report.csv",
    ]


def test_download_keeps_owner_and_copy_full_cleanup_removes_everything(fake_storage):
    from app.core.eseguibili import dashboard_data, output_store

    _task_folder(fake_storage)
    key = _write_copy([(1, "x")])
    output_store.delete_task_outputs("task-1", group_id="g1", retain_owner=True)  # download
    remaining = set(fake_storage.objects)
    assert remaining == {"tenants/g1/eseguibili-outputs/task-1/__owner.json", key}
    assert json.loads(dashboard_data.read("task-1", group_id="g1", now=1_500.0)) == [{"a": 1, "b": "x"}]

    output_store.delete_task_outputs("task-1", group_id="g1")  # full-cleanup / scarto
    assert fake_storage.objects == {}
    with pytest.raises(dashboard_data.DashboardDataGone):
        dashboard_data.read("task-1", group_id="g1", now=1_500.0)


def test_read_is_not_destructive_and_expired_copy_is_gone_and_deleted(fake_storage):
    from app.core.eseguibili import dashboard_data

    key = _write_copy([(1, "x"), (2, "y")])
    for _ in range(3):
        assert len(json.loads(dashboard_data.read("task-1", group_id="g1", now=2_000.0))) == 2
    with pytest.raises(dashboard_data.DashboardDataGone):
        dashboard_data.read("task-1", group_id="g1", now=2_800.0)
    assert key not in fake_storage.objects


def test_rewrite_replaces_the_previous_copy(fake_storage):
    from app.core.eseguibili import dashboard_data

    first = _write_copy([(1, "x")], now=1_000.0)
    second = _write_copy([(2, "y")], now=1_100.0)
    assert first not in fake_storage.objects and second in fake_storage.objects
    assert json.loads(dashboard_data.read("task-1", group_id="g1", now=1_200.0)) == [{"a": 2, "b": "y"}]


def test_beat_purge_removes_only_expired_copies(fake_storage):
    from app.core.eseguibili import dashboard_data

    old = _write_copy([(1, "x")], now=1_000.0, task="old")
    fresh = _write_copy([(1, "x")], now=5_000.0, task="fresh")
    fake_storage.objects["tenants/g1/eseguibili-outputs/old/report.csv"] = b"keep"
    assert dashboard_data.purge_expired(["g1", "g2"], now=3_000.0) == 1
    assert old not in fake_storage.objects and fresh in fake_storage.objects
    assert "tenants/g1/eseguibili-outputs/old/report.csv" in fake_storage.objects


# ── Collettore ──────────────────────────────────────────────────────────────

def test_collector_caps_rows_and_bytes_and_normalizes_values():
    from app.core.eseguibili.dashboard_data import DashboardRowsCollector

    capped = DashboardRowsCollector(max_rows=3, max_bytes=1 << 20)
    capped.add_rows(["n"], [(i,) for i in range(10)])
    assert capped.rows == 3 and capped.truncated and capped.full
    assert json.loads(capped.to_bytes()) == [{"n": 0}, {"n": 1}, {"n": 2}]

    tiny = DashboardRowsCollector(max_rows=100, max_bytes=20)
    tiny.add_rows(["n"], [(i,) for i in range(10)])
    assert len(tiny.to_bytes()) <= 20 and tiny.truncated

    values = DashboardRowsCollector(max_rows=10, max_bytes=1 << 20)
    values.add_rows(
        ["f", "d", "t", "day", "nan", "inf", "blob"],
        [(1.5, Decimal("2.50"), datetime(2026, 11, 11, 9, 30), date(2026, 11, 11), math.nan, math.inf, b"x")],
    )
    assert json.loads(values.to_bytes()) == [{
        "f": 1.5, "d": 2.5, "t": "2026-11-11T09:30:00", "day": "2026-11-11", "nan": None, "inf": None, "blob": None,
    }]


def test_collector_from_dataframe_takes_first_rows_and_skips_geometry():
    pd = pytest.importorskip("pandas")
    from app.core.eseguibili.dashboard_data import DashboardRowsCollector

    df = pd.DataFrame({"a": range(5), "geom": ["POINT(0 0)"] * 5, "b": [None, 1.0, float("nan"), 3.0, 4.0]})
    collector = DashboardRowsCollector(max_rows=2, max_bytes=1 << 20)
    collector.add_dataframe(df, skip_columns=["geom"])
    assert json.loads(collector.to_bytes()) == [{"a": 0, "b": None}, {"a": 1, "b": 1.0}]
    assert collector.truncated


def test_default_limit_is_fifty_thousand_rows():
    from app.core.eseguibili.dashboard_data import DashboardRowsCollector

    assert DashboardRowsCollector().max_rows == 50_000


# ── Produttori: DataFrame e streaming ───────────────────────────────────────

class _Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, chunk):
        self.data.extend(chunk if isinstance(chunk, bytes) else chunk.encode())
        return len(chunk)

    def finalize(self):
        return "tenants/g1/eseguibili-outputs/task-1/out.csv"

    def abort(self):
        pass


@pytest.mark.parametrize("dashboard_copy", [True, False])
def test_dataframe_output_writes_the_copy_only_with_the_flag(monkeypatch, dashboard_copy):
    pd = pytest.importorskip("pandas")
    from app.core.eseguibili import dashboard_data, output_persistence

    written = []
    monkeypatch.setattr(output_persistence.ExecutableOutputPersistence, "_writer", lambda self, f, fmt: _Writer())
    monkeypatch.setattr(dashboard_data, "write", lambda task_id, *, group_id, collector: written.append(
        (task_id, group_id, json.loads(collector.to_bytes()))))
    persistence = output_persistence.ExecutableOutputPersistence(
        task_id="task-1", group_id="g1", dashboard_copy=dashboard_copy,
    )
    persistence.persist_dataframe_output(df=pd.DataFrame({"a": [1, 2]}), output_format="csv", filename_stem="out")
    assert written == ([("task-1", "g1", [{"a": 1}, {"a": 2}])] if dashboard_copy else [])


def test_streaming_output_tees_cursor_rows_into_the_copy(monkeypatch):
    from app.core.eseguibili import dashboard_data, output_persistence
    from app.core.query_execution.query_streaming import _TeeResult

    written = []
    monkeypatch.setattr(output_persistence.ExecutableOutputPersistence, "_writer", lambda self, f, fmt: _Writer())
    monkeypatch.setattr(dashboard_data, "write", lambda task_id, *, group_id, collector: written.append(
        json.loads(collector.to_bytes())))

    class _Cursor:
        def __init__(self):
            self.chunks = [[(1, "x"), (2, "y")], [(3, "z")], []]

        def fetchmany(self, size):
            return self.chunks.pop(0)

    def fake_stream(**kwargs):
        cursor = _TeeResult(_Cursor(), ["n", "s"], kwargs["row_sink"]) if "row_sink" in kwargs else _Cursor()
        total = 0
        while chunk := cursor.fetchmany(10):
            total += len(chunk)
        return total, ["n", "s"]

    for flag, expected in ((True, [[{"n": 1, "s": "x"}, {"n": 2, "s": "y"}, {"n": 3, "s": "z"}]]), (False, [])):
        written.clear()
        persistence = output_persistence.ExecutableOutputPersistence(
            task_id="task-1", group_id="g1", stream_query_to_writer=fake_stream, dashboard_copy=flag,
        )
        result = persistence.persist_streaming_query_to_storage(
            project_id="exec_1", engine_factory=None, connection=None, sql="select 1", params=None,
            output_format="csv", output_filename="out.csv",
        )
        assert result.rows_count == 3
        assert written == expected


def test_storage_failure_of_the_copy_never_fails_the_execution(monkeypatch):
    pd = pytest.importorskip("pandas")
    from app.core.eseguibili import dashboard_data, output_persistence

    def boom(*_a, **_k):
        raise RuntimeError("storage giu'")

    monkeypatch.setattr(output_persistence.ExecutableOutputPersistence, "_writer", lambda self, f, fmt: _Writer())
    monkeypatch.setattr(dashboard_data, "write", boom)
    saved = output_persistence.ExecutableOutputPersistence(
        task_id="task-1", group_id="g1", dashboard_copy=True,
    ).persist_dataframe_output(df=pd.DataFrame({"a": [1]}), output_format="csv", filename_stem="out")
    assert saved.output_filename == "out.csv"


# ── Chi decide la copia: solo il server ─────────────────────────────────────

def _contract(subject, runtime_context=None):
    from app.core.orchestrator.execution_contract import PipelineExecutionContract

    return PipelineExecutionContract(
        schema_version=2, project_id="exec_1", project_type="manipola", group_id="g1", canvas={"nodes": []},
        execution_subject=subject, runtime_context=runtime_context or {},
    )


def test_copy_flag_comes_only_from_the_server_subject():
    from app.shared.execution.dashboard_copy import CONTEXT_FIELD

    # Il client non puo' chiederla dal runtime_context.
    forged = _contract({"kind": "eseguibile", "id": "exec_1"}, {CONTEXT_FIELD: True})
    assert CONTEXT_FIELD not in forged.build_runtime_context(output_task_id="task-1")
    with_dashboard = _contract({"kind": "eseguibile", "id": "exec_1", "dashboard": True})
    assert with_dashboard.build_runtime_context(output_task_id="task-1")[CONTEXT_FIELD] is True
    # Senza output nello spazio dell'attivita' nessuna copia.
    assert CONTEXT_FIELD not in with_dashboard.build_runtime_context(output_task_id=None)
    # Una prova dall'editor non e' un Eseguibile.
    test_run = _contract({"kind": "test_run", "username": "u", "dashboard": True})
    assert CONTEXT_FIELD not in test_run.build_runtime_context(output_task_id="task-1")


@pytest.mark.parametrize("uid,flag,token,expected", [
    ("plenora-exec_1", "true", "tok", True),
    (None, "true", "tok", False),
    ("plenora-exec_1", "false", "tok", False),
    ("plenora-exec_1", "true", "", False),
])
def test_executable_subject_sets_the_copy_only_with_dashboard_and_grafana(monkeypatch, uid, flag, token, expected):
    from types import SimpleNamespace

    from app.core.eseguibili import service

    monkeypatch.setenv("GRAFANA_ENABLED", flag)
    monkeypatch.setenv("GRAFANA_SERVICE_TOKEN", token)
    record = SimpleNamespace(id="exec_1", dashboard_uid=uid)
    subject = service.executable_subject(record, version_id="v1", run_id="r1")
    assert subject["kind"] == "eseguibile" and subject["version_id"] == "v1"
    assert subject.get("dashboard", False) is expected


def test_enrichment_run_mode_receives_the_flag():
    from app.core.arricchisci import enrichment_run_mode

    assert enrichment_run_mode.for_task(True, None, dashboard_copy=True).dashboard_copy is True
    assert enrichment_run_mode.for_task(True, None).dashboard_copy is False


# ── La copia non fa MAI fallire l'esecuzione (punto 5 della conferma) ───────
#
# Enumerazione: i due produttori (DataFrame e cursore in streaming dentro il
# writer vero) x valori non serializzabili o enormi x un errore forzato nel
# raccoglitore. L'esecuzione riesce sempre; la copia e' JSON valido o non
# c'e' (mai parziale).

_HOSTILE_VALUES = {
    "inf": math.inf,
    "-inf": -math.inf,
    "nan": math.nan,
    "decimal_enorme": Decimal("1E+400"),
    "decimal_minimo": Decimal("-1E+400"),
    "decimal_nan": Decimal("NaN"),
    "decimal_inf": Decimal("Infinity"),
    "decimal_fine": Decimal("0.1000000000000000055511151231257827"),
    "intero_enorme": 10 ** 400,
    "bytes": b"\x00\xff",
    "memoryview": memoryview(b"abc"),
    "data": date(2026, 11, 11),
    "ora": datetime(2026, 11, 11, 9, 30).time(),
    "data_ora_tz": datetime(2026, 11, 11, 9, 30).astimezone(),
    "testo_lungo": "x" * 5000,
    "insieme": {1, 2},
    "oggetto": object(),
}


class _FakeStreamingResult:
    def __init__(self, columns, rows):
        self._columns = list(columns)
        self._rows = [tuple(row) for row in rows]

    def keys(self):
        return list(self._columns)

    def fetchmany(self, size):
        chunk, self._rows = self._rows[:size], self._rows[size:]
        return chunk


class _FakeEngine:
    def __init__(self, result):
        self._result = result

    def connect(self):
        engine = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, *_a, **_k):
                return engine._result

        return _Conn()


def _produce(path, monkeypatch, columns, rows):
    """Esegue il produttore vero e restituisce (risultato, copie scritte)."""
    pd = pytest.importorskip("pandas")
    from app.core.eseguibili import dashboard_data, output_persistence
    from app.core.query_execution import query_streaming

    written = []
    monkeypatch.setattr(output_persistence.ExecutableOutputPersistence, "_writer", lambda self, f, fmt: _Writer())
    monkeypatch.setattr(dashboard_data, "write", lambda task_id, *, group_id, collector: written.append(
        None if collector.failed else json.loads(collector.to_bytes())))
    if path == "dataframe":
        persistence = output_persistence.ExecutableOutputPersistence(
            task_id="task-1", group_id="g1", dashboard_copy=True,
        )
        frame = pd.DataFrame([list(row) for row in rows], columns=list(columns), dtype=object)
        result = persistence.persist_dataframe_output(df=frame, output_format="csv", filename_stem="out")
        return result, written
    class _BinaryWriter(BytesIO):  # il writer vero dello storage e' un file binario
        def finalize(self):
            return "tenants/g1/eseguibili-outputs/task-1/out.csv"

        def abort(self):
            pass

    monkeypatch.setattr(output_persistence.ExecutableOutputPersistence, "_writer",
                        lambda self, f, fmt: _BinaryWriter())
    monkeypatch.setattr(query_streaming, "_configure_session", lambda conn, connection: None)
    monkeypatch.setattr(query_streaming, "_columns_from_result", lambda result, connection: list(result.keys()))
    persistence = output_persistence.ExecutableOutputPersistence(
        task_id="task-1", group_id="g1", dashboard_copy=True,
        stream_query_to_writer=query_streaming.execute_streaming_to_writer,
    )
    factory = SimpleNamespace(create_engine=lambda connection: _FakeEngine(_FakeStreamingResult(columns, rows)))
    result = persistence.persist_streaming_query_to_storage(
        project_id="exec_1", engine_factory=factory, connection=SimpleNamespace(type="postgresql"),
        sql="select 1", params=None, output_format="csv", output_filename="out.csv",
    )
    return result, written


@pytest.mark.parametrize("path", ["dataframe", "streaming"])
@pytest.mark.parametrize("name", sorted(_HOSTILE_VALUES))
def test_hostile_values_never_fail_the_execution_and_the_copy_is_valid_json(monkeypatch, path, name):
    if (path, name) == ("dataframe", "intero_enorme"):
        pytest.skip("e' l'export CSV dell'OUTPUT a rifiutare 10**400 (prima della copia): fuori da questa regola")
    value = _HOSTILE_VALUES[name]
    result, written = _produce(path, monkeypatch, ["id", "v"], [(1, value), (2, "ok")])
    assert result is not None
    assert len(written) == 1 and written[0] is not None
    rows = written[0]
    assert [row["id"] for row in rows] == [1, 2] and rows[1]["v"] == "ok"
    json.dumps(rows, allow_nan=False)  # JSON valido, niente NaN/Infinity


@pytest.mark.parametrize("path", ["dataframe", "streaming"])
def test_a_failing_collector_never_fails_the_execution_and_writes_no_copy(monkeypatch, path):
    from app.core.eseguibili import dashboard_data

    def boom(_value):
        raise RuntimeError("valore impossibile")

    monkeypatch.setattr(dashboard_data, "_jsonable", boom)
    result, written = _produce(path, monkeypatch, ["id"], [(1,), (2,)])
    assert result is not None
    assert written == [None]  # raccoglitore «failed»: nessuna copia


def test_failed_collector_removes_a_previous_copy(fake_storage):
    from app.core.eseguibili import dashboard_data

    _task_folder(fake_storage)
    first = _write_copy([(1, "x")])
    collector = dashboard_data.DashboardRowsCollector(max_rows=10, max_bytes=1 << 20)
    collector.failed = True
    assert dashboard_data.write("task-1", group_id="g1", collector=collector) is None
    assert first not in fake_storage.objects


def test_huge_decimal_is_kept_as_text_not_infinity():
    from app.core.eseguibili.dashboard_data import DashboardRowsCollector

    collector = DashboardRowsCollector(max_rows=10, max_bytes=1 << 20)
    collector.add_rows(["d"], [(Decimal("1E+400"),), (Decimal("2.5"),), (Decimal("12345678901234567890"),)])
    assert not collector.failed
    assert json.loads(collector.to_bytes()) == [{"d": "1E+400"}, {"d": 2.5}, {"d": 1.2345678901234567e19}]


def test_collector_guards_are_the_only_try_of_the_copy():
    """Un solo punto intercetta: nei produttori nessun ``try`` dedicato alla
    copia (``output_persistence`` chiama solo ``add_*`` e ``save``)."""
    source = (_BACKEND_ROOT / "app/core/eseguibili/output_persistence.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body = ast.unparse(node)
            assert "collector" not in body and "dashboard" not in body, body[:200]


# ── Pulizia fisica delle copie (punto 7 della conferma) ─────────────────────

def test_beat_purges_copies_on_its_own_short_cycle(monkeypatch):
    from workers import beat_scheduler

    purged = []
    monkeypatch.setattr(beat_scheduler.object_storage_module, "object_storage_enabled", lambda: True)
    monkeypatch.setattr(beat_scheduler.users_db, "list_groups", lambda limit=10_000: [SimpleNamespace(id="g1")])
    monkeypatch.setattr(beat_scheduler.eseguibili_dashboard_data, "purge_expired",
                        lambda group_ids, now: purged.append(now) or 0)
    clock = {"now": 10_000.0}
    monkeypatch.setattr(beat_scheduler._t, "time", lambda: clock["now"])
    scheduler = beat_scheduler.PlenoraBeatScheduler.__new__(beat_scheduler.PlenoraBeatScheduler)
    scheduler._last_dashboard_copies_cleanup = 0.0
    for step in range(0, 1_801, 60):  # 30 minuti, un tick al minuto
        clock["now"] = 10_000.0 + step
        scheduler._cleanup_dashboard_copies()
    # Ogni 5 minuti (DASHBOARD_DATA_PURGE_INTERVAL_SECONDS), non ogni 30.
    assert len(purged) == 7
    assert all(b - a == 300 for a, b in zip(purged, purged[1:]))
