"""Parser del catalogo dei test e2e.

Legge i test (da `test-e2e/catalog/*.yaml` se presente, altrimenti
fallback su `test-e2e/test-e2e.md`) e li classifica nelle celle delle
matrici definite in `matrices.py`.

Il fallback markdown e' temporaneo: serve finche' Agent A non ha
finito di splittare il catalogo in YAML. La logica di classificazione
e' la stessa per entrambi i formati: si basa su tag espliciti +
euristiche su titolo/asserzione/dataset.

Output: dict[matrix_name][cell_key] -> list[TestRef]
dove cell_key = "row__col" e TestRef e' una dataclass con id+status+title.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from matrices import ALL_MATRICES, Matrix


# Path canonici (relativi alla root di test-e2e/, calcolata da __file__).
_HERE = Path(__file__).resolve().parent
_TEST_E2E_ROOT = _HERE.parent
_CATALOG_DIR = _TEST_E2E_ROOT / "catalog"
_MARKDOWN_FALLBACK = _TEST_E2E_ROOT / "test-e2e.md"


# ---------------------------------------------------------------------------
# Modello: un test estratto dal catalogo
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TestRef:
    """Reference a un test estratto dal catalogo.

    `tags` e' la lista esplicita (se presente) o quella euristica.
    `text_blob` e' tutto il testo rilevante (titolo + asserzione +
    dataset + steps) usato per la classificazione euristica.
    """

    id: str
    area: str
    status: str  # TODO | WIP | DONE | SKIP
    title: str
    tags: Tuple[str, ...]
    text_blob: str

    def has_tag(self, tag: str) -> bool:
        return tag.lower() in (t.lower() for t in self.tags)

    def matches(self, *needles: str) -> bool:
        """True se TUTTI i needle compaiono (case-insensitive) nel blob."""
        blob = self.text_blob.lower()
        return all(n.lower() in blob for n in needles)

    def matches_any(self, *needles: str) -> bool:
        """True se ALMENO UNO dei needle compare nel blob."""
        blob = self.text_blob.lower()
        return any(n.lower() in blob for n in needles)


# ---------------------------------------------------------------------------
# Loader: catalog YAML (se esiste) o markdown fallback
# ---------------------------------------------------------------------------
def load_catalog() -> Tuple[List[TestRef], str]:
    """Carica il catalogo dei test.

    Ritorna (tests, source) dove `source` e' "yaml" o "markdown".
    Se la dir catalog/ contiene almeno un .yaml diverso da `_schema.yaml`
    e PyYAML e' disponibile, usa quello. Altrimenti fallback su markdown.
    """
    yaml_files = sorted(
        f for f in _CATALOG_DIR.glob("*.yaml") if f.name != "_schema.yaml"
    )
    if yaml_files:
        try:
            tests = _load_yaml_catalog(yaml_files)
            return tests, "yaml"
        except _YamlUnavailable:
            # PyYAML non installato: fallback silenzioso al markdown.
            pass
    tests = _load_markdown_catalog(_MARKDOWN_FALLBACK)
    return tests, "markdown"


class _YamlUnavailable(RuntimeError):
    pass


def _load_yaml_catalog(paths: Iterable[Path]) -> List[TestRef]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise _YamlUnavailable(str(exc)) from exc

    # Preferisci il C-loader (libyaml) quando disponibile: il pure-python
    # loader ha un bug noto su Python 3.14 + PyYAML 6.0.3 (state corruption
    # su parsing successivi multipli, manifest TypeError sulle scanner ops).
    Loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader

    tests: List[TestRef] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.load(fh, Loader=Loader) or {}
        area = str(doc.get("area", path.stem))
        for raw in doc.get("tests", []) or []:
            tid = str(raw.get("id", "")).strip()
            if not tid:
                continue
            title = str(raw.get("title", "")).strip()
            status = str(raw.get("status", "TODO")).strip().upper() or "TODO"
            tags = tuple(
                str(t).strip().lower() for t in (raw.get("tags") or []) if str(t).strip()
            )
            blob_parts = [
                title,
                str(raw.get("assertion", "")),
                str(raw.get("steps", "")),
                str(raw.get("dataset", "")),
            ]
            blob = "\n".join(p for p in blob_parts if p)
            tests.append(
                TestRef(
                    id=tid,
                    area=area.lower(),
                    status=status,
                    title=title,
                    tags=tags,
                    text_blob=blob,
                )
            )
    tests.sort(key=lambda t: t.id)
    return tests


# Regex per il fallback markdown.
_RE_HEADER = re.compile(r"^###\s+([A-Z]+-\d+)\s*[—-]\s*(.+?)\s*$")
_RE_FIELD = re.compile(r"^-\s+\*\*([^*]+)\*\*:\s*(.*?)\s*$")
_RE_SECTION = re.compile(r"^##\s+([A-Z]+)\b")


def _load_markdown_catalog(path: Path) -> List[TestRef]:
    if not path.exists():
        return []
    tests: List[TestRef] = []
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()

    current_id: Optional[str] = None
    current_title: str = ""
    current_area: str = ""
    current_status: str = "TODO"
    current_blob: List[str] = []

    def _flush() -> None:
        nonlocal current_id, current_title, current_area, current_status, current_blob
        if current_id and current_id != "XXX-NNN":
            blob = "\n".join([current_title, *current_blob])
            tests.append(
                TestRef(
                    id=current_id,
                    area=current_area.lower(),
                    status=current_status,
                    title=current_title,
                    tags=(),  # markdown non ha tag espliciti
                    text_blob=blob,
                )
            )
        current_id = None
        current_title = ""
        current_status = "TODO"
        current_blob = []

    section_area = ""
    for raw in lines:
        line = raw.rstrip("\n")
        sec = _RE_SECTION.match(line)
        if sec:
            section_area = sec.group(1)
            continue

        hdr = _RE_HEADER.match(line)
        if hdr:
            _flush()
            current_id = hdr.group(1)
            current_title = hdr.group(2)
            current_area = current_id.split("-", 1)[0] if "-" in current_id else section_area
            continue

        if current_id is None:
            continue

        fld = _RE_FIELD.match(line)
        if fld:
            key = fld.group(1).strip().lower()
            val = fld.group(2).strip()
            if key in ("stato", "status"):
                if val:
                    current_status = val.upper()
            current_blob.append(f"{key}: {val}")
        elif line.strip():
            current_blob.append(line.strip())

    _flush()
    tests.sort(key=lambda t: t.id)
    return tests


# ---------------------------------------------------------------------------
# Classifier — mappa test -> celle delle matrici
# ---------------------------------------------------------------------------
# Vocabolario: parole chiave -> token canonico usato nelle righe della
# matrice. La chiave e' lowercase, il valore e' uno dei row labels.
_SOURCE_KEYWORDS: dict[str, str] = {
    "postgres": "postgres",
    "postgis": "postgres",
    "psql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sql server": "sqlserver",
    "sqlserver": "sqlserver",
    "mssql": "sqlserver",
    "oracle": "oracle",
    "sdo_geometry": "oracle",
    "sqlite": "sqlite",
    " csv": "csv",
    "csv ": "csv",
    "xlsx": "xlsx",
    " xls ": "xls",
    ".xls ": "xls",
    "ndjson": "ndjson",
    "geojson": "geojson",
    "shapefile": "shp",
    " shp": "shp",
    "geopackage": "gpkg",
    " gpkg": "gpkg",
    "parquet": "parquet",
    " ftp": "ftp",
    "ftp:": "ftp",
    " sftp": "sftp",
    "sftp:": "sftp",
    "sharepoint": "sharepoint",
    "minio": "s3",
    " s3": "s3",
    "s3:": "s3",
    "s3/": "s3",
    "azure": "azure_blob",
}

_REST_AUTH_KEYWORDS: dict[str, str] = {
    "api_key in query": "api_key_query",
    "api_key in querystring": "api_key_query",
    "key_location=query": "api_key_query",
    "api_key": "api_key_header",  # default header se non specificato
    "key_location=header": "api_key_header",
    "x-api-key": "api_key_header",
    "bearer": "bearer",
    "basic auth": "basic",
    "basic-auth": "basic",
    "auth_type=basic": "basic",
    "client_credentials": "oauth2_cc",
    "oauth2_cc": "oauth2_cc",
    "oauth2 cc": "oauth2_cc",
    "oauth2 client": "oauth2_cc",
    "oauth2_client_credentials": "oauth2_cc",
    "grant_type=password": "oauth2_password",
    "oauth2 password": "oauth2_password",
    "password grant": "oauth2_password",
    "arcgis_token": "arcgis_token",
    "generatetoken": "arcgis_token",
    "ssh key": "ssh_key",
    "ssh-key": "ssh_key",
    "key-based": "ssh_key",
    " iam": "iam",
    "instance role": "iam",
}

_PAGINATION_KEYWORDS: dict[str, str] = {
    "offset": "offset",
    "page+size": "page_size",
    "page_size": "page_size",
    "page&size": "page_size",
    "cursor": "cursor",
    "link header": "link_header",
    "link_header": "link_header",
    "no_meta": "no_meta",
    "no meta": "no_meta",
}

_PAGINATION_SHAPE_KEYWORDS: dict[str, str] = {
    "object_root": "object_root",
    "object root": "object_root",
    "array_root": "array_root",
    "array root": "array_root",
    "nested": "nested_path",
    "json_path": "nested_path",
    "featurecollection": "feature_collection",
    "feature collection": "feature_collection",
    "arcgis": "arcgis_query",
    "featureserver": "arcgis_query",
}

_ERROR_KEYWORDS: dict[str, str] = {
    "401": "401",
    "403": "403",
    "404": "404",
    "422": "422",
    "429": "429",
    "500": "500",
    "503": "503",
    "malformed_json": "malformed_json",
    "malformed json": "malformed_json",
    "timeout": "timeout",
    "redirect": "redirect_loop",
}

_DATASET_BLOCK_KEYWORDS: dict[str, str] = {
    "edge": "edge",
    "unicode": "edge",
    "emoji": "edge",
    "o'brien": "edge",
    "bom": "edge",
    "utf-8 bom": "edge",
    "formul": "edge",  # formula / formule
    "null": "null",
    "10k": "volume",
    "10000": "volume",
    "100k": "volume",
    "1k": "volume",
    "volume": "volume",
    "streaming": "streaming",
    "ndjson": "streaming",
    "line-by-line": "streaming",
    "chunk": "streaming",
}


def _detect_sources(test: TestRef) -> set[str]:
    """Restituisce l'insieme di sorgenti coinvolte nel test."""
    found: set[str] = set()
    blob = (" " + test.text_blob.lower() + " ").replace("\n", " ")
    for kw, token in _SOURCE_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    for tag in test.tags:
        t = tag.lower()
        if t in {
            "postgres", "mysql", "sqlserver", "oracle", "sqlite",
            "csv", "xlsx", "xls", "json", "ndjson", "geojson",
            "shp", "gpkg", "parquet", "ftp", "sftp", "sharepoint",
            "s3", "azure_blob",
        }:
            found.add(t)
    # REST sotto-tipi
    if any(t in blob for t in (" rest", "fake-rest", "api_connection", "/data/", "endpoint")):
        # sotto-classifica per pattern di auth
        if "api_key" in blob or "x-api-key" in blob or "api key" in blob:
            found.add("rest_api_key")
        if "bearer" in blob:
            found.add("rest_bearer")
        if "client_credentials" in blob or "oauth2_cc" in blob:
            found.add("rest_oauth2_cc")
        if "password grant" in blob or "grant_type=password" in blob:
            found.add("rest_oauth2_pwd")
        if "auth_type=basic" in blob or "basic auth" in blob or "basic-auth" in blob:
            found.add("rest_basic")
        if "arcgis" in blob or "generatetoken" in blob:
            found.add("rest_arcgis")
    return found


