"""Shared helpers for host-launched Plenora E2E v2 tests.

These tests are intentionally run outside the backend container. They use the
public HTTP API for application behavior and Docker CLI only to seed/read the
application database fixture state needed by output_contract regressions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from csv import DictReader
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any


TEST_ROOT = Path(__file__).resolve().parent
ENV_FILES = (
    TEST_ROOT.parent.parent / ".env",
    TEST_ROOT.parent / ".env",
    TEST_ROOT / ".env",
)


def _load_env_file() -> None:
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()


API_BASE_URL = os.environ.get("PLENORA_E2E_API_BASE_URL", "http://127.0.0.1:5004/api").rstrip("/")
DB_CONTAINER = os.environ.get("PLENORA_E2E_DB_CONTAINER", "plenora-postgres-1")
DB_USER = os.environ.get("PLENORA_E2E_DB_USER", "plenora")
DB_NAME = os.environ.get("PLENORA_E2E_DB_NAME", "plenora")
TASK_TIMEOUT_SECONDS = int(os.environ.get("PLENORA_E2E_TASK_TIMEOUT_SECONDS", "90"))


class E2EError(AssertionError):
    """Readable assertion failure for standalone E2E scripts."""


def fail(message: str) -> None:
    raise E2EError(message)


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        fail(f"{message}\nexpected: {expected!r}\nactual:   {actual!r}")


def assert_true(value: Any, message: str) -> None:
    if not value:
        fail(message)


def _json_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: Any = None,
    expected: tuple[int, ...] = (200,),
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = exc.code
        if status not in expected:
            fail(f"{method} {path} returned HTTP {status}: {body}")
        return json.loads(body) if body else None
    except urllib.error.URLError as exc:
        fail(f"{method} {path} failed: {exc}")

    if status not in expected:
        fail(f"{method} {path} returned HTTP {status}: {body}")
    return json.loads(body) if body else None


def _raw_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    expected: tuple[int, ...] = (200,),
) -> bytes:
    headers = {"Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        if status not in expected:
            fail(
                f"{method} {path} returned HTTP {status}: "
                f"{body.decode('utf-8', errors='replace')}"
            )
        return body
    except urllib.error.URLError as exc:
        fail(f"{method} {path} failed: {exc}")

    if status not in expected:
        fail(
            f"{method} {path} returned HTTP {status}: "
            f"{body.decode('utf-8', errors='replace')}"
        )
    return body


def auth_token() -> str:
    token = os.environ.get("PLENORA_E2E_TOKEN")
    if token:
        return token

    username = os.environ.get("PLENORA_E2E_USERNAME")
    password = os.environ.get("PLENORA_E2E_PASSWORD")
    if not username or not password:
        fail(
            "Missing auth. Set PLENORA_E2E_TOKEN or "
            "PLENORA_E2E_USERNAME + PLENORA_E2E_PASSWORD."
        )

    response = _json_request(
        "POST",
        "/auth/login",
        payload={"username": username, "password": password},
    )
    access_token = response.get("access_token") if isinstance(response, dict) else None
    if not access_token:
        fail(f"Login response did not contain access_token: {response!r}")
    return str(access_token)


@dataclass
class E2EContext:
    token: str
    created_project_ids: list[str] = field(default_factory=list)
    created_api_connection_ids: list[str] = field(default_factory=list)
    created_connection_ids: list[str] = field(default_factory=list)
    created_datasource_ids: list[str] = field(default_factory=list)
    keep_on_success: bool = field(
        default_factory=lambda: os.environ.get("PLENORA_E2E_KEEP_ON_SUCCESS", "").lower()
        in {"1", "true", "yes"}
    )

    def create_project(
        self,
        *,
        name: str,
        project_type: str,
        default_connection_id: str | None = None,
        canvas: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "name": name,
            "project_type": project_type,
        }
        if default_connection_id is not None:
            payload["default_connection_id"] = default_connection_id
        if canvas is not None:
            payload["canvas"] = canvas
        response = _json_request(
            "POST",
            "/projects",
            token=self.token,
            payload=payload,
            expected=(201,),
        )
        project_id = response.get("id") or (response.get("metadata") or {}).get("id")
        if not project_id:
            fail(f"Project create response did not contain id: {response!r}")
        self.created_project_ids.append(str(project_id))
        return str(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return _json_request("GET", f"/projects/{project_id}", token=self.token)

    def update_canvas(self, project_id: str, canvas: dict[str, Any]) -> dict[str, Any]:
        return _json_request(
            "PUT",
            f"/projects/{project_id}/canvas",
            token=self.token,
            payload=canvas,
        )

    def validate_project(self, project_id: str) -> dict[str, Any]:
        """POST /validate (Epic Canvas Validation §12).

        Valida la catena del progetto sul canvas SALVATO e persiste lo stato
        ``canvas_validated``. Ritorna ``{valid, has_errors, issues:[...]}`` (HTTP 200
        anche con errori: e' il chiamante a decidere). Solleva solo su HTTP != 200.
        """
        return _json_request(
            "POST",
            f"/projects/{project_id}/validate",
            token=self.token,
        )

    def delete_project(self, project_id: str) -> None:
        _json_request(
            "DELETE",
            f"/projects/{project_id}",
            token=self.token,
            expected=(204,),
        )

    def create_api_connection(self, payload: dict[str, Any]) -> str:
        response = _json_request(
            "POST",
            "/api-connections",
            token=self.token,
            payload=payload,
            expected=(201,),
        )
        api_id = response.get("id") if isinstance(response, dict) else None
        if not api_id:
            fail(f"API connection create response did not contain id: {response!r}")
        self.created_api_connection_ids.append(str(api_id))
        return str(api_id)

    def update_api_connection(self, api_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = _json_request(
            "PUT",
            f"/api-connections/{api_id}",
            token=self.token,
            payload=payload,
        )
        if not isinstance(response, dict):
            fail(f"API connection update response is not an object: {response!r}")
        return response

    def delete_api_connection(self, api_id: str) -> None:
        _json_request(
            "DELETE",
            f"/api-connections/{api_id}",
            token=self.token,
            expected=(204,),
        )

    def create_connection(self, payload: dict[str, Any]) -> str:
        response = _json_request(
            "POST",
            "/connections",
            token=self.token,
            payload=payload,
            expected=(201,),
        )
        connection_id = response.get("id") if isinstance(response, dict) else None
        if not connection_id:
            fail(f"Connection create response did not contain id: {response!r}")
        self.created_connection_ids.append(str(connection_id))
        return str(connection_id)

    def delete_connection(self, connection_id: str) -> None:
        _json_request(
            "DELETE",
            f"/connections/{connection_id}",
            token=self.token,
            expected=(204,),
        )

    def create_datasource(self, payload: dict[str, Any]) -> str:
        response = _json_request(
            "POST",
            "/datasources",
            token=self.token,
            payload=payload,
            expected=(201,),
        )
        datasource_id = response.get("id") if isinstance(response, dict) else None
        if not datasource_id:
            fail(f"Datasource create response did not contain id: {response!r}")
        self.created_datasource_ids.append(str(datasource_id))
        return str(datasource_id)

    def delete_datasource(self, datasource_id: str) -> None:
        _json_request(
            "DELETE",
            f"/datasources/{datasource_id}",
            token=self.token,
            expected=(204,),
        )

    def test_connection(self, connection_id: str) -> dict[str, Any]:
        return _json_request(
            "POST",
            f"/connections/{connection_id}/test",
            token=self.token,
        )

    def preview_project(self, project_id: str, *, limit: int = 100) -> dict[str, Any]:
        task = _json_request(
            "GET",
            f"/projects/{project_id}/preview?limit={limit}",
            token=self.token,
        )
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not task_id:
            fail(f"Preview response did not contain task_id: {task!r}")
        status = self.wait_task(str(task_id))
        result = status.get("result")
        if not isinstance(result, dict):
            fail(f"Preview task did not contain result: {status!r}")
        if result.get("success") is False:
            fail(f"Preview failed: {result!r}")
        return result

    def execute_project(self, project_id: str, *, force_recalculate: bool = True) -> dict[str, Any]:
        # Gate canvas-validation attivo (Epic Canvas Validation §12): un progetto non
        # validato non parte. Valida-prima come il pulsante "Esegui" del FE
        # (salva→valida→esegui); il canvas e' gia' salvato via update_canvas, quindi
        # qui basta validare. I test che verificano il BLOCCO usano /execute raw.
        report = self.validate_project(project_id)
        if report.get("has_errors"):
            fail(f"Validation failed before execute: {report.get('issues')!r}")
        task = _json_request(
            "POST",
            f"/projects/{project_id}/execute",
            token=self.token,
            payload={"force_recalculate": force_recalculate},
        )
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not task_id:
            fail(f"Execute response did not contain task_id: {task!r}")
        status = self.wait_task(str(task_id))
        result = status.get("result")
        if not isinstance(result, dict):
            fail(f"Execute task did not contain result: {status!r}")
        if result.get("success") is False:
            fail(f"Execute failed: {result!r}")
        return result

    def wait_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + TASK_TIMEOUT_SECONDS
        last_status: dict[str, Any] | None = None
        while time.time() < deadline:
            status = _json_request(
                "GET",
                f"/tasks/{task_id}",
                token=self.token,
                expected=(200, 404),
            )
            if not isinstance(status, dict):
                fail(f"Task status response is not an object: {status!r}")
            if status.get("error") == "task_not_found" or (
                isinstance(status.get("detail"), dict)
                and status["detail"].get("error") == "task_not_found"
            ):
                last_status = status
                time.sleep(0.5)
                continue
            last_status = status
            state = str(status.get("status") or "").lower()
            if state in {"success", "failure", "failed", "revoked"}:
                if state != "success":
                    fail(f"Task {task_id} ended with status {state}: {status!r}")
                return status
            time.sleep(0.5)
        fail(f"Task {task_id} timed out after {TASK_TIMEOUT_SECONDS}s: {last_status!r}")

    def download_task_csv(self, task_id: str) -> list[dict[str, str]]:
        raw = _raw_request("GET", f"/tasks/{task_id}/download", token=self.token)
        text = raw.decode("utf-8-sig")
        return list(DictReader(StringIO(text)))

    def api_json(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return _json_request(
            method,
            path,
            token=self.token,
            payload=payload,
            expected=expected,
        )

    def api_raw(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
    ) -> bytes:
        return _raw_request(method, path, token=self.token, expected=expected)

    def cleanup_on_success(self) -> None:
        if self.keep_on_success:
            print("PLENORA_E2E_KEEP_ON_SUCCESS is set; leaving projects:")
            for project_id in self.created_project_ids:
                print(f"  {project_id}")
            for api_id in self.created_api_connection_ids:
                print(f"  api_connection:{api_id}")
            for connection_id in self.created_connection_ids:
                print(f"  connection:{connection_id}")
            return
        for project_id in reversed(self.created_project_ids):
            try:
                self.delete_project(project_id)
            except Exception as exc:
                print(f"WARNING: cleanup failed for {project_id}: {exc}", file=sys.stderr)
        for api_id in reversed(self.created_api_connection_ids):
            try:
                self.delete_api_connection(api_id)
            except Exception as exc:
                print(f"WARNING: cleanup failed for API connection {api_id}: {exc}", file=sys.stderr)
        for connection_id in reversed(self.created_connection_ids):
            try:
                self.delete_connection(connection_id)
            except Exception as exc:
                print(f"WARNING: cleanup failed for connection {connection_id}: {exc}", file=sys.stderr)
        for datasource_id in reversed(self.created_datasource_ids):
            try:
                self.delete_datasource(datasource_id)
            except Exception as exc:
                print(f"WARNING: cleanup failed for datasource {datasource_id}: {exc}", file=sys.stderr)


def new_context() -> E2EContext:
    return E2EContext(token=auth_token())


def run_sql(sql: str) -> str:
    command = [
        "docker",
        "exec",
        "-i",
        DB_CONTAINER,
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-v",
        "ON_ERROR_STOP=1",
        "-P",
        "pager=off",
    ]
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail(
            "psql fixture command failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


def ensure_plenora_test_network_access() -> None:
    """Attach running Plenora backend/worker containers to the E2E fixture network."""

    network = os.environ.get("PLENORA_TEST_NETWORK", "plenora-test")
    containers: list[str] = []
    for service in ("backend", "worker"):
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=plenora",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.Names}}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            fail(f"docker ps failed while discovering {service} containers: {result.stderr}")
        containers.extend(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )

    if not containers:
        fail("No running Plenora backend/worker containers found.")

    for container in containers:
        result = subprocess.run(
            ["docker", "network", "connect", network, container],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            continue
        stderr = result.stderr.lower()
        if "already exists" in stderr or "is already attached" in stderr:
            continue
        fail(
            f"docker network connect {network} {container} failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def set_output_contract(project_id: str, contract: dict[str, Any]) -> None:
    raw = json.dumps(contract, separators=(",", ":"), ensure_ascii=True)
    run_sql(
        "UPDATE plenora.projects "
        f"SET output_contract = $contract${raw}$contract$::jsonb "
        f"WHERE id = {sql_literal(project_id)};\n"
    )


def output_contract(columns: list[dict[str, Any]], *, producer_project_id: str) -> dict[str, Any]:
    return {
        "version": "1.0",
        "producer_project_id": producer_project_id,
        "revision": int(time.time()),
        "columns": columns,
    }


def contract_column(
    *,
    column_id: str,
    source_name: str,
    physical_name: str,
    logical_type: str = "integer",
    ordinal: int = 0,
) -> dict[str, Any]:
    return {
        "id": column_id,
        "source_name": source_name,
        "physical_name": physical_name,
        "display_name": physical_name,
        "type": {"logical": logical_type, "nullable": True},
        "ordinal": ordinal,
        "status": "active",
    }


def postgres_fixture_connection_payload(name: str) -> dict[str, Any]:
    password = os.environ.get("POSTGRES_PASSWORD", "plenora_test_pwd")
    return {
        "name": name,
        "description": "E2E v2 PostgreSQL fixture",
        "type": "postgres",
        "host": os.environ.get("PLENORA_E2E_SOURCE_DB_HOST", "plenora-test-postgres"),
        "port": int(os.environ.get("PLENORA_E2E_SOURCE_DB_PORT", "5432")),
        "database": os.environ.get("POSTGRES_DB", "plenora_test"),
        "db_schema": os.environ.get("PLENORA_E2E_SOURCE_DB_SCHEMA", "public"),
        "username": os.environ.get("POSTGRES_USER", "plenora"),
        "password": password,
    }


def mysql_fixture_connection_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "E2E v2 MySQL fixture",
        "type": "mysql",
        "host": os.environ.get("PLENORA_E2E_MYSQL_HOST", "plenora-test-mysql"),
        "port": int(os.environ.get("PLENORA_E2E_MYSQL_PORT", "3306")),
        "database": os.environ.get("MYSQL_DATABASE", "plenora_test"),
        "db_schema": os.environ.get("PLENORA_E2E_MYSQL_SCHEMA", ""),
        "username": os.environ.get("MYSQL_USER", "plenora"),
        "password": os.environ.get("MYSQL_PASSWORD", "plenora_test_pwd"),
    }


def mssql_fixture_connection_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "E2E v2 SQL Server fixture",
        "type": "mssql",
        "host": os.environ.get("PLENORA_E2E_MSSQL_HOST", "plenora-test-sqlserver"),
        "port": int(os.environ.get("PLENORA_E2E_MSSQL_PORT", "1433")),
        "database": os.environ.get("MSSQL_DATABASE", "plenora_test"),
        "db_schema": os.environ.get("PLENORA_E2E_MSSQL_SCHEMA", "dbo"),
        "username": os.environ.get("MSSQL_USER", "sa"),
        "password": os.environ.get("MSSQL_SA_PASSWORD", "PlenoraTest!2026"),
    }


def oracle_fixture_connection_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "E2E v2 Oracle fixture",
        "type": "oracle",
        "host": os.environ.get("PLENORA_E2E_ORACLE_HOST", "plenora-test-oracle"),
        "port": int(os.environ.get("PLENORA_E2E_ORACLE_PORT", "1521")),
        "database": os.environ.get("ORACLE_DATABASE", "plenora_test"),
        "db_schema": os.environ.get("PLENORA_E2E_ORACLE_SCHEMA", os.environ.get("ORACLE_APP_USER", "plenora")),
        "username": os.environ.get("ORACLE_APP_USER", "plenora"),
        "password": os.environ.get("ORACLE_APP_USER_PASSWORD", "plenora_test_pwd"),
    }


def unique_name(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def source_column(
    name: str,
    *,
    logical_type: str = "str",
    alias: str | None = None,
    visible: bool = True,
    column_id: str | None = None,
) -> dict[str, Any]:
    column: dict[str, Any] = {
        "name": name,
        "type": logical_type,
        "visible": visible,
    }
    if alias:
        column["alias"] = alias
    if column_id:
        column["id"] = column_id
    return column


def source_node(
    *,
    node_id: str,
    table_name: str,
    alias: str,
    columns: list[dict[str, Any]],
    filters: list[dict[str, Any]] | None = None,
    connection_id: str | None = None,
    position: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "source",
        "position": position or {"x": 0, "y": 0},
        "table_name": table_name,
        "alias": alias,
        "columns": columns,
        "filters": filters or [],
        "connection_id": connection_id,
        "data": {
            "table_name": table_name,
            "alias": alias,
            "columns": columns,
            "filters": filters or [],
            "connection_id": connection_id,
        },
    }


def project_source_node(
    *,
    node_id: str,
    source_project_id: str,
    column_id: str,
    name: str,
    source_name: str,
) -> dict[str, Any]:
    column = {
        "id": column_id,
        "name": name,
        "source_name": source_name,
        "display_name": name,
        "type": "integer",
        "visible": True,
    }
    return {
        "id": node_id,
        "type": "project_source",
        "position": {"x": 0, "y": 0},
        "source_project_id": source_project_id,
        "columns": [column],
        "data": {"columns": [column], "source_project_id": source_project_id},
    }


def project_source_node_from_contract(
    *,
    node_id: str,
    source_project_id: str,
    contract: dict[str, Any],
    position: dict[str, int] | None = None,
) -> dict[str, Any]:
    columns = []
    for column in contract.get("columns") or []:
        if not isinstance(column, dict) or column.get("status", "active") != "active":
            continue
        name = column.get("physical_name") or column.get("display_name")
        if not name:
            continue
        columns.append({
            "id": column.get("id"),
            "name": name,
            "source_name": column.get("source_name") or name,
            "display_name": column.get("display_name") or name,
            "type": (column.get("type") or {}).get("logical") or "str",
            "visible": True,
        })
    return {
        "id": node_id,
        "type": "project_source",
        "position": position or {"x": 0, "y": 0},
        "source_project_id": source_project_id,
        "columns": columns,
        "data": {"columns": columns, "source_project_id": source_project_id},
    }


def transform_node(
    *,
    node_id: str,
    transform_op: str,
    config: dict[str, Any],
    position: dict[str, int] | None = None,
    input_columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"config": config}
    if input_columns is not None:
        data["inputColumns"] = input_columns
    return {
        "id": node_id,
        "type": "transform",
        "position": position or {"x": 240, "y": 0},
        "transform_op": transform_op,
        "config": config,
        "data": data,
    }


def result_node(node_id: str = "result-1", **overrides: Any) -> dict[str, Any]:
    node = {
        "id": node_id,
        "type": "result",
        "position": {"x": 700, "y": 0},
        "columns": [],
        "data": {},
    }
    node.update(overrides)
    return node


def edge(edge_id: str, source: str, target: str) -> dict[str, Any]:
    return {"id": edge_id, "source": source, "target": target}


def run_test(fn) -> None:
    ctx = new_context()
    passed = False
    try:
        fn(ctx)
        passed = True
    finally:
        if passed:
            ctx.cleanup_on_success()
        else:
            print("Test failed; leaving generated projects for inspection:", file=sys.stderr)
            for project_id in ctx.created_project_ids:
                print(f"  {project_id}", file=sys.stderr)
            for api_id in ctx.created_api_connection_ids:
                print(f"  api_connection:{api_id}", file=sys.stderr)


__all__ = [
    "E2EContext",
    "assert_equal",
    "assert_true",
    "contract_column",
    "edge",
    "ensure_plenora_test_network_access",
    "fail",
    "mssql_fixture_connection_payload",
    "mysql_fixture_connection_payload",
    "new_context",
    "oracle_fixture_connection_payload",
    "output_contract",
    "postgres_fixture_connection_payload",
    "project_source_node",
    "project_source_node_from_contract",
    "result_node",
    "run_test",
    "set_output_contract",
    "source_column",
    "source_node",
    "transform_node",
    "unique_name",
]
