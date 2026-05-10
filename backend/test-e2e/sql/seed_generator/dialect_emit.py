"""Render SQL idiomatico per dialetto.

Ogni funzione ``emit_<table>(records, dialect)`` ritorna la stringa
di INSERT statements per la tabella, con la sintassi corretta per il
dialetto:

    * ``postgres`` -> PostGIS (ST_GeomFromText, ST_SetSRID/ST_MakePoint),
                      `DATE '...'` e `TIMESTAMP '...'` literal
    * ``mysql``    -> MySQL 8 spatial (ST_SRID(POINT(...), 4326),
                      ST_GeomFromText('...', 4326, 'axis-order=long-lat')),
                      DATETIME via stringa, escape backslash
    * ``oracle``   -> SDO_GEOMETRY(2001/2007/2002, ...),
                      `DATE '...'` / `TIMESTAMP '...'`
                      (NB: Oracle non supporta multi-row INSERT;
                       ogni record diventa una INSERT separata)
    * ``mssql``    -> geography::Point(lat, lng, 4326) e
                      geography::STGeomFromText('...', 4326),
                      DATETIME2 via stringa.
                      Specializzazioni: lat=90 -> 89.9999;
                      LineString antipodale split a 90E.

Pure rendering: no I/O.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from .models import (
    City, Customer, Employee, EventLog, LineString2D, MultiPolygon2D,
    Order, OrderItem, Point2D, Product, Region, Route,
)

DIALECTS = ("postgres", "mysql", "oracle", "mssql")


# ── helpers value rendering ─────────────────────────────────────────────

def _esc_str(s: str, dialect: str) -> str:
    """Escapa stringa per literal SQL: raddoppia apostrofi.

    Per MySQL anche il backslash ``\\`` va raddoppiato (escape char dei
    literal). Negli altri dialetti il backslash e' literal e non viene
    toccato (rispecchia la convenzione delle SQL esistenti).
    """
    out = s.replace("'", "''")
    if dialect == "mysql":
        out = out.replace("\\", "\\\\")
    return out


def _q(s: str, dialect: str) -> str:
    """Stringa SQL-quotata."""
    return f"'{_esc_str(s, dialect)}'"


def _opt_str(s: Optional[str], dialect: str) -> str:
    return "NULL" if s is None else _q(s, dialect)


def _opt_int(i: Optional[int]) -> str:
    return "NULL" if i is None else str(i)


def _dec(d: Decimal) -> str:
    """Decimal -> letterale SQL (senza apostrofi)."""
    return format(d, "f")


def _opt_dec(d: Optional[Decimal]) -> str:
    return "NULL" if d is None else _dec(d)


def _date_lit(d: date, dialect: str) -> str:
    iso = d.isoformat()  # YYYY-MM-DD
    if dialect in ("postgres", "oracle"):
        return f"DATE '{iso}'"
    # mysql / mssql -> stringa literale
    return f"'{iso}'"


def _opt_date(d: Optional[date], dialect: str) -> str:
    return "NULL" if d is None else _date_lit(d, dialect)


def _ts_lit(dt: datetime, dialect: str) -> str:
    iso = dt.strftime("%Y-%m-%d %H:%M:%S")
    if dialect in ("postgres", "oracle"):
        return f"TIMESTAMP '{iso}'"
    return f"'{iso}'"


# ── geometry rendering ──────────────────────────────────────────────────

def _emit_point(p: Optional[Point2D], dialect: str) -> str:
    if p is None:
        return "NULL"
    lng, lat = p.lng, p.lat
    if dialect == "postgres":
        return f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)"
    if dialect == "mysql":
        return f"ST_SRID(POINT({lng}, {lat}), 4326)"
    if dialect == "oracle":
        return f"SDO_GEOMETRY(2001, 4326, SDO_POINT_TYPE({lng}, {lat}, NULL), NULL, NULL)"
    if dialect == "mssql":
        # geography::Point usa (lat, lng) — convenzione opposta
        return f"geography::Point({lat}, {lng}, 4326)"
    raise ValueError(f"Unknown dialect: {dialect}")


def _wkt_multipolygon(mp: MultiPolygon2D) -> str:
    """Rende il WKT MULTIPOLYGON((..),(..)) con assi (lng lat)."""
    parts: list[str] = []
    for poly in mp.polygons:
        ring_strs: list[str] = []
        for ring in poly:
            ring_str = ", ".join(f"{lng} {lat}" for lng, lat in ring)
            ring_strs.append(f"({ring_str})")
        parts.append(f"({','.join(ring_strs)})")
    return f"MULTIPOLYGON({','.join(parts)})"


def _emit_multipolygon(mp: Optional[MultiPolygon2D], dialect: str) -> str:
    if mp is None:
        return "NULL"
    if dialect == "postgres":
        return f"ST_GeomFromText('{_wkt_multipolygon(mp)}', 4326)"
    if dialect == "mysql":
        return f"ST_GeomFromText('{_wkt_multipolygon(mp)}', 4326, 'axis-order=long-lat')"
    if dialect == "mssql":
        return f"geography::STGeomFromText('{_wkt_multipolygon(mp)}', 4326)"
    if dialect == "oracle":
        # SDO_GEOMETRY MultiPolygon (gtype=2007) richiede SDO_ELEM_INFO_ARRAY
        # per indicare l'inizio di ogni poligono nel ORDINATE_ARRAY.
        # Ogni poligono e' un exterior ring (etype 1003, interpretation 1).
        elem_info: list[int] = []
        ordinates: list[float] = []
        offset = 1
        for poly in mp.polygons:
            elem_info.extend([offset, 1003, 1])
            for ring in poly:
                for lng, lat in ring:
                    ordinates.append(lng)
                    ordinates.append(lat)
            offset = len(ordinates) + 1
        ei = ", ".join(str(x) for x in elem_info)
        # Oracle: serializza float in modo "naturale" (no trailing zero forzati;
        # ma manteniamo la precisione del literal Python).
        ord_str = ", ".join(_oracle_num(x) for x in ordinates)
        return (
            f"SDO_GEOMETRY(2007, 4326, NULL, "
            f"SDO_ELEM_INFO_ARRAY({ei}), "
            f"SDO_ORDINATE_ARRAY({ord_str}))"
        )
    raise ValueError(f"Unknown dialect: {dialect}")


def _oracle_num(x: float) -> str:
    """Serializza float per Oracle SDO (mantiene decimali significativi)."""
    # Match formato dei seed esistenti: 8.5, 14.20, 14.30, 9.1900, ...
    # Una semplice repr Python con formattazione "g" puo' perdere zero
    # significativi; usiamo una formattazione pragmatica.
    if x == int(x):
        return f"{x:.1f}"
    # Up to 4 decimals, strip trailing zero-only.
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return s if "." in s else f"{s}.0"


def _emit_linestring(ls: Optional[LineString2D], dialect: str) -> str:
    if ls is None:
        return "NULL"
    if dialect == "mssql":
        # SQL Server geography rifiuta segmenti antipodali esatti (es.
        # (0,0) -> (180,0)). Se rileviamo un segmento con delta_lng=180,
        # spezziamo a 90E inserendo un vertice intermedio.
        verts = _split_antipodal_for_mssql(ls.vertices)
        wkt = "LINESTRING(" + ", ".join(f"{lng} {lat}" for lng, lat in verts) + ")"
        return f"geography::STGeomFromText('{wkt}', 4326)"
    wkt = "LINESTRING(" + ", ".join(f"{lng} {lat}" for lng, lat in ls.vertices) + ")"
    if dialect == "postgres":
        return f"ST_GeomFromText('{wkt}', 4326)"
    if dialect == "mysql":
        return f"ST_GeomFromText('{wkt}', 4326, 'axis-order=long-lat')"
    if dialect == "oracle":
        ords = ", ".join(_oracle_num(c) for v in ls.vertices for c in v)
        return (
            f"SDO_GEOMETRY(2002, 4326, NULL, "
            f"SDO_ELEM_INFO_ARRAY(1, 2, 1), "
            f"SDO_ORDINATE_ARRAY({ords}))"
        )
    raise ValueError(f"Unknown dialect: {dialect}")


def _split_antipodal_for_mssql(
    verts: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    """Inserisce un vertice intermedio se 2 vertici consecutivi sono
    antipodali (delta_lng = 180 e stesso lat). SQL Server geography
    rifiuta segmenti antipodali esatti."""
    out: list[tuple[float, float]] = []
    for i, v in enumerate(verts):
        out.append(v)
        if i + 1 < len(verts):
            v2 = verts[i + 1]
            if abs(v2[0] - v[0]) >= 180.0 and v[1] == v2[1]:
                mid = ((v[0] + v2[0]) / 2.0, v[1])
                out.append(mid)
    return tuple(out)


# ── lat=90 fix per SQL Server geography ─────────────────────────────────

def _mssql_safe_lat(lat: float) -> float:
    """SQL Server geography rifiuta lat=90 esatto -> usa 89.9999."""
    if lat >= 90.0:
        return 89.9999
    if lat <= -90.0:
        return -89.9999
    return lat


# ── multi-row vs single-row INSERT ──────────────────────────────────────

def _supports_multirow(dialect: str) -> bool:
    """Oracle non supporta multi-row VALUES (...) (...) — usa INSERT
    separati."""
    return dialect != "oracle"


def _emit_insert(
    table: str,
    columns: list[str],
    rows: list[str],
    dialect: str,
) -> str:
    """Genera INSERT INTO ... VALUES (multi-row se supportato)."""
    if not rows:
        return ""
    cols_csv = ", ".join(columns)
    if _supports_multirow(dialect):
        rows_str = ",\n    ".join(rows)
        return f"INSERT INTO {table} ({cols_csv}) VALUES\n    {rows_str};\n"
    # Oracle: una INSERT per riga
    out_lines = []
    for r in rows:
        out_lines.append(f"INSERT INTO {table} ({cols_csv}) VALUES {r};")
    return "\n".join(out_lines) + "\n"


# ── per-table emit ──────────────────────────────────────────────────────

def emit_regions(records: Iterable[Region], dialect: str) -> str:
    rows: list[str] = []
    for r in records:
        boundary = _emit_multipolygon(r.boundary, dialect)
        rows.append(
            f"({r.id}, {_q(r.code, dialect)}, {_q(r.name, dialect)}, {boundary})"
        )
    return _emit_insert(
        "regions", ["id", "code", "name", "boundary"], rows, dialect,
    )


def emit_cities(records: Iterable[City], dialect: str) -> str:
    rows: list[str] = []
    for c in records:
        # SQL Server lat-safe (vedi PoleStation lat=90)
        if dialect == "mssql":
            lat = _mssql_safe_lat(c.latitude)
            geom_pt = (
                Point2D(lng=c.geom.lng, lat=_mssql_safe_lat(c.geom.lat))
                if c.geom is not None else None
            )
        else:
            lat = c.latitude
            geom_pt = c.geom
        rows.append(
            f"({c.id}, {c.region_id}, {_q(c.name, dialect)}, "
            f"{c.population}, {_opt_int(c.founded_year)}, "
            f"{lat}, {c.longitude}, {_emit_point(geom_pt, dialect)})"
        )
    return _emit_insert(
        "cities",
        ["id", "region_id", "name", "population", "founded_year",
         "latitude", "longitude", "geom"],
        rows, dialect,
    )


def emit_customers(records: Iterable[Customer], dialect: str) -> str:
    rows: list[str] = []
    for c in records:
        rows.append(
            f"({c.id}, {_opt_int(c.city_id)}, {_q(c.full_name, dialect)}, "
            f"{_opt_str(c.email, dialect)}, {_opt_int(c.age)}, "
            f"{_date_lit(c.signup_date, dialect)}, {_q(c.status, dialect)})"
        )
    return _emit_insert(
        "customers",
        ["id", "city_id", "full_name", "email", "age", "signup_date", "status"],
        rows, dialect,
    )


def emit_products(records: Iterable[Product], dialect: str) -> str:
    rows: list[str] = []
    for p in records:
        rows.append(
            f"({p.id}, {_q(p.code, dialect)}, {_q(p.name, dialect)}, "
            f"{_q(p.category, dialect)}, {_dec(p.price)}, "
            f"{_opt_dec(p.weight_kg)}, {p.in_stock_count})"
        )
    return _emit_insert(
        "products",
        ["id", "code", "name", "category", "price", "weight_kg", "in_stock_count"],
        rows, dialect,
    )


def emit_orders(records: Iterable[Order], dialect: str) -> str:
    rows: list[str] = []
    for o in records:
        rows.append(
            f"({o.id}, {o.customer_id}, {_ts_lit(o.order_date, dialect)}, "
            f"{_opt_dec(o.total_amount)}, {_q(o.status, dialect)})"
        )
    return _emit_insert(
        "orders",
        ["id", "customer_id", "order_date", "total_amount", "status"],
        rows, dialect,
    )


def emit_order_items(records: Iterable[OrderItem], dialect: str) -> str:
    rows: list[str] = []
    for oi in records:
        rows.append(
            f"({oi.id}, {oi.order_id}, {oi.product_id}, {oi.qty}, "
            f"{_dec(oi.unit_price)}, {_dec(oi.line_total)})"
        )
    return _emit_insert(
        "order_items",
        ["id", "order_id", "product_id", "qty", "unit_price", "line_total"],
        rows, dialect,
    )


def emit_employees(records: Iterable[Employee], dialect: str) -> str:
    rows: list[str] = []
    for e in records:
        rows.append(
            f"({e.id}, {_opt_int(e.manager_id)}, {_q(e.name, dialect)}, "
            f"{_q(e.role, dialect)}, {_opt_dec(e.salary)}, "
            f"{_opt_date(e.hire_date, dialect)})"
        )
    return _emit_insert(
        "employees",
        ["id", "manager_id", "name", "role", "salary", "hire_date"],
        rows, dialect,
    )


def emit_routes(records: Iterable[Route], dialect: str) -> str:
    rows: list[str] = []
    for r in records:
        rows.append(
            f"({r.id}, {r.city_from_id}, {r.city_to_id}, "
            f"{_q(r.name, dialect)}, {_dec(r.length_km)}, "
            f"{_emit_linestring(r.geom, dialect)})"
        )
    return _emit_insert(
        "routes",
        ["id", "city_from_id", "city_to_id", "name", "length_km", "geom"],
        rows, dialect,
    )


def emit_events_log(records: Iterable[EventLog], dialect: str) -> str:
    """events_log: lo emettiamo come INSERT ... VALUES diretto.

    NB: i file ``02_volume.sql`` esistenti usano CTE/generate_series/
    CONNECT BY per generare le righe SERVER-SIDE; qui generiamo i
    valori CLIENT-SIDE e li passiamo come VALUES. Risultato finale
    semanticamente identico, payload SQL piu' grande ma piu' portabile
    (e indipendente da ``cte_max_recursion_depth`` / SDO).
    """
    records = list(records)
    if not records:
        return ""
    cols = ["id", "customer_id", "event_type", "occurred_at", "value"]
    rows: list[str] = []
    for ev in records:
        rows.append(
            f"({ev.id}, {ev.customer_id}, {_q(ev.event_type, dialect)}, "
            f"{_ts_lit(ev.occurred_at, dialect)}, {_opt_dec(ev.value)})"
        )
    # Per dataset grossi (>= 1k righe) chunked per leggibilita' / parser limits.
    CHUNK = 1000
    out = []
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        out.append(_emit_insert("events_log", cols, chunk, dialect))
    return "".join(out)
