"""One-shot generator for .claude/io-refactor/allowlist-baseline.yaml.

Sweep dei pattern filesystem nel backend + workers + scripts,
classifica per fase di migrazione (vedi refactor_io_storage_ram_only.md),
emette YAML che il contract test consumera' come allowlist.

Eseguire da `backend/` cwd:
    python3 scripts/_gen_io_allowlist.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

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
    # Match anche `open(var, "w")` (oltre al gia' coperto
    # `open("file", "w")`). Punto 3 follow-up (2026-05-26).
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


# Buffer hints: se la stessa riga matcha uno di questi, il pattern
# df_to_xxx scrive su buffer in-memory (BytesIO/StringIO) e NON e' un
# filesystem write. Il regex coarse `\.to_csv\(` non distingue da solo;
# i hint sotto restringono il match per evitare false positive.
_BUFFER_HINTS = re.compile(
    r"\b(BytesIO|StringIO|io\.BytesIO|io\.StringIO)\b|"
    r"\.to_(csv|excel|json|parquet)\([^)]*?(buf\b|buffer\b)"
)


def _is_buffer_write(pattern_name: str, line: str) -> bool:
    """True se il match df_to_xxx scrive su buffer RAM, non su path.

    Heuristics:
      1. `BytesIO/StringIO` literal sulla stessa riga.
      2. Primo arg di `to_xxx(buf, ...)` e' una variabile chiamata
         `buf*`/`buffer*`.
      3. Primo arg e' una kwarg (`to_xxx(index=False, ...)`):
         `df.to_csv()`/`to_json()` senza positional restituisce
         string/dict, niente disk.
      4. Empty parens (`to_json()` / `to_xxx()`): returns string/dict,
         niente disk.
    """
    if pattern_name not in (
        "df_to_csv_path", "df_to_excel_path",
        "df_to_json_path", "df_to_parquet_path",
    ):
        return False
    if _BUFFER_HINTS.search(line):
        return True
    # `df.to_xxx(kwarg=...)` -> no positional, returns value not write.
    if re.search(
        r"\.to_(csv|excel|json|parquet)\(\s*[A-Za-z_]\w*\s*=",
        line,
    ):
        return True
    # `df.to_xxx()` (empty parens) -> returns value, niente disk.
    if re.search(r"\.to_(csv|excel|json|parquet)\(\s*\)", line):
        return True
    return False

# Mappa file -> fase di migrazione (refactor_io_storage_ram_only.md).
PHASE_MAP: dict[str, str] = {
    "arricchisci/api_connection_rest_file.py": "Phase4-rest-file-enrichment",
    "arricchisci/api_connection_support.py": "Phase4-rest-file-enrichment",
    "files/upload.py": "Phase4-upload-file-source",
    "files/source_blob.py": "Phase4-upload-file-source",
    "files/source_loading.py": "Phase4-upload-file-source",
    "files/source_read.py": "Phase4-upload-file-source",
    "files/preview_temp.py": "Phase4-upload-file-source",
    "files/multi_layer_upload.py": "Phase4-upload-file-source",
    "dataframes/frame_io.py": "Phase1-dataframe-boundary",
    # geospatial_io.py: bytes-export e' migrato (geospatial_dataframe_to_bytes
    # ora supporta GeoJSON/GPKG via pyogrio bytes); restano solo i path-export
    # legacy (`_salva_formato_geo`, `_salva_shapefile_zippato`, gdf.to_file)
    # che andranno in Phase5.
    "dataframes/geospatial_io.py": "Phase5-legacy-path-export",
    "results/result_reader.py": "Phase5-results-download",
    "results/pipeline_result.py": "Phase5-results-download",
    "results/blob_writer.py": "Phase5-results-download",
    "artifacts/resolver.py": "Phase5-results-download",
    "scheduler/run_archive_store.py": "Phase5-scheduler-archive",
    "scheduler/router.py": "Phase5-scheduler-archive",
    "imdf/": "Phase6-imdf",
    "workers/tasks/imdf_publish_task.py": "Phase6-imdf",
    "db/query_executor.py": "Phase5-export-csv-xlsx",
    "db/query_streaming.py": "Phase5-export-csv-xlsx",
    "features/features_service.py": "Phase5-export-csv-xlsx",
    "intermediate/artifact_writer.py": "Phase5-export-csv-xlsx",
    "workers/tasks/carica_task.py": "Phase5-export-csv-xlsx",
    "workers/tasks/enrichment_task.py": "Phase4-rest-file-enrichment",
    "datasources/local.py": "Phase8-datasource-local-dev-only",
    "scripts/backup_db.py": "Phase7-backup-restore",
    "scripts/test_restore.py": "Phase7-backup-restore",
    # Utility scripts one-off (manifest export, E2E checks): non sono
    # application code, restano path-based by design.
    "scripts/export_openapi_manifest.py": "Phase-scripts-utility",
    "scripts/imdf_real_e2e_check.py": "Phase-scripts-utility",
}


# Pattern-aware override: i pattern di EXPORT path-based (CSV/XLSX/
# Parquet/to_file/to_json) sono sempre Phase5 indipendentemente dal
# file in cui vivono, perche' la migrazione richiede ridisegnare il
# flow `payload -> bytes -> storage.save` (vedi documento Fase 5).
PATTERN_PHASE_OVERRIDES: dict[str, str] = {
    "df_to_csv_path": "Phase5-export-csv-xlsx",
    "df_to_excel_path": "Phase5-export-csv-xlsx",
    "df_to_parquet_path": "Phase5-export-csv-xlsx",
    "df_to_file_path": "Phase5-legacy-path-export",
    "df_to_json_path": "Phase5-legacy-path-export",
}


# `legitimate-cache`: callsite che operano su una cache locale deliberata
# (REST connector file_source cache, scheduler run archive, path-only
# adapter per librerie che richiedono path). Non sono "leak" di disk
# write: il disk e' il destination (cache persistente) o il sorgente
# (path-only library) per design. Non rientrano nel target "lista vuota"
# del refactor RAM-only.
_LEGITIMATE_CACHE_FILES: tuple[str, ...] = (
    "app/core/files/source_loading.py",
    "app/core/results/blob_writer.py",  # storage.upload(path) streaming
    # Path-only adapter helpers (callsiti che richiedono path per
    # library esterne che non hanno bytes API).
    "app/core/eseguibili/storage.py",  # eseguibili download from S3
    "app/core/resources/loaders/result_blob.py",  # result blob disk materialize
    # IMDF cache infrastructure: persistent local bundle dir + S3 backing
    # store. Pattern cache-with-hydration (vedi `imdf.storage.ensure_local_bundle`):
    # on-disk e' la representation canonica per i bundle IMDF (consumati da
    # endpoint di download/render), non una materializzazione transitoria.
    "app/core/imdf/",
    # Punto 3 cleanup (2026-05-25): rimossi `app/core/results/temp_paths.py`
    # e `app/core/files/service_deps.py` — tombstonati i symbol path-only
    # (`get_temp_output_path`, `DefaultFileSystem.{write_text,extract_zip,write_binary}`).
)

# Data plane endpoints (Punto 3 follow-up 2026-05-26): datasource
# user-configured destinations (filesystem locale / SMB share / SFTP
# server). NON sono temp/cache interni — sono il punto in cui la
# pipeline scrive l'output di un task `CARICA` verso lo storage che
# l'utente ha configurato. Contratto RAM-first NON si applica.
_DATAPLANE_FILES: tuple[str, ...] = (
    "app/core/datasources/local.py",
    "app/core/datasources/sftp.py",
)


_SCRIPTS_PREFIX = "scripts/"


def _status_for(filepath: str) -> str:
    norm = filepath.replace("\\", "/")
    if norm.startswith(_SCRIPTS_PREFIX) or f"/{_SCRIPTS_PREFIX}" in norm:
        return "scripts-utility"
    for legit in _LEGITIMATE_CACHE_FILES:
        if legit in norm:
            return "legitimate-cache"
    return "active-legacy"


def phase_for(filepath: str, pattern: str = "") -> str:
    if pattern and pattern in PATTERN_PHASE_OVERRIDES:
        return PATTERN_PHASE_OVERRIDES[pattern]
    norm = filepath.replace("\\", "/")
    for key, phase in PHASE_MAP.items():
        if key in norm:
            return phase
    return "unassigned"


# BOUNDARY FROZEN (2026-05-25, refactor_io_storage_ram_only.md §"Boundary
# congelato"): le directory/file qui sotto sono dichiarati by-design come
# detentori della capability filesystem. Il report
# `test_report_inside_boundary_frozen` enforce il count per-file: una
# modifica deliberata richiede aggiornamento contestuale del doc + del
# frozen-snapshot nel test.
_BOUNDARY_DIRS = (
    # `app/core/io/`: layer materializer + helper geospaziali. Casa di
    # `temporary_path_from_bytes` (escape hatch tracciato) e dei wrapper
    # tempdir per librerie path-only (driver legacy GDAL).
    "app/core/io/",
    # `app/core/storage/`: storage backend abstraction. Manipola path
    # nei flussi che richiedono streaming upload/download (multipart S3).
    "app/core/storage/",
    # `app/core/imdf/`: OGC IMDF format-as-directory subsystem. Bundle
    # `.gdb` letto da pyogrio via filesystem; cache hydration on-demand
    # per publish/validate-publish. IMDF-5+ migrera' publish a
    # storage-direct quando il design pyogrio-from-zip sara' chiuso.
    "app/core/imdf/",
)
# File singoli che fanno parte del boundary I/O ma non vivono in
# `app/core/io/`. Sono primitive che esistono PER fornire la capability
# filesystem al resto del codice in modo controllato.
_BOUNDARY_FILES = (
    # ResultBlobLoader: post-Phase5 supporta solo mode="bytes"
    # (path-mode rimosso). Resta nel boundary per coerenza storica
    # (era il punto di materializzazione tempfile pre-Phase5) e come
    # ancora per la classification legitimate-cache.
    "app/core/resources/loaders/result_blob.py",
    # Punto 3 cleanup (2026-05-25): rimossi
    #   - `app/utils/zip_safe.py` (safe_extractall + safe_extract_zip_path
    #     tombstonati; restano validate_zip_bomb_limits +
    #     pick_zip_member_by_extension, entrambi RAM-only)
    #   - `app/core/results/temp_paths.py` (tutti i symbol path-only
    #     tombstonati: get_temp_output_path, make_path_only_adapter_dir,
    #     ecc. Modulo svuotato)
    #   - `app/core/files/service_deps.py` (DefaultFileSystem.extract_zip
    #     e write_text tombstonati; write_binary resta ma il regex non
    #     matcha `open(path, 'wb')` con path variable)
    # Post-QUERY-1: `blob_writer.py` rimosso dal boundary set.
    # `save_result_blob(path)` tombstoned; tutti i writer canonical
    # vanno via `save_result_blob_from_bytes` (RAM-first) o
    # `StreamingResultBlobWriter` (S3 multipart real-time, no
    # filesystem callsite — boto3 client diretto).
)
# Top-level roots di codice escluso dal sweep applicativo: gli script
# one-off (`backup_db`, `test_restore`, `export_openapi_manifest`,
# `imdf_real_e2e_check`) sono utility — non application code — e
# possono fare filesystem I/O liberamente.
_EXCLUDED_ROOTS = ("scripts",)


def _is_boundary(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    if any(norm.startswith(p) for p in _BOUNDARY_DIRS):
        return True
    if any(norm.endswith(f) or f"/{f}" in norm for f in _BOUNDARY_FILES):
        return True
    # Data plane endpoints (user-configured destinations) sono fuori
    # contratto RAM-first.
    return any(norm.endswith(f) or f"/{f}" in norm for f in _DATAPLANE_FILES)


def scan() -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    roots = tuple(r for r in ("app", "workers", "scripts") if r not in _EXCLUDED_ROOTS)
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                # Skip lo script generator stesso e i suoi prodotti.
                if fname == "_gen_io_allowlist.py":
                    continue
                path = os.path.join(dirpath, fname)
                # Allinea il sweep al contract test: i moduli del boundary
                # `app/core/io/` e `app/core/storage/` sono autorizzati a
                # fare disk I/O; non devono apparire nella allowlist.
                if _is_boundary(path):
                    continue
                try:
                    with open(path, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            stripped = line.lstrip()
                            if stripped.startswith("#"):
                                continue
                            if stripped.startswith('"""') or stripped.startswith("'''"):
                                continue
                            for pname, pre in PATTERNS:
                                if pre.search(line):
                                    if _is_buffer_write(pname, line):
                                        break
                                    findings.append((path, i, pname, line.strip()[:100]))
                                    break
                except (UnicodeDecodeError, OSError):
                    continue
    return findings


