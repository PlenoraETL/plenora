"""Ownership / authorization helpers per risorse owned-by-group.

Estratto verbatim da `app.core.auth.auth` (STRUCT-005 Seam B'). Contiene
i sei helper puri usati dai router su risorse con `group_id`/`created_by`:

  - `_extract_group_and_id(resource)`        — coercizione dict|object
  - `assert_group_access(resource, user)`    — write su risorse owned (404/403)
  - `compute_can_edit(resource, user)`       — flag UI hide-buttons
  - `assert_can_modify(resource, user)`      — write hierarchical (admin vs superadmin)
  - `assert_resource_visible(resource, …)`   — read soft + share-aware
  - `_check_ownership(resource, …)`          — helper interno per require_*_owner

Le 3 dependency FastAPI (`require_project_owner`, `require_schedule_owner`,
`require_file_owner`) restano in `auth.py` perche' usano
`Depends(get_current_user)`: estrarle qui creerebbe un cycle
`auth.py ↔ ownership.py`. Sono ridotte a thin wrapper che chiamano
`_check_ownership` re-esportato.

`auth.py` re-esporta tutti i 6 simboli per backward-compat: i 17 router
che fanno `from app.core.auth.auth import assert_group_access, …`
continuano a funzionare invariati.

Cycle prevention:
  - `CurrentUser` viene da `app.core.auth.models` (leaf module).
  - `ROLES_ADMIN_OR_ABOVE` viene da `app.core.auth.roles` (leaf module
    senza dipendenze app — vedi roles.py:1). BE-GO-READY-AUTH-ENGINE-005
    ha promosso questo import da lazy a eager (top-level): la facade
    `auth.py` non e' piu' nella catena.
  - `app.shared.sharing` resta lazy dentro `assert_resource_visible`.
"""

from __future__ import annotations

from fastapi import HTTPException

import app.db.db_service as db
from app.core.auth import disclosure as _disclosure
from app.core.auth.models import CurrentUser
from app.core.auth.roles import ROLES_ADMIN_OR_ABOVE


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

    Sostituisce il pattern duplicato:
        if current_user.role != "superadmin" and resource.group_id != current_user.group_id:
            disclosure.raise_disclosure(disclosure.access_denied())

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
        return  # superadmin bypassa

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
        return False  # user/viewer non possono modificare le risorse condivise

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        return False

    # admin del gruppo: ok solo se NON creata da un superadmin
    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        creator = db.get_user(created_by)
        if creator and creator.get("role") == "superadmin":
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
        return  # superadmin bypassa sempre

    resource_group, _ = _extract_group_and_id(resource)
    if resource_group != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    # Hierarchical check: chi l'ha creata?
    if isinstance(resource, dict):
        created_by = resource.get("created_by")
    else:
        created_by = getattr(resource, "created_by", None)

    if created_by and created_by != current_user.user_id:
        # Lookup live del ruolo del creator
        creator = db.get_user(created_by)
        if creator and creator.get("role") == "superadmin":
            _disclosure.raise_disclosure(
                _disclosure.hierarchical_owner_required(label)
            )

    # Owned dal proprio gruppo, NON creato da un superadmin → ok
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

    # Owned dal proprio gruppo: ok subito (no DB query)
    if resource_group == current_user.group_id:
        return

    # Shared via *_shares?
    if resource_id:
        from app.shared.sharing import is_resource_visible, is_shareable
        if is_shareable(table_name) and is_resource_visible(
            table_name,
            resource_id,
            current_user.group_id,
            is_superadmin=False,
            resource_owner_group_id=resource_group,
        ):
            return

    _disclosure.raise_disclosure(_disclosure.access_denied())


def _check_ownership(
    resource: dict | None,
    resource_label: str,
    current_user: CurrentUser,
) -> None:
    """
    Logica condivisa per le 3 dependency `require_*_owner`.

    - 404 se la risorsa non esiste.
    - 403 se l'utente non è del gruppo (a meno che superadmin).
    - 403 se l'utente non è owner E non è admin/superadmin.
    """
    if not resource:
        raise HTTPException(status_code=404, detail=f"{resource_label} non trovato")

    is_super = current_user.role == "superadmin"
    is_admin_plus = current_user.role in ROLES_ADMIN_OR_ABOVE

    # Filtro gruppo (superadmin bypassa)
    if not is_super and resource.get("group_id") != current_user.group_id:
        _disclosure.raise_disclosure(_disclosure.access_denied())

    # Owner check: created_by deve combaciare oppure admin/superadmin
    created_by = resource.get("created_by")
    if is_admin_plus:
        return  # admin del gruppo (o superadmin) può sempre
    if created_by and created_by == current_user.user_id:
        return  # sei tu l'owner

    _disclosure.raise_disclosure(_disclosure.owner_only_required(resource_label))