def _detect_dataset_block(test: TestRef) -> set[str]:
    blob = test.text_blob.lower()
    found: set[str] = set()
    for kw, token in _DATASET_BLOCK_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    if not found:
        # default: i test "Estrai" senza altri marker -> base happy path.
        if test.area in {"ext", "load", "man", "enr"} or "estra" in blob:
            found.add("base")
    return found


def _detect_targets(test: TestRef) -> set[str]:
    """Per i test di LOAD: estrae la destinazione."""
    blob = test.text_blob.lower()
    found: set[str] = set()
    if "rest sink" in blob or "/sink/" in blob or "sink batch" in blob or "rest_sink" in blob:
        found.add("rest_sink")
    for kw in ("postgres", "mysql", "sqlserver", "oracle", "sqlite",
              "csv", "xlsx", "json", "geojson", "shp", "parquet"):
        if f"su {kw}" in blob or f"on {kw}" in blob or f"target {kw}" in blob \
                or f"scrivi {kw}" in blob or f"write {kw}" in blob \
                or f".{kw}" in blob or f"/{kw}" in blob:
            found.add(kw)
    if "minio" in blob or " s3" in blob or "s3/" in blob or "s3:" in blob:
        found.add("s3")
    return found


def _detect_auth_pattern(test: TestRef) -> set[str]:
    blob = test.text_blob.lower()
    found: set[str] = set()
    for kw, token in _REST_AUTH_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    return found


