"""Contract test — no NEW direct filesystem writes outside `app.core.io`.

Strategia (Fase 0 di `refactor_io_storage_ram_only.md`):

  1. Sweep dei pattern filesystem in `app/` + `workers/` + `scripts/`.
  2. Confronto con la allowlist baseline generata da
     `scripts/_gen_io_allowlist.py` (file
     `.claude/io-refactor/allowlist-baseline.yaml`).
  3. NUOVI usi fuori dalla allowlist -> test fallisce, blocking PR.
  4. Usi gia' presenti nella allowlist sono tollerati come legacy in
     migrazione (status=`active-legacy`).
  5. Allowlist e' decrementale: ogni migrazione che chiude un callsite
     DEVE rimuovere la voce. A fine refactor lista vuota.

Whitelist directories (autorizzate a fare filesystem I/O):
  - `app/core/io/`: il boundary stesso
  - `app/core/storage/`: layer storage (Fase 3)
  - `tests2/`: i test possono creare tmpfile per fixture
  - `scripts/_gen_io_allowlist.py`: lo script generator
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("tempfile_named_temp_file", re.compile(r"tempfile\.NamedTemporaryFile\b")),
    ("tempfile_temp_directory", re.compile(r"tempfile\.TemporaryDirectory\b")),
    ("tempfile_mkdtemp", re.compile(r"tempfile\.mkdtemp\b")),
    ("tempfile_mkstemp", re.compile(r"tempfile\.mkstemp\b")),
    ("tempfile_temporary_file", re.compile(r"tempfile\.TemporaryFile\b")),
    ("zipfile_extractall", re.compile(r"\.extractall\(")),
    ("safe_extractall", re.compile(r"safe_extractall\(|safe_extract_zip_path\(")),
    ("path_write_bytes", re.compile(r"\.write_bytes\(")),
    ("path_write_text", re.compile(r"\.write_text\(")),
    # Match anche `open(var, "w")` / `open(path, 'wb')` (oltre al gia'
    # coperto `open("file", "w")`). Punto 3 follow-up (2026-05-26):
    # il regex precedente richiedeva quote/whitespace come primo char
    # dentro `open(`, perdendo i call con prima variabile (es.
    # `LocalDataSource.write_file` -> `open(full_path, 'wb')`).
    ("open_write_mode", re.compile(r"""open\(\s*[^,)]+,\s*['"][wax]""")),
    ("fileresponse", re.compile(r"FileResponse\(")),
    ("shutil_copy", re.compile(r"shutil\.(copy|copyfile|copy2|copytree)\(")),
    ("shutil_move", re.compile(r"shutil\.move\(")),
    ("df_to_csv_path", re.compile(r"\.to_csv\(")),
    ("df_to_excel_path", re.compile(r"\.to_excel\(")),
    ("df_to_parquet_path", re.compile(r"\.to_parquet\(")),
    ("df_to_file_path", re.compile(r"\.to_file\(")),
    ("df_to_json_path", re.compile(r"\.to_json\(")),
    ("storage_upload_path", re.compile(r"\.upload\([^)]*path")),
    ("storage_download_path", re.compile(r"\.download\([^)]*path")),
)

# Buffer hints: stesso meccanismo del generator script
# (`scripts/_gen_io_allowlist.py`). Skippa i `df.to_xxx(buf)` su buffer
# RAM (BytesIO/StringIO), che sono RAM-only by design.
_BUFFER_HINTS = re.compile(
    r"\b(BytesIO|StringIO|io\.BytesIO|io\.StringIO)\b|"
    r"\.to_(csv|excel|json|parquet)\([^)]*?(buf\b|buffer\b)"
)


def _is_buffer_write(pattern_name: str, line: str) -> bool:
    """True se il match df_to_xxx scrive su buffer RAM, non su path.

    Stessa logica del generator (`scripts/_gen_io_allowlist.py`)."""
    if pattern_name not in (
        "df_to_csv_path", "df_to_excel_path",
        "df_to_json_path", "df_to_parquet_path",
    ):
        return False
    if _BUFFER_HINTS.search(line):
        return True
    if re.search(
        r"\.to_(csv|excel|json|parquet)\(\s*[A-Za-z_]\w*\s*=",
        line,
    ):
        return True
    if re.search(r"\.to_(csv|excel|json|parquet)\(\s*\)", line):
        return True
    return False


