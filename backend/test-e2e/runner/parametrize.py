"""Helper per parametrizzare test pytest su dimensioni comuni del catalogo.

Le dimensioni coperte sono quelle ricorrenti nel catalogo
``test-e2e/test-e2e.md`` e nei file ``catalog/*.yaml``:

- **DB dialects**: postgres / mysql / mssql / oracle / sqlite
- **Docker profiles**: core / db / db-heavy / cloud / files / full
- **REST auth styles**: api_key (header/query) / bearer / basic /
  oauth2_cc / oauth2_password / arcgis_token
- **Pagination styles**: offset / page+size / cursor / link_header /
  no_meta
- **Geometry types**: Point / LineString / Polygon / MultiPoint /
  MultiLineString / MultiPolygon / GeometryCollection
- **File formats**: tabular (csv/xlsx/xls/json/ndjson/parquet) e
  spatial (geojson/shp/gpkg)

Esempio d'uso::

    from runner.parametrize import parametrize_dialects

    @parametrize_dialects(only_light=True)
    def test_extract_simple(dialect: str, db_connection_for_dialect: str):
        assert dialect in {"postgres", "mysql", "mssql", "sqlite"}

I helper applicano automaticamente i marker ``profile_<name>`` corretti
così che ``pytest -m profile_core`` non esegua i casi heavy.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest


# ── Costanti di dimensione ────────────────────────────────────────────

ALL_DB_DIALECTS: list[str] = ["postgres", "mysql", "mssql", "oracle", "sqlite"]
"""Dialetti SQL supportati da plenora (vedi ConnectionCreate.type)."""

HEAVY_DIALECTS: list[str] = ["oracle"]
"""Dialetti che richiedono profilo ``db-heavy`` (Oracle è ~3GB image)."""

LIGHT_DIALECTS: list[str] = ["postgres", "mysql", "mssql", "sqlite"]
"""Dialetti eseguibili nei profili ``core``/``db`` (no Oracle)."""

CORE_DIALECTS: list[str] = ["postgres", "sqlite"]
"""Dialetti eseguibili nel profilo ``core`` (solo postgres + sqlite locale)."""


DOCKER_PROFILES: list[str] = ["core", "db", "db-heavy", "cloud", "files", "full"]
"""Profili docker compose esposti dallo stack di test."""


REST_AUTH_STYLES: list[str] = [
    "api_key_header",
    "api_key_query",
    "bearer",
    "basic",
    "oauth2_client_credentials",
    "oauth2_password",
    "arcgis_token",
]
"""Tipi di autenticazione supportati per ``api_connections``.

Vedi ``app/core/arricchisci/api_connection_*.py`` per la lista completa.
"""

OAUTH_GRANTS: list[str] = ["client_credentials", "password"]


PAGINATION_STYLES: list[str] = [
    "offset",
    "page_size",
    "cursor",
    "link_header",
    "no_meta",
]
"""Stili di paginazione gestiti dal connector REST plenora."""


GEOMETRY_TYPES: list[str] = [
    "Point",
    "LineString",
    "Polygon",
    "MultiPoint",
    "MultiLineString",
    "MultiPolygon",
    "GeometryCollection",
]
"""Tipi geometrici GeoJSON / OGC SFA che il backend deve gestire."""


SPATIAL_PREDICATES: list[str] = [
    "ST_Within",
    "ST_Intersects",
    "ST_Contains",
    "ST_DWithin",
    "ST_Touches",
    "ST_Overlaps",
]
"""Predicati spaziali esposti dal canvas (vedi canvas_models.Edge)."""


FILE_FORMATS_TABULAR: list[str] = [
    "csv",
    "xlsx",
    "xls",
    "json",
    "ndjson",
    "parquet",
]

FILE_FORMATS_SPATIAL: list[str] = ["geojson", "shp", "gpkg"]

FILE_FORMATS_ALL: list[str] = FILE_FORMATS_TABULAR + FILE_FORMATS_SPATIAL


WRITE_MODES: list[str] = [
    "create",
    "replace",
    "append",
    "upsert",
    "update",
    "truncate_insert",
]
"""Modalità di scrittura ``carica`` (vedi canvas_models.Node.write_mode)."""


JOIN_TYPES: list[str] = ["inner", "left", "right", "outer", "cross"]


PROJECT_TYPES: list[str] = ["estrai", "manipola", "arricchisci"]


# ── Internal helpers ──────────────────────────────────────────────────

def _profile_marker_for_dialect(dialect: str) -> Any:
    """Mappa dialetto → marker pytest del profilo minimo richiesto."""
    if dialect in HEAVY_DIALECTS:
        return pytest.mark.profile_db_heavy
    if dialect in {"mysql", "mssql"}:
        return pytest.mark.profile_db
    # postgres + sqlite girano su core
    return pytest.mark.profile_core


def _profile_marker_for_format(fmt: str) -> Any:
    """File spatial richiedono ``files`` per FTP/SFTP, ma per local può
    essere ``core``. Per default tagghiamo come ``files`` perché il caso
    canonico è "estrai da datasource FTP".
    """
    if fmt in FILE_FORMATS_SPATIAL:
        return pytest.mark.profile_files
    return pytest.mark.profile_files


# ── Decorator factories ───────────────────────────────────────────────

def parametrize_dialects(
    *,
    exclude: list[str] | None = None,
    only_light: bool = False,
    only_core: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``pytest.mark.parametrize`` per dialetti DB con profile marker.

    Args:
        exclude: dialetti da escludere (es. ``["oracle"]``).
        only_light: solo dialetti del profilo ``db`` (no Oracle).
        only_core: solo dialetti del profilo ``core`` (postgres+sqlite).

    Il marker ``profile_*`` è applicato per-param: una run con
    ``-m profile_core`` esclude automaticamente i casi heavy/db.
    """
    if only_core:
        dialects = list(CORE_DIALECTS)
    elif only_light:
        dialects = list(LIGHT_DIALECTS)
    else:
        dialects = list(ALL_DB_DIALECTS)

    if exclude:
        dialects = [d for d in dialects if d not in exclude]

    params = [
        pytest.param(d, marks=[_profile_marker_for_dialect(d)], id=d)
        for d in dialects
    ]
    return pytest.mark.parametrize("dialect", params)


