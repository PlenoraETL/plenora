"""
Tests the resource accesss behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from dataclasses import dataclass

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@dataclass
class ResourceContext:
    owner_group_id: str | None
    current_group_id: str | None
    is_shared: bool = False
    is_superadmin: bool = False


def can_access_resource(ctx: ResourceContext) -> bool:
    """Contract test per il futuro ResourceAccessService.

    Semantica che vogliamo conservare dopo il refactor:
    - superadmin bypass
    - stesso gruppo -> consentito
    - shared -> consentito
    - contesto assente -> fail-closed
    """
    if ctx.is_superadmin:
        return True
    if not ctx.current_group_id:
        raise ValueError("Missing security context")
    if ctx.owner_group_id == ctx.current_group_id:
        return True
    if ctx.is_shared:
        return True
    return False


def test_resource_access_contract_fails_closed_without_context():
    with pytest.raises(ValueError, match="Missing security context"):
        can_access_resource(ResourceContext(owner_group_id="grp-1", current_group_id=None))


def test_resource_access_contract_allows_same_group():
    assert can_access_resource(
        ResourceContext(owner_group_id="grp-1", current_group_id="grp-1")
    ) is True


def test_resource_access_contract_allows_shared_visibility():
    assert can_access_resource(
        ResourceContext(owner_group_id="grp-2", current_group_id="grp-1", is_shared=True)
    ) is True


def test_resource_access_contract_blocks_other_group_when_not_shared():
    assert can_access_resource(
        ResourceContext(owner_group_id="grp-2", current_group_id="grp-1")
    ) is False