# Whitelist: directories autorizzate a fare filesystem I/O sono escluse
# dal sweep (sono il boundary stesso o test/scripts utility).
EXCLUDE_PREFIXES: tuple[str, ...] = (
    "app/core/io/",
    "app/core/storage/",
    "app/core/imdf/",
)

# Boundary files puntuali fuori dalle dir whitelisted. Storia rimozioni:
# - 2026-05-25 (Punto 3): `app/utils/zip_safe.py`, `app/core/results/temp_paths.py`,
#   `app/core/files/service_deps.py` rimossi dall'esclusione (zero callsite
#   filesystem residui dopo tombstone safe_extractall / get_temp_output_path /
#   DefaultFileSystem.{write_text,extract_zip,write_binary}).
# - 2026-05-26 (audit follow-up): `app/core/datasources/local.py` rimosso —
#   la scrittura ora delega a `app.core.io.local_filesystem.write_local_bytes`
#   (data plane boundary, gia' coperto da EXCLUDE_PREFIXES). `sftp.py`
#   resta nello scope per il match sintattico su `self._sftp.open(...,'wb')`
#   (false positive: e' il client paramiko remoto, non filesystem locale).
# DEVE essere allineato con `scripts/_gen_io_allowlist.py` (_BOUNDARY_FILES)
# altrimenti il test segnala "nuovi offender" per findings che il generator
# non ha mai messo nella allowlist.
EXCLUDE_FILES: tuple[str, ...] = (
    "app/core/resources/loaders/result_blob.py",
    "app/core/arricchisci/api_connection_rest_file.py",
    "app/core/results/blob_writer.py",
    # SFTP datasource endpoint: il regex `open_write_mode` matcha
    # `self._sftp.open(...,'wb')` sintatticamente (e' il metodo del
    # client paramiko, NON il built-in open), out-of-contract.
    "app/core/datasources/sftp.py",
    # startup_validation: il regex matcha `path.touch()` come pattern,
    # ma touch() non e' nella lista pattern del gate; tuttavia il path
    # passa per `verify_local_writable` (boundary local_filesystem)
    # che e' gia' coperto da EXCLUDE_PREFIXES. Resta fuori dal set.
)

# Top-level roots esclusi dal sweep (script one-off, non application code).
EXCLUDED_ROOTS: tuple[str, ...] = ("scripts",)


def _backend_root() -> Path:
    """tests2 e' sotto backend/tests2 — risali alla root backend."""
    return Path(__file__).resolve().parents[2]


def _allowlist_path() -> Path:
    """`.claude/io-refactor/allowlist-baseline.yaml` accanto al repo root."""
    return _backend_root().parent / ".claude" / "io-refactor" / "allowlist-baseline.yaml"


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _load_allowlist() -> set[tuple[str, int, str]]:
    """Carica la allowlist YAML come set di (file, line, pattern)."""
    allowlist_path = _allowlist_path()
    if not allowlist_path.exists():
        pytest.skip(
            f"Allowlist baseline non presente ({allowlist_path}). "
            f"Esegui `python scripts/_gen_io_allowlist.py` da backend/."
        )
    raw = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    return {
        (
            _normalize(item["file"]),
            int(item["line"]),
            item["pattern"],
        )
        for item in raw.get("allowed_callsites", [])
    }


