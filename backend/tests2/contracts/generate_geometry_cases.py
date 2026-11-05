"""Genera ``geometry_cases.json`` (Fase 5, P7).

Corpus condiviso del lettore di geometrie delle anteprime: i risultati attesi
sono quelli di shapely (GEOS). Ogni geometria del corpus si scrive in tutte le
forme che il frontend riceve dal backend o da un database:

- il WKT com'e' scritto nel corpus e il WKT canonico di shapely;
- EWKT con prefisso ``SRID=4326;``;
- WKB ISO esadecimale little e big endian (codici 1000/2000/3000 per Z/M/ZM);
- EWKB esadecimale little e big endian con SRID (flag Z/M/SRID, la forma di
  PostGIS), e la forma ``\\x...`` di un bytea PostgreSQL;
- GeoJSON di shapely (quando shapely sa scriverlo).

Il risultato atteso e' il tipo di shapely e la geometria GeoJSON in 2D
(``force_2d``). I valori in ``UNREADABLE`` devono risultare non leggibili per
entrambi: il test ``test_geometry_corpus_parity.py`` verifica che shapely li
rifiuti e che il file sia aggiornato; il frontend
(``frontend/tests/shared/geometry.contract.test.js``) fa girare
``utils/geometry.js`` sugli stessi casi.

Uso (da backend/):  python -m tests2.contracts.generate_geometry_cases
"""

from __future__ import annotations

import json
from pathlib import Path

import shapely
from shapely.geometry import mapping

CASES_PATH = Path(__file__).with_name("geometry_cases.json")

SRID = 4326

WKT_CORPUS: list[str] = [
    # Punti: 2D, Z, M, ZM, dimensioni implicite, forme attaccate, notazione
    # scientifica, minuscole, EMPTY.
    "POINT (11.3426 44.4949)",
    "POINT Z (30 10 5)",
    "POINT M (30 10 7)",
    "POINT ZM (30 10 5 7)",
    "POINT (30 10 5)",
    "POINT (30 10 5 7)",
    "POINTZ(30 10 5)",
    "POINTM (30 10 7)",
    "point (1e3 -2.5E-1)",
    "POINT (-0.5 .25)",
    "POINT EMPTY",
    # Linee.
    "LINESTRING (30 10, 10 30, 40 40)",
    "LINESTRING Z (30 10 1, 10 30 2, 40 40 3)",
    "LINESTRING M (30 10 1, 10 30 2)",
    "LINESTRING EMPTY",
    # Poligoni, anche con buco.
    "POLYGON ((30 10, 40 40, 20 40, 10 20, 30 10))",
    "POLYGON ((35 10, 45 45, 15 40, 10 20, 35 10), (20 30, 35 35, 30 20, 20 30))",
    "POLYGON ZM ((0 0 1 2, 1 0 1 2, 1 1 1 2, 0 0 1 2))",
    "POLYGON EMPTY",
    # MULTIPOINT con e senza parentesi interne.
    "MULTIPOINT ((10 40), (40 30), (20 20), (30 10))",
    "MULTIPOINT (10 40, 40 30, 20 20, 30 10)",
    "MULTIPOINT Z (1 2 3, 4 5 6)",
    "MULTIPOINT M ((1 2 3), (4 5 6))",
    "MULTIPOINT ZM ((1 2 3 4), (5 6 7 8))",
    "MULTIPOINT EMPTY",
    # Multi-linee, anche con una parte vuota.
    "MULTILINESTRING ((10 10, 20 20, 10 40), (40 40, 30 30, 40 20, 30 10))",
    "MULTILINESTRING M ((0 0 1, 1 1 2), (2 2 3, 3 3 4))",
    "MULTILINESTRING ((0 0, 1 1), EMPTY)",
    "MULTILINESTRING EMPTY",
    # Multi-poligoni, anche con buco.
    "MULTIPOLYGON (((30 20, 45 40, 10 40, 30 20)), ((15 5, 40 10, 10 20, 5 10, 15 5)))",
    "MULTIPOLYGON (((40 40, 20 45, 45 30, 40 40)), "
    "((20 35, 10 30, 10 10, 30 5, 45 20, 20 35), (30 20, 20 15, 20 25, 30 20)))",
    "MULTIPOLYGON Z (((0 0 5, 1 0 5, 1 1 5, 0 0 5)))",
    "MULTIPOLYGON EMPTY",
    # Collezioni, anche annidate, con Z e con membri vuoti.
    "GEOMETRYCOLLECTION (POINT (40 10), LINESTRING (10 10, 20 20, 10 40), "
    "POLYGON ((40 40, 20 45, 45 30, 40 40)))",
    "GEOMETRYCOLLECTION (POINT (1 2), GEOMETRYCOLLECTION (LINESTRING (0 0, 1 1), "
    "MULTIPOINT ((3 4), (5 6))))",
    "GEOMETRYCOLLECTION Z (POINT Z (1 2 3), LINESTRING Z (0 0 0, 1 1 1))",
    "GEOMETRYCOLLECTION (POINT EMPTY, POINT (1 2))",
    "GEOMETRYCOLLECTION EMPTY",
]

