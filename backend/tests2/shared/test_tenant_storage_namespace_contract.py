from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.io.tenant_namespace import (
    TenantStorageKeyError,
    assert_runtime_key,
    assert_tenant_key,
    build_tenant_key,
    parse_tenant_key,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_build_and_parse_tenant_key_round_trip():
    key = build_tenant_key("group-a", "results", "project-1", "result.bin")
    assert key == "tenants/group-a/results/project-1/result.bin"
    parsed = parse_tenant_key(key)
    assert parsed is not None
    assert parsed.group_id == "group-a"
    assert parsed.domain == "results"
    assert parsed.relative_segments == ("project-1", "result.bin")


def test_cross_tenant_key_is_rejected_fail_closed():
    key = "tenants/group-b/results/project-1/result.bin"
    with pytest.raises(TenantStorageKeyError, match="altro tenant"):
        assert_tenant_key(
            key,
            expected_group_id="group-a",
            expected_domain="results",
        )


def test_legacy_key_requires_explicit_compatibility_flag():
    key = "results/project-1/result.bin"
    with pytest.raises(TenantStorageKeyError):
        assert_tenant_key(key, expected_group_id="group-a")
    assert (
        assert_tenant_key(
            key,
            expected_group_id="group-a",
            expected_domain="results",
            allow_legacy=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    ["../group", "group/other", "group\\other", "", ".."],
)
def test_unsafe_tenant_segments_are_rejected(value):
    with pytest.raises(TenantStorageKeyError):
        build_tenant_key(value, "results", "p", "r.bin")


def test_operator_runtime_guard_rejects_unknown_prefix():
    with pytest.raises(TenantStorageKeyError):
        assert_runtime_key("backups/database.dump")


class _Storage:
    bucket = "runtime"

    def list_objects_page(self, prefix, *, limit, cursor=None):
        now = datetime.now(timezone.utc)
        return {
            "objects": [
                {
                    "key": "tenants/g1/results/p1/r.bin",
                    "size": 12,
                    "last_modified": now,
                },
                {
                    "key": "tenants/g2/previews/p2/t.json",
                    "size": 5,
                    "last_modified": now,
                },
            ],
            "next_cursor": "next",
        }


def test_admin_catalog_classifies_tenant_objects(monkeypatch):
    from app.core.admin_object_storage import service

    monkeypatch.setattr(service, "get_runtime_object_storage", lambda: _Storage())
    page = service.list_runtime_objects(group_id="g1", domain="results")
    assert page.bucket == "runtime"
    assert page.prefix == "tenants/g1/results/"
    assert page.next_cursor == "next"
    assert page.objects[0].tenant_group_id == "g1"
    assert page.objects[0].domain == "results"
    assert page.objects[0].resource_id == "p1"
