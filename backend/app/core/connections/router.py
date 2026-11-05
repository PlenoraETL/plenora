"""
Router API per il modulo Connections.

Endpoint CRUD per le connessioni database.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, Query

from app.utils.logger import get_logger
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_admin,
)
from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from app.core.connections import errors as connection_errors
from app.utils.router_utils import require_service
from app.shared import error_codes
from app.infrastructure.security.crypto import CredentialUnavailableError
from app.shared.error_envelope import raise_credential_unavailable, raise_typed_http
from app.observability.audit_action import audit_action
from app.shared.connection_tests.results import (
    attaches_connection_tests,
    records_connection_test,
    resets_connection_test,
)
from app.core.auth.visibility import visible_resource_ids
from app.core.projects import usage as project_usage
from .models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult,
    ConnectionSet,
    ConnectionSetCreate,
    ConnectionSetList,
    ConnectionSetUpdate,
)
from . import connection_sets

logger = get_logger(__name__)

router = APIRouter(tags=["connections"])

# Servizi inizializzati da main.py
_connection_service = None
_database_inspector = None


def init_connections(
    connection_service: object, database_inspector: object
) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _connection_service, _database_inspector
    _connection_service = connection_service
    _database_inspector = database_inspector


def _assert_mssql_tls_change_allowed(data, current_user, existing=None) -> None:
    """Only superadmins may change the certificate-validation exception."""
    requested = getattr(data, "mssql_tls_mode", None)
    if requested is None:
        return
    current = getattr(existing, "mssql_tls_mode", "verify") if existing else "verify"
    if requested != current and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "MSSQL_TLS_OVERRIDE_FORBIDDEN",
                "message": "Solo un superadmin può modificare l'eccezione di verifica del certificato MS SQL.",
            },
        )


@router.post("/connections", response_model=DatabaseConnection, status_code=201)
@audit_action(action="connection.create")
def create_connection(
    data: ConnectionCreate,
    current_user: CurrentUser = Depends(require_admin),
):
    """Crea nuova connessione database. Solo admin/superadmin."""
    require_service(_connection_service, "ConnectionService")
    _assert_mssql_tls_change_allowed(data, current_user)
    try:
        conn = _connection_service.create_connection(
            data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
    except connection_errors.ConnectionDuplicateNameError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": connection_errors.CONNECTION_DUPLICATE_NAME,
                "message": str(e),
                "name": e.name,
                "group_id": e.group_id,
            },
        )
    conn.can_edit = compute_can_edit(conn, current_user)
    return conn


@router.get("/connections", response_model=ConnectionList)
@attaches_connection_tests("connection", items_field="connections")
def list_connections(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista connessioni visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    require_service(_connection_service, "ConnectionService")
    is_super = current_user.role == "superadmin"

    if is_super:
        all_resources = _connection_service.list_connections(
            group_id=None, limit=limit, offset=offset,
        )
    else:
        visible_ids = visible_resource_ids(
            'connections',
            current_user.group_id,
            is_superadmin=False,
        )
        all_resources = _connection_service.list_connections(
            group_id=None,
            resource_ids=visible_ids,
            limit=limit,
            offset=offset,
        )

    # Calcola can_edit per ogni record
    for c in all_resources.connections:
        c.can_edit = compute_can_edit(c, current_user)

    return all_resources


@router.get("/connection-sets", response_model=ConnectionSetList)
def list_connection_sets(
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Elenca i set del tenant usati per il routing cliente -> connessione."""

    rows = connection_sets.list_connection_sets(
        group_id=None if current_user.role == "superadmin" else current_user.group_id
    )
    for item in rows:
        item.can_edit = compute_can_edit(item, current_user)
    return ConnectionSetList(connection_sets=rows)


