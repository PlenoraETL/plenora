"""CARICA payload validation use-cases for orchestrator task creation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.core.carica.rest_auth import (
    RestAuthUnavailable,
    actor_from_user,
    check_rest_auth_reference,
    rest_auth_api_id,
)
from app.core.carica.rest_config_parser import parse_carica_rest_config, rest_target_url
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http


_REST_CARICA_DEST_TYPES = {"rest", "http", "https", "rest_api"}


def _nested_carica_value(carica_node_data: Mapping[str, object], key: str) -> object | None:
    if key in carica_node_data:
        return carica_node_data.get(key)
    nested = carica_node_data.get("data")
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def _rest_carica_parser_input(
    carica_node_data: Mapping[str, object],
) -> Mapping[str, object]:
    has_rest_config = isinstance(carica_node_data.get("rest_config"), dict)
    has_rest_alias = isinstance(carica_node_data.get("rest"), dict)
    if has_rest_config or has_rest_alias:
        return carica_node_data
    for key in ("destination", "target"):
        value = _nested_carica_value(carica_node_data, key)
        if isinstance(value, dict):
            return {"rest_config": value}
    return carica_node_data


def is_rest_carica_destination(carica_node_data: Mapping[str, object]) -> bool:
    dest_type = _nested_carica_value(carica_node_data, "dest_type")
    if dest_type is None:
        dest_type = _nested_carica_value(carica_node_data, "destination_type")
    return str(dest_type or "").strip().lower() in _REST_CARICA_DEST_TYPES


def validate_rest_carica_task_payload(carica_node_data: Mapping[str, object]) -> None:
    """Validate REST CARICA task configuration.

    Raises:
        ValueError: when the payload declares a REST destination but the REST
            config parser rejects its shape.
    """
    if not is_rest_carica_destination(carica_node_data):
        return

    parse_carica_rest_config(_rest_carica_parser_input(carica_node_data))


# Motivi di permesso -> 403; gli altri sono configurazione da correggere -> 400.
_REST_AUTH_FORBIDDEN_REASONS = frozenset({"actor_unknown", "role_not_allowed", "not_visible"})


def _node_payload(node: Mapping[str, object]) -> Mapping[str, object]:
    data = node.get("data")
    return {**data, **node} if isinstance(data, Mapping) else node


def assert_rest_auth_usable(
    carica_node_data: Mapping[str, object],
    current_user: object,
    *,
    project_group_id: str,
) -> None:
    """Regola di carica.rest_auth prima della scrittura, come errore HTTP tipizzato.

    Si applica a POST /tasks (CARICA) e al salvataggio del canvas: chi lancia
    o salva deve poter usare il connettore e l'endpoint deve avere la sua
    stessa origine. Senza ``rest_auth_api_id`` non c'e' niente da controllare.
    """
    payload = _node_payload(carica_node_data)
    if not rest_auth_api_id(payload):
        return
    try:
        check_rest_auth_reference(
            payload,
            actor=actor_from_user(current_user),
            project_group_id=project_group_id,
            target_url=rest_target_url(_rest_carica_parser_input(payload)),
        )
    except RestAuthUnavailable as exc:
        raise_typed_http(
            error_codes.CARICA_REST_AUTH_UNAVAILABLE,
            status_code=403 if exc.reason in _REST_AUTH_FORBIDDEN_REASONS else 400,
            message=str(exc),
            details={"reason": exc.reason, "node_id": str(payload.get("id") or "")},
        )


def assert_canvas_rest_auth_usable(
    nodes: Iterable[Mapping[str, object]],
    current_user: object,
    *,
    project_group_id: str,
) -> None:
    """``assert_rest_auth_usable`` per ogni nodo CARICA REST del canvas."""
    for node in nodes:
        if isinstance(node, Mapping) and is_rest_carica_destination(_node_payload(node)):
            assert_rest_auth_usable(node, current_user, project_group_id=project_group_id)
