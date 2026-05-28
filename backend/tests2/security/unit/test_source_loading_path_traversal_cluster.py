"""Cluster 5 contract test: path traversal + superadmin bypass.

Bug originali (audit P0 SECURITY cluster):
- etl-sources P0 #1 (`files/source_loading.py:89-94`): `_expected_filename`
  ritorna `original_filename` raw dal DB. Un record con
  `original_filename = "../../../etc/passwd"` produce un path
  `source_dir / "../../../etc/passwd"` che esce dalla source_dir
  sia in lettura (`target.is_file()`) sia in scrittura
  (`tmp_path.write_bytes(...)`).
- etl-sources P0 #2 (`files/source_loading.py:165-168`):
  `is_superadmin=expected_group_id is None` significa "se nessun
  tenant specificato, leggi come superadmin (no group filter)".
  Bypass cross-tenant se il guard `assert_file_source_group_access`
  viene bypassato per iniezione diretta.

Post-fix:
- `_expected_filename` ritorna `Path(raw).name` (solo basename) +
  fallback "input.bin" su stringa vuota.
- `assert_path_within(target, source_dir)` come backstop pre-read e
  pre-write per defense-in-depth.
- `is_superadmin=False` fisso nel blob loader (rimosso il branching
  sul group_id None).

Pattern Go-friendly: helper `_expected_filename` puro testabile in
isolamento, no infrastruttura. Mappa a Go come
`expectedFilename(meta map[string]any) string`.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


# Path 1: _expected_filename normalizza il basename

_TRAVERSAL_FILENAMES = [
    "../../../etc/passwd",
    "../../secret.csv",
    "/absolute/path/evil.csv",
    "dir/../../../sibling.parquet",
    "C:\\Windows\\System32\\config\\SAM",
    "..\\..\\windows\\file.csv",
    "./normal/../escape.txt",
]


@pytest.mark.parametrize("malicious_filename", _TRAVERSAL_FILENAMES)
def test_expected_filename_strips_directory_components(malicious_filename, tmp_path):
    """_expected_filename DEVE ritornare solo il basename — qualunque
    directory component (relative, absolute, mixed-separator) viene tagliato.
    Anti-regression CVE-grade path traversal."""
    from app.core.files.source_loading import _expected_filename

    row_meta = {"original_filename": malicious_filename}
    result_name = _expected_filename(row_meta)

    # Il risultato deve essere SOLO basename, no directory component
    assert result_name, "Il filename non deve essere vuoto"
    assert "/" not in result_name, (
        f"filename contiene '/' (directory separator): {result_name!r} "
        f"(input: {malicious_filename!r})"
    )
    assert "\\" not in result_name, (
        f"filename contiene '\\\\' (Windows directory separator): "
        f"{result_name!r} (input: {malicious_filename!r})"
    )
    # `..` puro non deve mai sopravvivere come basename
    assert result_name != "..", (
        f"filename normalizzato a '..' (input: {malicious_filename!r})"
    )

    # Verifica path containment
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    final_path = (source_dir / result_name).resolve()
    try:
        final_path.relative_to(source_dir.resolve())
    except ValueError:
        pytest.fail(
            f"Path traversal NON bloccato: {final_path} fuori da {source_dir}. "
            f"Input filename: {malicious_filename!r}, "
            f"normalized: {result_name!r}"
        )


def test_expected_filename_fallback_on_empty():
    """Se `original_filename` e' vuoto/None/missing → fallback `input.bin`,
    non stringa vuota (che produrrebbe `source_dir/` ambiguo)."""
    from app.core.files.source_loading import _expected_filename

    assert _expected_filename({}) == "input.bin"
    assert _expected_filename({"original_filename": None}) == "input.bin"
    assert _expected_filename({"original_filename": ""}) == "input.bin"
    # `..` viene normalizzato a "" da Path.name, deve cadere nel fallback
    assert _expected_filename({"original_filename": ".."}) == "input.bin"


def test_expected_filename_passes_through_safe_basenames():
    """Casi legittimi: il filename rimane invariato."""
    from app.core.files.source_loading import _expected_filename

    assert _expected_filename({"original_filename": "data.csv"}) == "data.csv"
    assert _expected_filename({"original_filename": "report-2026.parquet"}) == "report-2026.parquet"
    assert _expected_filename({"original_filename": "file with spaces.json"}) == "file with spaces.json"


# Path 2: superadmin bypass

def test_storage_blob_loader_called_with_is_superadmin_false(monkeypatch):
    """Il loader blob storage-aware DEVE essere invocato con
    `is_superadmin=False` SEMPRE, anche se `expected_group_id` fosse
    None (caso che il guard upstream dovrebbe bloccare comunque, ma
    backstop defense-in-depth).

    Pre-fix: `is_superadmin=expected_group_id is None` -> True quando
    group e' None -> bypass cross-tenant se guard bypassato."""
    from app.core.files import source_loading

    captured_calls: list[dict] = []

    def _spy_load(*, file_source_id, expected_group_id, is_superadmin):
        captured_calls.append({
            "file_source_id": file_source_id,
            "expected_group_id": expected_group_id,
            "is_superadmin": is_superadmin,
        })
        return b"fake-blob-data"

    monkeypatch.setattr(
        "app.core.resources.file_source_blob_access.load_file_source_blob_bytes",
        _spy_load,
    )

    # Setup: mock get_file_source per ritornare metadata + storage_key
    def _fake_get_file_source(fid, include_data):
        if include_data:
            return {"data": None, "storage_key": "s3://bucket/key", "original_filename": "data.csv"}
        return {"original_filename": "data.csv"}

    # Exercise: chiamata con expected_group_id valorizzato
    source_loading.resolve_file_source_id_bytes(
        file_source_id="file-1",
        expected_group_id="grp-1",
        get_file_source=_fake_get_file_source,
        visibility_check=lambda *a, **kw: True,
    )

    assert len(captured_calls) == 1, "blob loader non e' stato chiamato"
    call = captured_calls[0]
    assert call["is_superadmin"] is False, (
        f"is_superadmin DEVE essere False fisso. Pre-fix era "
        f"`expected_group_id is None` che diventava True quando group=None. "
        f"call={call}"
    )
    assert call["expected_group_id"] == "grp-1"


# Path 3: architectural lint - no raw original_filename access in path building

def test_source_loading_uses_basename_normalizer():
    """Architectural assertion: tutti gli usi di `original_filename` in
    source_loading.py passano attraverso `_expected_filename` (che fa il
    basename strip), MAI accesso diretto al row_meta["original_filename"]
    seguito da concatenation con source_dir."""
    import inspect
    from app.core.files import source_loading

    source = inspect.getsource(source_loading)

    # Verifica che _expected_filename usi Path(...).name
    expected_filename_src = inspect.getsource(source_loading._expected_filename)
    assert ".name" in expected_filename_src or "Path(" in expected_filename_src, (
        "_expected_filename deve usare `Path(raw).name` per stripping "
        "directory component. Anti-regression P0 path traversal."
    )

    # Verifica che assert_path_within sia importato/usato in source_loading
    assert "assert_path_within" in source, (
        "source_loading.py deve invocare assert_path_within come backstop "
        "defense-in-depth contro path traversal residui."
    )

    # Verifica che `is_superadmin=expected_group_id is None` NON sia presente
    # (anti-regression contro re-introduzione del bypass)
    src_no_comments = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "is_superadmin=expected_group_id is None" not in src_no_comments, (
        "Detected bypass pattern `is_superadmin=expected_group_id is None`: "
        "superadmin bypass cross-tenant. Usa `is_superadmin=False` fisso."
    )
