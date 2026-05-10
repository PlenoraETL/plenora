"""Definizione delle matrici di coverage per i test e2e plenora.

Ogni matrice e' una griglia (rows x cols) di celle. Il parser
(`coverage_parser.py`) classifica i test del catalogo in queste celle;
il renderer (`render.py`) le visualizza in markdown / html / json.

Le matrici sono dataclass `frozen=True` per garantire che siano
trattate come valori immutabili (deterministicita' dell'output).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Matrix:
    """Definizione canonica di una matrice di coverage.

    - `name`        slug univoco (lowercase, ASCII).
    - `title`       titolo umano usato nei renderer.
    - `rows`        etichette delle righe (ordine deterministico).
    - `cols`        etichette delle colonne (ordine deterministico).
    - `description` descrizione di una riga della matrice.
    - `critical`    se True, conta nei "top gap" del summary.
    """

    name: str
    title: str
    rows: Tuple[str, ...]
    cols: Tuple[str, ...]
    description: str
    critical: bool = False

    def cells(self) -> list[tuple[str, str]]:
        """Tutte le coppie (row, col) in ordine deterministico."""
        return [(r, c) for r in self.rows for c in self.cols]


# ---------------------------------------------------------------------------
# 1. EXTRACTION — sorgente x blocco di dataset
# ---------------------------------------------------------------------------
# Per ogni source dichiarato nel catalogo, quali "blocchi di dato"
# tocchiamo davvero: base = happy path; edge = caratteri/edge case
# (Unicode, apostrofi, BOM, formula); null = NULL handling; volume =
# >1k righe / paginazione massiva; streaming = chunked / line-by-line.
EXTRACTION = Matrix(
    name="extraction",
    title="Extraction coverage (source x dataset_block)",
    rows=(
        "postgres",
        "mysql",
        "sqlserver",
        "oracle",
        "sqlite",
        "csv",
        "xlsx",
        "xls",
        "json",
        "ndjson",
        "geojson",
        "shp",
        "gpkg",
        "parquet",
        "ftp",
        "sftp",
        "sharepoint",
        "rest_api_key",
        "rest_oauth2_cc",
        "rest_oauth2_pwd",
        "rest_basic",
        "rest_arcgis",
        "rest_bearer",
        "s3",
        "azure_blob",
    ),
    cols=("base", "edge", "null", "volume", "streaming"),
    description=(
        "Per ogni sorgente, quali tipi di dato estraiamo: base (happy path), "
        "edge (Unicode/formule/BOM), null (NULL handling), volume (>1k rows), "
        "streaming (chunk/line-by-line)."
    ),
    critical=True,
)


# ---------------------------------------------------------------------------
# 2. LOAD — sorgente x destinazione
# ---------------------------------------------------------------------------
# Cross-dialect DB->DB, DB->file, file->DB, DB->REST sink, ecc.
# Le righe sono "il punto di partenza del canvas", le colonne sono
# il punto di scrittura finale.
LOAD = Matrix(
    name="load",
    title="Load coverage (source x target)",
    rows=(
        "postgres",
        "mysql",
        "sqlserver",
        "oracle",
        "sqlite",
        "csv",
        "xlsx",
        "json",
        "geojson",
        "parquet",
        "rest",
        "s3",
    ),
    cols=(
        "postgres",
        "mysql",
        "sqlserver",
        "oracle",
        "sqlite",
        "csv",
        "xlsx",
        "json",
        "geojson",
        "shp",
        "parquet",
        "rest_sink",
        "s3",
    ),
    description=(
        "Per ogni coppia (sorgente canvas, destinazione di scrittura): "
        "DB->DB cross-dialect, DB->file, file->DB, DB->REST sink, ecc."
    ),
    critical=True,
)


# ---------------------------------------------------------------------------
# 3. AUTH — protocollo sorgente x pattern di autenticazione
# ---------------------------------------------------------------------------
# Solo per sorgenti che richiedono auth (REST principalmente).
AUTH = Matrix(
    name="auth",
    title="Auth coverage (source_protocol x auth_pattern)",
    rows=(
        "rest",
        "arcgis",
        "ftp",
        "sftp",
        "sharepoint",
        "s3",
        "azure_blob",
    ),
    cols=(
        "no_auth",
        "api_key_query",
        "api_key_header",
        "bearer",
        "basic",
        "oauth2_cc",
        "oauth2_password",
        "arcgis_token",
        "ssh_key",
        "iam",
    ),
    description=(
        "Per ogni protocollo che richiede autenticazione, quali pattern "
        "abbiamo testato. Le celle vuote per coppie 'impossibili' "
        "(es. ftp x oauth2_cc) sono gap noti, non bug."
    ),
    critical=True,
)


# ---------------------------------------------------------------------------
# 4. PAGINATION — endpoint REST x stile di paginazione
# ---------------------------------------------------------------------------
PAGINATION = Matrix(
    name="pagination",
    title="Pagination coverage (REST endpoint shape x pagination_style)",
    rows=(
        "object_root",
        "array_root",
        "nested_path",
        "feature_collection",
        "arcgis_query",
    ),
    cols=(
        "none",
        "offset",
        "page_size",
        "cursor",
        "link_header",
        "no_meta",
    ),
    description=(
        "Per ogni shape della response REST, quali stili di paginazione "
        "supportiamo end-to-end. 'none' = endpoint non paginato."
    ),
)


# ---------------------------------------------------------------------------
# 5. ERROR PATHS — sorgente x classe di errore
# ---------------------------------------------------------------------------
# Negative tests: cosa succede quando la sorgente fallisce in modo
# non-deterministico (rete, auth scaduta, payload corrotto).
ERROR_PATHS = Matrix(
    name="error_paths",
    title="Error path coverage (source x http_status_class)",
    rows=(
        "rest",
        "arcgis",
        "postgres",
        "mysql",
        "sqlserver",
        "oracle",
        "ftp",
        "sftp",
        "s3",
    ),
    cols=(
        "401",
        "403",
        "404",
        "422",
        "429",
        "500",
        "503",
        "malformed_json",
        "timeout",
        "redirect_loop",
    ),
    description=(
        "Per ogni sorgente, quali classi di errore esercitiamo nei test "
        "(auth scaduta, throttling, payload corrotto, network timeout)."
    ),
    critical=True,
)


# ---------------------------------------------------------------------------
# Registry — usato da render.py / check.py per iterare le matrici.
# ---------------------------------------------------------------------------
ALL_MATRICES: Tuple[Matrix, ...] = (
    EXTRACTION,
    LOAD,
    AUTH,
    PAGINATION,
    ERROR_PATHS,
)


def get_matrix(name: str) -> Matrix:
    """Restituisce la matrice per nome o solleva ValueError."""
    for m in ALL_MATRICES:
        if m.name == name:
            return m
    raise ValueError(f"matrix not found: {name!r}")


def total_cells() -> int:
    """Numero totale di celle su tutte le matrici."""
    return sum(len(m.rows) * len(m.cols) for m in ALL_MATRICES)
