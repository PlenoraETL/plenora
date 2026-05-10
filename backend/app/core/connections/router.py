"""
Router API per il modulo Connections.

Endpoint CRUD per le connessioni database.
"""

from fastapi import APIRouter, HTTPException, Depends

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
from app.utils.router_utils import require_service
from app.shared import error_codes
from app.shared.crypto import CredentialUnavailableError
from app.shared.error_envelope import raise_typed_http
from app.shared.audit_action import audit_action
from app.core.auth.visibility import visible_resource_ids
from app.core.projects import usage as project_usage
from .models import (
    DatabaseConnection,
    ConnectionCreate,
    ConnectionUpdate,
    ConnectionList,
    ConnectionTestResult,
)

logger = get_logger(__name__)

router = APIRouter(tags=["connections"])

# Servizi inizializzati da main.py
_connection_service = None
_database_inspector = None


def init_connections(connection_service, database_inspector):
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _connection_service, _database_inspector
    _connection_service = connection_service
    _database_inspector = database_inspector


@router.post("/connections", response_model=DatabaseConnection, status_code=201)
@audit_action(action="connection.create")
def create_connection(
    data: ConnectionCreate,
    current_user: CurrentUser = Depends(require_admin),
):
    """Crea nuova connessione database. Solo admin/superadmin."""
    require_service(_connection_service, "ConnectionService")
    conn = _connection_service.create_connection(
        data,
        group_id=current_user.group_id,
        created_by=current_user.user_id,
    )
    conn.can_edit = compute_can_edit(conn, current_user)
    return conn


@router.get("/connections", response_model=ConnectionList)
def list_connections(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista connessioni visibili: owned dal gruppo + condivise via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    require_service(_connection_service, "ConnectionService")
    is_super = current_user.role == "superadmin"

    all_resources = _connection_service.list_connections(group_id=None)

    if not is_super:
        visible_ids = visible_resource_ids(
            'connections',
            current_user.group_id,
            is_superadmin=False,
        )
        all_resources.connections = [
            c for c in all_resources.connections if c.id in visible_ids
        ]

    # Calcola can_edit per ogni record
    for c in all_resources.connections:
        c.can_edit = compute_can_edit(c, current_user)

    return all_resources


@router.put("/connections/{connection_id}", response_model=DatabaseConnection)
@audit_action(action="connection.update", target_param="connection_id")
def update_connection(
    connection_id: str,
    data: ConnectionUpdate,
    current_user: CurrentUser = Depends(require_admin),
):
    """Aggiorna connessione esistente. Solo admin/superadmin del gruppo, e
    solo se non creata da un superadmin (hierarchical ownership)."""
    require_service(_connection_service, "ConnectionService")
    # FIXME: #1: get_connection raise CredentialUnavailableError su password None/corrotta
    try:
        existing = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))
    assert_can_modify(existing, current_user, "Connessione")
    conn = _connection_service.update_connection(connection_id, data)
    if not conn:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.CONNECTION_NOT_FOUND,
                "message": f"Connessione '{connection_id}' non trovata",
            },
        )
    conn.can_edit = compute_can_edit(conn, current_user)
    return conn


@router.delete("/connections/{connection_id}", status_code=204)
@audit_action(action="connection.delete", target_param="connection_id")
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
        existing = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))
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
                "error": error_codes.CONNECTION_IN_USE,
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

    _connection_service.delete_connection(connection_id)
    # F2-B1 (2026-04-24): DELETE risponde 204 No Content, body vuoto
    # FastAPI ignora il return quando status_code=204; return esplicito
    # rimosso per coerenza col contract


@router.post("/connections/{connection_id}/test", response_model=ConnectionTestResult)
@audit_action(action="connection.test", target_param="connection_id")
def test_connection(
    connection_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Testa una connessione database. Visibile a chi vede la risorsa (owned + shared)."""
    require_service(_connection_service, "ConnectionService")
    # FIXME: #1 (2026-04-23): password=None o decrypt-fail → 422 esplicito
    # non 500 da Pydantic ValidationError
    try:
        existing = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))
    assert_resource_visible(existing, "connections", current_user, "Connessione")
    try:
        return _connection_service.test_connection(connection_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/connections/{connection_id}/tables")
def get_connection_tables(
    connection_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Ottieni tabelle e viste del database (esplora schema). Read-only su risorsa visibile."""
    require_service(_connection_service, "ConnectionService")
    require_service(_database_inspector, "DatabaseInspector")
    try:
        connection = _connection_service.get_connection(connection_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))
    assert_resource_visible(connection, "connections", current_user, "Connessione")

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
