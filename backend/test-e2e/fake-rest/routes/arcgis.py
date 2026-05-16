"""ArcGIS-style endpoints.

Implements the minimal slice of ArcGIS Online / FeatureServer that plenora
uses: portal lookup, item / folder listing, ``createService``,
``createFolder``, service-name availability, OAuth2 ``token`` exchange,
catalog root, layer query and ``applyEdits``. Following ArcGIS conventions,
missing or wrong tokens return HTTP 200 with an error body (not 4xx).

State for ``applyEdits`` is kept in process memory and can be cleared with
the ``X-Test-Reset: 1`` header on any call.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Optional

from fastapi import APIRouter, Form, Header, Query, Request
from fastapi.responses import JSONResponse

from . import (
    ARCGIS_OAUTH_CLIENT_ID,
    ARCGIS_OAUTH_CLIENT_SECRET,
    ARCGIS_OAUTH_TOKEN,
    ARCGIS_TOKEN,
    ARCGIS_USER,
    load_fixture,
)

router = APIRouter()

_state_lock = threading.Lock()
# Per-service in-memory feature store. Pre-seeded with 5 cities so the
# initial /query responds with deterministic data.
_initial_features: list[dict[str, Any]] = [
    {
        "attributes": {
            "OBJECTID": idx + 1,
            "id": city["id"],
            "region_id": city["region_id"],
            "name": city["name"],
            "population": city["population"],
        },
        "geometry": {
            "x": city["longitude"],
            "y": city["latitude"],
            "spatialReference": {"wkid": 4326},
        },
    }
    for idx, city in enumerate(load_fixture("cities.json")[:5])
]
_features_state: dict[str, list[dict[str, Any]]] = {}


def _features_for(service: str) -> list[dict[str, Any]]:
    with _state_lock:
        if service not in _features_state:
            _features_state[service] = copy.deepcopy(_initial_features)
        return _features_state[service]


def _maybe_reset(x_test_reset: Optional[str]) -> None:
    if x_test_reset == "1":
        with _state_lock:
            _features_state.clear()


def _token_ok(token_q: Optional[str], authorization: Optional[str]) -> bool:
    if token_q in (ARCGIS_TOKEN, ARCGIS_OAUTH_TOKEN):
        return True
    if authorization in (f"Bearer {ARCGIS_TOKEN}", f"Bearer {ARCGIS_OAUTH_TOKEN}"):
        return True
    return False


def _arcgis_invalid_token() -> JSONResponse:
    # ArcGIS pattern: HTTP 200 + body error.
    return JSONResponse(
        content={"error": {"code": 498, "message": "Invalid token"}}
    )


# ---------------------------------------------------------------------------
# Sharing / Portal endpoints
# ---------------------------------------------------------------------------


@router.get("/sharing/rest/portals/self")
def portal_self(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    return JSONResponse(
        content={
            "id": "fake_portal",
            "name": "Plenora Fake Portal",
            "user": {"username": ARCGIS_USER, "fullName": "Tester"},
        }
    )


@router.get("/sharing/rest/content/users/tester")
def user_content(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    return JSONResponse(
        content={
            "username": ARCGIS_USER,
            "items": [],
            "folders": [{"id": "f1", "title": "test_folder"}],
            "total": 0,
        }
    )


@router.get("/sharing/rest/content/users/tester/items/{item_id}")
def item_metadata(
    item_id: str,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    return JSONResponse(
        content={
            "id": item_id,
            "owner": ARCGIS_USER,
            "title": f"Fake item {item_id}",
            "type": "Feature Service",
            "url": f"http://fake/services/Service_{item_id}/FeatureServer",
            "tags": ["plenora", "fake"],
            "created": 1700000000000,
            "modified": 1700000000000,
        }
    )


@router.post("/sharing/rest/content/users/tester/createService")
async def create_service(
    request: Request,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()

    # ArcGIS clients usually post `createParameters` as a JSON-string field
    # inside form data; accept also raw JSON bodies for convenience.
    name = "Test_FS"
    try:
        form = await request.form()
        raw = form.get("createParameters")
        if raw:
            parsed = json.loads(str(raw))
            name = parsed.get("name") or name
    except Exception:
        pass
    if name == "Test_FS":
        try:
            body = await request.json()
            params = body.get("createParameters") or body
            if isinstance(params, dict):
                name = params.get("name") or name
        except Exception:
            pass

    return JSONResponse(
        content={
            "success": True,
            "serviceurl": f"http://fake/services/{name}/FeatureServer",
            "itemId": "abc123",
            "name": name,
        }
    )


# ---------------------------------------------------------------------------
# FeatureServer endpoints
# ---------------------------------------------------------------------------


@router.get("/services/{service_name}/FeatureServer")
def feature_server(
    service_name: str,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    return JSONResponse(
        content={
            "serviceDescription": f"Plenora fake FS {service_name}",
            "layers": [{"id": 0, "name": "layer0"}],
            "tables": [],
        }
    )


@router.get("/services/{service_name}/FeatureServer/0")
def layer_descriptor(
    service_name: str,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    descriptor = copy.deepcopy(load_fixture("arcgis_layer.json"))
    descriptor["serviceName"] = service_name
    return JSONResponse(content=descriptor)


@router.get("/services/{service_name}/FeatureServer/0/query")
def layer_query(
    service_name: str,
    where: str = Query(default="1=1"),
    f: str = Query(default="json"),
    returnCountOnly: bool = Query(default=False),
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()

    features = _features_for(service_name)
    if returnCountOnly:
        return JSONResponse(content={"count": len(features)})

    return JSONResponse(
        content={
            "objectIdFieldName": "OBJECTID",
            "globalIdFieldName": "",
            "geometryType": "esriGeometryPoint",
            "spatialReference": {"wkid": 4326},
            "fields": load_fixture("arcgis_layer.json")["fields"],
            "features": features,
            "exceededTransferLimit": False,
        }
    )


def _next_oid(features: list[dict[str, Any]]) -> int:
    if not features:
        return 1
    return max(int(f["attributes"].get("OBJECTID", 0)) for f in features) + 1


@router.post("/services/{service_name}/FeatureServer/0/applyEdits")
async def apply_edits(
    service_name: str,
    request: Request,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
    adds: Optional[str] = Form(default=None),
    updates: Optional[str] = Form(default=None),
    deletes: Optional[str] = Form(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    _maybe_reset(x_test_reset)

    # ArcGIS posts `adds`/`updates`/`deletes` as JSON-string form fields.
    def _parse_list(raw: Optional[str]) -> list[Any]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    adds_list = _parse_list(adds)
    updates_list = _parse_list(updates)
    deletes_list = _parse_list(deletes)

    features = _features_for(service_name)
    add_results: list[dict[str, Any]] = []
    update_results: list[dict[str, Any]] = []
    delete_results: list[dict[str, Any]] = []

    with _state_lock:
        for feat in adds_list:
            oid = _next_oid(features)
            attrs = dict(feat.get("attributes") or {})
            attrs["OBJECTID"] = oid
            features.append({"attributes": attrs, "geometry": feat.get("geometry")})
            add_results.append({"objectId": oid, "success": True})

        for feat in updates_list:
            attrs = feat.get("attributes") or {}
            oid = attrs.get("OBJECTID")
            found = False
            for existing in features:
                if existing["attributes"].get("OBJECTID") == oid:
                    existing["attributes"].update(attrs)
                    if "geometry" in feat:
                        existing["geometry"] = feat["geometry"]
                    found = True
                    break
            update_results.append({"objectId": oid, "success": found})

        for oid in deletes_list:
            try:
                oid_int = int(oid)
            except (TypeError, ValueError):
                delete_results.append({"objectId": oid, "success": False})
                continue
            before = len(features)
            features[:] = [f for f in features if f["attributes"].get("OBJECTID") != oid_int]
            delete_results.append({"objectId": oid_int, "success": len(features) < before})

    return JSONResponse(
        content={
            "addResults": add_results,
            "updateResults": update_results,
            "deleteResults": delete_results,
        }
    )


# ---------------------------------------------------------------------------
# Additional sharing/portal endpoints used by plenora's ArcGIS connector
# ---------------------------------------------------------------------------


@router.post("/sharing/rest/oauth2/token")
async def arcgis_oauth2_token(request: Request) -> JSONResponse:
    """ArcGIS OAuth2 ``client_credentials`` token exchange.

    Wrong credentials follow the ArcGIS pattern: HTTP 200 + body error.
    """
    form: dict[str, str] = {}
    try:
        form_data = await request.form()
        form = {k: str(v) for k, v in form_data.items()}
    except Exception:
        form = {}
    params = dict(request.query_params)
    payload = {**params, **form}

    grant_type = payload.get("grant_type")
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")

    if (
        grant_type != "client_credentials"
        or client_id != ARCGIS_OAUTH_CLIENT_ID
        or client_secret != ARCGIS_OAUTH_CLIENT_SECRET
    ):
        return JSONResponse(
            content={"error": {"code": 400, "message": "Invalid client credentials"}}
        )

    return JSONResponse(
        content={
            "access_token": ARCGIS_OAUTH_TOKEN,
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )


def _folder_id_from_title(title: str) -> str:
    """Deterministic folder id derived from the title (12 hex chars)."""
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()
    return digest[:12]


@router.post("/sharing/rest/content/users/tester/createFolder")
async def create_folder(
    request: Request,
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()

    title: Optional[str] = None
    # Accept form-encoded body (the ArcGIS default) and JSON body.
    try:
        form_data = await request.form()
        if form_data:
            value = form_data.get("title")
            if value is not None:
                title = str(value)
    except Exception:
        pass
    if title is None:
        try:
            body = await request.json()
            if isinstance(body, dict):
                value = body.get("title")
                if value is not None:
                    title = str(value)
        except Exception:
            pass
    if title is None:
        # Last-resort fallback: query string.
        title = request.query_params.get("title")

    if not title:
        # ArcGIS pattern: HTTP 200 + body error.
        return JSONResponse(
            content={"error": {"code": 400, "message": "Missing title"}}
        )

    return JSONResponse(
        content={
            "success": True,
            "folder": {
                "username": ARCGIS_USER,
                "id": _folder_id_from_title(title),
                "title": title,
            },
        }
    )


@router.get("/sharing/rest/portals/self/isServiceNameAvailable")
def is_service_name_available(
    name: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None),  # noqa: A002 - ArcGIS contract
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()

    if not name:
        return JSONResponse(
            content={"error": {"code": 400, "message": "Missing name"}}
        )

    if name == "occupied":
        return JSONResponse(content={"available": False, "name": "occupied"})
    return JSONResponse(content={"available": True, "name": name})


@router.get("/rest/services")
def services_catalog(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """ArcGIS REST services catalog root."""
    if not _token_ok(token, authorization):
        return _arcgis_invalid_token()
    return JSONResponse(
        content={
            "currentVersion": 11.2,
            "folders": ["test_folder"],
            "services": [
                {"name": "test_folder/Test_FS", "type": "FeatureServer"},
            ],
        }
    )
