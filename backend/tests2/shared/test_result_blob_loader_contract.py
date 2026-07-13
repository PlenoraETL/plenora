"""Object-storage-only contracts for the result blob loader."""

from unittest.mock import patch

import pytest

from tests2.shared._resource_loader_helpers import fake_fetchone, make_result_ref


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_loader_rejects_wrong_kind_and_mode():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = make_result_ref()
    object.__setattr__(ref, "kind", "file_source_blob")
    with pytest.raises(ValueError, match="does not handle kind"):
        ResultBlobLoader().load(ref, mode="bytes")

    ref = make_result_ref()
    with pytest.raises(ValueError, match="not supported"):
        ResultBlobLoader().load(ref, mode="path")


def test_loader_filters_tenant_and_downloads_object():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    captured, fake = fake_fetchone(
        row={"storage_key": "results/p/r.bin", "project_id": "p"}
    )
    with patch("app.db.pg_connection.fetchone", fake), patch(
        "app.core.resources.loaders.result_blob._download_object_storage",
        return_value=b"payload",
    ):
        out = ResultBlobLoader().load(make_result_ref(), mode="bytes")

    assert "WHERE id = %s AND group_id = %s" in captured["sql"]
    assert "data" not in captured["sql"].lower()
    assert captured["params"] == ("r1", "g1")
    assert out.data == b"payload"


def test_loader_missing_row_is_not_found():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    _, fake = fake_fetchone(row=None)
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(FileNotFoundError, match="not found"):
            ResultBlobLoader().load(make_result_ref(), mode="bytes")


def test_loader_rejects_catalog_row_without_storage_key():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    _, fake = fake_fetchone(row={"storage_key": None, "project_id": "p"})
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(RuntimeError, match="senza storage_key"):
            ResultBlobLoader().load(make_result_ref(), mode="bytes")


def test_result_boundary_uses_same_object_storage_dispatch():
    from app.core.results import blob

    captured, fake = fake_fetchone(
        row={"storage_key": "results/p/r.bin", "project_id": "p"}
    )
    with patch("app.db.pg_connection.fetchone", fake), patch(
        "app.core.resources.loaders.result_blob._download_object_storage",
        return_value=b"ok",
    ):
        assert blob.get_result_data("r1", group_id="g1") == b"ok"
    assert captured["params"] == ("r1", "g1")
