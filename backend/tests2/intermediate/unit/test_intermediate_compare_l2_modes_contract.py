"""
Contract test schema swap batch L2 semantics (2026-04-28).

`GET /api/runs/compare/l2` deve essere onesto rispetto alla
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests2.intermediate.unit.conftest import build_client


pytestmark = [
    pytest.mark.unit, pytest.mark.intermediate,
    pytest.mark.contract, pytest.mark.regression,
]


def _node_meta(persistence_mode=None, **extra):
    base = {
        "row_count": 0,
        "col_schema": {},
        "persistence_mode": persistence_mode,
        "artifact_uri": None,
        "artifact_status": None,
        "artifact_format": None,
        "artifact_error": None,
        "persisted_rows_count": None,
        "preview_truncated": None,
    }
    base.update(extra)
    return base


# 1. full_pg + full_pg -> diff completo invariato


def test_l2_full_pg_full_pg_returns_full_diff(superadmin_user):
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        # full_pg explicit per entrambe
        return _node_meta(persistence_mode="full_pg")

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ), patch(
        "app.db.pg_connection.fetchall",
        return_value=[],
    ), patch(
        "app.db.pg_connection.fetchone",
        return_value={"cnt": 0},
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["persistence_mode_a"] == "full_pg"
    assert body["persistence_mode_b"] == "full_pg"
    assert body["diff_available"] is True
    assert body["partial"] is False
    assert "reason" not in body or body.get("reason") != "preview_only"


def test_l2_legacy_null_treated_as_full_pg(superadmin_user):
    """Record legacy senza `persistence_mode` -> mappato a `full_pg`
    (vedi `policy.persistence_mode_for_record`)."""
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(persistence_mode=None)  # NOTE: legacy record without persistence_mode.

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ), patch(
        "app.db.pg_connection.fetchall",
        return_value=[],
    ), patch(
        "app.db.pg_connection.fetchone",
        return_value={"cnt": 0},
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 200
    body = response.json()
    assert body["persistence_mode_a"] == "full_pg"
    assert body["persistence_mode_b"] == "full_pg"
    assert body["diff_available"] is True


# 2. preview_only + preview_only -> 200 partial=True


def test_l2_preview_only_pair_is_partial(superadmin_user):
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(persistence_mode="preview_only", row_count=5000)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ), patch(
        "app.db.pg_connection.fetchall",
        return_value=[],
    ), patch(
        "app.db.pg_connection.fetchone",
        return_value={"cnt": 0},
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 200
    body = response.json()
    assert body["persistence_mode_a"] == "preview_only"
    assert body["persistence_mode_b"] == "preview_only"
    assert body["diff_available"] is True
    assert body["partial"] is True
    assert body.get("reason") == "preview_only"
    assert "preview" in body.get("message", "").lower()


# 3. metadata_only -> 422 operativo


def test_l2_metadata_only_returns_422_operational(superadmin_user):
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(persistence_mode="metadata_only")

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ), patch(
        "app.db.pg_connection.fetchall",
        side_effect=AssertionError("non deve raggiungere il diff query"),
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["diff_available"] is False
    assert detail["reason"] == "metadata_only_no_rows"
    assert "metadata_only" in detail["message"]
    assert detail["persistence_mode_a"] == "metadata_only"
    assert detail["persistence_mode_b"] == "metadata_only"


def test_l2_metadata_only_on_one_side_only_returns_422(superadmin_user):
    """Anche se solo un lato e' metadata_only, il diff non e' disponibile
    (asimmetria intrinseca: 0 righe vs N righe)."""
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        if rid == "rA":
            return _node_meta(persistence_mode="metadata_only")
        return _node_meta(persistence_mode="full_pg")

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "metadata_only_no_rows"
    assert detail["persistence_mode_a"] == "metadata_only"
    assert detail["persistence_mode_b"] == "full_pg"


# 4. full_artifact + complete -> 501 non implementato


def test_l2_full_artifact_complete_returns_501_not_implemented(superadmin_user):
    """Diff completo da artifact = batch successivo. Per ora 501
    operativo, NIENTE storage hit, niente parquet download."""
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(
            persistence_mode="full_artifact",
            artifact_status="complete",
            artifact_uri="some/key/data.parquet",
        )

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        side_effect=AssertionError("L2 non deve scaricare l'artifact"),
    ), patch(
        "app.db.pg_connection.fetchall",
        side_effect=AssertionError("L2 non deve toccare node_rows"),
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 501, response.text
    detail = response.json()["detail"]
    assert detail["diff_available"] is False
    assert detail["reason"] == "full_artifact_diff_not_implemented"
    assert detail["persistence_mode_a"] == "full_artifact"
    assert detail["persistence_mode_b"] == "full_artifact"
    assert detail["artifact_status_a"] == "complete"
    assert detail["artifact_status_b"] == "complete"


# 5. full_artifact pending/failed -> 422 non pronto


def test_l2_full_artifact_pending_returns_422_operational(superadmin_user):
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(
            persistence_mode="full_artifact",
            artifact_status="pending",
        )

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "full_artifact_not_ready"
    assert detail["diff_available"] is False
    assert detail["artifact_status_a"] == "pending"


def test_l2_full_artifact_failed_returns_422_operational(superadmin_user):
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        return _node_meta(
            persistence_mode="full_artifact",
            artifact_status="failed",
            artifact_error="upload data.parquet fallito",
        )

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "full_artifact_not_ready"


# 6. Mixed mode (preview_only vs full_pg) -> 422 conservativo


def test_l2_preview_only_vs_full_pg_returns_422_mode_mismatch(superadmin_user):
    """A=preview_only (cap 500), B=full_pg (tutte le righe) -> il diff
    sarebbe asimmetrico e darebbe falsi positivi. Conservativo: 422
    invece di mascherare con partial=true."""
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        if rid == "rA":
            return _node_meta(persistence_mode="preview_only", row_count=10000)
        return _node_meta(persistence_mode="full_pg", row_count=10000)

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "persistence_mode_mismatch"
    assert detail["diff_available"] is False
    assert detail["persistence_mode_a"] == "preview_only"
    assert detail["persistence_mode_b"] == "full_pg"


def test_l2_full_pg_vs_preview_only_symmetric(superadmin_user):
    """Stesso comportamento con run swapped (asimmetria del payload
    non influenza la decisione)."""
    client = build_client(superadmin_user)

    def _fake_meta(rid, _nid):
        if rid == "rA":
            return _node_meta(persistence_mode="full_pg")
        return _node_meta(persistence_mode="preview_only")

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "persistence_mode_mismatch"


# 7. Mixed metadata + full_pg -> 422 metadata (precedenza
#    "non disponibile" su "mode mismatch")


def test_l2_mixed_metadata_full_pg_returns_metadata_reason(superdmin_or_admin: bool = False):
    """Quando uno dei due e' metadata_only il reason giusto e'
    `metadata_only_no_rows`, non `mode_mismatch`. La precedenza
    e' deterministica: metadata_only ha priorita' (motivo specifico)."""
    # Riusiamo la fixture superadmin_user via build_client
    from tests2.intermediate.unit.conftest import build_client as _bc
    from app.shared.contracts.auth import CurrentUser
    user = CurrentUser(user_id="u", username="x", group_id="g", role="superadmin")
    client = _bc(user)

    def _fake_meta(rid, _nid):
        if rid == "rA":
            return _node_meta(persistence_mode="metadata_only")
        return _node_meta(persistence_mode="full_pg")

    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.idb.get_node_meta",
        side_effect=_fake_meta,
    ):
        response = client.get("/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1")

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "metadata_only_no_rows"


# 8. Anti-regression: nessun 500


def test_l2_does_not_return_500_on_any_documented_mode(superadmin_user):
    """Smoke matrix: per ogni combinazione documentata, l'endpoint
    non deve restituire 500."""
    client = build_client(superadmin_user)

    cases = [
        # (mode_a, mode_b, status_a, status_b, expected_status)
        ("full_pg", "full_pg", None, None, 200),
        ("preview_only", "preview_only", None, None, 200),
        ("metadata_only", "metadata_only", None, None, 422),
        ("metadata_only", "full_pg", None, None, 422),
        ("full_artifact", "full_artifact", "complete", "complete", 501),
        ("full_artifact", "full_artifact", "pending", "complete", 422),
        ("full_artifact", "full_artifact", "failed", "complete", 422),
        ("preview_only", "full_pg", None, None, 422),
        (None, None, None, None, 200),  # NOTE: legacy NULL = full_pg
    ]

    for mode_a, mode_b, sa, sb, expected in cases:
        def _fake_meta(rid, _nid, _ma=mode_a, _mb=mode_b, _sa=sa, _sb=sb):
            if rid == "rA":
                return _node_meta(persistence_mode=_ma, artifact_status=_sa)
            return _node_meta(persistence_mode=_mb, artifact_status=_sb)

        with patch(
            "app.core.intermediate.router._check_run_access",
            return_value={"run_id": "r", "group_id": "g"},
        ), patch(
            "app.core.intermediate.router.idb.get_node_meta",
            side_effect=_fake_meta,
        ), patch(
            "app.db.pg_connection.fetchall",
            return_value=[],
        ), patch(
            "app.db.pg_connection.fetchone",
            return_value={"cnt": 0},
        ):
            response = client.get(
                "/api/runs/compare/l2?run_id_a=rA&run_id_b=rB&node_id=n1"
            )

        assert response.status_code != 500, (
            f"500 inatteso per mode_a={mode_a}, mode_b={mode_b}, "
            f"status_a={sa}, status_b={sb}: {response.text}"
        )
        assert response.status_code == expected, (
            f"Atteso {expected} per mode_a={mode_a}, mode_b={mode_b}, "
            f"status_a={sa}, status_b={sb}; ricevuto {response.status_code}"
        )
