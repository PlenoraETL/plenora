"""Regression contracts for remote datasource safety and stream integrity."""

from __future__ import annotations

import ftplib
import ssl
import threading
import time
from types import MethodType, SimpleNamespace

import pytest

from app.core.datasources.ftp import FTPDataSource
from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourcePermissionError,
    validate_relative_path,
)
from app.core.datasources.models import DataSourceCreate
from app.core.datasources.sftp import SFTPDataSource
from app.core.datasources.sharepoint import SharePointDataSource
import app.core.datasources.ftp as ftp_module
import app.core.datasources.sftp as sftp_module
import app.core.datasources.sharepoint as sharepoint_module


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


@pytest.mark.parametrize(
    "path", ["../outside.csv", "a/../../outside.csv", "/absolute.csv", r"..\outside.csv", "C:/x.csv"],
)
def test_remote_relative_path_rejects_escape_attempts(path):
    with pytest.raises(DataSourcePermissionError):
        validate_relative_path(path)


def test_sftp_model_defaults_to_verified_host_keys():
    data = DataSourceCreate(name="secure", type="sftp")
    assert data.host_key_policy == "reject"


def test_ftp_stream_does_not_lose_end_marker_when_queue_is_full():
    class FakeFTP:
        def retrbinary(self, command, callback, blocksize):
            for chunk in (b"a", b"b", b"c", b"d", b"e"):
                callback(chunk)

    ds = FTPDataSource(BackendConfig("x", "ftp", "", {}))
    ds._ftp = FakeFTP()
    stream = ds.iter_read_chunks("file.bin", chunk_size=1)
    result = []

    def consume():
        result.append(next(stream))
        time.sleep(1.2)
        result.extend(stream)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive()
    assert result == [b"a", b"b", b"c", b"d", b"e"]


def test_ftps_connect_enables_ca_and_hostname_verification(monkeypatch):
    instances = []

    class FakeTLS:
        def __init__(self, *, context):
            self.context = context
            instances.append(self)

        def connect(self, **kwargs): pass
        def login(self, **kwargs): pass
        def prot_p(self): pass
        def set_pasv(self, value): pass
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr(ftplib, "FTP_TLS", FakeTLS)
    ds = FTPDataSource(BackendConfig(
        "x", "ftp", "", {"host": "ftp.example", "tls": True, "username": "u", "password": "p"},
    ))
    ds.connect()
    assert instances[0].context.verify_mode == ssl.CERT_REQUIRED
    assert instances[0].context.check_hostname is True


def test_ftp_failed_login_closes_partial_socket(monkeypatch):
    instances = []

    class FakeFTP:
        def __init__(self): self.closed = False; instances.append(self)
        def connect(self, **kwargs): pass
        def login(self, **kwargs): raise ftplib.error_perm("530 rejected")
        def close(self): self.closed = True

    monkeypatch.setattr(ftp_module.ftplib, "FTP", FakeFTP)
    ds = FTPDataSource(BackendConfig(
        "x", "ftp", "", {"host": "ftp.example", "username": "u", "password": "p"},
    ))
    with pytest.raises(DataSourceConnectionError):
        ds.connect()
    assert instances[0].closed is True


def test_ftp_atomic_replace_preserves_old_file_until_upload_is_complete():
    class FakeFTP:
        def __init__(self): self.files = {"final.csv": b"old"}
        def storbinary(self, command, fileobj):
            path = command.removeprefix("STOR ")
            data = bytearray()
            while chunk := fileobj.read(8192):
                data.extend(chunk)
            self.files[path] = bytes(data)
        def rename(self, source, target):
            if target == "final.csv" and target in self.files:
                raise ftplib.error_perm("553 target exists")
            self.files[target] = self.files.pop(source)
        def size(self, path): return len(self.files[path])
        def delete(self, path):
            if path not in self.files:
                raise ftplib.error_perm("550 missing")
            del self.files[path]

    fake = FakeFTP()
    ds = FTPDataSource(BackendConfig("x", "ftp", "", {}))
    ds._ftp = fake
    ds.write_chunks("final.csv", [b"new", b"-complete"])
    assert fake.files == {"final.csv": b"new-complete"}


def test_sftp_failed_producer_leaves_only_a_cleaned_temporary_file():
    class RemoteFile:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, data): pass

    class FakeSFTP:
        def __init__(self): self.opened = []; self.removed = []
        def open(self, path, mode): self.opened.append(path); return RemoteFile()
        def remove(self, path): self.removed.append(path)

    fake = FakeSFTP()
    ds = SFTPDataSource(BackendConfig("x", "sftp", "", {}))
    ds._sftp = fake

    def broken():
        yield b"prefix"
        raise RuntimeError("serializer failed")

    with pytest.raises(RuntimeError, match="serializer failed"):
        ds.write_chunks("final.csv", broken())
    assert fake.opened[0] != "final.csv"
    assert fake.opened[0].endswith(".part")
    assert fake.removed == [fake.opened[0]]


