from datetime import datetime, timezone
from types import SimpleNamespace

from app.shared.contracts.auth import CurrentUser
from app.core.features.models import ArcGISConnection, ArcGISCreate


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_current_user(
    *,
    user_id: str = "u-1",
    username: str = "tester",
    group_id: str = "grp-1",
    role: str = "admin",
) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        username=username,
        group_id=group_id,
        role=role,
    )


def build_arcgis_connection(
    now: datetime | None = None,
    **overrides,
) -> ArcGISConnection:
    ts = now or utc_now()
    data = {
        "id": "conn-1",
        "group_id": "grp-1",
        "name": "ArcGIS",
        "description": "",
        "server_url": "https://example.test",
        "server_type": "arcgis_server",
        "auth_type": "oauth2",
        "username": "user-1",
        "password": "pwd-123",
        "client_id": "client-1",
        "client_secret": "secret-123",
        "token_url": None,
        "created_at": ts,
        "updated_at": ts,
        "created_by": "u-1",
    }
    data.update(overrides)
    return ArcGISConnection(**data)


def build_arcgis_create(**overrides) -> ArcGISCreate:
    data = {
        "name": "ArcGIS",
        "description": "",
        "server_url": "https://example.test",
        "server_type": "arcgis_server",
        "auth_type": "oauth2",
        "username": "user-1",
        "password": "pwd-123",
        "client_id": "client-1",
        "client_secret": "secret-123",
        "token_url": None,
    }
    data.update(overrides)
    return ArcGISCreate(**data)


def build_node(node_type: str, **attrs):
    payload = {
        "id": attrs.pop("id", "node-1"),
        "type": node_type,
        "data": attrs.pop("data", None),
    }
    payload.update(attrs)
    return SimpleNamespace(**payload)


def build_canvas(*nodes):
    return SimpleNamespace(nodes=list(nodes))