@router.post("/connection-sets", response_model=ConnectionSet, status_code=201)
@audit_action(action="connection_set.create")
def create_connection_set(
    data: ConnectionSetCreate,
    current_user: CurrentUser = Depends(require_admin),
):
    try:
        created = connection_sets.create_connection_set(
            data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    created.can_edit = compute_can_edit(created, current_user)
    return created


@router.get("/connection-sets/{connection_set_id}", response_model=ConnectionSet)
def get_connection_set(
    connection_set_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    item = connection_sets.get_connection_set(connection_set_id)
    if item is None or (
        current_user.role != "superadmin" and item.group_id != current_user.group_id
    ):
        raise HTTPException(status_code=404, detail="Connection Set non trovato")
    item.can_edit = compute_can_edit(item, current_user)
    return item


@router.put("/connection-sets/{connection_set_id}", response_model=ConnectionSet)
@audit_action(action="connection_set.update", target_param="connection_set_id")
def update_connection_set(
    connection_set_id: str,
    data: ConnectionSetUpdate,
    current_user: CurrentUser = Depends(require_admin),
):
    existing = connection_sets.get_connection_set(connection_set_id)
    assert_can_modify(existing, current_user, "Connection Set")
    try:
        updated = connection_sets.update_connection_set(connection_set_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Connection Set non trovato")
    updated.can_edit = compute_can_edit(updated, current_user)
    return updated


@router.delete("/connection-sets/{connection_set_id}", status_code=204)
@audit_action(action="connection_set.delete", target_param="connection_set_id")
def delete_connection_set(
    connection_set_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    existing = connection_sets.get_connection_set(connection_set_id)
    assert_can_modify(existing, current_user, "Connection Set")
    dependencies = connection_sets.connection_set_dependencies(connection_set_id)
    if dependencies:
        raise HTTPException(
            status_code=409,
            detail={
                "error": connection_errors.CONNECTION_IN_USE,
                "message": (
                    "Connection Set usato da progetti o eseguibili. "
                    "Rimuovere prima le associazioni."
                ),
                "dependencies": dependencies,
            },
        )
    connection_sets.delete_connection_set(connection_set_id)


@router.put("/connections/{connection_id}", response_model=DatabaseConnection)
@audit_action(action="connection.update", target_param="connection_id")
@resets_connection_test("connection", id_param="connection_id")
def update_connection(
    connection_id: str,
    data: ConnectionUpdate,
    current_user: CurrentUser = Depends(require_admin),
):
    """Aggiorna connessione esistente. Solo admin/superadmin del gruppo, e
    solo se non creata da un superadmin (hierarchical ownership)."""
    require_service(_connection_service, "ConnectionService")
    try:
        existing = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)
    assert_can_modify(existing, current_user, "Connessione")
    _assert_mssql_tls_change_allowed(data, current_user, existing)
    try:
        conn = _connection_service.update_connection(connection_id, data)
    except connection_errors.ConnectionDuplicateNameError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": connection_errors.CONNECTION_DUPLICATE_NAME,
                "message": str(e),
                "name": e.name,
                "group_id": e.group_id,
            },
        )
    if not conn:
        raise HTTPException(
            status_code=404,
            detail={
                "error": connection_errors.CONNECTION_NOT_FOUND,
                "message": f"Connessione '{connection_id}' non trovata",
            },
        )
    conn.can_edit = compute_can_edit(conn, current_user)
    return conn


@router.delete("/connections/{connection_id}", status_code=204)
@audit_action(action="connection.delete", target_param="connection_id")
@resets_connection_test("connection", id_param="connection_id")
def delete_connection(
    connection_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    """Elimina una connessione. Solo admin/superadmin del gruppo, e solo
    se non creata da un superadmin (hierarchical ownership).

    Cascade check: rifiuta con 409 se la connessione è usata come
    `connection_id` di uno o più progetti (i progetti si romperebbero
    al prossimo run).
    """
    require_service(_connection_service, "ConnectionService")
    try:
        existing = _connection_service.get_connection_public(connection_id)
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)
    assert_can_modify(existing, current_user, "Connessione")

    # Check dipendenze: progetti che usano questa connessione
    # PR-δ: filtrare `dependencies` per visibility lato chiamante per non
    # leakare nomi di progetti cross-tenant nell'errore 409 (la connessione
    # puo' essere shared con altri gruppi via allowed_groups: i loro
    # progetti che la usano restano dipendenze legittime, ma admin del
    # gruppo proprietario non deve vederne i nomi). Superadmin: nessun
    # filtro
    using_projects = project_usage.list_projects_using_connection(connection_id)
    if using_projects:
        is_super = current_user.role == "superadmin"
        if is_super:
            visible = list(using_projects)
            hidden_count = 0
        else:
            visible = [p for p in using_projects if p.get("group_id") == current_user.group_id]
            hidden_count = len(using_projects) - len(visible)

        # Shape 409 cascade canonica (6.1): dependencies con kind
        # PR-δ: aggiunto `hidden_count` opzionale (0 di default, FE
        # backward-compatible: se 0 o assente, formatter ignora
        raise HTTPException(
            status_code=409,
            detail={
                "error": connection_errors.CONNECTION_IN_USE,
                "message": (
                    f"Connessione usata in {len(using_projects)} progetto/i. "
                    f"Rimuoverla dai progetti prima di cancellarla."
                ),
                "dependencies": [
                    {
                        "kind": "project",
                        "id": p["id"],
                        "name": p["name"],
                        "project_type": p["project_type"],
                    }
                    for p in visible
                ],
                "hidden_count": hidden_count,
            },
        )

    set_usage_loader = getattr(
        _connection_service,
        "list_connection_sets_using_connection",
        None,
    )
    raw_using_sets = (
        set_usage_loader(connection_id) if callable(set_usage_loader) else []
    )
    using_sets = (
        list(raw_using_sets)
        if isinstance(raw_using_sets, (list, tuple, set))
        else []
    )
    if using_sets:
        raise HTTPException(
            status_code=409,
            detail={
                "error": connection_errors.CONNECTION_IN_USE,
                "message": (
                    f"Connessione usata in {len(using_sets)} Connection Set. "
                    "Rimuoverla dai set prima di cancellarla."
                ),
                "dependencies": [
                    {"kind": "connection_set", "id": item_id, "name": item_id}
                    for item_id in using_sets
                ],
            },
        )

    _connection_service.delete_connection(connection_id)
    # DELETE risponde 204 No Content, body vuoto.


@router.post("/connections/{connection_id}/test", response_model=ConnectionTestResult)
@audit_action(action="connection.test", target_param="connection_id")
@records_connection_test("connection", id_param="connection_id")
def test_connection(
    connection_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Testa una connessione database. Visibile a chi vede la risorsa (owned + shared)."""
    require_service(_connection_service, "ConnectionService")
    try:
        existing = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)
    assert_resource_visible(existing, "connections", current_user, "Connessione")
    try:
        return _connection_service.test_connection(connection_id)
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)


@router.get("/connections/{connection_id}/tables")
def get_connection_tables(
    connection_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Ottieni tabelle e viste del database (esplora schema). Read-only su risorsa visibile."""
    require_service(_connection_service, "ConnectionService")
    require_service(_database_inspector, "DatabaseInspector")
    public_connection = _connection_service.get_connection_public(connection_id)
    assert_resource_visible(
        public_connection,
        "connections",
        current_user,
        "Connessione",
    )
    try:
        connection = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise_credential_unavailable(e)

    try:
        tables = _database_inspector.get_tables_and_columns(connection)

        # Viste — in try/except per non bloccare se il DB non le supporta
        views = {}
        try:
            views = _database_inspector.get_views_and_columns(connection)
        except Exception as e:
            logger.warning(f"Impossibile recuperare viste per {connection_id}: {e}")

        return {"tables": tables, "views": views}
    except HTTPException:
        raise
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