def _is_excluded(rel_path: str) -> bool:
    if any(rel_path.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    return any(
        rel_path == f or rel_path.endswith(f"/{f}") for f in EXCLUDE_FILES
    )


def _is_skip_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")


def _scan_findings() -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    backend = _backend_root()
    roots = tuple(
        r for r in ("app", "workers", "scripts") if r not in EXCLUDED_ROOTS
    )
    for root_name in roots:
        root = backend / root_name
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                if fname == "_gen_io_allowlist.py":
                    continue
                abs_path = Path(dirpath) / fname
                rel_path = _normalize(str(abs_path.relative_to(backend)))
                if _is_excluded(rel_path):
                    continue
                try:
                    content = abs_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    if _is_skip_line(line):
                        continue
                    for pname, pre in PATTERNS:
                        if pre.search(line):
                            # La call puo' essere multi-riga: `df.to_csv(` con il
                            # primo arg (il buffer) sulla riga successiva. Includi
                            # la riga dopo nella finestra cosi' il buffer-skip RAM
                            # riconosce `to_csv(... buf)` anche se spezzato.
                            window = line if i >= len(lines) else f"{line} {lines[i]}"
                            if _is_buffer_write(pname, window):
                                break
                            findings.append((rel_path, i, pname, line.strip()[:120]))
                            break
    return findings


def test_no_new_filesystem_writes_outside_io_boundary() -> None:
    """NUOVI usi di pattern filesystem fuori da `app/core/io/` (e
    `app/core/storage/`, futuro) DEVONO essere registrati in
    `.claude/io-refactor/allowlist-baseline.yaml` come legacy in
    migrazione.

    Per chiudere un callsite migrato, rimuovere la riga dalla
    allowlist (oppure regenerare con
    `python scripts/_gen_io_allowlist.py` dopo la migrazione).
    """
    allowlist = _load_allowlist()
    findings = _scan_findings()

    new_offenders = [
        f for f in findings
        if (f[0], f[1], f[2]) not in allowlist
    ]

    if new_offenders:
        lines = "\n".join(
            f"  - {f[0]}:{f[1]} [{f[2]}]  {f[3]}"
            for f in new_offenders
        )
        pytest.fail(
            f"Trovati {len(new_offenders)} NUOVI usi di pattern "
            f"filesystem fuori da `app/core/io/`:\n{lines}\n\n"
            f"Soluzioni:\n"
            f"  1. Migra il callsite a `app.core.io.materialize` o\n"
            f"     equivalente boundary I/O (RAM-first).\n"
            f"  2. Se la migrazione e' bloccata, registra il "
            f"callsite nella allowlist con motivo + fase via\n"
            f"     `python scripts/_gen_io_allowlist.py`.\n"
            f"  3. Allowlist e' decrementale: ogni voce ha una "
            f"fase di rimozione documentata.\n"
        )


def test_allowlist_is_consumable_yaml() -> None:
    """Smoke check: allowlist baseline esiste ed e' YAML valido con
    la shape attesa."""
    allowlist = _load_allowlist()
    assert isinstance(allowlist, set)
    # Ogni voce deve avere file path normalizzato (no backslash).
    for entry in allowlist:
        assert "\\" not in entry[0], (
            f"path con backslash nella allowlist: {entry[0]}"
        )


def test_io_boundary_dir_excluded_from_sweep() -> None:
    """Il modulo `app/core/io/` puo' usare tempfile (e' il punto
    autorizzato). Il sweep deve escluderlo."""
    findings = _scan_findings()
    for finding in findings:
        assert not finding[0].startswith("app/core/io/"), (
            f"Il sweep ha trovato un finding dentro app/core/io/ "
            f"(dovrebbe essere escluso): {finding}"
        )


def _scan_excluded_findings() -> list[tuple[str, int, str, str]]:
    """Sweep DEI SOLI file/dir esclusi dal gate principale.

    Rispetto a `_scan_findings`, NON applica `_is_excluded` ne'
    `EXCLUDED_ROOTS`. Usato dal report informativo per tracciare il
    "disk usage residuo dentro boundary" come metrica separata.
    """
    findings: list[tuple[str, int, str, str]] = []
    backend = _backend_root()
    for root_name in ("app", "workers", "scripts"):
        root = backend / root_name
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs
                if d != "__pycache__" and not d.startswith(".")
            ]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                if fname == "_gen_io_allowlist.py":
                    continue
                abs_path = Path(dirpath) / fname
                rel_path = _normalize(str(abs_path.relative_to(backend)))
                # Includiamo SOLO i file esclusi dal gate principale.
                in_excluded_root = any(
                    rel_path.startswith(f"{r}/") for r in EXCLUDED_ROOTS
                )
                if not (_is_excluded(rel_path) or in_excluded_root):
                    continue
                try:
                    content = abs_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(content.splitlines(), 1):
                    if _is_skip_line(line):
                        continue
                    for pname, pre in PATTERNS:
                        if pre.search(line):
                            if _is_buffer_write(pname, line):
                                break
                            findings.append(
                                (rel_path, i, pname, line.strip()[:120]),
                            )
                            break
    return findings


