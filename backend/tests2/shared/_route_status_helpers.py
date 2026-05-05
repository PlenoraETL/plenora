"""Shared route-status assertion helpers for contract tests.

BE-GO-READY-TEST-INFRA-001-ROUTE-STATUS-HELPER-EXTRACTION.

Single home for the two helpers that were previously duplicated
across `test_post_create_201_contract*.py` and
`test_delete_204_contract*.py`. Bodies + assertion messages are
preserved BYTE-EXACT so failing-test diffs do not change.

The leading underscore in the module name keeps pytest from
collecting it as a test module (pytest collects `test_*.py` by
default). The helpers themselves keep their leading-underscore
naming (`_assert_*`) to signal "test-internal helper" to readers
of the call sites.
"""

from __future__ import annotations


def _assert_post_route_status_201(router, path: str, context: str):
    """Helper comune: cerca il route POST esatto e verifica status_code=201."""
    post_routes = [
        r for r in router.routes
        if getattr(r, "path", None) == path
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(post_routes) == 1, (
        f"{context}: mi aspetto esattamente 1 route POST {path!r}, "
        f"trovato: {[(r.path, r.methods) for r in router.routes if 'POST' in getattr(r, 'methods', set())]!r}"
    )
    assert post_routes[0].status_code == 201, (
        f"{context}: POST {path} DEVE dichiarare status_code=201 nel "
        f"decoratore; ottenuto {post_routes[0].status_code!r} "
        f"(None = default 200)"
    )


def _assert_delete_route_status_204(router, path: str, context: str):
    """Helper: cerca route DELETE esatto, verifica status_code=204."""
    delete_routes = [
        r for r in router.routes
        if getattr(r, "path", None) == path
        and "DELETE" in getattr(r, "methods", set())
    ]
    assert len(delete_routes) == 1, (
        f"{context}: mi aspetto esattamente 1 route DELETE {path!r}, "
        f"trovato: {[(r.path, r.methods) for r in router.routes if 'DELETE' in getattr(r, 'methods', set())]!r}"
    )
    assert delete_routes[0].status_code == 204, (
        f"{context}: DELETE {path} DEVE dichiarare status_code=204 nel "
        f"decoratore; ottenuto {delete_routes[0].status_code!r} "
        f"(None = default 200)"
    )