def _detect_pagination_style(test: TestRef) -> set[str]:
    blob = test.text_blob.lower()
    found: set[str] = set()
    for kw, token in _PAGINATION_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    if "non paginat" in blob:
        found.add("none")
    return found


def _detect_pagination_shape(test: TestRef) -> set[str]:
    blob = test.text_blob.lower()
    found: set[str] = set()
    for kw, token in _PAGINATION_SHAPE_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    return found


def _detect_error_class(test: TestRef) -> set[str]:
    blob = test.text_blob.lower()
    found: set[str] = set()
    for kw, token in _ERROR_KEYWORDS.items():
        if kw in blob:
            found.add(token)
    return found


# ---------------------------------------------------------------------------
# Classification entry point
# ---------------------------------------------------------------------------
def cell_key(row: str, col: str) -> str:
    return f"{row}__{col}"


def classify(tests: List[TestRef]) -> Dict[str, Dict[str, List[TestRef]]]:
    """Classifica i test nelle celle di tutte le matrici.

    Ritorna: {matrix_name: {cell_key: [TestRef, ...]}}
    Ogni cella contiene la lista dei test che la coprono, in ordine
    deterministico (sort by test.id).
    """
    out: Dict[str, Dict[str, List[TestRef]]] = {}
    for matrix in ALL_MATRICES:
        out[matrix.name] = {cell_key(r, c): [] for r in matrix.rows for c in matrix.cols}

    for test in tests:
        sources = _detect_sources(test)
        targets = _detect_targets(test)
        dsblocks = _detect_dataset_block(test)
        authpats = _detect_auth_pattern(test)
        pgstyles = _detect_pagination_style(test)
        pgshapes = _detect_pagination_shape(test)
        errors = _detect_error_class(test)

        # --- EXTRACTION ---
        if test.area in {"ext", "prep"} or any(
            s in sources for s in {"postgres", "mysql", "sqlserver", "oracle", "sqlite"}
        ):
            for src in sources:
                if src in (m for m in ALL_MATRICES[0].rows):
                    pass  # explicit allow check below
            for src in sources:
                if src in ALL_MATRICES[0].rows:
                    for blk in dsblocks or {"base"}:
                        if blk in ALL_MATRICES[0].cols:
                            out["extraction"][cell_key(src, blk)].append(test)

        # --- LOAD --- (test area LOAD esplicita)
        if test.area == "load":
            # source = la sorgente del canvas; se non rilevata, salta
            src_candidates = sources & set(ALL_MATRICES[1].rows)
            tgt_candidates = targets & set(ALL_MATRICES[1].cols)
            if not src_candidates:
                # fallback: per i LOAD-* assumi postgres come source default
                src_candidates = {"postgres"}
            if not tgt_candidates:
                tgt_candidates = set()
                # heuristics dirette dal titolo
                low = test.title.lower()
                for kw in ALL_MATRICES[1].cols:
                    if kw in low:
                        tgt_candidates.add(kw)
            for s in sorted(src_candidates):
                for t in sorted(tgt_candidates):
                    if s in ALL_MATRICES[1].rows and t in ALL_MATRICES[1].cols:
                        out["load"][cell_key(s, t)].append(test)

        # --- AUTH --- (qualsiasi test che parli di auth pattern)
        if authpats:
            # source_protocol e' uno tra rest/arcgis/ftp/sftp/sharepoint/s3/azure
            proto_hits = set()
            blob = test.text_blob.lower()
            if "arcgis" in blob:
                proto_hits.add("arcgis")
            if "rest" in blob or "api_connection" in blob or "fake-rest" in blob:
                proto_hits.add("rest")
            if "ftp:" in blob or test.has_tag("ftp"):
                proto_hits.add("ftp")
            if "sftp" in blob or test.has_tag("sftp"):
                proto_hits.add("sftp")
            if "sharepoint" in blob:
                proto_hits.add("sharepoint")
            if "minio" in blob or " s3" in blob:
                proto_hits.add("s3")
            if "azure" in blob:
                proto_hits.add("azure_blob")
            for proto in proto_hits:
                for ap in authpats:
                    if proto in ALL_MATRICES[2].rows and ap in ALL_MATRICES[2].cols:
                        out["auth"][cell_key(proto, ap)].append(test)

        # --- PAGINATION ---
        if pgstyles or pgshapes:
            shapes = pgshapes or {"object_root"}  # default shape se non specificato
            styles = pgstyles or set()
            for sh in shapes:
                for st in styles:
                    if sh in ALL_MATRICES[3].rows and st in ALL_MATRICES[3].cols:
                        out["pagination"][cell_key(sh, st)].append(test)

        # --- ERROR PATHS ---
        if errors:
            err_sources = set()
            blob = test.text_blob.lower()
            for src in ("rest", "arcgis", "postgres", "mysql", "sqlserver",
                        "oracle", "ftp", "sftp", "s3"):
                if src in blob:
                    err_sources.add(src)
            if not err_sources:
                # fallback: rest e' il piu' frequente
                err_sources.add("rest")
            for s in err_sources:
                for e in errors:
                    if s in ALL_MATRICES[4].rows and e in ALL_MATRICES[4].cols:
                        out["error_paths"][cell_key(s, e)].append(test)

    # Dedup + sort di ogni cella (deterministico).
    for matrix_name, cells in out.items():
        for k, lst in cells.items():
            unique = {t.id: t for t in lst}
            cells[k] = sorted(unique.values(), key=lambda t: t.id)
    return out