# Valori che nessuno dei due lettori deve accettare (forma: kind, valore).
UNREADABLE: list[tuple[str, str]] = [
    ("wkt", "POINT (1)"),
    ("wkt", "POINT (1 2 3 4 5)"),
    ("wkt", "POINT Z (1 2)"),
    ("wkt", "LINESTRING (0 0, 1 1"),
    ("wkt", "POINT (1 2) POINT (3 4)"),
    ("wkt", "POINT (a b)"),
    ("wkt", "CIRCULARSTRING (0 0, 1 1, 2 0)"),
    ("wkt", "ciao"),
    ("wkb", "0101"),
    ("wkb", "01010000000000000000"),
    ("wkb", "0108000000000000000000F03F0000000000000040"),
    ("wkb", "01040000000500000001010000000000000000000000"),
]


def _plain(value):
    """Tuple -> liste e numeri come float JSON (come li legge il frontend)."""
    return json.loads(json.dumps(value))


def _forms(source: str, geom) -> list[tuple[str, str]]:
    forms = [("wkt", source)]
    if geom.wkt != source:
        forms.append(("wkt_canonico", geom.wkt))
    forms.append(("ewkt", f"SRID={SRID};{source}"))
    forms.append(("wkb_iso_le", shapely.to_wkb(geom, hex=True, flavor="iso", byte_order=1)))
    forms.append(("wkb_iso_be", shapely.to_wkb(geom, hex=True, flavor="iso", byte_order=0)))
    with_srid = shapely.set_srid(geom, SRID)
    ewkb = shapely.to_wkb(with_srid, hex=True, flavor="extended", include_srid=True, byte_order=1)
    forms.append(("ewkb_le", ewkb))
    forms.append(("ewkb_be", shapely.to_wkb(
        with_srid, hex=True, flavor="extended", include_srid=True, byte_order=0)))
    forms.append(("bytea", "\\x" + ewkb.lower()))
    try:
        forms.append(("geojson", shapely.to_geojson(geom)))
    except Exception:  # noqa: BLE001 - shapely non scrive GeoJSON di alcune forme vuote
        pass
    return forms


def build_cases() -> dict:
    readable = []
    for source in WKT_CORPUS:
        geom = shapely.from_wkt(source)
        expected = {
            "type": geom.geom_type,
            "geometry": _plain(mapping(shapely.force_2d(geom))),
        }
        for kind, value in _forms(source, geom):
            readable.append({"source": source, "kind": kind, "value": value, "expected": expected})
    unreadable = [{"kind": kind, "value": value} for kind, value in UNREADABLE]
    return {"readable": readable, "unreadable": unreadable}


def main() -> None:
    CASES_PATH.write_text(json.dumps(build_cases(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
