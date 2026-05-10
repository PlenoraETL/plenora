"""Dataset deterministico per il seed E2E plenora.

Replica 1:1 i valori dei file ``01_seed.sql`` esistenti (postgres /
mysql / oracle / sqlserver). I valori sono organizzati in tre blocchi
per ogni tabella:

    * BASE_<table>      -> seed canonico (12 customer, 9 product, ...)
    * EDGE_<table>      -> edge cases (id discontinui)
    * NULL_<table>      -> NULL cases

Le funzioni ``build_baseline_dataset()`` / ``build_dataset()``
compongono i blocchi in ordine FK-safe e ritornano un :class:`SeedDataset`
pronto per ``dialect_emit``.

NB: gli id sono identici tra i 4 dialetti (le SQL esistenti li
condividono). PointStation in SQL Server usa lat=89.9999 perche' il
tipo geography rifiuta lat=90 esatto; questa specializzazione viene
gestita da ``dialect_emit`` (override) e NON dal dataset.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .models import (
    City, Customer, Employee, EventLog, LineString2D, MultiPolygon2D,
    Order, OrderItem, Point2D, Product, Region, Route, SeedDataset,
)


# ── helpers compatti ────────────────────────────────────────────────────

def _D(x: str) -> Decimal:
    return Decimal(x)


def _pt(lng: float, lat: float) -> Point2D:
    return Point2D(lng=lng, lat=lat)


def _ring(*vertices: tuple[float, float]) -> tuple[tuple[float, float], ...]:
    return tuple(vertices)


def _mpoly(*polygons: tuple[tuple[tuple[float, float], ...], ...]) -> MultiPolygon2D:
    return MultiPolygon2D(polygons=tuple(polygons))


# ── REGIONS ─────────────────────────────────────────────────────────────

# Rettangoli ~1° di lato in WGS84.
# CAM ha 2 poligoni: regione + isola di Capri.
LOM_RING = _ring((8.5, 45.0), (10.5, 45.0), (10.5, 46.0), (8.5, 46.0), (8.5, 45.0))
LAZ_RING = _ring((12.0, 41.0), (14.0, 41.0), (14.0, 42.5), (12.0, 42.5), (12.0, 41.0))
CAM_MAIN = _ring((14.0, 40.5), (15.5, 40.5), (15.5, 41.5), (14.0, 41.5), (14.0, 40.5))
CAM_CAPRI = _ring((14.20, 40.53), (14.30, 40.53), (14.30, 40.57), (14.20, 40.57), (14.20, 40.53))

BASE_REGIONS: tuple[Region, ...] = (
    Region(1, "LOM", "Lombardia", _mpoly((LOM_RING,))),
    Region(2, "LAZ", "Lazio",     _mpoly((LAZ_RING,))),
    Region(3, "CAM", "Campania",  _mpoly((CAM_MAIN,), (CAM_CAPRI,))),
)
EDGE_REGIONS: tuple[Region, ...] = (
    # regione SENZA cities ne' boundary (LEFT JOIN edge)
    Region(4, "VEN", "Veneto", None),
)


# ── CITIES ──────────────────────────────────────────────────────────────

BASE_CITIES: tuple[City, ...] = (
    City(1, 1, "Milano",    1396000, -222, 45.4642,  9.1900, _pt( 9.1900, 45.4642)),
    City(2, 1, "Bergamo",    122000,  -49, 45.6983,  9.6773, _pt( 9.6773, 45.6983)),
    City(3, 1, "Brescia",    196000,  -27, 45.5398, 10.2200, _pt(10.2200, 45.5398)),
    City(4, 2, "Roma",      2873000, -753, 41.9028, 12.4964, _pt(12.4964, 41.9028)),
    City(5, 2, "Latina",     127000, 1932, 41.4676, 12.9038, _pt(12.9038, 41.4676)),
    City(6, 2, "Frosinone",   46000, None, 41.6403, 13.3500, _pt(13.3500, 41.6403)),
    City(7, 3, "Napoli",     914000, -470, 40.8518, 14.2681, _pt(14.2681, 40.8518)),
    City(8, 3, "Salerno",    133000, -197, 40.6824, 14.7681, _pt(14.7681, 40.6824)),
    City(9, 3, "Caserta",     74000, 1750, 41.0721, 14.3327, _pt(14.3327, 41.0721)),
)
EDGE_CITIES: tuple[City, ...] = (
    City(10, 1, "Milano Centro",  5000, None, 45.4660,   9.1908, _pt(  9.1908, 45.4660)),
    City(11, 1, "Mêntina",        8000, 1245, 45.5000,   9.5000, _pt(  9.5000, 45.5000)),
    City(12, 3, "Roma",           1500, None, 40.8000,  14.5000, _pt( 14.5000, 40.8000)),
    City(13, 3, "D'Amato",        2300, None, 40.9000,  14.4000, _pt( 14.4000, 40.9000)),
    City(14, 3, "Tokyo Outpost",  1000, None, 35.6762, 139.6503, _pt(139.6503, 35.6762)),
    City(15, 3, "NullIsland",      100, None,  0.0000,   0.0000, _pt(  0.0000,  0.0000)),
    # Nota: lat=90 e' gestita SQL-server-side come 89.9999 in dialect_emit.
    City(16, 3, "PoleStation",      50, None, 90.0000,   0.0000, _pt(  0.0000, 90.0000)),
    City(17, 3, "DateLineSpot",     50, None,  0.0000, 180.0000, _pt(180.0000,  0.0000)),
    City(18, 2, "Roma Bis",          1, None, 41.9028,  12.4964, _pt( 12.4964, 41.9028)),
)


# ── CUSTOMERS ───────────────────────────────────────────────────────────

BASE_CUSTOMERS: tuple[Customer, ...] = (
    Customer( 1,    1, "Anna Rossi",         "anna.rossi@example.com",      34, date(2023,  1, 15), "active"),
    Customer( 2,    1, "Marco Bianchi",      "marco.bianchi@example.com",   41, date(2023,  2, 20), "active"),
    Customer( 3,    1, "Giulia Verdi",       "giulia.verdi@example.com",    28, date(2023,  3, 10), "pending"),
    Customer( 4,    3, "Paolo Ferrari",      "paolo.ferrari@example.com",   52, date(2023,  4,  5), "active"),
    Customer( 5,    4, "Laura Romano",       "laura.romano@example.com",    45, date(2023,  5, 12), "inactive"),
    Customer( 6,    4, "Stefano Conti",      "stefano.conti@example.com",   37, date(2023,  6, 18), "active"),
    Customer( 7,    4, "Elena Greco",        "elena.greco@example.com",     29, date(2023,  7, 22), "active"),
    Customer( 8,    5, "Davide Marini",      "davide.marini@example.com",   60, date(2023,  8,  8), "inactive"),
    Customer( 9,    7, "Chiara Esposito",    "chiara.esposito@example.com", 31, date(2023,  9, 14), "active"),
    Customer(10,    7, "Luca Russo",         "luca.russo@example.com",      48, date(2023, 10, 25), "pending"),
    Customer(11,    8, "Francesca Galli",    "francesca.galli@example.com", 39, date(2023, 11,  3), "active"),
    Customer(12, None, "Roberto Costa",      "roberto.costa@example.com",   55, date(2023, 12, 19), "active"),
)
EDGE_CUSTOMERS: tuple[Customer, ...] = (
    Customer(13,    1, "O'Brien Patrick",  "patrick.obrien@example.com", 36, date(2024, 1, 10), "active"),
    Customer(14,    1, "Müller Hans",      "hans.muller@example.com",    44, date(2024, 1, 15), "active"),
    Customer(15,    1, "北京 用户",         "beijing.user@example.com",   29, date(2024, 2,  1), "active"),
    Customer(16,    1, "  Anna Rossi  ",   "anna.spazi@example.com",     34, date(2024, 2, 10), "active"),
    Customer(17,    4, "anna ROSSI",       "anna.case@example.com",      34, date(2024, 2, 15), "active"),
    Customer(18,    1, "Anna Rossi",       "anna.rossi.dup@example.com", 34, date(2024, 2, 20), "active"),
    Customer(19,    4, "EmojiUser 🎉",     "emoji.user@example.com",     22, date(2024, 3,  1), "active"),
    Customer(20, None, "Empty Status",     "empty.status@example.com",   30, date(2024, 3, 15), ""),
)
NULL_CUSTOMERS: tuple[Customer, ...] = (
    Customer(21,    1, "Null Age User",   "null.age@example.com", None, date(2024, 4, 1), "active"),
    Customer(22,    1, "Null Email User", None,                     33, date(2024, 4, 5), "active"),
)


# ── PRODUCTS ────────────────────────────────────────────────────────────

BASE_PRODUCTS: tuple[Product, ...] = (
    Product(1, "ELE-001", "Smartphone X1",     "ELECTRONICS",  _D("599.00"),  _D("0.180"),   50),
    Product(2, "ELE-002", "Laptop Pro 14",     "ELECTRONICS", _D("1299.00"),  _D("1.500"),   20),
    Product(3, "ELE-003", "Wireless Earbuds",  "ELECTRONICS",   _D("99.00"),  _D("0.050"),  200),
    Product(4, "FOO-001", "Olive Oil 1L",      "FOOD",          _D("12.50"),  _D("0.920"),  500),
    Product(5, "FOO-002", "Pasta Pack 500g",   "FOOD",           _D("1.80"),  _D("0.500"), 1000),
    Product(6, "CLO-001", "Cotton T-Shirt",    "CLOTHING",      _D("19.90"),  _D("0.250"),  300),
    Product(7, "CLO-002", "Denim Jeans",       "CLOTHING",      _D("59.00"),  _D("0.700"),  150),
    Product(8, "CLO-003", "Wool Sweater",      "CLOTHING",      _D("89.00"),  _D("0.600"),    0),
)
EDGE_PRODUCTS: tuple[Product, ...] = (
    Product( 9, "BOO-001",  "O'Reilly Programming Book",  "BOOKS",      _D("0.00"), _D("0.500"),  10),
    Product(10, "FOO-VOID", "Mystery Item",                "FOOD",   _D("9999.99"), _D("0.001"),   1),
    # Backslash literal nel name: dialect_emit gestisce escape per MySQL.
    Product(11, "CLO-LRG",  "Wool Sweater XL\\Special",    "CLOTHING",  _D("89.00"), _D("0.600"),   5),
    Product(12, "FOO-Q",    "A'B'C",                       "FOOD",       _D("1.00"), _D("0.100"),   1),
)
NULL_PRODUCTS: tuple[Product, ...] = (
    Product(13, "WGT-NULL", "Weightless Item", "FOOD", _D("5.00"), None, 100),
)


# ── ORDERS ──────────────────────────────────────────────────────────────

BASE_ORDERS: tuple[Order, ...] = (
    Order( 1,  1, datetime(2024, 1, 10,  9, 30,  0),  _D("698.00"),  "paid"),
    Order( 2,  1, datetime(2024, 2, 15, 14, 22,  0),  _D("118.90"),  "paid"),
    Order( 3,  2, datetime(2024, 1, 20, 11,  0,  0), _D("1299.00"),  "paid"),
    Order( 4,  2, datetime(2024, 3,  5, 16, 45,  0),   _D("25.00"),  "cancelled"),
    Order( 5,  4, datetime(2024, 2, 28, 10, 15,  0),  _D("239.50"),  "paid"),
    Order( 6,  5, datetime(2024, 3, 12, 13,  0,  0),  _D("148.80"),  "paid"),
    Order( 7,  6, datetime(2024, 4,  2,  9,  0,  0),   _D("59.00"),  "pending"),
    Order( 8,  6, datetime(2024, 4, 18, 15, 30,  0),  _D("199.00"),  "paid"),
    Order( 9,  7, datetime(2024, 5,  1, 12,  0,  0),   _D("39.80"),  "paid"),
    Order(10,  7, datetime(2024, 5, 15, 18, 20,  0),   _D("89.00"),  "pending"),
    Order(11,  9, datetime(2024, 6,  8, 10, 30,  0), _D("1318.00"),  "paid"),
    Order(12,  9, datetime(2024, 6, 22, 14,  0,  0),   _D("24.50"),  "cancelled"),
    Order(13, 10, datetime(2024, 7,  4, 11, 45,  0),  _D("179.10"),  "pending"),
    Order(14, 11, datetime(2024, 7, 30,  9,  0,  0),  _D("198.00"),  "paid"),
    Order(15, 11, datetime(2024, 8, 12, 16, 15,  0),  _D("118.00"),  "pending"),
)
EDGE_ORDERS: tuple[Order, ...] = (
    Order(16, 1, datetime(2023, 12, 31, 23, 59,  0),   _D("0.00"),  "paid"),
    Order(17, 1, datetime(2024,  2, 29,  0,  0,  0),  _D("50.00"),  "paid"),
    Order(18, 1, datetime(2025,  1,  1,  3, 30,  0), _D("100.00"),  "paid"),
    Order(19, 1, datetime(1999,  6, 15, 14,  0,  0),  _D("75.00"),  "paid"),
    Order(20, 1, datetime(2024, 12, 31, 23, 59, 59),  _D("25.00"),  "pending"),
)
NULL_ORDERS: tuple[Order, ...] = (
    Order(21, 21, datetime(2024, 4, 10, 10, 0, 0), None, "pending"),
)


# ── ORDER_ITEMS ─────────────────────────────────────────────────────────

BASE_ORDER_ITEMS: tuple[OrderItem, ...] = (
    OrderItem( 1,  1, 1,  1,  _D("599.00"),  _D("599.00")),
    OrderItem( 2,  1, 3,  1,   _D("99.00"),   _D("99.00")),
    OrderItem( 3,  2, 6,  1,   _D("19.90"),   _D("19.90")),
    OrderItem( 4,  2, 7,  1,   _D("59.00"),   _D("59.00")),
    OrderItem( 5,  2, 4,  2,   _D("12.50"),   _D("25.00")),
    OrderItem( 6,  3, 2,  1, _D("1299.00"), _D("1299.00")),
    OrderItem( 7,  4, 4,  2,   _D("12.50"),   _D("25.00")),
    OrderItem( 8,  5, 6,  4,   _D("19.90"),   _D("79.60")),
    OrderItem( 9,  5, 7,  1,   _D("59.00"),   _D("59.00")),
    OrderItem(10,  5, 8,  1,   _D("89.00"),   _D("89.00")),
    OrderItem(11,  5, 5,  7,    _D("1.80"),   _D("12.60")),
    OrderItem(12,  6, 7,  2,   _D("59.00"),  _D("118.00")),
    OrderItem(13,  6, 4,  1,   _D("12.50"),   _D("12.50")),
    OrderItem(14,  6, 5, 10,    _D("1.80"),   _D("18.00")),
    OrderItem(15,  6, 3,  0,   _D("99.00"),    _D("0.00")),  # qty=0 stub
    OrderItem(16,  7, 7,  1,   _D("59.00"),   _D("59.00")),
    OrderItem(17,  8, 1,  1,  _D("199.00"),  _D("199.00")),  # discount price
    OrderItem(18,  9, 6,  2,   _D("19.90"),   _D("39.80")),
    OrderItem(19, 10, 8,  1,   _D("89.00"),   _D("89.00")),
    OrderItem(20, 11, 2,  1, _D("1299.00"), _D("1299.00")),
    OrderItem(21, 11, 3,  1,   _D("19.00"),   _D("19.00")),
    OrderItem(22, 12, 5,  5,    _D("1.80"),    _D("9.00")),
    OrderItem(23, 12, 4,  1,   _D("15.50"),   _D("15.50")),
    OrderItem(24, 13, 7,  3,   _D("59.00"),  _D("177.00")),
    OrderItem(25, 13, 5,  1,    _D("2.10"),    _D("2.10")),
    OrderItem(26, 14, 6,  5,   _D("19.90"),   _D("99.50")),
    OrderItem(27, 14, 7,  1,   _D("59.00"),   _D("59.00")),
    OrderItem(28, 14, 4,  3,   _D("13.00"),   _D("39.00")),
    OrderItem(29, 14, 5,  1,    _D("0.50"),    _D("0.50")),
    OrderItem(30, 15, 8,  1,  _D("118.00"),  _D("118.00")),
)
EDGE_ORDER_ITEMS: tuple[OrderItem, ...] = (
    OrderItem(31, 16,  9,    1,    _D("0.00"),    _D("0.00")),
    OrderItem(32, 17, 10,    1, _D("9999.99"), _D("9999.99")),
    OrderItem(33, 18,  7,    1,   _D("59.00"),   _D("99.00")),  # inconsistente
    OrderItem(34, 19,  5, 1000,    _D("0.05"),   _D("50.00")),
    OrderItem(35, 20, 11,    1,   _D("25.00"),   _D("25.00")),
)


# ── EMPLOYEES ───────────────────────────────────────────────────────────

BASE_EMPLOYEES: tuple[Employee, ...] = (
    Employee( 1, None, "Alice CEO",          "manager", _D("12000.00"), date(2018, 1,  1)),
    Employee( 2, None, "Bruno COO",          "manager", _D("11000.00"), date(2018, 3, 15)),
    Employee( 3,    1, "Carla Senior Dev",   "senior",   _D("6500.00"), date(2019, 5, 20)),
    Employee( 4,    1, "Diego Senior PM",    "senior",   _D("6800.00"), date(2019, 7, 10)),
    Employee( 5,    2, "Eva Senior Sales",   "senior",   _D("7000.00"), date(2020, 2,  3)),
    Employee( 6,    3, "Franco Junior Dev",  "junior",   _D("3200.00"), date(2021, 9,  1)),
    Employee( 7,    3, "Greta Junior Dev",   "junior",   _D("3300.00"), date(2022, 1, 15)),
    Employee( 8,    4, "Hans Junior PM",     "junior",   _D("3100.00"), date(2022, 6, 20)),
    Employee( 9,    5, "Irene Junior Sales", "junior",   _D("3000.00"), date(2023, 3, 10)),
    Employee(10,    5, "Jacopo Junior Sales","junior",   _D("2900.00"), date(2023, 8, 25)),
)
EDGE_EMPLOYEES: tuple[Employee, ...] = (
    Employee(11,    6, "Kira Helper",     "helper",   _D("2000.00"), date(2024,  1, 15)),
    Employee(12,   11, "Lia Intern",      "intern",   _D("1000.00"), date(2024,  6,  1)),
    Employee(13, None, "Mario Boss-Lone", "manager", _D("15000.00"), date(2017,  5, 10)),
    Employee(14,    2, "Néstor López",    "senior",   _D("7200.00"), date(2021, 11, 15)),
    Employee(15,   14, "O'Sullivan Tom",  "junior",   _D("3500.00"), date(2023,  4, 20)),
)
NULL_EMPLOYEES: tuple[Employee, ...] = (
    Employee(16, 1, "Null Salary Emp",   "senior", None,            date(2022, 1, 10)),
    Employee(17, 1, "Null HireDate Emp", "junior", _D("3000.00"),   None),
)


# ── ROUTES ──────────────────────────────────────────────────────────────

BASE_ROUTES: tuple[Route, ...] = (
    Route(1, 1, 4, "Milano - Roma",      _D("478.00"),
          LineString2D(((9.1900, 45.4642), (12.4964, 41.9028)))),
    Route(2, 3, 1, "Brescia - Milano",    _D("80.00"),
          LineString2D(((10.2200, 45.5398), (9.1900, 45.4642)))),
    Route(3, 4, 7, "Roma - Napoli",      _D("188.00"),
          LineString2D(((12.4964, 41.9028), (14.2681, 40.8518)))),
    Route(4, 7, 8, "Napoli - Salerno",    _D("50.00"),
          LineString2D(((14.2681, 40.8518), (14.7681, 40.6824)))),
    # Edge: cross-meridian. Per SQL Server vogliamo split a 90E in
    # dialect_emit (rifiuta segmenti antipodali esatti).
    Route(5, 15, 17, "NullIsland - DateLine (cross-meridian)", _D("20015.00"),
          LineString2D(((0.0000, 0.0000), (180.0000, 0.0000)))),
)


# ── EVENTS_LOG (volume) — generato deterministicamente ──────────────────

def make_events_log(rows: int) -> list[EventLog]:
    """Genera ``rows`` righe di events_log con la stessa distribuzione
    deterministica dei file ``02_volume.sql``:

        customer_id  = ((id-1) % 12) + 1                 (cycle 1..12)
        event_type   = id % 4   (0=view, 1=click, 2=purchase, 3=logout)
        occurred_at  = '2024-01-01' + (id % 365) days
        value        = NULL ogni 5 righe, altrimenti id * 0.10

    NB: ``customer_id`` cicla 1..12, quindi per essere FK-safe il
    seed BASE_CUSTOMERS deve esistere prima del volume.
    """
    base_dt = datetime(2024, 1, 1, 0, 0, 0)
    out: list[EventLog] = []
    for i in range(1, rows + 1):
        customer_id = ((i - 1) % 12) + 1
        et = i % 4
        if et == 0:
            event_type = "view"
        elif et == 1:
            event_type = "click"
        elif et == 2:
            event_type = "purchase"
        else:
            event_type = "logout"
        # date arithmetic via timedelta
        from datetime import timedelta
        occurred_at = base_dt + timedelta(days=i % 365)
        if i % 5 == 0:
            value = None
        else:
            # i * 0.10 con due decimali
            value = (Decimal(i) * Decimal("0.10")).quantize(Decimal("0.01"))
        out.append(EventLog(
            id=i, customer_id=customer_id, event_type=event_type,
            occurred_at=occurred_at, value=value,
        ))
    return out


# ── builder dataset ─────────────────────────────────────────────────────

DEFAULT_ROWS_EVENTS = 10000


def build_baseline_dataset() -> SeedDataset:
    """Solo blocco BASE (12 customer, 8 product, ...). Niente edge/null/volume."""
    return SeedDataset(
        regions=list(BASE_REGIONS),
        cities=list(BASE_CITIES),
        customers=list(BASE_CUSTOMERS),
        products=list(BASE_PRODUCTS),
        orders=list(BASE_ORDERS),
        order_items=list(BASE_ORDER_ITEMS),
        employees=list(BASE_EMPLOYEES),
        routes=[],
        events_log=[],
    )


def build_dataset(
    *,
    include_edge: bool = True,
    include_null: bool = True,
    include_routes: bool = True,
    rows_events: int = 0,
) -> SeedDataset:
    """Compone il dataset completo (FK-safe).

    Ordine inserimento (rispecchia gli SQL esistenti):

        1. regions (base, poi edge)
        2. cities (base, poi edge)
        3. customers (base, poi edge, poi null)
        4. products (base, poi edge, poi null)
        5. orders (base, poi edge, poi null)
        6. order_items (base, poi edge)
        7. employees (base, poi edge, poi null)
        8. routes (base solo, copre cross-meridian via id 5)
        9. events_log (deterministico, ``rows_events`` righe)
    """
    ds = build_baseline_dataset()
    if include_edge:
        ds.regions.extend(EDGE_REGIONS)
        ds.cities.extend(EDGE_CITIES)
        ds.customers.extend(EDGE_CUSTOMERS)
        ds.products.extend(EDGE_PRODUCTS)
        ds.orders.extend(EDGE_ORDERS)
        ds.order_items.extend(EDGE_ORDER_ITEMS)
        ds.employees.extend(EDGE_EMPLOYEES)
    if include_null:
        ds.customers.extend(NULL_CUSTOMERS)
        ds.products.extend(NULL_PRODUCTS)
        ds.orders.extend(NULL_ORDERS)
        ds.employees.extend(NULL_EMPLOYEES)
    if include_routes:
        ds.routes = list(BASE_ROUTES)
    if rows_events > 0:
        ds.events_log = make_events_log(rows_events)
    return ds