def main() -> None:
    findings = scan()

    by_pattern: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    for f in findings:
        by_pattern[f[2]] = by_pattern.get(f[2], 0) + 1
        p = phase_for(f[0], f[2])
        by_phase[p] = by_phase.get(p, 0) + 1

    print(f"TOTAL findings: {len(findings)}\n")
    print("BY PATTERN:")
    for k, v in sorted(by_pattern.items(), key=lambda x: -x[1]):
        print(f"  {k:35} {v}")
    print("\nBY PHASE:")
    for k, v in sorted(by_phase.items(), key=lambda x: -x[1]):
        print(f"  {k:42} {v}")

    out_path = Path("../.claude/io-refactor/allowlist-baseline.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "metadata": {
            "generated_at": "2026-05-25",
            "baseline_total": len(findings),
            "by_pattern": dict(sorted(by_pattern.items(), key=lambda x: -x[1])),
            "by_phase": dict(sorted(by_phase.items(), key=lambda x: -x[1])),
            "note": (
                "Auto-generated dal sweep filesystem-pattern. Aggiungere "
                "motivo/target/replacement quando si migra. Il contract "
                "test legge questa lista come allowlist: NUOVI usi fuori "
                "lista falliscono, ESISTENTI sono tollerati con motivo "
                "esplicito di fase di migrazione."
            ),
            "policy": (
                "Decrementale: ogni migrazione che chiude un callsite "
                "DEVE rimuoverlo dalla allowlist. A fine refactor la "
                "lista deve essere vuota (criterio in "
                "refactor_io_storage_ram_only.md sezione 'Criterio di "
                "fine refactor')."
            ),
        },
        "allowed_callsites": [
            {
                "file": f[0].replace("\\", "/"),
                "line": f[1],
                "pattern": f[2],
                "snippet": f[3],
                "phase": phase_for(f[0], f[2]),
                "status": _status_for(f[0]),
            }
            for f in findings
        ],
    }
    out_path.write_text(
        yaml.dump(data, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    print(f"\nAllowlist written: {out_path}")


if __name__ == "__main__":
    main()