# BOUNDARY FROZEN (2026-05-25, refactor_io_storage_ram_only.md
# §"Boundary congelato"): snapshot per-file delle entries by-design
# all'interno del boundary I/O. Il test ratchet sotto compara il
# report corrente a questo snapshot; deviazioni in qualunque direzione
# falliscono richiedendo aggiornamento contestuale del doc.
#
# Come aggiornare:
#   1. Modifica deliberata che cambia uno di questi count?
#   2. Aggiorna sia questo dict sia la sezione "Boundary congelato" nel
#      doc con il NUOVO razionale.
#   3. Run `python scripts/_gen_io_allowlist.py` per rigenerare baseline.
#
# Path normalizzato con forward slash; le entries esatte (line/pattern)
# non sono congelate — solo il count per-file. Aggiunte/rimozioni di
# callsite all'interno di un file fanno cambiare il count e devono
# essere intenzionali.
_BOUNDARY_FROZEN_SNAPSHOT: dict[str, int] = {
    # Punto 3 cleanup (2026-05-25): tombstonati interi moduli/metodi
    # con zero caller runtime:
    #   - `app/core/io/materialize.py`: `temporary_path_from_bytes` +
    #     `DiskWriteDeniedError` rimossi (modulo svuotato).
    #   - `app/core/results/temp_paths.py`: `make_path_only_adapter_dir`,
    #     `write_path_only_adapter_bytes`, `cleanup_path_only_adapter`,
    #     `get_temp_output_path` rimossi (modulo svuotato).
    #   - `app/utils/zip_safe.py`: `safe_extractall`,
    #     `safe_extract_zip_path`, `_validate_member`, `ZipSlipError`
    #     rimossi. Restano `validate_zip_bomb_limits` +
    #     `pick_zip_member_by_extension` (entrambi RAM-only).
    #   - `app/core/files/service_deps.py`: `DefaultFileSystem.write_text`,
    #     `DefaultFileSystem.extract_zip` + corrispondenti voci di
    #     `FileSystemProtocol` rimossi.
    # io/geospatial.py post QUERY-1 step 4: rimosso dallo snapshot.
    # `write_shapefile_zip_bytes` ora fully-RAM via `gdal_vsi.vsi_temp_dir`
    # (ctypes adapter su libgdal). Zero filesystem callsite.
    # blob_writer.py post-QUERY-1: `save_result_blob(path)` rimosso
    # (era 1 storage_upload_path). Lo streaming query end-to-end ora
    # passa da StreamingResultBlobWriter (S3 multipart via boto3
    # client diretto, non boundary pattern).
    # IMDF subsystem (post QUERY-1 step 3 tombstone):
    # `loader.py`, `persist.py`, `storage.py` non hanno piu' callsite
    # filesystem. `extract_imdf_zip(path)`, `persist_imdf_bundle_to_disk`,
    # `ensure_local_bundle` tombstonati. I path runtime canonical sono:
    #   - extract_imdf_zip_bytes (RAM, no tmp dir)
    #   - persist_imdf_bundle (storage-direct)
    #   - open_gdb_in_vsimem (ctypes /vsimem, no disco)
    # Scripts one-off tooling (gli unici residui boundary):
    "scripts/backup_db.py": 1,
    "scripts/test_restore.py": 1,
    "scripts/export_openapi_manifest.py": 1,
    # Dev tooling roadmap accoppiamento (2026-06-12): scrive la baseline
    # JSON del grafo import (tests2/shared/config/) via --write-baseline.
    # Eseguito a mano/in lane, mai nel runtime applicativo.
    "scripts/dependency_graph.py": 1,
    # Data plane endpoints (Punto 3 follow-up 2026-05-26: il regex
    # `open_write_mode` ora intercetta anche `open(var, "w")`).
    # `local.py` non e' piu' qui post audit follow-up 2026-05-26:
    # la write delega a `app.core.io.local_filesystem.write_local_bytes`
    # (boundary unico per data plane locale + operational self-test).
    # `sftp.py` resta: e' un false positive (paramiko remoto, non
    # filesystem locale) ma match sintattico del regex.
    # Count = 2 post RAM-B2 (write_chunks + iter_read_chunks, entrambi
    # `self._sftp.open(path, "wb"|"rb")`).
    "app/core/datasources/sftp.py": 2,
    # Local filesystem boundary (audit follow-up 2026-05-26): single
    # entry point per scritture su filesystem locale. I match sono
    # `open(target, "wb")` dentro `write_local_bytes` (1) e
    # `write_local_chunks` (1, RAM-B1 streaming write), by-design.
    "app/core/io/local_filesystem.py": 2,
}


