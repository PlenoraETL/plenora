"""
Ownership / authorization helpers per risorse owned-by-group.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.auth import db as _users_dal
from app.core.auth import disclosure as _disclosure
from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE
from app.core.auth.visibility import is_resource_visible, is_shareable
from app.shared.contracts.auth import CurrentUser


def _extract_group_and_id(resource):
    """Estrae (group_id, id) da un dict o un oggetto Pydantic."""
    if isinstance(resource, dict):
        return resource.get("group_id"), resource.get("id")
    return getattr(resource, "group_id", None), getattr(resource, "id", None)


def assert_group_access(
    resource: dict | object | None,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Helper centralizzato per il group ownership check (operazioni di SCRITTURA).

    - 404 se `resource` è None.
    - 403 se l'utente non è superadmin e il gruppo della risorsa non combacia.
    - Accetta sia dict che oggetti Pydantic.

    NB: NON considera gli share (allowed_groups). Per le operazioni di SOLA LETTURA
    su risorse shareable usa `assert_resource_visible` che invece include gli share.

    NB: NON controlla l'ownership puntuale (`created_by`). Per quello le 3 dependency
    `require_*_owner`.
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())


def compute_can_edit(
    resource: dict | object | None,
    current_user: CurrentUser,
) -> bool:
    """
    Calcola se l'utente corrente può modificare/eliminare la risorsa.
    Pensato per le list/get response: il frontend usa il flag per nascondere
    i bottoni edit/delete.

    Regole (stesse di `assert_can_modify`):
    - True per superadmin (sempre)
    - True per admin del gruppo proprietario, MA solo se la risorsa NON è
      stata creata da un superadmin (hierarchical ownership)
    - False per user/viewer/altri gruppi/risorse create da super
    """
    if resource is None:
        return False
    if current_user.role == "superadmin":
        return True
    if current_user.role != "admin":
        return False

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        return False

    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        creator = _users_dal.get_user(created_by)
        if creator and creator.role == "superadmin":
            return False

    return True


def assert_can_modify(
    resource: dict | object | None,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Hierarchical ownership check (operazioni di SCRITTURA).

    Regole:
    - 404 se resource is None
    - 403 se non sei superadmin e non sei del gruppo proprietario
    - 403 se l'utente che ha creato la risorsa è SUPERADMIN e tu sei admin
      (un admin di gruppo non può modificare risorse create da un superadmin)
    - Altrimenti OK

    Esempio:
    - superadmin crea connessione X nel gruppo "milano"
    - admin di "milano" prova update X -> 403 ("creata da superadmin")
    - solo superadmin può modificare X
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        creator = _users_dal.get_user(created_by)
        if creator and creator.role == "superadmin":
            _disclosure.raise_disclosure(
                _disclosure.hierarchical_owner_required(label)
            )

    return


def assert_resource_visible(
    resource: dict | object | None,
    table_name: str,
    current_user: CurrentUser,
    label: str = "Risorsa",
) -> None:
    """
    Variante "soft" di `assert_group_access` per le operazioni di SOLA LETTURA
    su risorse shareable (connections, datasources, eseguibili, api_connections,
    arcgis_connections).

    Visibile = owned dal proprio gruppo OPPURE condiviso via *_shares (incluso
    magic share '*'). Una risorsa condivisa può essere LETTA / TESTATA /
    BROWSATA, ma non modificata né eliminata (per quello serve `assert_group_access`).

    - 404 se `resource` è None
    - bypassa per superadmin
    - 403 altrimenti se non è né owned né shared
    """
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{label} non trovata")

    if current_user.role == "superadmin":
        return

    resource_group, resource_id = _extract_group_and_id(resource)

    if resource_group == current_user.group_id:
        return

    if resource_id:
        if is_shareable(table_name) and is_resource_visible(
            table_name,
            resource_id,
            current_user.group_id,
            is_superadmin=False,
            resource_owner_group_id=resource_group,
        ):
            return

    _disclosure.raise_disclosure(_disclosure.access_denied())


def owns_or_admin(
    resource: dict | object | None,
    current_user: CurrentUser,
) -> bool:
    """Regola UNICA delle dependency ``require_*_owner`` come booleano.

    True per superadmin; per gli altri solo nel gruppo della risorsa e se
    admin oppure creatore (``created_by``). Senza ``created_by`` decide il
    solo ruolo admin. La usano ``_check_ownership`` (azione) e le liste che
    espongono ``can_edit`` per le stesse azioni (es. file_sources): la lista
    dice can_edit=true esattamente quando l'azione e' permessa.
    """
    if not resource:
        return False
    if current_user.role == "superadmin":
        return True
    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        return False
    if current_user.role in ROLES_ADMIN_OR_ABOVE:
        return True
    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)
    return bool(created_by) and created_by == current_user.user_id


def _check_ownership(
    resource: dict | None,
    resource_label: str,
    current_user: CurrentUser,
) -> None:
    """
    Logica condivisa per le 3 dependency `require_*_owner` (regola in
    ``owns_or_admin``).

    - 404 se la risorsa non esiste.
    - 403 se l'utente non è del gruppo (a meno che superadmin).
    - 403 se l'utente non è owner E non è admin/superadmin.
    """
    if not resource:
        raise HTTPException(status_code=404, detail=f"{resource_label} non trovato")
    if owns_or_admin(resource, current_user):
        return
    if current_user.role != "superadmin" and resource.get("group_id") != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())
    _disclosure.raise_disclosure(_disclosure.owner_only_required(resource_label))
