"""
PLENORA-ETL — Router per gestione allowed_groups (cross-group sharing).

Espone endpoint REST per leggere/modificare le righe di sharing delle
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.auth.dependencies import require_superadmin
from app.core.auth.visibility import (
    _SHAREABLE,
    ShareableResourceOwner,
    load_shareable_resource_owner,
)
from app.core.auth.shares import (
    list_resource_shares,
    set_resource_shares,
    add_resource_share,
    remove_resource_share,
)
from app.shared.contracts.auth import CurrentUser
from app.observability.audit_action import audit_action

router = APIRouter(prefix="/shares", tags=["shares"])


# UTILS

def _validate_resource_type(resource_type: str) -> None:
    """Solleva 400 se il tipo di risorsa non è shareable."""
    if resource_type not in _SHAREABLE:
        raise HTTPException(
            status_code=400,
            detail=f"resource_type non valido: '{resource_type}'. "
                   f"Validi: {list(_SHAREABLE)}",
        )


def _load_resource_owner_group(
    resource_type: str,
    resource_id: str,
) -> ShareableResourceOwner:
    """
    Carica la riga della risorsa per ottenere il group_id proprietario.
    Solleva 404 se non esiste.

    NB: usa una query SELECT diretta per indipendenza dai vari service.
    """
    row = load_shareable_resource_owner(resource_type, resource_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"{resource_type[:-1].capitalize()} '{resource_id}' non trovata",
        )
    return row


def _check_owner_access(
    resource_type: str,
    resource_id: str,
    current_user: CurrentUser,
) -> ShareableResourceOwner:
    """
    Verifica che la risorsa esista e restituisce il suo proprietario. La
    dependency `require_superadmin` sull'endpoint garantisce già che solo i
    superadmin arrivino qui.
    """
    _validate_resource_type(resource_type)
    return _load_resource_owner_group(resource_type, resource_id)  # 404 se non esiste


# MODELS

class SharesResponse(BaseModel):
    resource_type: str
    resource_id: str
    group_ids: List[str]
    # Gruppo proprietario della risorsa, dal server: il selettore delle
    # condivisioni lo esclude (audit frontend R10.7, caso superadmin).
    owner_group_id: Optional[str] = None


class SetSharesRequest(BaseModel):
    group_ids: List[str]


# ENDPOINT

@router.get("/{resource_type}/{resource_id}", response_model=SharesResponse)
def get_shares(
    resource_type: str,
    resource_id: str,
    current_user: CurrentUser = Depends(require_superadmin),
):
    """
    Lista i group_id con cui la risorsa è condivisa.
    Solo superadmin.
    """
    owner = _check_owner_access(resource_type, resource_id, current_user)
    shares = list_resource_shares(resource_type, resource_id)
    return SharesResponse(
        resource_type=resource_type,
        resource_id=resource_id,
        group_ids=shares,
        owner_group_id=owner.get("group_id"),
    )


@router.put("/{resource_type}/{resource_id}", response_model=SharesResponse)
@audit_action(action="share.set", target_param="resource_id")
def set_shares(
    resource_type: str,
    resource_id: str,
    data: SetSharesRequest,
    current_user: CurrentUser = Depends(require_superadmin),
):
    """
    Sostituisce TUTTE le righe di sharing per la risorsa.
    Pass `["*"]` per condivisione globale, `[]` per rimuovere tutte.
    Solo superadmin.
    """
    owner = _check_owner_access(resource_type, resource_id, current_user)
    set_resource_shares(resource_type, resource_id, data.group_ids)
    return SharesResponse(
        resource_type=resource_type,
        resource_id=resource_id,
        group_ids=list_resource_shares(resource_type, resource_id),
        owner_group_id=owner.get("group_id"),
    )


@router.post("/{resource_type}/{resource_id}/{group_id}", response_model=SharesResponse)
@audit_action(action="share.add", target_param="resource_id")
def add_share(
    resource_type: str,
    resource_id: str,
    group_id: str,
    current_user: CurrentUser = Depends(require_superadmin),
):
    """
    Aggiunge un singolo gruppo alla lista shares della risorsa.
    Idempotente. Pass `*` come group_id per share globale.
    """
    owner = _check_owner_access(resource_type, resource_id, current_user)
    add_resource_share(resource_type, resource_id, group_id)
    return SharesResponse(
        resource_type=resource_type,
        resource_id=resource_id,
        group_ids=list_resource_shares(resource_type, resource_id),
        owner_group_id=owner.get("group_id"),
    )


@router.delete("/{resource_type}/{resource_id}/{group_id}", response_model=SharesResponse)
@audit_action(action="share.remove", target_param="resource_id")
def remove_share(
    resource_type: str,
    resource_id: str,
    group_id: str,
    current_user: CurrentUser = Depends(require_superadmin),
):
    """Rimuove una singola riga di sharing."""
    owner = _check_owner_access(resource_type, resource_id, current_user)
    remove_resource_share(resource_type, resource_id, group_id)
    return SharesResponse(
        resource_type=resource_type,
        resource_id=resource_id,
        group_ids=list_resource_shares(resource_type, resource_id),
        owner_group_id=owner.get("group_id"),
    )
