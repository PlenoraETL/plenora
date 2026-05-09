"""
Contract test — upload/delete allegati ticket.

Copre il flusso `/api/tickets/{id}/attachments`:
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.tickets.router import _require_not_esecutore, router as tickets_router
from app.core.tickets import db as tdb


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


def _user(role: str = "user", user_id: str = "u1", group_id: str = "g1") -> CurrentUser:
    return CurrentUser(
        user_id=user_id, username=f"user-{user_id}",
        group_id=group_id, role=role,
    )


def _ticket(
    ticket_id: str = "t1",
    group_id: str = "g1",
    created_by: str = "u1",
    kind: str = "user_support",
) -> tdb.TicketRow:
    return tdb.TicketRow(
        id=ticket_id,
        kind=kind,
        title="t",
        description="",
        severity="medium",
        status="open",
        category="other",
        created_by=created_by,
        assignee=None,
        group_id=group_id,
        created_at="2026-04-19T00:00:00Z",
        updated_at="2026-04-19T00:00:00Z",
        resolved_at=None,
    )


def _attachment(**overrides) -> tdb.AttachmentRow:
    data = {
        "id": "att-1",
        "ticket_id": "t1",
        "file_source_id": "fs-1",
        "uploaded_by": "u1",
        "uploaded_at": "2026-04-19T00:00:00Z",
    }
    data.update(overrides)
    return tdb.AttachmentRow(**data)


def _make_client(current_user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[_require_not_esecutore] = lambda: current_user
    app.include_router(tickets_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_tdb(monkeypatch):
    """Mocka il DB layer dove il router (e service) lo importano."""
    import app.core.tickets.db as real_tdb
    m = MagicMock(spec=real_tdb)
    m.list_attachments.return_value = []
    m.list_comments.return_value = []
    m.get_ticket.return_value = _ticket()
    m.link_attachment.return_value = _attachment()
    m.delete_attachment.return_value = True
    m.get_attachment.return_value = _attachment()

    import app.core.tickets.router as router_mod
    import app.core.tickets.service as svc_mod
    monkeypatch.setattr(router_mod, "tdb", m)
    monkeypatch.setattr(svc_mod, "tdb", m)
    return m


# Magic bytes minimi per ciascun formato in allowlist.
# Servono perche' il router ora valida MIME content-based via filetype:
# i placeholder b"data" / b"PNGDATA"*100 non passano piu'.
_VALID_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x01\x01\x00\x00\x00\x00"
    b"\x37\x6e\xf9\x24"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_VALID_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01"
    b"\x00\x01\x00\x00\xff\xd9"
)
_VALID_GIF = (
    b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
# WEBP: 'RIFF' + size + 'WEBPVP8 ' + minimal payload
_VALID_WEBP = (
    b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00"
    + b"\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00\x02\x00\x34\x25\xa4\x00\x03\x70\x00\xfe\xfb\x94\x00\x00"
)
_VALID_TEXT = b"hello world\nseconda riga\n"

_VALID_CONTENT_BY_EXT = {
    "png":  _VALID_PNG,
    "jpg":  _VALID_JPEG,
    "jpeg": _VALID_JPEG,
    "gif":  _VALID_GIF,
    "webp": _VALID_WEBP,
    "log":  _VALID_TEXT,
    "txt":  _VALID_TEXT,
}


@pytest.fixture
def mock_file_service():
    """Patch FileSourceService — il router lo importa lazy dentro l'endpoint."""
    fake_fs = MagicMock()
    fake_fs.create_raw_from_upload.return_value = MagicMock(
        id="fs-1", name="ticket-12345678-screenshot.png",
        original_filename="screenshot.png", format="png", file_size=1234,
    )
    fake_fs.delete_file_source.return_value = True
    with patch(
        "app.core.files.service.FileSourceService", return_value=fake_fs,
    ) as cls:
        yield fake_fs


# UPLOAD


def test_upload_happy_path_returns_attachment_metadata(mock_tdb, mock_file_service):
    client = _make_client(_user("user"))
    files = {"file": ("screenshot.png", _VALID_PNG, "image/png")}

    response = client.post("/api/tickets/t1/attachments", files=files)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "att-1"
    assert body["file_source_id"] == "fs-1"
    assert body["original_filename"] == "screenshot.png"
    assert body["format"] == "png"
    assert body["size_bytes"] == 1234
    mock_file_service.create_raw_from_upload.assert_called_once()
    kwargs = mock_file_service.create_raw_from_upload.call_args.kwargs
    assert kwargs["filename"] == "screenshot.png"
    assert kwargs["group_id"] == "g1"
    mock_tdb.link_attachment.assert_called_once_with(
        ticket_id="t1", file_source_id="fs-1", uploaded_by="u1",
    )


