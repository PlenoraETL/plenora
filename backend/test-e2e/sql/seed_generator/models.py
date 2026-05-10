"""Modelli dataclass per le tabelle del seed E2E plenora.

Le dataclass sono *dialect-neutral*: ogni record contiene i valori
"raw" (Python types) — lat/lng come float, date come ``datetime.date``,
ecc. La conversione SQL idiomatica (geometrie WKT/WKB/SDO,
DATE/TIMESTAMP literal, escape stringhe) avviene in ``dialect_emit``.

Nove tabelle baseline (allineate a `00_schema.sql` di tutti i dialetti):
    regions, cities, customers, products, orders, order_items,
    employees, events_log, routes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Tuple, List


# ── Geometry payloads (dialect-neutral) ─────────────────────────────────

@dataclass(frozen=True)
class Point2D:
    """Punto 2D (lng, lat) in WGS84/4326. None per geometrie assenti."""
    lng: float
    lat: float


@dataclass(frozen=True)
class LineString2D:
    """LineString 2D — sequenza ordinata di vertici (lng, lat)."""
    vertices: Tuple[Tuple[float, float], ...]


@dataclass(frozen=True)
class MultiPolygon2D:
    """MultiPolygon 2D — lista di poligoni; ogni poligono e' una lista
    di anelli; ogni anello e' una sequenza di vertici (lng, lat) chiusa
    (primo == ultimo).

    Per il seed E2E usiamo SOLO exterior rings (no holes), per
    semplicita' (CAM ha 2 poligoni esterni, isola di Capri).
    """
    polygons: Tuple[Tuple[Tuple[Tuple[float, float], ...], ...], ...]


# ── Tabelle ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Region:
    id: int
    code: str
    name: str
    boundary: Optional[MultiPolygon2D]


@dataclass(frozen=True)
class City:
    id: int
    region_id: int
    name: str
    population: int
    founded_year: Optional[int]
    latitude: float
    longitude: float
    geom: Optional[Point2D]  # tipicamente == Point2D(longitude, latitude)


@dataclass(frozen=True)
class Customer:
    id: int
    city_id: Optional[int]
    full_name: str
    email: Optional[str]
    age: Optional[int]
    signup_date: date
    status: str  # 'active' | 'inactive' | 'pending' | ''


@dataclass(frozen=True)
class Product:
    id: int
    code: str
    name: str
    category: str
    price: Decimal
    weight_kg: Optional[Decimal]
    in_stock_count: int


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: datetime
    total_amount: Optional[Decimal]
    status: str


@dataclass(frozen=True)
class OrderItem:
    id: int
    order_id: int
    product_id: int
    qty: int
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class Employee:
    id: int
    manager_id: Optional[int]
    name: str
    role: str
    salary: Optional[Decimal]
    hire_date: Optional[date]


@dataclass(frozen=True)
class EventLog:
    id: int
    customer_id: int
    event_type: str  # 'view' | 'click' | 'purchase' | 'logout'
    occurred_at: datetime
    value: Optional[Decimal]


@dataclass(frozen=True)
class Route:
    id: int
    city_from_id: int
    city_to_id: int
    name: str
    length_km: Decimal
    geom: Optional[LineString2D]


# ── Bundle ──────────────────────────────────────────────────────────────

@dataclass
class SeedDataset:
    """Dataset completo (baseline + edge + null + routes + volume)."""
    regions: List[Region] = field(default_factory=list)
    cities: List[City] = field(default_factory=list)
    customers: List[Customer] = field(default_factory=list)
    products: List[Product] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    order_items: List[OrderItem] = field(default_factory=list)
    employees: List[Employee] = field(default_factory=list)
    routes: List[Route] = field(default_factory=list)
    events_log: List[EventLog] = field(default_factory=list)
