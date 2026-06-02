"""Expanded E2E v2 coverage up to test_400.

These cases stay in the current perimeter:
- cross-db spatial/CARICA regressions;
- FTP/SFTP datasource formats;
- fake REST sink contract;
- static guards around the code paths fixed by the cross-db run.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from _cross_db_extended import _enabled
from _cross_db_full import (
    _carica_mode_node,
    _datasource_format_canvas,
    _datasource_payload,
    _execute_carica_canvas,
    _extract_active_canvas,
    _extract_customers_by_status_canvas,
    _http_json,
    _loop_dialects,
    _preview_spatial_case,
    _verify_table_canvas,
)
from _e2e import (
    E2EContext,
    E2EError,
    assert_equal,
    assert_true,
    edge,
    ensure_plenora_test_network_access,
    fail,
    run_test,
    source_column,
    unique_name,
)


CaseFn = Callable[[], None]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _read_text(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads(_read_text(relative_path))


def _schemas() -> dict[str, str]:
    return {
        "mysql": os.environ.get("MYSQL_DATABASE", "plenora_test"),
        "mssql": os.environ.get("PLENORA_E2E_MSSQL_SCHEMA", "dbo"),
        "oracle": os.environ.get("ORACLE_APP_USER", "plenora"),
    }


def _run_for_dialect(ctx: E2EContext, dialect: str, scenario: str) -> str | None:
    if not _enabled(dialect):
        print(f"SKIP {scenario}/{dialect}: set PLENORA_E2E_CROSS_DB=1", flush=True)
        return None
    for current, connection_id in _loop_dialects(ctx, scenario):
        if current == dialect:
            return connection_id
    return None


def _static_parquet_helpers() -> None:
    helpers = _read_text("backend/app/core/files/helpers.py")
    assert_true("'.parquet': 'parquet'" in helpers, "files helper must detect .parquet as parquet")
    assert_true("'parquet'" in helpers, "tabular helper must include parquet")


def _static_parquet_loader_port() -> None:
    ports = _read_text("backend/app/core/dataframes/ports.py")
    default_loader = _read_text("backend/app/core/dataframes/default_loader.py")
    frame_io = _read_text("backend/app/core/dataframes/frame_io.py")
    assert_true("def load_parquet" in ports, "dataframe port must expose load_parquet")
    assert_true("def load_parquet" in default_loader, "default dataframe loader must implement load_parquet")
    assert_true('format_hint == "parquet"' in frame_io, "frame_io bytes path must route parquet")


def _static_oracle_preflight_normalization() -> None:
    source = _read_text("backend/app/core/carica/writer_preflight_lifecycle.py")
    assert_true('getattr(self, "db_type", "").lower() == "oracle"' in source, "Oracle preflight must branch by db_type")
    assert_true("str(value).upper()" in source, "Oracle preflight must uppercase column names")
    assert_true("normalize_col(cm.dest_column)" in source, "Oracle mapping validation must normalize dest columns")


def _static_spatial_registry_covers() -> None:
    spatial = _read_json("backend/app/db/sql_functions_registry.json")["spatial"]
    mysql_covers = spatial["ST_Covers"]["dialects"]["mysql"]
    mysql_covered_by = spatial["ST_CoveredBy"]["dialects"]["mysql"]
    assert_true("ST_Covers(" not in mysql_covers, "MySQL ST_Covers must be emulated")
    assert_true("ST_CoveredBy(" not in mysql_covered_by, "MySQL ST_CoveredBy must be emulated")
    assert_true("ST_Contains" in mysql_covers and "ST_Equals" in mysql_covers, "MySQL covers emulation must preserve equality")
    assert_true("ST_Within" in mysql_covered_by and "ST_Equals" in mysql_covered_by, "MySQL coveredBy emulation must preserve equality")


def _static_spatial_registry_mssql_oracle() -> None:
    spatial = _read_json("backend/app/db/sql_functions_registry.json")["spatial"]
    assert_true("STDistance" in spatial["ST_Touches"]["dialects"]["mssql"], "SQL Server touches must use STDistance")
    assert_true("STIntersection" in spatial["ST_Crosses"]["dialects"]["mssql"], "SQL Server crosses must use intersection emulation")
    assert_true(
        "OVERLAPBDYDISJOINT" in spatial["ST_Crosses"]["dialects"]["oracle"],
        "Oracle crosses must keep OVERLAPBDYDISJOINT",
    )


def _static_spatial_translator_predicates() -> None:
    translator = _read_text("backend/app/db/sql_translator.py")
    for predicate in ("equals", "disjoint", "covers", "coveredby"):
        assert_true(f"'{predicate}'" in translator, f"SQL translator must map {predicate}")


def _static_carica_unsafe_append_guard() -> None:
    models = _read_text("backend/app/core/carica/models.py")
    canvas_models = _read_text("backend/app/core/projects/canvas_models.py")
    preflight = _read_text("backend/app/core/carica/writer_preflight_lifecycle.py")
    assert_true("allow_unsafe_append=data.get(\"allow_unsafe_append\") is True" in models, "unsafe append must require literal true")
    assert_true("if_table_exists:" in canvas_models, "canvas Node must preserve if_table_exists for CARICA")
    assert_true("allow_unsafe_append:" in canvas_models, "canvas Node must preserve allow_unsafe_append for CARICA")
    assert_true("write_mode=append + transaction_mode=per_batch" in preflight, "per-batch append guard must fail loudly")
    assert_true("allow_unsafe_append=True" in preflight, "guard message must document explicit opt-in")


def _static_rest_sink_contract_docs() -> None:
    readme = _read_text("backend/test-e2e-v2/fake-rest/README.md")
    assert_true("POST   /data/sink/records" in readme, "fake REST docs must expose records sink")
    assert_true("POST   /data/sink/batch" in readme, "fake REST docs must expose idempotent batch sink")
    assert_true("X-API-Key: PLENORA_TEST_KEY" in readme, "fake REST docs must document sink auth")


def _fake_rest_base() -> str:
    return os.environ.get("PLENORA_E2E_FAKE_REST_HOST_URL", "http://127.0.0.1:58080").rstrip("/")


def _sink_headers(**extra: str) -> dict[str, str]:
    headers = {"X-API-Key": "PLENORA_TEST_KEY"}
    headers.update(extra)
    return headers


def _reset_sink() -> None:
    _, payload = _http_json(
        "POST",
        f"{_fake_rest_base()}/data/sink/records",
        payload={"records": []},
        headers=_sink_headers(**{"X-Test-Reset": "1"}),
        expected=(201,),
    )
    assert_true(payload.get("ok"), "sink reset must acknowledge ok=true")


def _rest_reset_empty_batch() -> None:
    _reset_sink()


def _rest_rejects_bad_api_key() -> None:
    _http_json(
        "POST",
        f"{_fake_rest_base()}/data/sink/records",
        payload={"records": []},
        headers={"X-API-Key": "WRONG"},
        expected=(401,),
    )


def _rest_rejects_bad_records_payload() -> None:
    _reset_sink()
    _http_json(
        "POST",
        f"{_fake_rest_base()}/data/sink/records",
        payload={"records": {"not": "a list"}},
        headers=_sink_headers(),
        expected=(400,),
    )


def _rest_sequential_ids_after_reset() -> None:
    _reset_sink()
    _, payload = _http_json(
        "POST",
        f"{_fake_rest_base()}/data/sink/records",
        payload={"records": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
        headers=_sink_headers(),
        expected=(201,),
    )
    assert_equal(payload.get("ids"), [1000, 1001, 1002], "sink ids must be deterministic after reset")


def _rest_put_patch_delete_unknowns() -> None:
    _reset_sink()
    for method in ("PUT", "PATCH", "DELETE"):
        _http_json(
            method,
            f"{_fake_rest_base()}/data/sink/by_id/999999",
            payload={"name": "missing"} if method != "DELETE" else None,
            headers=_sink_headers(),
            expected=(404,),
        )


def _datasource_case(ctx: E2EContext, *, kind: str, path: str, expected_rows: int, expected_column: str, expected_value: str, mode: str, fmt: str | None = None) -> None:
    ensure_plenora_test_network_access()
    datasource_id = ctx.create_datasource(_datasource_payload(kind, unique_name(f"e2e_v2_400_{kind}")))
    tested = ctx.api_json("POST", f"/datasources/{datasource_id}/test")
    assert_true(tested.get("success"), f"{kind}: datasource connection must succeed")
    schema = ctx.api_json("GET", f"/datasources/{datasource_id}/files/schema?path={quote(path)}")
    columns = schema.get("columns") or []
    names = {str(column.get("name")) for column in columns}
    assert_true(expected_column in names, f"{kind}: schema for {path} must include {expected_column}")
    if mode == "schema":
        return

    project_id = ctx.create_project(
        name=f"e2e_v2_400_{kind}_{Path(path).stem}_{mode}",
        project_type="manipola",
        canvas=_datasource_format_canvas(datasource_id, path, kind, columns, fmt=fmt),
    )
    rows = ctx.preview_project(project_id, limit=100).get("rows") or []
    assert_equal(len(rows), expected_rows, f"{kind}: preview row count for {path}")
    assert_true(
        any(str(row.get(expected_column)) == expected_value for row in rows),
        f"{kind}: preview must preserve {expected_column}={expected_value!r}",
    )


DATASOURCE_FORMATS: list[tuple[str, int, str, str, str | None]] = [
    ("fixtures/csv/customers_unicode.csv", 16, "full_name", "EmojiUser 🎉", None),
    ("fixtures/json/customers.json", 12, "full_name", "Anna Rossi", None),
    ("fixtures/json/with_nulls.json", 5, "full_name", "Anna Rossi", None),
    ("fixtures/parquet/customers.parquet", 12, "full_name", "Anna Rossi", None),
    ("fixtures/xlsx/single_sheet.xlsx", 12, "full_name", "Anna Rossi", None),
    ("fixtures/geojson/cities_points.geojson", 9, "name", "Milano", "geojson"),
]


def _carica_prepare_table(ctx: E2EContext, dialect: str, connection_id: str, schema_name: str, table_name: str) -> tuple[dict[str, Any], int]:
    canvas = _extract_active_canvas(connection_id, dialect)
    expected_id = ctx.create_project(
        name=f"e2e_v2_400_{dialect}_active_expected",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    active_rows = ctx.preview_project(expected_id, limit=100).get("rows") or []
    assert_true(active_rows, f"{dialect}: active fixture rows required")
    create_canvas = json.loads(json.dumps(canvas))
    create_canvas["nodes"].append(
        _carica_mode_node(connection_id, schema_name, table_name, write_mode="create", if_table_exists="error")
    )
    create_canvas["edges"].append(edge("e-result-create", "result-1", "carica-1"))
    _execute_carica_canvas(
        ctx,
        dialect=dialect,
        connection_id=connection_id,
        canvas=create_canvas,
        project_suffix=f"400_{dialect}_prepare_create",
    )
    return canvas, len(active_rows)


def _carica_mode_case(ctx: E2EContext, *, dialect: str, mode: str) -> None:
    connection_id = _run_for_dialect(ctx, dialect, f"carica_400_{mode}")
    if connection_id is None:
        return
    schema_name = _schemas()[dialect]
    table_name = unique_name(f"e2e_400_{dialect}_{mode}").lower()[:48]
    base_canvas, active_count = _carica_prepare_table(ctx, dialect, connection_id, schema_name, table_name)
    verify_id = ctx.create_project(
        name=f"e2e_v2_400_{dialect}_{mode}_verify",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_verify_table_canvas(connection_id, table_name, dialect),
    )

    if mode == "create":
        rows = ctx.preview_project(verify_id, limit=200).get("rows") or []
        assert_equal(len(rows), active_count, f"{dialect}: create must write active rows")
        return

    if mode == "append":
        canvas = json.loads(json.dumps(base_canvas))
        canvas["nodes"].append(_carica_mode_node(connection_id, schema_name, table_name, write_mode="append"))
        canvas["edges"].append(edge("e-result-append", "result-1", "carica-1"))
        _execute_carica_canvas(ctx, dialect=dialect, connection_id=connection_id, canvas=canvas, project_suffix=f"400_{dialect}_append")
        rows = ctx.preview_project(verify_id, limit=400).get("rows") or []
        assert_equal(len(rows), active_count * 2, f"{dialect}: append must add rows")
        return

    pending_canvas = _extract_customers_by_status_canvas(connection_id, dialect, "pending")
    pending_id = ctx.create_project(
        name=f"e2e_v2_400_{dialect}_{mode}_pending_expected",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=pending_canvas,
    )
    pending_rows = ctx.preview_project(pending_id, limit=100).get("rows") or []
    assert_true(pending_rows, f"{dialect}: pending rows required")
    canvas = json.loads(json.dumps(pending_canvas))
    canvas["nodes"].append(
        _carica_mode_node(
            connection_id,
            schema_name,
            table_name,
            write_mode=mode,
            if_table_exists="replace" if mode == "replace" else "error",
        )
    )
    canvas["edges"].append(edge(f"e-result-{mode}", "result-1", "carica-1"))
    _execute_carica_canvas(ctx, dialect=dialect, connection_id=connection_id, canvas=canvas, project_suffix=f"400_{dialect}_{mode}")
    rows = ctx.preview_project(verify_id, limit=200).get("rows") or []
    assert_equal(len(rows), len(pending_rows), f"{dialect}: {mode} must leave pending row count")
    assert_equal(
        {int(row["customer_id"]) for row in rows},
        {int(row["customer_id"]) for row in pending_rows},
        f"{dialect}: {mode} must leave only pending ids",
    )


def _expect_execute_failure(ctx: E2EContext, project_id: str, expected_fragment: str, label: str) -> None:
    try:
        result = ctx.execute_project(project_id)
    except E2EError as exc:
        assert_true(expected_fragment.lower() in str(exc).lower(), f"{label}: failure must mention {expected_fragment!r}: {exc}")
        return
    tasks = ((result.get("carica") or {}).get("tasks") or [])
    assert_true(tasks, f"{label}: execute must expose CARICA task ids")
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        assert_true(task_id, f"{label}: CARICA task id missing: {task!r}")
        try:
            status = ctx.wait_task(task_id)
        except E2EError as exc:
            assert_true(
                expected_fragment.lower() in str(exc).lower(),
                f"{label}: CARICA task failure must mention {expected_fragment!r}: {exc}",
            )
            return
        task_result = status.get("result") or {}
        if task_result.get("success") is False:
            message = " ".join(
                str(task_result.get(key) or "")
                for key in ("message", "error_message", "error")
            )
            assert_true(
                expected_fragment.lower() in message.lower(),
                f"{label}: CARICA failure must mention {expected_fragment!r}: {task_result!r}",
            )
            return
    fail(f"{label}: CARICA task unexpectedly succeeded")


def _carica_create_existing_error(ctx: E2EContext, *, dialect: str) -> None:
    connection_id = _run_for_dialect(ctx, dialect, "carica_400_create_existing")
    if connection_id is None:
        return
    schema_name = _schemas()[dialect]
    table_name = unique_name(f"e2e_400_exists_{dialect}").lower()[:48]
    base_canvas, _ = _carica_prepare_table(ctx, dialect, connection_id, schema_name, table_name)
    duplicate = json.loads(json.dumps(base_canvas))
    duplicate["nodes"].append(
        _carica_mode_node(connection_id, schema_name, table_name, write_mode="create", if_table_exists="error")
    )
    duplicate["edges"].append(edge("e-result-create-again", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_400_{dialect}_create_existing_error",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=duplicate,
    )
    _expect_execute_failure(ctx, project_id, "esiste", f"{dialect}: create existing")


def _carica_append_missing_error(ctx: E2EContext, *, dialect: str) -> None:
    connection_id = _run_for_dialect(ctx, dialect, "carica_400_append_missing")
    if connection_id is None:
        return
    schema_name = _schemas()[dialect]
    table_name = unique_name(f"e2e_400_missing_{dialect}").lower()[:48]
    canvas = _extract_active_canvas(connection_id, dialect)
    canvas["nodes"].append(_carica_mode_node(connection_id, schema_name, table_name, write_mode="append"))
    canvas["edges"].append(edge("e-result-append-missing", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_400_{dialect}_append_missing_error",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    _expect_execute_failure(ctx, project_id, "non esiste", f"{dialect}: append missing table")


def _spatial_case(ctx: E2EContext, *, dialect: str, predicate: str, exact: str) -> None:
    connection_id = _run_for_dialect(ctx, dialect, f"spatial_400_{predicate.lower()}_{exact}")
    if connection_id is None:
        return

    if exact == "touches_regions":
        rows = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Touches",
            left_table="regions",
            right_table="regions",
            left_alias="a",
            right_alias="b",
            left_geom="boundary",
            right_geom="boundary",
            left_columns=[source_column("code", logical_type="str", alias="region_a_code")],
            right_columns=[source_column("code", logical_type="str", alias="region_b_code")],
        )
        pairs = {(str(row["region_a_code"]), str(row["region_b_code"])) for row in rows}
        assert_equal(pairs, {("LAZ", "CAM"), ("CAM", "LAZ")}, f"{dialect}: touches exact pairs")
        return

    if exact == "crosses_routes":
        rows = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Crosses",
            left_table="routes",
            right_table="regions",
            left_alias="ro",
            right_alias="r",
            left_geom="geom",
            right_geom="boundary",
            left_columns=[source_column("id", logical_type="int", alias="route_id")],
            right_columns=[source_column("code", logical_type="str", alias="region_code")],
        )
        pairs = {(str(row["route_id"]), str(row["region_code"])) for row in rows}
        assert_equal(pairs, {("1", "LOM"), ("1", "LAZ"), ("3", "LAZ"), ("3", "CAM")}, f"{dialect}: crosses exact pairs")
        return

    if exact == "covers_regions_self":
        rows = _preview_spatial_case(
            ctx,
            dialect=dialect,
            connection_id=connection_id,
            predicate="ST_Covers",
            left_table="regions",
            right_table="regions",
            left_alias="a",
            right_alias="b",
            left_geom="boundary",
            right_geom="boundary",
            left_columns=[source_column("code", logical_type="str", alias="region_a_code")],
            right_columns=[source_column("code", logical_type="str", alias="region_b_code")],
        )
        pairs = {(str(row["region_a_code"]), str(row["region_b_code"])) for row in rows}
        assert_equal(pairs, {("LOM", "LOM"), ("LAZ", "LAZ"), ("CAM", "CAM")}, f"{dialect}: covers exact self pairs")
        return

    raise AssertionError(f"Unsupported spatial exact case: {exact}")


def _ctx_case(fn: Callable[[E2EContext], None]) -> Callable[[], None]:
    return lambda: run_test(fn)


STATIC_CASES: dict[str, CaseFn] = {
    "test_338": _static_parquet_helpers,
    "test_339": _static_parquet_loader_port,
    "test_340": _static_oracle_preflight_normalization,
    "test_341": _static_spatial_registry_covers,
    "test_342": _static_spatial_registry_mssql_oracle,
    "test_343": _static_spatial_translator_predicates,
    "test_344": _static_carica_unsafe_append_guard,
    "test_345": _static_rest_sink_contract_docs,
    "test_346": _rest_reset_empty_batch,
    "test_347": _rest_rejects_bad_api_key,
    "test_348": _rest_rejects_bad_records_payload,
    "test_349": _rest_sequential_ids_after_reset,
    "test_350": _rest_put_patch_delete_unknowns,
    "test_393": _static_oracle_preflight_normalization,
    "test_394": _static_spatial_translator_predicates,
}


CASES: dict[str, CaseFn] = dict(STATIC_CASES)


_test_no = 351
for _kind in ("ftp", "sftp"):
    for _path, _rows, _column, _value, _fmt in DATASOURCE_FORMATS:
        for _mode in ("schema", "preview"):
            _case_id = f"test_{_test_no:03d}"
            CASES[_case_id] = _ctx_case(
                lambda ctx, kind=_kind, path=_path, rows=_rows, column=_column, value=_value, fmt=_fmt, mode=_mode: _datasource_case(
                    ctx,
                    kind=kind,
                    path=path,
                    expected_rows=rows,
                    expected_column=column,
                    expected_value=value,
                    mode=mode,
                    fmt=fmt,
                )
            )
            _test_no += 1


for _dialect in ("mysql", "mssql", "oracle"):
    for _mode in ("create", "append", "truncate_insert", "replace"):
        _case_id = f"test_{_test_no:03d}"
        CASES[_case_id] = _ctx_case(lambda ctx, dialect=_dialect, mode=_mode: _carica_mode_case(ctx, dialect=dialect, mode=mode))
        _test_no += 1


for _dialect in ("mysql", "mssql", "oracle"):
    _case_id = f"test_{_test_no:03d}"
    CASES[_case_id] = _ctx_case(lambda ctx, dialect=_dialect: _carica_create_existing_error(ctx, dialect=dialect))
    _test_no += 1


for _dialect in ("mysql", "mssql", "oracle"):
    _case_id = f"test_{_test_no:03d}"
    CASES[_case_id] = _ctx_case(lambda ctx, dialect=_dialect: _carica_append_missing_error(ctx, dialect=dialect))
    _test_no += 1


for _case_id, _dialect, _predicate, _exact in (
    ("test_395", "mysql", "ST_Touches", "touches_regions"),
    ("test_396", "mssql", "ST_Touches", "touches_regions"),
    ("test_397", "oracle", "ST_Touches", "touches_regions"),
    ("test_398", "mysql", "ST_Covers", "covers_regions_self"),
    ("test_399", "mssql", "ST_Crosses", "crosses_routes"),
    ("test_400", "oracle", "ST_Crosses", "crosses_routes"),
):
    CASES[_case_id] = _ctx_case(
        lambda ctx, dialect=_dialect, predicate=_predicate, exact=_exact: _spatial_case(
            ctx,
            dialect=dialect,
            predicate=predicate,
            exact=exact,
        )
    )


assert _test_no == 393, f"unexpected expanded test numbering stop: {_test_no}"


def run_case(case_id: str) -> None:
    try:
        case = CASES[case_id]
    except KeyError:
        raise AssertionError(f"Unknown expanded 400 case: {case_id}") from None
    try:
        case()
    except urllib.error.URLError as exc:
        fail(f"{case_id} failed to reach fake REST service: {exc}")
