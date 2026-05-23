"""Multi-tenant negative E2E coverage.

These tests intentionally exercise both supported HTTP boundaries and
runtime bypass paths where a tenant-owned project is tampered directly in the
config DB. The runtime cases are the critical part: API validation is not a
complete tenant boundary if workers can still dereference foreign resources.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from _cross_db_full import (
    _carica_mode_node,
    _datasource_payload,
    _extract_active_canvas,
)
from _e2e import (
    API_BASE_URL,
    E2EContext,
    E2EError,
    assert_equal,
    assert_true,
    auth_token,
    edge,
    ensure_plenora_test_network_access,
    fail,
    postgres_fixture_connection_payload,
    project_source_node,
    project_source_node_from_contract,
    result_node,
    run_sql,
    source_column,
    source_node,
    unique_name,
)


CaseFn = Callable[[E2EContext, E2EContext, E2EContext, str, str], None]


@dataclass
class HttpResult:
    status: int
    body: Any


def _request(
    token: str,
    method: str,
    path: str,
    *,
    payload: Any = None,
    timeout: int = 30,
) -> HttpResult:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        fail(f"{method} {path} failed: {exc}")

    body: Any = None
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
    return HttpResult(status=status, body=body)


def _expect_status(result: HttpResult, expected: tuple[int, ...], label: str) -> None:
    if result.status not in expected:
        fail(f"{label}: expected HTTP {expected}, got {result.status}: {result.body!r}")


def _expect_denied(result: HttpResult, label: str) -> None:
    _expect_status(result, (403, 404), label)


def _ids_from_list(payload: Any, key: str) -> set[str]:
    if isinstance(payload, dict):
        rows = payload.get(key) or payload.get("items") or []
    else:
        rows = payload if isinstance(payload, list) else []
    return {str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")}


def _login(username: str, password: str) -> str:
    request = urllib.request.Request(
        f"{API_BASE_URL}/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        fail(f"login for {username} failed: HTTP {exc.code} {exc.read().decode('utf-8', errors='replace')}")
    token = body.get("access_token") if isinstance(body, dict) else None
    assert_true(token, f"login for {username} must return access_token")
    return str(token)


def _tenant_contexts(case_id: str) -> tuple[E2EContext, E2EContext, E2EContext, str, str]:
    super_token = auth_token()
    super_ctx = E2EContext(token=super_token)
    suffix = unique_name(case_id).lower().replace("-", "_")[-24:]
    group_a = f"e2e_mt_a_{suffix}"
    group_b = f"e2e_mt_b_{suffix}"
    user_a = f"e2e_mt_a_{suffix}"
    user_b = f"e2e_mt_b_{suffix}"
    password_a = "E2eTenantPwd!2026A"
    password_b = "E2eTenantPwd!2026B"

    for group_id, name in ((group_a, "Tenant A"), (group_b, "Tenant B")):
        _expect_status(
            _request(
                super_token,
                "POST",
                "/auth/groups",
                payload={
                    "group_id": group_id,
                    "name": name,
                    "description": f"E2E multi-tenant negative {case_id}",
                },
            ),
            (201,),
            f"{case_id}: create group {group_id}",
        )

    for username, password, group_id in (
        (user_a, password_a, group_a),
        (user_b, password_b, group_b),
    ):
        _expect_status(
            _request(
                super_token,
                "POST",
                "/auth/users",
                payload={
                    "username": username,
                    "password": password,
                    "email": f"{username}@example.test",
                    "group_id": group_id,
                    "role": "admin",
                },
            ),
            (201,),
            f"{case_id}: create admin {username}",
        )

    return (
        super_ctx,
        E2EContext(token=_login(user_a, password_a)),
        E2EContext(token=_login(user_b, password_b)),
        group_a,
        group_b,
    )


def _delete_group(super_ctx: E2EContext, group_id: str) -> None:
    result = _request(super_ctx.token, "DELETE", f"/auth/groups/{urllib.parse.quote(group_id)}")
    if result.status not in (200, 404):
        print(f"WARNING: group cleanup failed for {group_id}: HTTP {result.status} {result.body!r}")


def _run_tenant_case(case_id: str, fn: CaseFn) -> None:
    super_ctx, ctx_a, ctx_b, group_a, group_b = _tenant_contexts(case_id)
    try:
        fn(super_ctx, ctx_a, ctx_b, group_a, group_b)
    finally:
        _delete_group(super_ctx, group_a)
        _delete_group(super_ctx, group_b)


def _assert_carica_task_fails(ctx: E2EContext, project_id: str, fragments: tuple[str, ...], label: str) -> None:
    try:
        execute_result = ctx.execute_project(project_id)
    except E2EError as exc:
        message = str(exc).lower()
        assert_true(
            any(fragment.lower() in message for fragment in fragments),
            f"{label}: execute failure must mention one of {fragments}: {exc}",
        )
        return

    tasks = ((execute_result.get("carica") or {}).get("tasks") or [])
    assert_true(tasks, f"{label}: execute result must include CARICA tasks")
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        assert_true(task_id, f"{label}: missing task_id in CARICA task payload")
        try:
            status = ctx.wait_task(task_id)
        except E2EError as exc:
            message = str(exc).lower()
            assert_true(
                any(fragment.lower() in message for fragment in fragments),
                f"{label}: CARICA task failure must mention one of {fragments}: {exc}",
            )
            return
        result = status.get("result") or {}
        if result.get("success") is False:
            message = " ".join(str(result.get(key) or "") for key in ("message", "error_message", "error")).lower()
            assert_true(
                any(fragment.lower() in message for fragment in fragments),
                f"{label}: CARICA task failure must mention one of {fragments}: {result!r}",
            )
            return
    fail(f"{label}: CARICA task unexpectedly succeeded")


def _assert_preview_fails(ctx: E2EContext, project_id: str, fragments: tuple[str, ...], label: str) -> None:
    try:
        ctx.preview_project(project_id, limit=5)
    except E2EError as exc:
        message = str(exc).lower()
        assert_true(
            any(fragment.lower() in message for fragment in fragments),
            f"{label}: failure must mention one of {fragments}: {exc}",
        )
        return
    fail(f"{label}: preview unexpectedly succeeded")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _replace_project_canvas(project_id: str, canvas: dict[str, Any]) -> None:
    payload = json.dumps(canvas, separators=(",", ":"))
    run_sql(
        "UPDATE projects "
        f"SET canvas = {_sql_literal(payload)}, updated_at = NOW() "
        f"WHERE id = {_sql_literal(project_id)};"
    )


def _replace_project_connection(project_id: str, connection_id: str) -> None:
    run_sql(
        "UPDATE projects "
        f"SET connection_id = {_sql_literal(connection_id)}, updated_at = NOW() "
        f"WHERE id = {_sql_literal(project_id)};"
    )


def _active_source_canvas(connection_id: str) -> dict[str, Any]:
    return _extract_active_canvas(connection_id, "postgres")


def _datasource_source_canvas(datasource_id: str) -> dict[str, Any]:
    columns = [
        {"id": "ds-id", "name": "id", "type": "int", "visible": True},
        {"id": "ds-name", "name": "full_name", "type": "str", "visible": True},
        {"id": "ds-age", "name": "age", "type": "int", "visible": True},
    ]
    node = {
        "id": "datasource-1",
        "type": "datasource_node",
        "position": {"x": 0, "y": 0},
        "datasource_id": datasource_id,
        "file_path": "fixtures/csv/customers_basic.csv",
        "columns": columns,
        "data": {
            "datasource_id": datasource_id,
            "file_path": "fixtures/csv/customers_basic.csv",
            "columns": columns,
        },
    }
    return {
        "nodes": [node, result_node(format="csv", fileName="e2e_mt_ds_source")],
        "edges": [edge("e-ds-result", "datasource-1", "result-1")],
    }


def _datasource_carica_node(datasource_id: str) -> dict[str, Any]:
    data = {
        "mode": "carica",
        "datasource_id": datasource_id,
        "dest_folder": "e2e_mt_forbidden",
        "dest_filename": f"denied_{int(time.time())}",
        "dest_format": "csv",
    }
    return {
        "id": "carica-ds",
        "type": "datasource_node",
        "position": {"x": 880, "y": 0},
        **data,
        "data": data,
    }


def _connection(ctx: E2EContext, suffix: str) -> str:
    ensure_plenora_test_network_access()
    connection_id = ctx.create_connection(
        postgres_fixture_connection_payload(unique_name(f"e2e_mt_{suffix}_pg"))
    )
    tested = ctx.test_connection(connection_id)
    assert_true(tested.get("success"), f"{suffix}: fixture connection must succeed")
    return connection_id


def _datasource(ctx: E2EContext, suffix: str) -> str:
    ensure_plenora_test_network_access()
    datasource_id = ctx.create_datasource(_datasource_payload("ftp", unique_name(f"e2e_mt_{suffix}_ftp")))
    tested = ctx.api_json("POST", f"/datasources/{datasource_id}/test")
    assert_true(tested.get("success"), f"{suffix}: FTP datasource must succeed")
    return datasource_id


def case_project_api_isolation(
    super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    project_id = ctx_a.create_project(name="e2e_mt_project_owner_a", project_type="manipola")
    listed = ctx_b.api_json("GET", "/projects")
    assert_true(project_id not in _ids_from_list(listed.get("projects") if isinstance(listed, dict) else listed, "projects"), "tenant B project list must not include tenant A project")
    _expect_status(_request(super_ctx.token, "GET", f"/projects/{project_id}"), (200,), "superadmin can read tenant A project")
    _expect_denied(_request(ctx_b.token, "GET", f"/projects/{project_id}"), "tenant B cannot read tenant A project")
    _expect_denied(_request(ctx_b.token, "PUT", f"/projects/{project_id}/canvas", payload={"nodes": [], "edges": []}), "tenant B cannot update tenant A project canvas")
    _expect_denied(_request(ctx_b.token, "DELETE", f"/projects/{project_id}"), "tenant B cannot delete tenant A project")


def case_connection_api_isolation(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    connection_id = _connection(ctx_a, "api_boundary_a")
    listed = ctx_b.api_json("GET", "/connections")
    assert_true(connection_id not in _ids_from_list(listed, "connections"), "tenant B connection list must not include tenant A connection")
    _expect_denied(_request(ctx_b.token, "POST", f"/connections/{connection_id}/test"), "tenant B cannot test tenant A connection")
    _expect_denied(_request(ctx_b.token, "GET", f"/connections/{connection_id}/tables"), "tenant B cannot browse tenant A connection")
    _expect_denied(_request(ctx_b.token, "GET", f"/carica/schemas/{connection_id}"), "tenant B cannot browse CARICA schemas on tenant A connection")
    _expect_denied(_request(ctx_b.token, "GET", f"/carica/tables/{connection_id}/public"), "tenant B cannot browse CARICA tables on tenant A connection")
    _expect_denied(
        _request(
            ctx_b.token,
            "POST",
            "/carica/auto-mapping",
            payload={
                "connection_id": connection_id,
                "schema_name": "public",
                "table_name": "customers",
                "source_columns": [{"name": "id", "type": "int"}],
            },
        ),
        "tenant B cannot auto-map tenant A connection",
    )
    _expect_denied(
        _request(
            ctx_b.token,
            "POST",
            "/carica/check-conflicts",
            payload={
                "connection_id": connection_id,
                "schema_name": "public",
                "table_name": "customers",
                "match_columns": ["id"],
                "source_rows": [{"id": 1}],
            },
        ),
        "tenant B cannot conflict-check tenant A connection",
    )
    _expect_denied(_request(ctx_b.token, "PUT", f"/connections/{connection_id}", payload={"description": "blocked"}), "tenant B cannot update tenant A connection")
    _expect_denied(_request(ctx_b.token, "DELETE", f"/connections/{connection_id}"), "tenant B cannot delete tenant A connection")


def case_datasource_api_isolation(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    datasource_id = _datasource(ctx_a, "api_boundary_a")
    listed = ctx_b.api_json("GET", "/datasources")
    assert_true(datasource_id not in _ids_from_list(listed, "datasources"), "tenant B datasource list must not include tenant A datasource")
    _expect_denied(_request(ctx_b.token, "GET", f"/datasources/{datasource_id}"), "tenant B cannot read tenant A datasource")
    _expect_denied(_request(ctx_b.token, "POST", f"/datasources/{datasource_id}/test"), "tenant B cannot test tenant A datasource")
    _expect_denied(_request(ctx_b.token, "GET", f"/datasources/{datasource_id}/files?path=fixtures/csv"), "tenant B cannot list tenant A datasource files")
    _expect_denied(_request(ctx_b.token, "GET", f"/datasources/{datasource_id}/files/schema?path=fixtures/csv/customers_basic.csv"), "tenant B cannot read tenant A datasource schema")
    _expect_denied(_request(ctx_b.token, "PUT", f"/datasources/{datasource_id}", payload={"description": "blocked"}), "tenant B cannot update tenant A datasource")
    _expect_denied(_request(ctx_b.token, "DELETE", f"/datasources/{datasource_id}"), "tenant B cannot delete tenant A datasource")


def case_save_time_cross_refs_blocked(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    connection_id = _connection(ctx_a, "save_time_a")
    datasource_id = _datasource(ctx_a, "save_time_a")
    upstream_id = ctx_a.create_project(name="e2e_mt_save_time_upstream", project_type="manipola")
    project_id = ctx_b.create_project(name="e2e_mt_save_time_target", project_type="manipola")

    _expect_status(
        _request(
            ctx_b.token,
            "POST",
            "/projects",
            payload={
                "name": "e2e_mt_cross_default_connection",
                "project_type": "estrai",
                "default_connection_id": connection_id,
            },
        ),
        (400,),
        "create project must reject foreign default_connection_id",
    )
    _expect_status(
        _request(
            ctx_b.token,
            "POST",
            "/projects",
            payload={
                "name": "e2e_mt_cross_project_source_create",
                "project_type": "manipola",
                "canvas": {
                    "nodes": [
                        project_source_node(
                            node_id="source-1",
                            source_project_id=upstream_id,
                            column_id="col-id",
                            name="id",
                            source_name="id",
                        ),
                        result_node(),
                    ],
                    "edges": [edge("e-source-result", "source-1", "result-1")],
                },
            },
        ),
        (400,),
        "create project must reject foreign project_source",
    )
    _expect_status(
        _request(
            ctx_b.token,
            "PUT",
            f"/projects/{project_id}/canvas",
            payload={
                "nodes": [_datasource_carica_node(datasource_id), result_node()],
                "edges": [edge("e-result-carica", "result-1", "carica-ds")],
            },
        ),
        (400,),
        "update canvas must reject foreign datasource target",
    )
    _expect_status(
        _request(
            ctx_b.token,
            "PUT",
            f"/projects/{project_id}/canvas",
            payload={
                "nodes": [
                    source_node(
                        node_id="customers",
                        table_name="customers",
                        alias="c",
                        connection_id=connection_id,
                        columns=[source_column("id", logical_type="int")],
                    ),
                    result_node(),
                ],
                "edges": [edge("e-source-result", "customers", "result-1")],
            },
        ),
        (400,),
        "update canvas must reject foreign connection source",
    )


def case_runtime_project_source_tamper_denied(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    connection_id = _connection(ctx_a, "runtime_project_source_a")
    upstream_id = ctx_a.create_project(
        name="e2e_mt_runtime_project_source_upstream",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=_active_source_canvas(connection_id),
    )
    ctx_a.execute_project(upstream_id)
    contract = ctx_a.get_project(upstream_id).get("output_contract") or {}
    assert_true(contract.get("columns"), "upstream output contract required")
    source = project_source_node_from_contract(
        node_id="source-1",
        source_project_id=upstream_id,
        contract=contract,
    )
    project_id = ctx_b.create_project(name="e2e_mt_runtime_project_source_target", project_type="manipola")
    _replace_project_canvas(
        project_id,
        {"nodes": [source, result_node()], "edges": [edge("e-source-result", "source-1", "result-1")]},
    )
    _assert_preview_fails(ctx_b, project_id, ("accesso negato", "non visibile", "denied"), "runtime project_source tamper")


def case_runtime_connection_tamper_denied(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    connection_id = _connection(ctx_a, "runtime_connection_a")
    project_id = ctx_b.create_project(name="e2e_mt_runtime_connection_target", project_type="estrai")
    _replace_project_connection(project_id, connection_id)
    _replace_project_canvas(project_id, _active_source_canvas(connection_id))
    _assert_preview_fails(ctx_b, project_id, ("non visibile", "accesso negato", "denied"), "runtime ESTRAI connection tamper")


def case_runtime_datasource_source_tamper_denied(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    datasource_id = _datasource(ctx_a, "runtime_datasource_source_a")
    project_id = ctx_b.create_project(name="e2e_mt_runtime_datasource_source_target", project_type="manipola")
    _replace_project_canvas(project_id, _datasource_source_canvas(datasource_id))
    _assert_preview_fails(ctx_b, project_id, ("datasource", "non visibile", "accesso negato", "denied"), "runtime datasource source tamper")


def case_runtime_carica_datasource_tamper_denied(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    datasource_id = _datasource(ctx_a, "runtime_carica_ds_a")
    connection_id = _connection(ctx_b, "runtime_carica_ds_b")
    canvas = _active_source_canvas(connection_id)
    project_id = ctx_b.create_project(
        name="e2e_mt_runtime_carica_datasource_target",
        project_type="estrai",
        default_connection_id=connection_id,
        canvas=canvas,
    )
    tampered = json.loads(json.dumps(canvas))
    tampered["nodes"].append(_datasource_carica_node(datasource_id))
    tampered["edges"].append(edge("e-result-carica-ds", "result-1", "carica-ds"))
    _replace_project_canvas(project_id, tampered)
    _assert_carica_task_fails(ctx_b, project_id, ("datasource", "non visibile", "visibility"), "runtime CARICA datasource tamper")


def case_runtime_carica_connection_tamper_denied(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    ctx_b: E2EContext,
    _group_a: str,
    _group_b: str,
) -> None:
    foreign_connection_id = _connection(ctx_a, "runtime_carica_conn_a")
    source_connection_id = _connection(ctx_b, "runtime_carica_conn_b")
    canvas = _active_source_canvas(source_connection_id)
    project_id = ctx_b.create_project(
        name="e2e_mt_runtime_carica_connection_target",
        project_type="estrai",
        default_connection_id=source_connection_id,
        canvas=canvas,
    )
    tampered = json.loads(json.dumps(canvas))
    target_table = unique_name("e2e_mt_forbidden").lower()[:48]
    tampered["nodes"].append(
        _carica_mode_node(
            foreign_connection_id,
            "public",
            target_table,
            write_mode="create",
            if_table_exists="error",
        )
    )
    tampered["edges"].append(edge("e-result-carica-db", "result-1", "carica-1"))
    _replace_project_canvas(project_id, tampered)
    _assert_carica_task_fails(ctx_b, project_id, ("connessione", "non visibile", "visibility"), "runtime CARICA DB connection tamper")


def case_admin_user_group_confinement(
    _super_ctx: E2EContext,
    ctx_a: E2EContext,
    _ctx_b: E2EContext,
    group_a: str,
    group_b: str,
) -> None:
    username = f"e2e_mt_user_{unique_name('confine')[-12:].lower()}"
    result = _request(
        ctx_a.token,
        "POST",
        "/auth/users",
        payload={
            "username": username,
            "password": "E2eTenantPwd!2026C",
            "email": f"{username}@example.test",
            "group_id": group_b,
            "role": "user",
        },
    )
    _expect_status(result, (201,), "tenant admin can create only confined user")
    assert_equal(result.body.get("group_id"), group_a, "non-superadmin user creation must be forced to own group")
    assert_equal(result.body.get("role"), "user", "non-superadmin user creation must not escalate role")


CASES: dict[str, CaseFn] = {
    "test_691": case_project_api_isolation,
    "test_692": case_connection_api_isolation,
    "test_693": case_datasource_api_isolation,
    "test_694": case_save_time_cross_refs_blocked,
    "test_695": case_runtime_project_source_tamper_denied,
    "test_696": case_runtime_connection_tamper_denied,
    "test_697": case_runtime_datasource_source_tamper_denied,
    "test_698": case_runtime_carica_datasource_tamper_denied,
    "test_699": case_runtime_carica_connection_tamper_denied,
    "test_700": case_admin_user_group_confinement,
}


def run_case(case_id: str) -> None:
    try:
        case = CASES[case_id]
    except KeyError:
        raise AssertionError(f"Unknown multi-tenant negative case: {case_id}") from None
    _run_tenant_case(case_id, case)
