"""Helper per creare/cancellare progetti e canvas negli integration test."""

from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Iterator, Optional

from tests2.functional.harness.http import http_json_response


def _assert_clean_project_payload(
    *,
    body: dict,
    action: str,
) -> None:
    """Fail-fast su payload progetto sospetti.

    Due casi da bloccare subito:
    - il backend ritorna `warning` / `warnings` e il test proseguirebbe
      osservando un errore downstream fuorviante
    La persistenza del canvas va verificata con una read-back separata:
    alcuni endpoint (es. update canvas) non ritornano il canvas completo.
    """
    warnings = []
    if isinstance(body, dict):
        if body.get("warning"):
            warnings.append(body["warning"])
        extra_warnings = body.get("warnings")
        if isinstance(extra_warnings, list):
            warnings.extend(str(w) for w in extra_warnings if w)
        elif extra_warnings:
            warnings.append(str(extra_warnings))
    if warnings:
        raise RuntimeError(
            f"{action} ha restituito warning inatteso: {' | '.join(warnings)}"
        )

def _assert_persisted_canvas(
    *,
    base_url: str,
    auth_headers: dict,
    project_id: str,
    requested_canvas: Optional[dict],
    action: str,
) -> None:
    """Verifica via read-back che un canvas richiesto sia stato persistito."""
    if not requested_canvas or not requested_canvas.get("nodes"):
        return

    deadline = time.time() + 30.0
    last_error = None
    while time.time() < deadline:
        try:
            status, fetched, _ = http_json_response(
                f"{base_url}/api/projects/{project_id}",
                headers=auth_headers,
                timeout=30.0,
            )
            if status == 200:
                persisted_canvas = (
                    fetched.get("canvas") if isinstance(fetched, dict) else None
                )
                if (
                    isinstance(persisted_canvas, dict)
                    and persisted_canvas.get("nodes")
                ):
                    return
                last_error = (
                    f"{action} canvas non persistito "
                    f"(nodes={len(requested_canvas.get('nodes', []))})"
                )
            else:
                last_error = f"{action} read-back progetto fallito: HTTP {status} - {fetched}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)

    raise RuntimeError(str(last_error))


def create_project_via_api(
    *,
    base_url: str,
    auth_headers: dict,
    name: str,
    project_type: str,
    canvas: Optional[dict] = None,
    default_connection_id: Optional[str] = None,
) -> dict:
    """Crea un progetto reale via API e ritorna il payload completo."""
    payload = {
        "name": name,
        "description": "tests2 integration",
        "project_type": project_type,
        "default_connection_id": default_connection_id,
        "canvas": canvas,
    }
    status, body, _ = http_json_response(
        f"{base_url}/api/projects",
        method="POST",
        payload=payload,
        headers=auth_headers,
        timeout=30.0,
    )
    if status != 200:
        raise RuntimeError(f"create_project_via_api fallito: HTTP {status} - {body}")
    _assert_clean_project_payload(
        body=body,
        action="create_project_via_api",
    )
    _assert_persisted_canvas(
        base_url=base_url,
        auth_headers=auth_headers,
        project_id=body["metadata"]["id"],
        requested_canvas=canvas,
        action="create_project_via_api",
    )
    return body


def update_canvas_via_api(
    *,
    base_url: str,
    auth_headers: dict,
    project_id: str,
    canvas: dict,
) -> dict:
    """Aggiorna il canvas di un progetto reale via API."""
    status, body, _ = http_json_response(
        f"{base_url}/api/projects/{project_id}/canvas",
        method="PUT",
        payload={
            "nodes": canvas.get("nodes", []),
            "edges": canvas.get("edges", []),
            "plugins": None,
        },
        headers=auth_headers,
        timeout=30.0,
    )
    if status != 200:
        raise RuntimeError(f"update_canvas_via_api fallito: HTTP {status} - {body}")
    _assert_clean_project_payload(
        body=body,
        action="update_canvas_via_api",
    )
    _assert_persisted_canvas(
        base_url=base_url,
        auth_headers=auth_headers,
        project_id=project_id,
        requested_canvas=canvas,
        action="update_canvas_via_api",
    )
    return body


def delete_project_via_api(
    *,
    base_url: str,
    auth_headers: dict,
    project_id: str,
) -> None:
    """Elimina un progetto via API. Tollerante ai 404 in cleanup."""
    status, _, _ = http_json_response(
        f"{base_url}/api/projects/{project_id}",
        method="DELETE",
        headers=auth_headers,
        timeout=30.0,
    )
    if status not in (200, 404):
        raise RuntimeError(f"delete_project_via_api fallito: HTTP {status}")


@contextmanager
def managed_project(
    *,
    base_url: str,
    auth_headers: dict,
    name: str,
    project_type: str,
    canvas: Optional[dict] = None,
    default_connection_id: Optional[str] = None,
) -> Iterator[dict]:
    """Context manager: crea progetto via API e lo elimina a fine test."""
    created = create_project_via_api(
        base_url=base_url,
        auth_headers=auth_headers,
        name=name,
        project_type=project_type,
        canvas=canvas,
        default_connection_id=default_connection_id,
    )
    project_id = created["metadata"]["id"]
    try:
        yield created
    finally:
        delete_project_via_api(
            base_url=base_url,
            auth_headers=auth_headers,
            project_id=project_id,
        )