def parametrize_auth_styles(
    *,
    include_arcgis: bool = True,
    only: list[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per gli auth style REST.

    Args:
        include_arcgis: include il caso ``arcgis_token``.
        only: lista esplicita di auth_style da usare; se None usa tutti.
    """
    if only is not None:
        styles = [s for s in REST_AUTH_STYLES if s in only]
    else:
        styles = list(REST_AUTH_STYLES)
        if not include_arcgis:
            styles = [s for s in styles if s != "arcgis_token"]

    # Tutti gli auth REST richiedono almeno il profilo core
    # (fake-rest container è in profilo core).
    params = [
        pytest.param(s, marks=[pytest.mark.profile_core], id=s)
        for s in styles
    ]
    return pytest.mark.parametrize("auth_style", params)


def parametrize_pagination_styles(
    *, exclude: list[str] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per gli stili di paginazione REST."""
    styles = list(PAGINATION_STYLES)
    if exclude:
        styles = [s for s in styles if s not in exclude]

    params = [
        pytest.param(s, marks=[pytest.mark.profile_core], id=s)
        for s in styles
    ]
    return pytest.mark.parametrize("pagination_style", params)


def parametrize_geometry_types(
    *, only_simple: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per i tipi geometrici GeoJSON/OGC.

    Args:
        only_simple: solo Point / LineString / Polygon (no Multi*).
    """
    if only_simple:
        types = ["Point", "LineString", "Polygon"]
    else:
        types = list(GEOMETRY_TYPES)

    params = [
        pytest.param(t, marks=[pytest.mark.profile_core], id=t)
        for t in types
    ]
    return pytest.mark.parametrize("geometry_type", params)


def parametrize_file_formats(
    *, kind: str = "all"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per formati file.

    Args:
        kind: ``"tabular"`` / ``"spatial"`` / ``"all"``.
    """
    if kind == "tabular":
        formats = list(FILE_FORMATS_TABULAR)
    elif kind == "spatial":
        formats = list(FILE_FORMATS_SPATIAL)
    elif kind == "all":
        formats = list(FILE_FORMATS_ALL)
    else:
        raise ValueError(
            f"kind deve essere 'tabular'/'spatial'/'all', non {kind!r}"
        )

    params = [
        pytest.param(f, marks=[_profile_marker_for_format(f)], id=f)
        for f in formats
    ]
    return pytest.mark.parametrize("file_format", params)


def parametrize_write_modes(
    *, exclude: list[str] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per le modalità di scrittura ``carica``."""
    modes = list(WRITE_MODES)
    if exclude:
        modes = [m for m in modes if m not in exclude]

    params = [
        pytest.param(m, marks=[pytest.mark.profile_db], id=m)
        for m in modes
    ]
    return pytest.mark.parametrize("write_mode", params)


def parametrize_join_types(
    *, include_cross: bool = True
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per i tipi di JOIN (inner/left/right/outer/cross)."""
    types = list(JOIN_TYPES)
    if not include_cross:
        types = [t for t in types if t != "cross"]

    params = [
        pytest.param(t, marks=[pytest.mark.profile_core], id=t)
        for t in types
    ]
    return pytest.mark.parametrize("join_type", params)


def parametrize_project_types() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per i 3 project_type plenora."""
    params = [
        pytest.param(p, marks=[pytest.mark.profile_core], id=p)
        for p in PROJECT_TYPES
    ]
    return pytest.mark.parametrize("project_type", params)


def parametrize_spatial_predicates() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``parametrize`` per i predicati spaziali del canvas."""
    params = [
        pytest.param(p, marks=[pytest.mark.profile_core], id=p)
        for p in SPATIAL_PREDICATES
    ]
    return pytest.mark.parametrize("spatial_predicate", params)


# ── Public registry ───────────────────────────────────────────────────

ALL_DIMENSIONS: dict[str, list[str]] = {
    "dialects": ALL_DB_DIALECTS,
    "docker_profiles": DOCKER_PROFILES,
    "auth_styles": REST_AUTH_STYLES,
    "pagination_styles": PAGINATION_STYLES,
    "geometry_types": GEOMETRY_TYPES,
    "spatial_predicates": SPATIAL_PREDICATES,
    "file_formats_tabular": FILE_FORMATS_TABULAR,
    "file_formats_spatial": FILE_FORMATS_SPATIAL,
    "write_modes": WRITE_MODES,
    "join_types": JOIN_TYPES,
    "project_types": PROJECT_TYPES,
}
"""Dict ``{dimension_name: [values]}`` per introspection nei test smoke."""


PARAMETRIZE_HELPERS: tuple[str, ...] = (
    "parametrize_dialects",
    "parametrize_auth_styles",
    "parametrize_pagination_styles",
    "parametrize_geometry_types",
    "parametrize_file_formats",
    "parametrize_write_modes",
    "parametrize_join_types",
    "parametrize_project_types",
    "parametrize_spatial_predicates",
)
