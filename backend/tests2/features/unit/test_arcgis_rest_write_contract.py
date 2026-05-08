"""Contract tests for feature-owned ArcGIS REST write boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.core.features.arcgis_rest_write import (
    ArcGISAddToDefinitionRequest,
    ArcGISApplyEditsRequest,
    ArcGISCreateServiceRequest,
    ArcGISDeleteFeaturesRequest,
    ArcGISFeatureBatchWriteRequest,
    ArcGISFeatureEditOperation,
    ArcGISUpdateDefinitionRequest,
)


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    text = ""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def conn() -> SimpleNamespace:
    return SimpleNamespace(server_url="https://portal.example.test/portal")


@pytest.fixture
def client() -> ArcGISClient:
    client = ArcGISClient.__new__(ArcGISClient)
    client.DEFAULT_TIMEOUT = 30
    client.WRITE_BATCH_SIZE = 1000
    client._request_with_token = MagicMock(return_value=_Response({}))  # type: ignore[attr-defined]
    return client


def test_create_service_request_posts_to_user_or_folder_create_service() -> None:
    request = ArcGISCreateServiceRequest(
        base_url="https://portal.example.test/sharing/rest",
        username="publisher",
        folder_id="folder-1",
        create_parameters={"name": "Sites", "capabilities": "Create,Update"},
    )

    assert request.endpoint() == (
        "https://portal.example.test/sharing/rest/content/users/"
        "publisher/folder-1/createService"
    )
    body = request.body()
    assert body["outputType"] == "featureService"
    assert body["f"] == "json"
    assert json.loads(body["createParameters"]) == {
        "name": "Sites",
        "capabilities": "Create,Update",
    }


def test_definition_requests_keep_add_and_update_payloads_explicit() -> None:
    definition = {"layers": [{"id": 0, "name": "Sites"}]}
    add_request = ArcGISAddToDefinitionRequest(
        admin_url="https://services.example.test/arcgis/rest/admin/services/Sites/FeatureServer/",
        definition=definition,
    )
    update_request = ArcGISUpdateDefinitionRequest(
        admin_url="https://services.example.test/arcgis/rest/admin/services/Sites/FeatureServer",
        definition=definition,
    )

    assert add_request.endpoint().endswith("/FeatureServer/addToDefinition")
    assert update_request.endpoint().endswith("/FeatureServer/updateDefinition")
    assert json.loads(add_request.body()["addToDefinition"]) == definition
    assert json.loads(update_request.body()["updateDefinition"]) == definition


def test_add_features_posts_layer_endpoint_and_serialized_features(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {"addResults": [{"objectId": 10, "success": True}]}
    )
    feature = {"attributes": {"SITE_ID": "A"}}

    result = client.add_features(
        conn,
        "https://services.example.test/FeatureServer/0/",
        [feature],
        batch_size=10,
    )

    assert result == {"success": True, "added_count": 1, "errors": []}
    call = client._request_with_token.call_args  # type: ignore[attr-defined]
    assert call.args[:3] == (
        "POST",
        "https://services.example.test/FeatureServer/0/addFeatures",
        conn,
    )
    assert json.loads(call.kwargs["data"]["features"]) == [feature]
    assert call.kwargs["data"]["f"] == "json"
    assert "token" not in call.kwargs["data"]
    assert call.kwargs["extra_headers"] == {"Referer": conn.server_url}


def test_update_features_uses_update_results_and_reports_item_errors(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {
            "updateResults": [
                {"objectId": 1, "success": True},
                {"objectId": 2, "success": False, "error": {"message": "locked"}},
            ]
        }
    )

    result = client.update_features(
        conn,
        "https://services.example.test/FeatureServer/0",
        [
            {"attributes": {"OBJECTID": 1, "NAME": "A"}},
            {"attributes": {"OBJECTID": 2, "NAME": "B"}},
        ],
    )

    assert result["success"] is False
    assert result["updated_count"] == 1
    assert result["errors"] == [
        {"objectId": 2, "success": False, "error": {"message": "locked"}}
    ]
    assert client._request_with_token.call_args.args[1].endswith("/updateFeatures")  # type: ignore[attr-defined]


def test_add_features_arcgis_error_returns_compat_error(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {"error": {"code": 400, "message": "invalid geometry"}}
    )

    result = client.add_features(
        conn,
        "https://services.example.test/FeatureServer/0",
        [{"attributes": {"SITE_ID": "A"}}],
    )

    assert result == {
        "success": False,
        "added_count": 0,
        "errors": [{"code": 400, "message": "invalid geometry"}],
    }


def test_delete_features_posts_object_ids_and_arcgis_error(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {"error": {"code": 400, "message": "delete denied"}}
    )

    result = client.delete_features(
        conn,
        "https://services.example.test/FeatureServer/0",
        object_ids=[7, 8],
    )

    assert result == {
        "success": False,
        "deleted_count": 0,
        "errors": [{"code": 400, "message": "delete denied"}],
    }
    call = client._request_with_token.call_args  # type: ignore[attr-defined]
    assert call.args[1] == "https://services.example.test/FeatureServer/0/deleteFeatures"
    assert call.kwargs["data"] == {"f": "json", "objectIds": "7,8"}


def test_delete_features_request_can_target_where_clause() -> None:
    request = ArcGISDeleteFeaturesRequest(
        layer_url="https://services.example.test/FeatureServer/0",
        where="SITE_ID = 'A'",
    )

    assert request.body() == {"f": "json", "where": "SITE_ID = 'A'"}


def test_apply_edits_posts_mixed_edits_and_counts_results(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {
            "addResults": [{"objectId": 11, "success": True}],
            "updateResults": [{"objectId": 12, "success": True}],
            "deleteResults": [{"objectId": 13, "success": True}],
        }
    )
    add = {"attributes": {"SITE_ID": "A"}}
    update = {"attributes": {"OBJECTID": 12, "SITE_ID": "B"}}

    result = client.apply_edits(
        conn,
        "https://services.example.test/FeatureServer/0",
        adds=[add],
        updates=[update],
        deletes=[13],
        rollback_on_failure=False,
        use_global_ids=True,
    )

    assert result == {
        "success": True,
        "added_count": 1,
        "updated_count": 1,
        "deleted_count": 1,
        "errors": [],
    }
    call = client._request_with_token.call_args  # type: ignore[attr-defined]
    assert call.args[1] == "https://services.example.test/FeatureServer/0/applyEdits"
    body = call.kwargs["data"]
    assert json.loads(body["adds"]) == [add]
    assert json.loads(body["updates"]) == [update]
    assert body["deletes"] == "13"
    assert body["rollbackOnFailure"] == "false"
    assert body["useGlobalIds"] == "true"


def test_apply_edits_arcgis_error_returns_typed_compat_error(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._request_with_token.return_value = _Response(  # type: ignore[attr-defined]
        {"error": {"code": 499, "message": "token required"}}
    )

    result = client.apply_edits(
        conn,
        "https://services.example.test/FeatureServer/0",
        adds=[{"attributes": {"SITE_ID": "A"}}],
    )

    assert result == {
        "success": False,
        "added_count": 0,
        "updated_count": 0,
        "deleted_count": 0,
        "errors": [{"code": 499, "message": "token required"}],
    }


def test_request_contracts_are_feature_owned_not_imdf_wrappers() -> None:
    request = ArcGISFeatureBatchWriteRequest(
        layer_url="https://services.example.test/FeatureServer/0",
        operation=ArcGISFeatureEditOperation.ADD,
        features=[{"attributes": {"A": 1}}],
    )
    apply_request = ArcGISApplyEditsRequest(
        layer_url="https://services.example.test/FeatureServer/0",
        adds=[{"attributes": {"A": 1}}],
    )

    assert request.__class__.__module__ == "app.core.features.arcgis_rest_write"
    assert apply_request.__class__.__module__ == "app.core.features.arcgis_rest_write"
