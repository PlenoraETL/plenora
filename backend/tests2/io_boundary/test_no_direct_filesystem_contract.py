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
    ("open_write_mode", re.compile(r"""open\(['"\s][^)]*?,\s*['"][wax]""")),
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
)

# Boundary files puntuali fuori dalle dir whitelisted. `app/utils/zip_safe.py`
# E' l'implementazione di `safe_extractall` / `validate_zip_bomb_limits` /
# `pick_zip_member_by_extension`: meta-circolare richiedere che non chiami
# extractall. DEVE essere allineato con `scripts/_gen_io_allowlist.py`
# (_BOUNDARY_FILES) altrimenti il test segnala "nuovi offender" per
# findings che il generator non ha mai messo nella allowlist.
EXCLUDE_FILES: tuple[str, ...] = (
    "app/utils/zip_safe.py",
    "app/core/results/temp_paths.py",
    "app/core/files/service_deps.py",
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
                for i, line in enumerate(content.splitlines(), 1):
                    if _is_skip_line(line):
                        continue
                    for pname, pre in PATTERNS:
                        if pre.search(line):
                            if _is_buffer_write(pname, line):
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
