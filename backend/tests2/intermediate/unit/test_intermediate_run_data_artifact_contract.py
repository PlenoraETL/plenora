"""
Contract test Batch API preview da artifact (2026-04-28).

Verifica che `GET /api/runs/{run_id}/nodes/{node_id}/data` supporti i
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tests2.intermediate.unit.conftest import build_client


pytestmark = [
    pytest.mark.unit, pytest.mark.intermediate,
    pytest.mark.contract, pytest.mark.regression,
]


# Helpers


def _node_meta(
    *,
    persistence_mode=None,
    artifact_uri=None,
    artifact_status=None,
    artifact_format=None,
    artifact_error=None,
    row_count=None,
    persisted_rows_count=None,
    preview_truncated=None,
    col_schema=None,
):
    """Mock di `idb.get_node_meta(...)` con i campi rilevanti."""
    return {
        "row_count": row_count,
        "col_schema": col_schema or {},
        "persistence_mode": persistence_mode,
        "artifact_uri": artifact_uri,
        "artifact_status": artifact_status,
        "artifact_format": artifact_format,
        "artifact_error": artifact_error,
        "persisted_rows_count": persisted_rows_count,
        "preview_truncated": preview_truncated,
    }


def _stream_from_bytes(payload: bytes):
    """Mock di `storage.get_object_stream(key)` -> dict con
    `body` iterable di chunks."""
    return {"body": iter([payload]), "size": len(payload), "content_type": "application/octet-stream"}


def _make_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", compression="snappy", index=False)
    return buf.getvalue()


# 1. NULL / full_pg / preview_only -> node_rows path


def test_null_full_pg_node_rows_path_unchanged(superadmin_user):
    """Persistence_mode NULL o full_pg: legge da
    node_rows come prima del batch artifact. Regression."""
    client = build_client(superadmin_user)

    full_pg_rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode=None,  # NOTE: NULL -> trattato come full_pg
            row_count=2,
            col_schema={"id": "int", "v": "str"},
        ),
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=full_pg_rows,
    ) as mock_rows:
        response = client.get("/api/runs/r1/nodes/n1/data")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == full_pg_rows
    assert body["row_count"] == 2
    # Persistence mode resa esplicita ai client (NULL -> "full_pg")
    assert body["persistence_mode"] == "full_pg"
    mock_rows.assert_called_once()


def test_preview_only_node_rows_path(superadmin_user):
    client = build_client(superadmin_user)
    preview_rows = [{"_row_hash": "h1", "x": 10}, {"_row_hash": "h2", "x": 20}]

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="preview_only",
            row_count=10000,
            persisted_rows_count=500,
            preview_truncated=True,
            col_schema={"x": "int"},
        ),
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=preview_rows,
    ):
        response = client.get("/api/runs/r1/nodes/n1/data?limit=500")

    assert response.status_code == 200
    body = response.json()
    assert body["persistence_mode"] == "preview_only"
    assert body["persisted_rows_count"] == 500
    assert body["preview_truncated"] is True
    assert body["rows"] == preview_rows
    assert body["row_count"] == 10000
    assert body["truncated"] is True  # row_count > limit


# 2. metadata_only -> rows=[] + message


def test_metadata_only_returns_empty_rows_and_message(superadmin_user):
    """Default produzione: nessun dato persistito. 200 + message."""
    client = build_client(superadmin_user)

    fake_get_storage = MagicMock(side_effect=AssertionError(
        "metadata_only NON deve toccare object storage"
    ))

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="metadata_only",
            row_count=2370000,
            artifact_status="none",
            col_schema={"a": "int"},
        ),
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        fake_get_storage,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        side_effect=AssertionError("node_rows NON deve essere letto"),
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["row_count"] == 2370000
    assert body["persistence_mode"] == "metadata_only"
    assert "metadata_only" in body.get("message", "")
    fake_get_storage.assert_not_called()


# 3. full_artifact + complete -> legge parquet


def test_full_artifact_complete_returns_parquet_preview(superadmin_user):
    client = build_client(superadmin_user)

    df = pd.DataFrame({
        "id": list(range(20)),
        "name": [f"row{i}" for i in range(20)],
        "score": [i / 10.0 for i in range(20)],
    })
    parquet_bytes = _make_parquet_bytes(df)

    fake_storage = MagicMock()
    fake_storage.get_object_stream.return_value = _stream_from_bytes(parquet_bytes)

    artifact_uri = (
        "intermediate/group=g1/project=p1/run=r1/module=manipola/"
        "node=n1/kind=final/data.parquet"
    )

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri=artifact_uri,
            artifact_status="complete",
            artifact_format="parquet",
            row_count=20,
            col_schema={"id": "int64", "name": "object", "score": "float64"},
        ),
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        response = client.get("/api/runs/r1/nodes/n1/data?limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["persistence_mode"] == "full_artifact"
    assert body["artifact_status"] == "complete"
    assert body["artifact_uri"] == artifact_uri
    assert body["artifact_format"] == "parquet"
    # Limit rispettato
    assert len(body["rows"]) == 5
    # Prime 5 righe del DataFrame originale
    assert body["rows"][0]["id"] == 0
    assert body["rows"][4]["name"] == "row4"
    fake_storage.get_object_stream.assert_called_once_with(artifact_uri)


def test_full_artifact_complete_sanitizes_nan_and_inf(superadmin_user):
    """NaN / Inf nel Parquet devono essere sanitizzati a null come per
    il path node_rows."""
    client = build_client(superadmin_user)

    df = pd.DataFrame({
        "id": [1, 2, 3],
        "value": [float("nan"), float("inf"), 3.14],
    })
    parquet_bytes = _make_parquet_bytes(df)

    fake_storage = MagicMock()
    fake_storage.get_object_stream.return_value = _stream_from_bytes(parquet_bytes)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri="some/key/data.parquet",
            artifact_status="complete",
            artifact_format="parquet",
            row_count=3,
        ),
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 3
    assert body["rows"][0]["value"] is None
    assert body["rows"][1]["value"] is None
    assert body["rows"][2]["value"] == 3.14
    # Strict json roundtrip OK.
    parsed = json.loads(response.content)
    assert all(
        r["value"] is None or isinstance(r["value"], float)
        for r in parsed["rows"]
    )


# 4. full_artifact failed/pending/missing -> 200 operativo


def test_full_artifact_failed_returns_200_with_message(superadmin_user):
    client = build_client(superadmin_user)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri=None,
            artifact_status="failed",
            artifact_format="parquet",
            artifact_error="upload data.parquet fallito: rete down",
            row_count=100,
        ),
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"] == []
    assert body["artifact_status"] == "failed"
    assert "failed" in body.get("message", "")
    assert "rete down" in body.get("message", "")


def test_full_artifact_pending_returns_200_with_message(superadmin_user):
    client = build_client(superadmin_user)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_status="pending",
            artifact_format="parquet",
            row_count=100,
        ),
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["artifact_status"] == "pending"
    assert "pending" in body.get("message", "")


def test_full_artifact_with_no_uri_returns_200_operational_message(superadmin_user):
    """Stato `complete` ma `artifact_uri` mancante (corruzione meta):
    200 operativo con messaggio."""
    client = build_client(superadmin_user)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri=None,
            artifact_status="complete",
            artifact_format="parquet",
            row_count=100,
        ),
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    # complete senza uri non deve crashare. Cade nel ramo "non uri" -> 200 message.
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []


# 5. Object storage disabled + full_artifact -> message chiaro


def test_full_artifact_with_storage_disabled_returns_operational_message(
    superadmin_user,
):
    from app.infrastructure.storage.cloud_backends import ObjectStorageDisabledError

    client = build_client(superadmin_user)

    fake_get = MagicMock(side_effect=ObjectStorageDisabledError("disabled"))

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri="key/data.parquet",
            artifact_status="complete",
            artifact_format="parquet",
            row_count=100,
        ),
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage", fake_get,
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    # 200, niente 500 fuorviante
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert "non disponibile" in body.get("message", "")


# 6. Limit rispettato sul Parquet (default 100)


def test_full_artifact_default_limit_is_100(superadmin_user):
    client = build_client(superadmin_user)

    df = pd.DataFrame({"id": list(range(500))})
    parquet_bytes = _make_parquet_bytes(df)

    fake_storage = MagicMock()
    fake_storage.get_object_stream.return_value = _stream_from_bytes(parquet_bytes)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(
            persistence_mode="full_artifact",
            artifact_uri="k/data.parquet",
            artifact_status="complete",
            artifact_format="parquet",
            row_count=500,
        ),
    ), patch(
        "app.core.io.object_storage.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")  # default limit=100

    body = response.json()
    assert len(body["rows"]) == 100
    assert body["row_count"] == 500
    assert body["truncated"] is True


# 7. Campi additivi sempre presenti


def test_response_contains_additive_fields_for_null_mode_path(superadmin_user):
    """I campi nuovi (persistence_mode, artifact_uri, ...) devono
    essere presenti anche sui record con persistence_mode NULL."""
    client = build_client(superadmin_user)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r1", "group_id": "g1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value=_node_meta(row_count=2),
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=[{"id": 1}, {"id": 2}],
    ):
        response = client.get("/api/runs/r1/nodes/n1/data")

    body = response.json()
    # Campi additivi sempre presenti
    assert "persistence_mode" in body
    assert "persisted_rows_count" in body
    assert "preview_truncated" in body
    assert "artifact_uri" in body
    assert "artifact_status" in body
    assert "artifact_format" in body