@pytest.mark.parametrize("ext,mime", [
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("gif", "image/gif"),
    ("webp", "image/webp"),
    ("log", None),
    ("txt", None),
])
def test_upload_accepts_allowlisted_extensions(
    mock_tdb, mock_file_service, ext, mime,
):
    client = _make_client(_user("user"))
    content = _VALID_CONTENT_BY_EXT[ext]
    files = {"file": (f"x.{ext}", content, mime or "application/octet-stream")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 201, f"ext={ext}: {response.text}"


@pytest.mark.parametrize("filename", ["evil.exe", "doc.pdf", "macro.docx", "file.zip"])
def test_upload_rejects_extensions_not_in_allowlist(
    mock_tdb, mock_file_service, filename,
):
    client = _make_client(_user("user"))
    files = {"file": (filename, b"payload", "application/octet-stream")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 400
    assert "non consentita" in response.json()["detail"]
    mock_file_service.create_raw_from_upload.assert_not_called()
    mock_tdb.link_attachment.assert_not_called()


def test_upload_rejects_empty_file(mock_tdb, mock_file_service):
    client = _make_client(_user("user"))
    files = {"file": ("empty.log", b"", "text/plain")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 400
    assert "vuoto" in response.json()["detail"].lower()
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_rejects_oversize_413(mock_tdb, mock_file_service):
    """File > 10MB → 413."""
    client = _make_client(_user("user"))
    too_big = b"X" * (10 * 1024 * 1024 + 1)  # 10MB + 1
    files = {"file": ("big.log", too_big, "text/plain")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 413
    assert "troppo grande" in response.json()["detail"].lower()
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_at_size_cap_succeeds(mock_tdb, mock_file_service):
    """File esattamente 10MB passa (cap inclusive)."""
    client = _make_client(_user("user"))
    at_cap = b"Y" * (10 * 1024 * 1024)
    files = {"file": ("big.log", at_cap, "text/plain")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 201


def test_upload_admin_other_group_gets_404(mock_tdb, mock_file_service):
    """Cross-tenant: admin di g2 prova a uploadare su ticket di g1 → 404
    (mascherato per non leakare esistenza)."""
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("admin", user_id="u2", group_id="g2"))
    files = {"file": ("x.png", _VALID_PNG, "image/png")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 404
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_user_on_other_user_ticket_gets_404(mock_tdb, mock_file_service):
    """User u2 prova a uploadare su ticket di u1 → 404."""
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    client = _make_client(_user("user", user_id="u2", group_id="g1"))
    files = {"file": ("x.png", _VALID_PNG, "image/png")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 404
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_ticket_not_found_returns_404(mock_tdb, mock_file_service):
    mock_tdb.get_ticket.return_value = None
    client = _make_client(_user("user"))
    files = {"file": ("x.png", _VALID_PNG, "image/png")}
    response = client.post("/api/tickets/missing/attachments", files=files)
    assert response.status_code == 404
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_extension_check_is_case_insensitive(mock_tdb, mock_file_service):
    client = _make_client(_user("user"))
    files = {"file": ("SCREEN.PNG", _VALID_PNG, "image/png")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 201


def test_upload_rejects_exe_renamed_to_png(mock_tdb, mock_file_service):
    """Adversarial: .exe con magic MZ rinominato in .png → 400 MIME mismatch."""
    client = _make_client(_user("user"))
    exe_bytes = b"MZ\x90\x00" + b"\x00" * 200  # Windows PE header
    files = {"file": ("photo.png", exe_bytes, "image/png")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 400
    assert "eseguibile" in response.json()["detail"].lower() or "firma" in response.json()["detail"].lower()
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_rejects_pdf_renamed_to_png(mock_tdb, mock_file_service):
    """Adversarial: PDF rinominato in .png → 400 MIME mismatch."""
    client = _make_client(_user("user"))
    pdf_bytes = b"%PDF-1.4\n%test\n" + b"\x00" * 200
    files = {"file": ("doc.png", pdf_bytes, "image/png")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 400
    mock_file_service.create_raw_from_upload.assert_not_called()


def test_upload_rejects_binary_renamed_to_log(mock_tdb, mock_file_service):
    """Adversarial: binary con null bytes rinominato in .log → 400."""
    client = _make_client(_user("user"))
    binary = b"\x00\x01\x02\x03" * 50
    files = {"file": ("trojan.log", binary, "text/plain")}
    response = client.post("/api/tickets/t1/attachments", files=files)
    assert response.status_code == 400
    mock_file_service.create_raw_from_upload.assert_not_called()


# DELETE


def test_delete_uploader_can_remove_own_attachment(mock_tdb, mock_file_service):
    """Creator dell'allegato puo' rimuoverlo (anche se non admin)."""
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    client = _make_client(_user("user", user_id="u1", group_id="g1"))
    response = client.delete("/api/tickets/t1/attachments/att-1")
    # F2-B3 (2026-04-24): DELETE attachment risponde 204 No Content.
    assert response.status_code == 204
    mock_tdb.delete_attachment.assert_called_once_with("att-1")
    mock_file_service.delete_file_source.assert_called_once_with("fs-1")


def test_delete_admin_can_remove_others_attachment(mock_tdb, mock_file_service):
    """Admin del gruppo puo' rimuovere allegati di altri user."""
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    client = _make_client(_user("admin", user_id="admin1", group_id="g1"))
    response = client.delete("/api/tickets/t1/attachments/att-1")
    assert response.status_code == 204
    mock_tdb.delete_attachment.assert_called_once_with("att-1")


def test_delete_other_user_gets_403(mock_tdb, mock_file_service):
    """User non-uploader e non-admin → 403."""
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    client = _make_client(_user("user", user_id="u2", group_id="g1"))
    response = client.delete("/api/tickets/t1/attachments/att-1")
    # u2 non vede nemmeno il ticket di u1 → 404 prima del 403
    assert response.status_code in (403, 404)
    mock_tdb.delete_attachment.assert_not_called()


def test_delete_attachment_mismatched_ticket_returns_404(mock_tdb, mock_file_service):
    """attachment_id appartiene a ticket diverso → 404."""
    mock_tdb.get_attachment.return_value = _attachment(ticket_id="OTHER", uploaded_at="x")
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    client = _make_client(_user("user", user_id="u1", group_id="g1"))
    response = client.delete("/api/tickets/t1/attachments/att-1")
    assert response.status_code == 404
    mock_tdb.delete_attachment.assert_not_called()


# DOWNLOAD


@pytest.fixture
def mock_download_deps(mock_file_service, monkeypatch):
    """Setup per download: mock blob + get_file_source metadata."""
    mock_file_service.get_blob_bytes.return_value = b"FAKEBLOB" * 10

    import app.core.files.db as db_mod
    monkeypatch.setattr(
        db_mod, "get_file_source",
        lambda file_id, include_data=False: {
            "id": file_id, "original_filename": "screenshot.png",
            "format": "png",
        },
    )
    return mock_file_service


def test_download_happy_path_returns_blob(mock_tdb, mock_download_deps):
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.status_code == 200
    assert response.content == b"FAKEBLOB" * 10
    # RBAC: get_blob_bytes chiamato col group_id del ticket
    mock_download_deps.get_blob_bytes.assert_called_once_with(
        "fs-1", expected_group_id="g1",
    )


def test_download_image_sets_content_type_and_inline(mock_tdb, mock_download_deps):
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.headers["content-type"].startswith("image/png")
    assert "inline" in response.headers["content-disposition"]
    assert 'filename="screenshot.png"' in response.headers["content-disposition"]


def test_download_log_forces_attachment_disposition(
    mock_tdb, mock_file_service, monkeypatch,
):
    mock_file_service.get_blob_bytes.return_value = b"log data\n"

    import app.core.files.db as db_mod
    monkeypatch.setattr(
        db_mod, "get_file_source",
        lambda file_id, include_data=False: {
            "id": file_id, "original_filename": "errors.log", "format": "log",
        },
    )
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert 'filename="errors.log"' in response.headers["content-disposition"]


def test_download_attachment_not_found_returns_404(mock_tdb, mock_download_deps):
    mock_tdb.get_attachment.return_value = None
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/missing/download")
    assert response.status_code == 404
    mock_download_deps.get_blob_bytes.assert_not_called()


def test_download_attachment_wrong_ticket_returns_404(mock_tdb, mock_download_deps):
    """Allegato appartiene a ticket diverso → 404 (no info leak)."""
    mock_tdb.get_attachment.return_value = _attachment(ticket_id="OTHER", uploaded_at="x")
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.status_code == 404
    mock_download_deps.get_blob_bytes.assert_not_called()


def test_download_cross_tenant_gets_404(mock_tdb, mock_download_deps):
    """User di altro gruppo non vede ticket → 404 sulla download."""
    mock_tdb.get_ticket.return_value = _ticket(group_id="g1", created_by="u1")
    client = _make_client(_user("admin", user_id="u2", group_id="g2"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.status_code == 404
    mock_download_deps.get_blob_bytes.assert_not_called()


def test_download_blob_unavailable_returns_404(mock_tdb, mock_file_service):
    """Se get_blob_bytes ritorna None (blob perso / S3 error) → 404."""
    mock_file_service.get_blob_bytes.return_value = None
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    client = _make_client(_user("user"))
    response = client.get("/api/tickets/t1/attachments/att-1/download")
    assert response.status_code == 404


# DELETE (continua)


def test_delete_cleanup_failure_does_not_break_response(mock_tdb, mock_file_service):
    """Se delete_file_source solleva (S3 down ecc.), la response resta 200
    e la row del ticket_attachment e' gia' stata cancellata."""
    mock_tdb.get_attachment.return_value = _attachment(uploaded_at="x")
    mock_tdb.get_ticket.return_value = _ticket(created_by="u1", group_id="g1")
    mock_file_service.delete_file_source.side_effect = RuntimeError("S3 down")

    client = _make_client(_user("user", user_id="u1", group_id="g1"))
    response = client.delete("/api/tickets/t1/attachments/att-1")
    # F2-B3: DELETE attachment risponde 204 anche se cleanup file_source
    # secondario fallisce (best-effort — vedi logger.warning nel router).
    assert response.status_code == 204
    mock_tdb.delete_attachment.assert_called_once_with("att-1")
