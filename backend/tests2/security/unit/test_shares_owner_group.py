"""
Le risposte delle condivisioni portano il gruppo proprietario della risorsa
(audit frontend R10.7): il selettore lo esclude leggendolo dal server, non
dal gruppo dell'utente (un superadmin del gruppo S che condivide una risorsa
del gruppo G).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_every_shares_endpoint_returns_owner_group():
    from app.core.auth import shares_router as router

    owner = {"id": "conn-1", "group_id": "grp-G"}
    with patch.object(router, "load_shareable_resource_owner", return_value=owner), \
         patch.object(router, "list_resource_shares", return_value=["grp-X"]), \
         patch.object(router, "set_resource_shares"), \
         patch.object(router, "add_resource_share"), \
         patch.object(router, "remove_resource_share"):
        user = object()
        responses = []
        for endpoint, args in (
            (router.get_shares, ("connections", "conn-1", user)),
            (router.set_shares, ("connections", "conn-1", router.SetSharesRequest(group_ids=["grp-X"]), user)),
            (router.add_share, ("connections", "conn-1", "grp-X", user)),
            (router.remove_share, ("connections", "conn-1", "grp-X", user)),
        ):
            target = getattr(endpoint, "__wrapped__", endpoint)
            responses.append(target(*args))

    assert [r.owner_group_id for r in responses] == ["grp-G"] * 4