def test_sftp_open_session_failure_closes_ssh_client(monkeypatch):
    instances = []

    class SSHError(Exception): pass
    class AuthError(SSHError): pass
    class BadHostError(SSHError): pass
    class Client:
        def __init__(self): self.closed = False; instances.append(self)
        def load_system_host_keys(self): pass
        def set_missing_host_key_policy(self, policy): pass
        def connect(self, **kwargs): pass
        def open_sftp(self): raise SSHError("channel failed")
        def close(self): self.closed = True

    fake_paramiko = SimpleNamespace(
        SSHClient=Client,
        SSHException=SSHError,
        AuthenticationException=AuthError,
        BadHostKeyException=BadHostError,
        RejectPolicy=type("RejectPolicy", (), {}),
        AutoAddPolicy=type("AutoAddPolicy", (), {}),
    )
    monkeypatch.setattr(sftp_module, "_import_paramiko", lambda: fake_paramiko)
    ds = SFTPDataSource(BackendConfig(
        "x", "sftp", "", {"host": "sftp.example", "username": "u", "password": "p", "host_key_policy": "reject"},
    ))
    with pytest.raises(DataSourceConnectionError):
        ds.connect()
    assert instances[0].closed is True


def test_sftp_posix_rename_atomically_overwrites_target():
    class RemoteFile:
        def __init__(self, files, path): self.files = files; self.path = path; self.data = bytearray()
        def __enter__(self): return self
        def __exit__(self, *args): self.files[self.path] = bytes(self.data)
        def write(self, data): self.data.extend(data)

    class FakeSFTP:
        def __init__(self): self.files = {"final.csv": b"old"}
        def open(self, path, mode): return RemoteFile(self.files, path)
        def posix_rename(self, source, target): self.files[target] = self.files.pop(source)
        def remove(self, path):
            if path not in self.files: raise IOError("No such file")
            del self.files[path]

    fake = FakeSFTP()
    ds = SFTPDataSource(BackendConfig("x", "sftp", "", {}))
    ds._sftp = fake
    ds.write_chunks("final.csv", [b"new"])
    assert fake.files == {"final.csv": b"new"}


def test_sharepoint_root_subfolder_is_used_by_url_operations():
    ds = SharePointDataSource(BackendConfig(
        "x", "sharepoint", "Library/base/sub", {"site_url": "https://tenant/sites/site"},
    ))
    assert ds._server_relative_url("file.xlsx") == "/sites/site/Library/base/sub/file.xlsx"


def test_sharepoint_failed_auth_closes_every_retry_context(monkeypatch):
    contexts = []

    class Session:
        def __init__(self): self.closed = False
        def close(self): self.closed = True

    class Context:
        def __init__(self, site_url):
            self.web = object()
            self.session = Session()
            self.authentication_context = SimpleNamespace(tenant=None)
            contexts.append(self)
        def with_credentials(self, credentials): return self
        def load(self, obj): pass
        def execute_query(self): raise OSError("temporary reset")

    monkeypatch.setattr(
        sharepoint_module,
        "_import_office365",
        lambda: (lambda client_id, secret: object(), Context),
    )
    ds = SharePointDataSource(BackendConfig(
        "x", "sharepoint", "Library", {
            "site_url": "https://tenant/sites/site", "client_id": "id", "client_secret": "secret",
        },
    ))
    with pytest.raises(DataSourceConnectionError):
        ds.connect()
    assert len(contexts) == 3
    assert all(ctx.session.closed for ctx in contexts)


def test_sharepoint_midstream_failure_never_restarts_from_byte_zero():
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield b"prefix"
            raise OSError("connection reset")

    class Session:
        def get(self, *args, **kwargs): return Response()

    class Context:
        def pending_request(self): return SimpleNamespace(_session=Session())

    ds = SharePointDataSource(BackendConfig(
        "x", "sharepoint", "Library", {"site_url": "https://tenant/sites/site"},
    ))
    ds._ctx = Context()
    fallback_called = []
    ds._fallback_download_then_slice = MethodType(
        lambda self, path, chunk_size: fallback_called.append(path) or iter([b"full"]), ds,
    )
    stream = ds.iter_read_chunks("file.bin", chunk_size=4)
    assert next(stream) == b"prefix"
    with pytest.raises(DataSourceConnectionError, match="interrotto"):
        next(stream)
    assert fallback_called == []


def test_sharepoint_promote_rolls_complete_temp_over_existing_target():
    files = {
        "/sites/site/Library/final.csv": b"old",
        "/sites/site/Library/.temp.part": b"new",
    }

    class FileRef:
        def __init__(self, url): self.url = url
        def rename(self, new_name):
            new_url = f"{self.url.rsplit('/', 1)[0]}/{new_name}"
            files[new_url] = files.pop(self.url)
            self.url = new_url
            return self
        def delete_object(self): files.pop(self.url)

    class Web:
        def get_file_by_server_relative_url(self, url):
            if url not in files: raise Exception("404 not found")
            return FileRef(url)

    class Context:
        web = Web()
        def load(self, obj): pass
        def execute_query(self): pass

    ds = SharePointDataSource(BackendConfig(
        "x", "sharepoint", "Library", {"site_url": "https://tenant/sites/site"},
    ))
    ds._ctx = Context()
    ds._promote_temp_file("/sites/site/Library/.temp.part", "final.csv")
    assert files == {"/sites/site/Library/final.csv": b"new"}