# ---------------------------------------------------------------------------
# Helper di alto livello, usato da render.py / check.py
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoverageData:
    tests: Tuple[TestRef, ...]
    source: str  # "yaml" | "markdown"
    by_matrix: Dict[str, Dict[str, Tuple[TestRef, ...]]] = field(default_factory=dict)


def build_coverage() -> CoverageData:
    tests, source = load_catalog()
    raw = classify(list(tests))
    by_matrix = {
        m: {k: tuple(v) for k, v in cells.items()}
        for m, cells in raw.items()
    }
    return CoverageData(tests=tuple(tests), source=source, by_matrix=by_matrix)


# ---------------------------------------------------------------------------
# Statistiche di copertura
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoverageStats:
    total_cells: int
    covered_done: int
    covered_any: int
    catalogued_only: int  # almeno un TODO/WIP, nessun DONE
    uncovered: int
    coverage_pct: float  # covered_done / total_cells * 100
    catalogued_pct: float  # covered_any / total_cells * 100


def compute_stats(data: CoverageData) -> CoverageStats:
    total = 0
    done = 0
    any_ = 0
    cat_only = 0
    for matrix in ALL_MATRICES:
        for r, c in matrix.cells():
            total += 1
            cell = data.by_matrix[matrix.name][cell_key(r, c)]
            if not cell:
                continue
            statuses = {t.status for t in cell}
            any_ += 1
            if "DONE" in statuses:
                done += 1
            else:
                cat_only += 1
    uncovered = total - any_
    return CoverageStats(
        total_cells=total,
        covered_done=done,
        covered_any=any_,
        catalogued_only=cat_only,
        uncovered=uncovered,
        coverage_pct=(done / total * 100.0) if total else 0.0,
        catalogued_pct=(any_ / total * 100.0) if total else 0.0,
    )


def top_gaps(data: CoverageData, limit: int = 5) -> List[Tuple[str, str, str]]:
    """Restituisce le prime `limit` celle scoperte in matrici critiche.

    Ogni elemento e' (matrix_name, row, col). Ordine deterministico per
    (matrix.name, row, col).
    """
    gaps: List[Tuple[str, str, str]] = []
    for matrix in ALL_MATRICES:
        if not matrix.critical:
            continue
        for r in matrix.rows:
            for c in matrix.cols:
                if not data.by_matrix[matrix.name][cell_key(r, c)]:
                    gaps.append((matrix.name, r, c))
    gaps.sort()
    return gaps[:limit]
