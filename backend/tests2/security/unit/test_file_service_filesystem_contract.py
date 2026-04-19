from io import BytesIO
import zipfile

import pytest

from app.services.file_service import DefaultFileSystem


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def _write_zip(path, *entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries:
            zf.writestr(name, content)


def test_default_filesystem_extract_zip_rejects_parent_traversal(tmp_path):
    zip_path = tmp_path / "bad.zip"
    _write_zip(zip_path, ("../evil.txt", "boom"))

    with pytest.raises(ValueError, match="Zip slip"):
        DefaultFileSystem().extract_zip(zip_path, tmp_path / "out")


def test_default_filesystem_extract_zip_rejects_absolute_windows_path(tmp_path):
    zip_path = tmp_path / "bad.zip"
    _write_zip(zip_path, ("C:/evil.txt", "boom"))

    # extract_zip ora delega a safe_extract_zip_path: i path con drive
    # letter Windows triggerano il check dedicato "drive letter rifiutata"
    # (ZipSlipError, sottoclasse di ValueError).
    with pytest.raises(ValueError, match="Zip slip"):
        DefaultFileSystem().extract_zip(zip_path, tmp_path / "out")


def test_default_filesystem_extract_zip_extracts_safe_members(tmp_path):
    zip_path = tmp_path / "ok.zip"
    out_dir = tmp_path / "out"
    _write_zip(zip_path, ("nested/data.txt", "ok"))

    DefaultFileSystem().extract_zip(zip_path, out_dir)

    assert (out_dir / "nested" / "data.txt").read_text(encoding="utf-8") == "ok"
