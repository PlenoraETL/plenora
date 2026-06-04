"""
Contract test BE-IMDF-001 — shared ESRI system fields module.

Verifica:
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.shared.connections.esri_system_fields import (
    ESRI_OID_FIELDS_LITERAL,
    ESRI_SYSTEM_FIELDS_LITERAL,
    is_esri_system_field,
    normalize_field_name,
)
from tests2.shared._ast_identifiers import (
    all_identifiers,
    imports_module,
    imports_symbol_from,
    uses_attribute,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# Set shape — exact pre-IMDF-001 casings

def test_oid_fields_literal_exact_pre_001_casings():
    """Gli stessi 5 valori che `arcgis_client.py:1864` aggiungeva a
    `skip_cols` per le operazioni `add` / `create_service`."""
    assert ESRI_OID_FIELDS_LITERAL == frozenset({
        "OBJECTID", "objectid",
        "GLOBALID", "globalid", "GlobalID",
    })


def test_system_fields_literal_exact_pre_001_casings():
    """Gli stessi 13 valori che vivevano in `loader.ESRI_SYSTEM_FIELDS`."""
    assert ESRI_SYSTEM_FIELDS_LITERAL == frozenset({
        "OBJECTID", "objectid",
        "GLOBALID", "globalid", "GlobalID",
        "Shape_Length", "Shape_Area",
        "SHAPE_Length", "SHAPE_Area",
        "CREATED_USER", "CREATED_DATE",
        "LAST_EDITED_USER", "LAST_EDITED_DATE",
    })


def test_oid_is_subset_of_system_fields():
    assert ESRI_OID_FIELDS_LITERAL <= ESRI_SYSTEM_FIELDS_LITERAL


def test_sets_are_immutable_frozenset():
    assert isinstance(ESRI_OID_FIELDS_LITERAL, frozenset)
    assert isinstance(ESRI_SYSTEM_FIELDS_LITERAL, frozenset)


# normalize_field_name — pattern legacy bit-per-bit

@pytest.mark.parametrize(
    "name, expected",
    [
        ("OBJECTID", "OBJECTID"),
        ("objectid", "OBJECTID"),
        ("Shape_Length", "SHAPELENGTH"),
        ("Shape__Length", "SHAPELENGTH"),  # double underscore hosted FS
        ("Shape_Area", "SHAPEAREA"),
        ("created_user", "CREATEDUSER"),
        ("GDBArchiveOID", "GDBARCHIVEOID"),
        ("custom_id", "CUSTOMID"),
        ("", ""),
    ],
)
def test_normalize_field_name(name, expected):
    assert normalize_field_name(name) == expected


def test_normalize_field_name_none_safe():
    """Pre-IMDF-001 `_is_system_field` accettava `name=None` con
    `(name or "").upper()`. Comportamento preservato."""
    assert normalize_field_name(None) == ""  # type: ignore[arg-type]


# is_esri_system_field — riconoscimento case-insensitive

@pytest.mark.parametrize(
    "name",
    [
        # OID/GLOBALID varianti
        "OBJECTID", "objectid", "ObjectID", "objectId",
        "GLOBALID", "globalid", "GlobalID",
        # Shape varianti (con underscore singolo, doppio, lowercase)
        "Shape_Length", "Shape_Area",
        "SHAPE_Length", "SHAPE_Area",
        "Shape__Length", "Shape__Area",
        "shape_length", "shape_area",
        # Created/edited
        "CREATED_USER", "CREATED_DATE",
        "LAST_EDITED_USER", "LAST_EDITED_DATE",
        "created_user", "last_edited_date",
        # GDB internal
        "GDBArchiveOID", "GDBARCHIVEOID", "gdb_archive_oid",
    ],
)
def test_is_esri_system_field_recognizes_known(name):
    assert is_esri_system_field(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "id", "ID", "name", "title", "category",
        "facility_id", "site_id", "level_id",
        "geometry", "GEOMETRY",
        "custom_field", "user_label",
        "",  # empty stringa: normalize ritorna "", non in set
    ],
)
def test_is_esri_system_field_excludes_user_fields(name):
    assert is_esri_system_field(name) is False


def test_is_esri_system_field_none_safe():
    assert is_esri_system_field(None) is False  # type: ignore[arg-type]


# Pure module — niente import pesanti / I/O / env

def test_module_has_no_heavy_imports():
    """Il modulo shared deve restare pure: niente pandas/geopandas/
    requests/ArcGIS clients, niente env reads, niente I/O."""
    src = (
        _BACKEND_APP / "shared" / "connections" / "esri_system_fields.py"
    ).read_text(encoding="utf-8")
    assert not imports_module(src, "pandas"), (
        "esri_system_fields.py NON deve contenere 'import pandas'/'from pandas': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "geopandas"), (
        "esri_system_fields.py NON deve contenere 'import geopandas'/'from geopandas': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "requests"), (
        "esri_system_fields.py NON deve contenere 'import requests'/'from requests': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "boto3"), (
        "esri_system_fields.py NON deve contenere 'import boto3': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "shapely"), (
        "esri_system_fields.py NON deve contenere 'import shapely'/'from shapely': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "pyogrio"), (
        "esri_system_fields.py NON deve contenere 'import pyogrio': "
        "il modulo deve restare pure."
    )
    assert not uses_attribute(src, "os", "getenv"), (
        "esri_system_fields.py NON deve contenere 'os.getenv': "
        "il modulo deve restare pure."
    )
    assert not uses_attribute(src, "os", "environ"), (
        "esri_system_fields.py NON deve contenere 'os.environ': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "app.config"), (
        "esri_system_fields.py NON deve contenere 'from app.config': "
        "il modulo deve restare pure."
    )
    assert not imports_module(src, "app.infrastructure.storage.cloud_backends"), (
        "esri_system_fields.py NON deve contenere 'from app.infrastructure.storage.cloud_backends': "
        "il modulo deve restare pure."
    )


# Source-bound: i 3 call site usano lo shared

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_arcgis_client_uses_shared_oid_set():
    src = _read(_BACKEND_APP / "core" / "features" / "arcgis_client.py")
    assert imports_symbol_from(src, "app.shared.connections.esri_system_fields", "ESRI_OID_FIELDS_LITERAL")
    assert uses_attribute(src, "skip_cols", "update") and "ESRI_OID_FIELDS_LITERAL" in all_identifiers(src)
    # Niente piu' literal set inline (la riga `{"OBJECTID", "objectid", ...}`)
    # RAW: pattern specifico con literal set di stringhe, non un identificatore
    assert (
        'skip_cols.update({"OBJECTID", "objectid", "GLOBALID", "globalid", "GlobalID"})'
        not in src
    )
