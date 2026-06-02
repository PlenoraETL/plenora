"""Final E2E v2 coverage for the remaining ESTRAI/MANIPOLA/CARICA perimeter."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from _cross_db_full import (
    _carica_mode_node,
    _datasource_payload,
    _execute_carica_canvas,
    _extract_active_canvas,
    _extract_customers_by_status_canvas,
    _http_json,
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
    result_node,
    run_test,
    source_column,
    source_node,
    unique_name,
)
from _expanded_400 import _run_for_dialect, _schemas


CaseFn = Callable[[], None]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _read_text(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


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
    assert_true(payload.get("ok"), "fake REST sink reset must acknowledge ok=true")


def _ctx_case(fn: Callable[[E2EContext], None]) -> CaseFn:
    return lambda: run_test(fn)


def _wait_carica_tasks(ctx: E2EContext, execute_result: dict[str, Any], label: str) -> list[dict[str, Any]]:
    tasks = ((execute_result.get("carica") or {}).get("tasks") or [])
    assert_true(tasks, f"{label}: execute result must include CARICA tasks")
    results: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        assert_true(task_id, f"{label}: missing task_id in {task!r}")
        status = ctx.wait_task(task_id)
        result = status.get("result")
        assert_true(isinstance(result, dict), f"{label}: CARICA task result must be an object: {status!r}")
        assert_true(result.get("success"), f"{label}: CARICA task must succeed: {result!r}")
        results.append(result)
    return results


def _expect_carica_task_failure(
    ctx: E2EContext,
    project_id: str,
    expected_fragment: str,
    label: str,
) -> None:
    try:
        result = ctx.execute_project(project_id)
    except E2EError as exc:
        assert_true(expected_fragment.lower() in str(exc).lower(), f"{label}: failure must mention {expected_fragment}: {exc}")
        return
    tasks = ((result.get("carica") or {}).get("tasks") or [])
    assert_true(tasks, f"{label}: execute must expose CARICA task ids")
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        try:
            status = ctx.wait_task(task_id)
        except E2EError as exc:
            assert_true(expected_fragment.lower() in str(exc).lower(), f"{label}: task failure must mention {expected_fragment}: {exc}")
            return
        task_result = status.get("result") or {}
        if task_result.get("success") is False:
            message = " ".join(str(task_result.get(key) or "") for key in ("message", "error_message", "error"))
            assert_true(expected_fragment.lower() in message.lower(), f"{label}: failure must mention {expected_fragment}: {task_result!r}")
            return
    fail(f"{label}: CARICA task unexpectedly succeeded")


def _verify_count(ctx: E2EContext, connection_id: str, table_name: str, dialect: str, expected_count: int) -> list[dict[str, Any]]:
    verify_id = ctx.create_project(
        name=f"e2e_v2_final_{dialect}_verify_{unique_name('count')}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_verify_table_canvas(connection_id, table_name, dialect),
    )
    rows = ctx.preview_project(verify_id, limit=1000).get("rows") or []
    assert_equal(len(rows), expected_count, f"{dialect}: loaded table row count")
    return rows


def _prepare_active_table(ctx: E2EContext, dialect: str, scenario: str) -> tuple[str, str, str, dict[str, Any], int]:
    connection_id = _run_for_dialect(ctx, dialect, scenario)
    if connection_id is None:
        return "", "", "", {}, 0
    schema_name = _schemas()[dialect]
    table_name = unique_name(f"e2e_final_{dialect}").lower()[:48]
    canvas = _extract_active_canvas(connection_id, dialect)
    expected_id = ctx.create_project(
        name=f"e2e_v2_final_{dialect}_{scenario}_expected",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    active_rows = ctx.preview_project(expected_id, limit=200).get("rows") or []
    assert_true(active_rows, f"{dialect}: active source rows required")
    create_canvas = json.loads(json.dumps(canvas))
    create_canvas["nodes"].append(
        _carica_mode_node(connection_id, schema_name, table_name, write_mode="create", if_table_exists="error")
    )
    create_canvas["edges"].append(edge("e-result-carica-create", "result-1", "carica-1"))
    _execute_carica_canvas(
        ctx,
        dialect=dialect,
        connection_id=connection_id,
        canvas=create_canvas,
        project_suffix=f"final_{scenario}_{dialect}_create",
    )
    return connection_id, schema_name, table_name, canvas, len(active_rows)


def _carica_with_node(
    ctx: E2EContext,
    *,
    dialect: str,
    connection_id: str,
    source_canvas: dict[str, Any],
    carica_node: dict[str, Any],
    suffix: str,
) -> list[dict[str, Any]]:
    canvas = json.loads(json.dumps(source_canvas))
    canvas["nodes"].append(carica_node)
    canvas["edges"].append(edge(f"e-result-carica-{suffix}", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_final_{dialect}_{suffix}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    result = ctx.execute_project(project_id)
    return _wait_carica_tasks(ctx, result, f"{dialect}:{suffix}")


def _pending_canvas_and_count(ctx: E2EContext, dialect: str, connection_id: str) -> tuple[dict[str, Any], int]:
    pending_canvas = _extract_customers_by_status_canvas(connection_id, dialect, "pending")
    pending_id = ctx.create_project(
        name=f"e2e_v2_final_{dialect}_pending_expected",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=pending_canvas,
    )
    rows = ctx.preview_project(pending_id, limit=200).get("rows") or []
    assert_true(rows, f"{dialect}: pending source rows required")
    return pending_canvas, len(rows)


def _advanced_node(
    connection_id: str,
    schema_name: str,
    table_name: str,
    *,
    write_mode: str,
    conflict_policy: str = "skip",
    transaction_mode: str = "all_or_nothing",
    allow_unsafe_append: bool = False,
) -> dict[str, Any]:
    node = _carica_mode_node(
        connection_id,
        schema_name,
        table_name,
        write_mode=write_mode,
        transaction_mode=transaction_mode,
        allow_unsafe_append=allow_unsafe_append,
    )
    patch = {
        "match_columns": ["customer_id"],
        "update_columns": ["customer_name", "age"],
        "conflict_policy": conflict_policy,
    }
    node.update(patch)
    node["data"].update(patch)
    return node


def _static_canvas_advanced_carica_fields() -> None:
    source = _read_text("backend/app/core/projects/canvas_models.py")
    for field in ("if_table_exists", "allow_unsafe_append", "match_columns", "update_columns", "rest_success_statuses", "dest_folder", "dest_filename", "dest_format"):
        assert_true(f"{field}:" in source, f"Canvas Node must preserve {field}")


def _static_write_config_advanced_fields() -> None:
    source = _read_text("backend/app/core/carica/models.py")
    for field in ("UPDATE = \"update\"", "UPSERT = \"upsert\"", "match_columns=data.get", "update_columns=data.get", "if_table_exists=data.get", "allow_unsafe_append"):
        assert_true(field in source, f"WriteConfig must preserve {field}")


def _static_writer_dispatch_advanced_modes() -> None:
    source = _read_text("backend/app/core/carica/database_writer.py")
    for marker in ("WriteMode.UPDATE", "WriteMode.UPSERT", "WriteMode.TRUNCATE_INSERT", "_batch_update", "_batch_upsert"):
        assert_true(marker in source, f"DatabaseWriter dispatch must include {marker}")


def _static_ddl_upsert_all_dialects() -> None:
    source = _read_text("backend/app/core/carica/ddl_generator.py")
    for marker in ("_upsert_postgres", "_upsert_mysql", "_upsert_mssql", "_upsert_oracle", "ON DUPLICATE KEY UPDATE", "MERGE INTO"):
        assert_true(marker in source, f"DDLGenerator UPSERT support missing {marker}")


def _static_update_sql_uses_match_params() -> None:
    source = _read_text("backend/app/core/carica/ddl_generator.py")
    assert_true("def generate_update" in source, "DDLGenerator must expose generate_update")
    assert_true(":match_" in source, "UPDATE SQL must use separate match parameters")
    assert_true("WHERE {where_sql}" in source, "UPDATE SQL must generate WHERE clause")


def _static_preflight_requires_match_columns() -> None:
    source = _read_text("backend/app/core/carica/writer_preflight_lifecycle.py")
    assert_true("WriteMode.UPDATE, WriteMode.UPSERT" in source, "preflight must cover update/upsert")
    assert_true("richiede" in source and "match" in source, "preflight must require match columns")


def _static_transaction_rollback_guard() -> None:
    source = _read_text("backend/app/core/carica/writer_transaction_exec.py")
    assert_true("engine.begin" in source, "all_or_nothing writes must use transactional engine.begin")
    assert_true("result.rows_written = 0" in source, "rollback path must reset rows_written")
    assert_true("WriteStatus.WRITE_ERROR" in source, "rollback path must report write_error")


def _static_target_lock_all_branches() -> None:
    source = _read_text("backend/app/core/carica/target_lock.py")
    for marker in ("_db_target", "_rest_target", "_arcgis_target", "datasource"):
        assert_true(marker in source, f"target lock must cover {marker}")


def _static_no_carica_retries() -> None:
    source = _read_text("backend/workers/tasks/carica_task.py")
    assert_true("max_retries=0" in source, "CARICA task must not retry non-idempotent writes automatically")
    assert_true("release_carica_target_lock" in source, "CARICA task must release destination lock")


def _static_rest_sink_validation_and_redaction() -> None:
    parser = _read_text("backend/app/core/carica/rest_config_parser.py")
    sink = _read_text("backend/app/core/connections/rest_sink.py")
    assert_true("must be absolute http/https" in parser, "REST parser must reject non-http URLs")
    assert_true("must not include credentials" in parser, "REST parser must reject credential URLs")
    assert_true("secret flag must be boolean" in parser, "REST parser must validate secret header flag")
    assert_true("***REDACTED***" in sink, "REST sink must redact secret headers")


def _static_datasource_write_paths() -> None:
    service = _read_text("backend/app/core/datasources/service.py")
    dispatch = _read_text("backend/app/core/carica/dispatch.py")
    for backend in ("ftp.py", "sftp.py", "local.py", "sharepoint.py"):
        assert_true("def write_file" in _read_text(f"backend/app/core/datasources/{backend}"), f"{backend} must implement write_file")
    assert_true("def write_file" in service, "DataSourceService must expose write_file")
    assert_true("execute_datasource_write" in dispatch, "CARICA dispatch must support datasource writes")


def _static_datasource_upload_mime_guard() -> None:
    source = _read_text("backend/app/core/datasources/router.py")
    assert_true("validate_no_executable" in source, "datasource upload must reject executable payloads")
    assert_true("MimeMismatchError" in source, "datasource upload must expose MIME validation failures")


def _static_datasource_path_sanitization() -> None:
    source = _read_text("backend/app/core/carica/dispatch.py")
    assert_true("PurePosixPath(dest_folder)" in source, "CARICA datasource write must normalize destination folder")
    assert_true(".replace('..', '')" in source, "CARICA datasource write must strip path traversal")
    assert_true("PurePosixPath(dest_filename).name" in source, "CARICA datasource write must keep only filename")


def _static_carica_result_count_derivation() -> None:
    source = _read_text("backend/app/core/carica/models.py")
    assert_true("def fill_write_counts" in source, "WriteResult must derive insert/update counters")
    assert_true("WriteMode.UPDATE.value, WriteMode.UPSERT.value" in source, "update/upsert counters must become rows_updated")


def _static_conflict_checker_duplicates() -> None:
    source = _read_text("backend/app/core/carica/conflict_checker.py")
    assert_true("_check_internal_duplicates" in source, "ConflictChecker must inspect duplicate rows in source batch")
    assert_true("duplicate_row_count" in source, "ConflictChecker must count internal duplicates")


def _static_conflict_checker_multi_column() -> None:
    source = _read_text("backend/app/core/carica/conflict_checker.py")
    assert_true("_check_multi_column" in source, "ConflictChecker must support multi-column match keys")
    assert_true("unique_rows_for_columns" in source, "ConflictChecker must collapse unique multi-column key combinations")


def _static_column_mapper_oracle_uppercase() -> None:
    source = _read_text("backend/app/core/carica/column_mapper.py")
    assert_true("self.db_type == \"oracle\"" in source, "ColumnMapper must branch for Oracle")
    assert_true("sanitized.upper()" in source, "ColumnMapper must uppercase Oracle identifiers")
    assert_true("max_len = 30 if self.db_type == \"oracle\"" in source, "ColumnMapper must enforce Oracle identifier length")


def _static_output_contract_types_enricher() -> None:
    source = _read_text("backend/app/shared/execution/output_contract_types_enricher.py")
    assert_true("output_contract" in source, "CARICA must enrich canvas types from output_contract")
    assert_true("canvas_types" in source, "CARICA node data must receive canvas_types")


def _static_carica_payload_validation_nested_rest() -> None:
    source = _read_text("backend/app/core/orchestrator/carica_payload_validation.py")
    assert_true("_nested_carica_value" in source, "orchestrator must read nested CARICA fields")
    assert_true("validate_rest_carica_task_payload" in source, "orchestrator must validate REST CARICA payload")


def _static_rest_sink_batch_modes() -> None:
    source = _read_text("backend/app/core/connections/rest_sink.py")
    assert_true("PER_RECORD = \"per_record\"" in source, "REST sink must support per-record mode")
    assert_true("BATCH = \"batch\"" in source, "REST sink must support batch mode")
    assert_true("_iter_batches" in source, "REST sink must split batches deterministically")


def _static_rest_sink_required_field_guard() -> None:
    source = _read_text("backend/app/core/connections/rest_sink.py")
    assert_true("REST sink missing required field" in source, "REST sink must fail when required mapped fields are absent")
    assert_true("include_unmapped" in source, "REST sink must support include_unmapped mapping")


def _static_datasource_scan_state_persistence() -> None:
    source = _read_text("backend/app/core/datasources/service.py")
    assert_true("get_scan_state" in source and "save_scan_state" in source, "datasource service must persist scan state")
    assert_true("mark_as_processed" in source, "datasource service must mark processed files")


def _static_ftp_sftp_delete_file_paths() -> None:
    ftp = _read_text("backend/app/core/datasources/ftp.py")
    sftp = _read_text("backend/app/core/datasources/sftp.py")
    assert_true("def delete_file" in ftp, "FTP datasource must support delete_file")
    assert_true("def delete_file" in sftp, "SFTP datasource must support delete_file")


def _static_scheduler_carica_uses_executor() -> None:
    source = _read_text("backend/app/core/scheduler/execution_manager.py")
    assert_true("CaricaExecutor" in source, "scheduler CARICA must use same CaricaExecutor path")
    assert_true("write_mode" in source and "transaction_mode" in source, "scheduler CARICA must pass write mode and transaction mode")


def _static_canvas_spatial_predicates_all_supported() -> None:
    source = _read_text("backend/app/core/projects/canvas_models.py")
    for predicate in ("ST_Crosses", "ST_Equals", "ST_Disjoint", "ST_Covers", "ST_CoveredBy"):
        assert_true(predicate in source, f"canvas Edge must allow {predicate}")


def _static_project_delete_cleans_intermediate_results() -> None:
    source = _read_text("backend/app/core/projects/service.py")
    assert_true("delete_runs_for_canvas" in source, "project delete must clean intermediate runs")
    assert_true("Cleanup results" in source or "cleanup results" in source.lower(), "project delete must clean result blobs")


def _static_run_id_idempotency_guard() -> None:
    source = _read_text("backend/workers/tasks/carica_task.py")
    assert_true("is_already_completed" in source, "CARICA task must check run_id idempotency")
    assert_true("skipped_idempotent" in _read_text("backend/app/core/carica/models.py"), "CARICA models must expose skipped_idempotent")


def _static_worker_connection_cleanup() -> None:
    source = _read_text("backend/workers/tasks/carica_task.py")
    assert_true("close_worker_connection" in source, "CARICA task must close intermediate worker connection")
    assert_true("close_connection" in source, "CARICA task must close config DB connection")


def _static_task_failure_result_sanitized() -> None:
    source = _read_text("backend/workers/tasks/carica_task.py")
    assert_true("Errore task CARICA (consulta log)" in source, "fatal CARICA task result must be sanitized")
    assert_true("error_details" in source, "fatal CARICA task must keep diagnostics separately")


def _rest_auth_query_and_header() -> None:
    base = _fake_rest_base()
    _http_json("GET", f"{base}/auth/api_key_query?api_key=PLENORA_TEST_KEY", expected=(200,))
    _http_json("GET", f"{base}/auth/api_key_query?api_key=WRONG", expected=(401,))
    _http_json("GET", f"{base}/auth/api_key_header", headers=_sink_headers(), expected=(200,))
    _http_json("GET", f"{base}/auth/api_key_header", headers={"X-API-Key": "WRONG"}, expected=(401,))


def _rest_auth_bearer_and_basic() -> None:
    base = _fake_rest_base()
    _http_json("GET", f"{base}/auth/bearer", headers={"Authorization": "Bearer PLENORA_TEST_BEARER"}, expected=(200,))
    _http_json("GET", f"{base}/auth/bearer", headers={"Authorization": "Bearer WRONG"}, expected=(401,))
    basic = "Basic dGVzdGVyOnRlc3Rlcl9wYXNz"
    _http_json("GET", f"{base}/auth/basic", headers={"Authorization": basic}, expected=(200,))
    _http_json("GET", f"{base}/auth/basic", headers={"Authorization": "Basic bad"}, expected=(401,))


def _rest_auth_oauth_flows() -> None:
    base = _fake_rest_base()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": "plenora",
        "client_secret": "plenora_secret",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/auth/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert_equal(payload.get("access_token"), "OAUTH_FAKE_TOKEN", "OAuth client_credentials token")
    _http_json("GET", f"{base}/auth/oauth2/protected", headers={"Authorization": "Bearer OAUTH_FAKE_TOKEN"}, expected=(200,))
    _http_json("GET", f"{base}/auth/oauth2/protected", headers={"Authorization": "Bearer WRONG"}, expected=(401,))


def _carica_rest_records_success(ctx: E2EContext) -> None:
    _reset_sink()
    connection_id = _run_for_dialect(ctx, "mysql", "final_rest_success")
    if connection_id is None:
        return
    canvas = _extract_active_canvas(connection_id, "mysql")
    data = {
        "dest_type": "rest",
        "rest_url": os.environ.get("PLENORA_E2E_FAKE_REST_CONTAINER_SINK_URL", "http://plenora-test-fake-rest:8080/data/sink/records"),
        "rest_method": "POST",
        "rest_headers": [{"name": "X-API-Key", "value": "PLENORA_TEST_KEY", "secret": True}],
        "rest_batch_mode": "batch",
        "rest_batch_size": 1,
        "rest_timeout_seconds": 15,
    }
    canvas["nodes"].append({"id": "carica-1", "type": "carica", "position": {"x": 940, "y": 0}, "data": data, **data})
    canvas["edges"].append(edge("e-result-rest", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name="e2e_v2_final_carica_rest_records_success",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    results = _wait_carica_tasks(ctx, ctx.execute_project(project_id), "carica rest records")
    rows_written = int(results[0].get("rows_written") or 0)
    batches = results[0].get("batches") or []
    assert_true(rows_written > 0, "CARICA REST must write rows")
    assert_true(len(batches) >= 1, "CARICA REST must expose at least one batch summary")
    assert_equal(
        sum(int(batch.get("sent_count") or 0) for batch in batches if isinstance(batch, dict)),
        rows_written,
        "CARICA REST batch summaries must match written rows",
    )


def _carica_rest_auth_failure(ctx: E2EContext) -> None:
    connection_id = _run_for_dialect(ctx, "mysql", "final_rest_auth_failure")
    if connection_id is None:
        return
    canvas = _extract_active_canvas(connection_id, "mysql")
    data = {
        "dest_type": "rest",
        "rest_url": os.environ.get("PLENORA_E2E_FAKE_REST_CONTAINER_SINK_URL", "http://plenora-test-fake-rest:8080/data/sink/records"),
        "rest_method": "POST",
        "rest_headers": [{"name": "X-API-Key", "value": "WRONG", "secret": True}],
        "rest_batch_mode": "batch",
        "rest_batch_size": 50,
        "rest_timeout_seconds": 15,
    }
    canvas["nodes"].append({"id": "carica-1", "type": "carica", "position": {"x": 940, "y": 0}, "data": data, **data})
    canvas["edges"].append(edge("e-result-rest-fail", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name="e2e_v2_final_carica_rest_auth_failure",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    _expect_carica_task_failure(ctx, project_id, "401", "CARICA REST auth failure")


def _datasource_write_node(datasource_id: str, dest_format: str, dest_filename: str) -> dict[str, Any]:
    data = {
        "datasource_id": datasource_id,
        "mode": "carica",
        "dest_folder": "e2e_final_write",
        "dest_filename": dest_filename,
        "dest_format": dest_format,
    }
    return {
        "id": "carica-1",
        "type": "datasource_node",
        "position": {"x": 940, "y": 0},
        "data": data,
        **data,
    }


def _carica_datasource_write(ctx: E2EContext, *, kind: str, dest_format: str) -> None:
    ensure_plenora_test_network_access()
    datasource_id = ctx.create_datasource(_datasource_payload(kind, unique_name(f"e2e_v2_final_{kind}_write")))
    tested = ctx.api_json("POST", f"/datasources/{datasource_id}/test")
    assert_true(tested.get("success"), f"{kind}: datasource connection must succeed")
    connection_id = _run_for_dialect(ctx, "mysql", f"final_{kind}_{dest_format}_write")
    if connection_id is None:
        return
    canvas = _extract_active_canvas(connection_id, "mysql")
    filename = unique_name(f"{kind}_{dest_format}")[:48]
    canvas["nodes"].append(_datasource_write_node(datasource_id, dest_format, filename))
    canvas["edges"].append(edge("e-result-datasource", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_final_{kind}_{dest_format}_datasource_write",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    _wait_carica_tasks(ctx, ctx.execute_project(project_id), f"{kind}:{dest_format}:datasource_write")
    path = f"e2e_final_write/{filename}.{dest_format}"
    schema = ctx.api_json("GET", f"/datasources/{datasource_id}/files/schema?path={urllib.parse.quote(path)}")
    columns = {str(column.get("name")) for column in schema.get("columns") or []}
    assert_true({"customer_id", "customer_name", "age"} <= columns, f"{kind}: written {dest_format} schema must be readable")


def _carica_update_case(ctx: E2EContext, *, dialect: str) -> None:
    connection_id, schema_name, table_name, active_canvas, active_count = _prepare_active_table(ctx, dialect, "final_update")
    if not connection_id:
        return
    node = _advanced_node(connection_id, schema_name, table_name, write_mode="update")
    results = _carica_with_node(
        ctx,
        dialect=dialect,
        connection_id=connection_id,
        source_canvas=active_canvas,
        carica_node=node,
        suffix=f"{dialect}_update",
    )
    assert_equal(results[0].get("rows_updated"), active_count, f"{dialect}: update must report updated rows")
    _verify_count(ctx, connection_id, table_name, dialect, active_count)


def _carica_upsert_insert_case(ctx: E2EContext, *, dialect: str) -> None:
    connection_id, schema_name, table_name, _, active_count = _prepare_active_table(ctx, dialect, "final_upsert")
    if not connection_id:
        return
    pending_canvas, pending_count = _pending_canvas_and_count(ctx, dialect, connection_id)
    node = _advanced_node(connection_id, schema_name, table_name, write_mode="upsert")
    results = _carica_with_node(
        ctx,
        dialect=dialect,
        connection_id=connection_id,
        source_canvas=pending_canvas,
        carica_node=node,
        suffix=f"{dialect}_upsert_insert",
    )
    assert_equal(results[0].get("rows_updated"), pending_count, f"{dialect}: upsert must process pending rows")
    _verify_count(ctx, connection_id, table_name, dialect, active_count + pending_count)


def _carica_append_conflict_case(ctx: E2EContext, *, policy: str) -> None:
    dialect = "mysql"
    connection_id, schema_name, table_name, active_canvas, active_count = _prepare_active_table(ctx, dialect, f"final_append_conflict_{policy}")
    if not connection_id:
        return
    node = _advanced_node(connection_id, schema_name, table_name, write_mode="append", conflict_policy=policy)
    canvas = json.loads(json.dumps(active_canvas))
    canvas["nodes"].append(node)
    canvas["edges"].append(edge(f"e-result-append-{policy}", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_final_mysql_append_conflict_{policy}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    if policy == "abort":
        _expect_carica_task_failure(ctx, project_id, "conflitti", "mysql append conflict abort")
        _verify_count(ctx, connection_id, table_name, dialect, active_count)
        return
    results = _wait_carica_tasks(ctx, ctx.execute_project(project_id), f"mysql append conflict {policy}")
    if policy == "skip":
        assert_equal(results[0].get("rows_written"), 0, "append conflict skip must write no duplicate rows")
        _verify_count(ctx, connection_id, table_name, dialect, active_count)
    elif policy == "force":
        assert_equal(results[0].get("rows_inserted"), active_count, "append conflict force must insert duplicate rows")
        _verify_count(ctx, connection_id, table_name, dialect, active_count * 2)


def _carica_per_batch_guard_case(ctx: E2EContext, *, allow: bool) -> None:
    dialect = "mysql"
    connection_id, schema_name, table_name, active_canvas, active_count = _prepare_active_table(ctx, dialect, f"final_per_batch_{allow}")
    if not connection_id:
        return
    node = _carica_mode_node(
        connection_id,
        schema_name,
        table_name,
        write_mode="append",
        transaction_mode="per_batch",
        allow_unsafe_append=allow,
    )
    canvas = json.loads(json.dumps(active_canvas))
    canvas["nodes"].append(node)
    canvas["edges"].append(edge("e-result-per-batch", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name=f"e2e_v2_final_mysql_per_batch_allow_{allow}",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    if not allow:
        _expect_carica_task_failure(ctx, project_id, "combo non sicura", "mysql per_batch unsafe append")
        _verify_count(ctx, connection_id, table_name, dialect, active_count)
        return
    _wait_carica_tasks(ctx, ctx.execute_project(project_id), "mysql per_batch unsafe append allowed")
    _verify_count(ctx, connection_id, table_name, dialect, active_count * 2)


def _carica_invalid_mapping_rollback(ctx: E2EContext) -> None:
    dialect = "mysql"
    connection_id, schema_name, table_name, active_canvas, active_count = _prepare_active_table(ctx, dialect, "final_invalid_mapping")
    if not connection_id:
        return
    node = _carica_mode_node(connection_id, schema_name, table_name, write_mode="append")
    # Mappa una sorgente valida su una colonna di destinazione inesistente
    # (NON duplicare la sorgente: un secondo mapping "age" verrebbe bloccato a
    # validazione come mapping.dup_source, mascherando il path che vogliamo
    # esercitare). Sorgenti uniche => la validazione passa e il fallimento
    # "Colonne non trovate" emerge a runtime, con rollback (rows_written=0).
    for _m in node["column_mappings"]:
        if _m.get("source_column") == "age":
            _m["dest_column"] = "not_existing_dest"
    node["data"]["column_mappings"] = node["column_mappings"]
    canvas = json.loads(json.dumps(active_canvas))
    canvas["nodes"].append(node)
    canvas["edges"].append(edge("e-result-invalid-mapping", "result-1", "carica-1"))
    project_id = ctx.create_project(
        name="e2e_v2_final_mysql_invalid_mapping_rollback",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    _expect_carica_task_failure(ctx, project_id, "Colonne non trovate", "mysql invalid mapping")
    _verify_count(ctx, connection_id, table_name, dialect, active_count)


CASES: dict[str, CaseFn] = {
    "test_401": _static_canvas_advanced_carica_fields,
    "test_402": _static_write_config_advanced_fields,
    "test_403": _static_writer_dispatch_advanced_modes,
    "test_404": _static_ddl_upsert_all_dialects,
    "test_405": _static_update_sql_uses_match_params,
    "test_406": _static_preflight_requires_match_columns,
    "test_407": _static_transaction_rollback_guard,
    "test_408": _static_target_lock_all_branches,
    "test_409": _static_no_carica_retries,
    "test_410": _static_rest_sink_validation_and_redaction,
    "test_411": _static_datasource_write_paths,
    "test_412": _rest_auth_query_and_header,
    "test_413": _rest_auth_bearer_and_basic,
    "test_414": _rest_auth_oauth_flows,
    "test_415": _ctx_case(_carica_rest_records_success),
    "test_416": _ctx_case(_carica_rest_auth_failure),
    "test_417": _ctx_case(lambda ctx: _carica_datasource_write(ctx, kind="ftp", dest_format="csv")),
    "test_418": _ctx_case(lambda ctx: _carica_datasource_write(ctx, kind="sftp", dest_format="csv")),
    "test_419": _ctx_case(lambda ctx: _carica_datasource_write(ctx, kind="ftp", dest_format="json")),
    "test_420": _ctx_case(lambda ctx: _carica_datasource_write(ctx, kind="sftp", dest_format="json")),
    "test_421": _ctx_case(lambda ctx: _carica_update_case(ctx, dialect="mysql")),
    "test_422": _ctx_case(lambda ctx: _carica_update_case(ctx, dialect="mssql")),
    "test_423": _ctx_case(lambda ctx: _carica_update_case(ctx, dialect="oracle")),
    "test_424": _ctx_case(lambda ctx: _carica_upsert_insert_case(ctx, dialect="mysql")),
    "test_425": _ctx_case(lambda ctx: _carica_upsert_insert_case(ctx, dialect="mssql")),
    "test_426": _ctx_case(lambda ctx: _carica_upsert_insert_case(ctx, dialect="oracle")),
    "test_427": _ctx_case(lambda ctx: _carica_append_conflict_case(ctx, policy="abort")),
    "test_428": _ctx_case(lambda ctx: _carica_append_conflict_case(ctx, policy="skip")),
    "test_429": _ctx_case(lambda ctx: _carica_append_conflict_case(ctx, policy="force")),
    "test_430": _ctx_case(lambda ctx: _carica_per_batch_guard_case(ctx, allow=False)),
    "test_431": _ctx_case(lambda ctx: _carica_per_batch_guard_case(ctx, allow=True)),
    "test_432": _ctx_case(_carica_invalid_mapping_rollback),
}


CASES.update({
    "test_433": _static_datasource_upload_mime_guard,
    "test_434": _static_datasource_path_sanitization,
    "test_435": _static_carica_result_count_derivation,
    "test_436": _static_conflict_checker_duplicates,
    "test_437": _static_conflict_checker_multi_column,
    "test_438": _static_column_mapper_oracle_uppercase,
    "test_439": _static_output_contract_types_enricher,
    "test_440": _static_carica_payload_validation_nested_rest,
    "test_441": _static_rest_sink_batch_modes,
    "test_442": _static_rest_sink_required_field_guard,
    "test_443": _static_datasource_scan_state_persistence,
    "test_444": _static_ftp_sftp_delete_file_paths,
    "test_445": _static_scheduler_carica_uses_executor,
    "test_446": _static_canvas_spatial_predicates_all_supported,
    "test_447": _static_project_delete_cleans_intermediate_results,
    "test_448": _static_run_id_idempotency_guard,
    "test_449": _static_worker_connection_cleanup,
    "test_450": _static_task_failure_result_sanitized,
})


def run_case(case_id: str) -> None:
    try:
        case = CASES[case_id]
    except KeyError:
        raise AssertionError(f"Unknown final coverage case: {case_id}") from None
    try:
        case()
    except urllib.error.URLError as exc:
        fail(f"{case_id} failed to reach local service: {exc}")