def test_report_inside_boundary_frozen() -> None:
    """Ratchet bidirezionale sul boundary congelato.

    A differenza del precedente `test_report_disk_usage_inside_boundary`
    (informativo, ora rinominato `_print` e mantenuto come print-only),
    questo test enforce che il count per-file matchi esattamente lo
    `_BOUNDARY_FROZEN_SNAPSHOT` dichiarato sopra.

    Il razionale e' nel doc `refactor_io_storage_ram_only.md` sezione
    "Boundary congelato". Modifiche deliberate del count richiedono
    aggiornamento contestuale del doc + dello snapshot qui.
    """
    findings = _scan_excluded_findings()
    by_file: dict[str, int] = {}
    for f in findings:
        by_file[f[0]] = by_file.get(f[0], 0) + 1

    expected = _BOUNDARY_FROZEN_SNAPSHOT
    actual = by_file

    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    diff_counts = sorted(
        f for f in set(expected) & set(actual)
        if expected[f] != actual[f]
    )

    if not (missing or unexpected or diff_counts):
        return  # snapshot matches exactly

    lines = ["Boundary frozen snapshot mismatch."]
    if unexpected:
        lines.append("")
        lines.append(
            "  NUOVI file con disk usage inside-boundary (NON congelati):"
        )
        for f in unexpected:
            lines.append(f"    + {f}: {actual[f]} entries")
        lines.append(
            "    -> Aggiungerli al doc 'Boundary congelato' con razionale,"
        )
        lines.append(
            "       poi aggiungerli a `_BOUNDARY_FROZEN_SNAPSHOT`."
        )
    if missing:
        lines.append("")
        lines.append(
            "  File congelati ma senza disk usage attuale (regressione del freeze):"
        )
        for f in missing:
            lines.append(f"    - {f}: era {expected[f]} entries, ora 0")
        lines.append(
            "    -> Confermare la rimozione nel doc e dropparli dallo snapshot."
        )
    if diff_counts:
        lines.append("")
        lines.append("  Count divergente:")
        for f in diff_counts:
            lines.append(
                f"    {f}: snapshot={expected[f]}, attuale={actual[f]}"
            )
        lines.append(
            "    -> Aggiornare doc + snapshot se la modifica e' deliberata."
        )
    pytest.fail("\n".join(lines))


def test_report_inside_boundary_print() -> None:
    """Report stampato (NON fa fallire il gate): summary per-file dei
    pattern filesystem residui dentro il boundary. Lo enforcement vive
    nel test `test_report_inside_boundary_frozen` sopra; questo resta
    come print utility per il debug post-modifica.

    Esegui:
        pytest tests2/io_boundary/test_no_direct_filesystem_contract.py::test_report_inside_boundary_print -s
    """
    findings = _scan_excluded_findings()
    if not findings:
        print("\n[io_boundary report] zero disk usage inside boundary.")
        return

    by_file: dict[str, list[tuple[int, str]]] = {}
    for f in findings:
        by_file.setdefault(f[0], []).append((f[1], f[2]))

    print(
        f"\n[io_boundary report] {len(findings)} disk usage residui "
        f"dentro boundary, distribuiti su {len(by_file)} file:"
    )
    for fpath in sorted(by_file):
        entries = by_file[fpath]
        patterns = sorted({p for _, p in entries})
        print(f"  {fpath}: {len(entries)} entries, patterns: {patterns}")


def test_no_active_legacy_callsites_remain() -> None:
    """Exit criterion: zero callsite con status `active-legacy`.

    Quando un nuovo callsite viene aggiunto allo sweep e non viene
    classificato come `legitimate-cache` o `scripts-utility` nello
    script generator, questo test fallisce — richiede di:
      1. Decidere se il callsite va MIGRATO (preferito: bytes-first).
      2. Oppure se e' un cache pattern intenzionale (aggiungere a
         `_LEGITIMATE_CACHE_FILES` nel generator con motivazione).

    Anti-regression sul target del refactor RAM-only completato.
    """
    allowlist_path = _allowlist_path()
    if not allowlist_path.exists():
        pytest.skip("Allowlist baseline non presente")
    raw = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    active = [
        e for e in raw.get("allowed_callsites", [])
        if e.get("status") == "active-legacy"
    ]
    if active:
        lines = "\n".join(
            f"  - {e['file']}:{e['line']} [{e['pattern']}] (phase={e['phase']})"
            for e in active
        )
        pytest.fail(
            f"Trovati {len(active)} callsite con status `active-legacy`. "
            f"Tutti i callsite residui devono essere `legitimate-cache` "
            f"o `scripts-utility`:\n{lines}"
        )
