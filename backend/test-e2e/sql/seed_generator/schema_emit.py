"""Render schema DDL per dialetto.

Lo schema replica i ``00_schema.sql`` esistenti — utile per test
isolati che vogliono auto-contenere `schema + seed` in un singolo
file generato.

Mantenere allineato a:
    test-e2e/sql/{postgres,mysql,oracle,sqlserver}/00_schema.sql
"""
from __future__ import annotations


_POSTGRES_SCHEMA = """\
-- Plenora E2E test dataset — schema PostgreSQL (generato)
-- Richiede PostGIS: CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS routes CASCADE;
DROP TABLE IF EXISTS events_log CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS cities CASCADE;
DROP TABLE IF EXISTS regions CASCADE;
DROP TABLE IF EXISTS employees CASCADE;

CREATE TABLE regions (
    id        INTEGER     PRIMARY KEY,
    code      VARCHAR(8)  NOT NULL UNIQUE,
    name      VARCHAR(64) NOT NULL,
    boundary  geometry(MultiPolygon, 4326)
);

CREATE TABLE cities (
    id            INTEGER          PRIMARY KEY,
    region_id     INTEGER          NOT NULL REFERENCES regions(id),
    name          VARCHAR(64)      NOT NULL,
    population    INTEGER          NOT NULL,
    founded_year  INTEGER,
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    geom          geometry(Point, 4326)
);
CREATE INDEX cities_region_idx ON cities(region_id);
CREATE INDEX cities_geom_idx   ON cities USING GIST(geom);

CREATE TABLE customers (
    id           INTEGER     PRIMARY KEY,
    city_id      INTEGER     REFERENCES cities(id),
    full_name    VARCHAR(64) NOT NULL,
    email        VARCHAR(96) UNIQUE,
    age          INTEGER,
    signup_date  DATE        NOT NULL,
    status       VARCHAR(16) NOT NULL
);
CREATE INDEX customers_city_idx ON customers(city_id);

CREATE TABLE products (
    id              INTEGER        PRIMARY KEY,
    code            VARCHAR(16)    NOT NULL UNIQUE,
    name            VARCHAR(64)    NOT NULL,
    category        VARCHAR(32)    NOT NULL,
    price           DECIMAL(12, 2) NOT NULL,
    weight_kg       DECIMAL(8, 3),
    in_stock_count  INTEGER        NOT NULL
);

CREATE TABLE orders (
    id            INTEGER        PRIMARY KEY,
    customer_id   INTEGER        NOT NULL REFERENCES customers(id),
    order_date    TIMESTAMP      NOT NULL,
    total_amount  DECIMAL(12, 2),
    status        VARCHAR(16)    NOT NULL
);
CREATE INDEX orders_customer_idx ON orders(customer_id);

CREATE TABLE order_items (
    id           INTEGER        PRIMARY KEY,
    order_id     INTEGER        NOT NULL REFERENCES orders(id),
    product_id   INTEGER        NOT NULL REFERENCES products(id),
    qty          INTEGER        NOT NULL,
    unit_price   DECIMAL(12, 2) NOT NULL,
    line_total   DECIMAL(12, 2) NOT NULL
);
CREATE INDEX order_items_order_idx   ON order_items(order_id);
CREATE INDEX order_items_product_idx ON order_items(product_id);

CREATE TABLE employees (
    id          INTEGER        PRIMARY KEY,
    manager_id  INTEGER        REFERENCES employees(id),
    name        VARCHAR(64)    NOT NULL,
    role        VARCHAR(32)    NOT NULL,
    salary      DECIMAL(12, 2),
    hire_date   DATE
);
CREATE INDEX employees_manager_idx ON employees(manager_id);

CREATE TABLE events_log (
    id            BIGINT       PRIMARY KEY,
    customer_id   INTEGER      NOT NULL REFERENCES customers(id),
    event_type    VARCHAR(32)  NOT NULL,
    occurred_at   TIMESTAMP    NOT NULL,
    value         DECIMAL(12, 2)
);
CREATE INDEX events_log_customer_idx   ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx   ON events_log(occurred_at);

CREATE TABLE routes (
    id            INTEGER        PRIMARY KEY,
    city_from_id  INTEGER        NOT NULL REFERENCES cities(id),
    city_to_id    INTEGER        NOT NULL REFERENCES cities(id),
    name          VARCHAR(120)   NOT NULL,
    length_km     DECIMAL(8, 2)  NOT NULL,
    geom          geometry(LineString, 4326)
);
CREATE INDEX routes_from_idx ON routes(city_from_id);
CREATE INDEX routes_to_idx   ON routes(city_to_id);
CREATE INDEX routes_geom_idx ON routes USING GIST(geom);
"""


_MYSQL_SCHEMA = """\
-- Plenora E2E test dataset — schema MySQL 8 (generato)

DROP TABLE IF EXISTS routes;
DROP TABLE IF EXISTS events_log;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS cities;
DROP TABLE IF EXISTS regions;
DROP TABLE IF EXISTS employees;

CREATE TABLE regions (
    id        INT          PRIMARY KEY,
    code      VARCHAR(8)   NOT NULL UNIQUE,
    name      VARCHAR(64)  NOT NULL,
    boundary  MULTIPOLYGON SRID 4326
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE cities (
    id            INT             PRIMARY KEY,
    region_id     INT             NOT NULL,
    name          VARCHAR(64)     NOT NULL,
    population    INT             NOT NULL,
    founded_year  INT,
    latitude      DOUBLE          NOT NULL,
    longitude     DOUBLE          NOT NULL,
    geom          POINT           SRID 4326,
    INDEX cities_region_idx (region_id),
    SPATIAL INDEX cities_geom_idx (geom),
    CONSTRAINT cities_region_fk FOREIGN KEY (region_id) REFERENCES regions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customers (
    id           INT          PRIMARY KEY,
    city_id      INT,
    full_name    VARCHAR(64)  NOT NULL,
    email        VARCHAR(96)  UNIQUE,
    age          INT,
    signup_date  DATE         NOT NULL,
    status       VARCHAR(16)  NOT NULL,
    INDEX customers_city_idx (city_id),
    CONSTRAINT customers_city_fk FOREIGN KEY (city_id) REFERENCES cities(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
    id              INT             PRIMARY KEY,
    code            VARCHAR(16)     NOT NULL UNIQUE,
    name            VARCHAR(64)     NOT NULL,
    category        VARCHAR(32)     NOT NULL,
    price           DECIMAL(12, 2)  NOT NULL,
    weight_kg       DECIMAL(8, 3),
    in_stock_count  INT             NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
    id            INT             PRIMARY KEY,
    customer_id   INT             NOT NULL,
    order_date    DATETIME        NOT NULL,
    total_amount  DECIMAL(12, 2),
    status        VARCHAR(16)     NOT NULL,
    INDEX orders_customer_idx (customer_id),
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
    id           INT             PRIMARY KEY,
    order_id     INT             NOT NULL,
    product_id   INT             NOT NULL,
    qty          INT             NOT NULL,
    unit_price   DECIMAL(12, 2)  NOT NULL,
    line_total   DECIMAL(12, 2)  NOT NULL,
    INDEX order_items_order_idx (order_id),
    INDEX order_items_product_idx (product_id),
    CONSTRAINT order_items_order_fk   FOREIGN KEY (order_id)   REFERENCES orders(id),
    CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE employees (
    id          INT             PRIMARY KEY,
    manager_id  INT,
    name        VARCHAR(64)     NOT NULL,
    role        VARCHAR(32)     NOT NULL,
    salary      DECIMAL(12, 2),
    hire_date   DATE,
    INDEX employees_manager_idx (manager_id),
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE events_log (
    id            BIGINT          PRIMARY KEY,
    customer_id   INT             NOT NULL,
    event_type    VARCHAR(32)     NOT NULL,
    occurred_at   DATETIME        NOT NULL,
    value         DECIMAL(12, 2),
    INDEX events_log_customer_idx (customer_id),
    INDEX events_log_occurred_idx (occurred_at),
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE routes (
    id            INT             PRIMARY KEY,
    city_from_id  INT             NOT NULL,
    city_to_id    INT             NOT NULL,
    name          VARCHAR(120)    NOT NULL,
    length_km     DECIMAL(8, 2)   NOT NULL,
    geom          LINESTRING      SRID 4326,
    INDEX routes_from_idx (city_from_id),
    INDEX routes_to_idx   (city_to_id),
    SPATIAL INDEX routes_geom_idx (geom),
    CONSTRAINT routes_from_fk FOREIGN KEY (city_from_id) REFERENCES cities(id),
    CONSTRAINT routes_to_fk   FOREIGN KEY (city_to_id)   REFERENCES cities(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


_ORACLE_SCHEMA = """\
-- Plenora E2E test dataset — schema Oracle 19c+ (generato)

BEGIN
    FOR t IN (
        SELECT 'routes'      AS name FROM dual UNION ALL
        SELECT 'events_log'  FROM dual UNION ALL
        SELECT 'order_items' FROM dual UNION ALL
        SELECT 'orders'      FROM dual UNION ALL
        SELECT 'products'    FROM dual UNION ALL
        SELECT 'customers'   FROM dual UNION ALL
        SELECT 'cities'      FROM dual UNION ALL
        SELECT 'regions'     FROM dual UNION ALL
        SELECT 'employees'   FROM dual
    ) LOOP
        BEGIN
            EXECUTE IMMEDIATE 'DROP TABLE ' || t.name || ' CASCADE CONSTRAINTS';
        EXCEPTION WHEN OTHERS THEN
            IF SQLCODE != -942 THEN
                RAISE;
            END IF;
        END;
    END LOOP;
END;
/

CREATE TABLE regions (
    id        NUMBER(10)   PRIMARY KEY,
    code      VARCHAR2(8)  NOT NULL UNIQUE,
    name      VARCHAR2(64) NOT NULL,
    boundary  SDO_GEOMETRY
);

CREATE TABLE cities (
    id            NUMBER(10)     PRIMARY KEY,
    region_id     NUMBER(10)     NOT NULL,
    name          VARCHAR2(64)   NOT NULL,
    population    NUMBER(10)     NOT NULL,
    founded_year  NUMBER(10),
    latitude      BINARY_DOUBLE  NOT NULL,
    longitude     BINARY_DOUBLE  NOT NULL,
    geom          SDO_GEOMETRY,
    CONSTRAINT cities_region_fk FOREIGN KEY (region_id) REFERENCES regions(id)
);
CREATE INDEX cities_region_idx ON cities(region_id);

CREATE TABLE customers (
    id           NUMBER(10)    PRIMARY KEY,
    city_id      NUMBER(10),
    full_name    VARCHAR2(64)  NOT NULL,
    email        VARCHAR2(96)  UNIQUE,
    age          NUMBER(10),
    signup_date  DATE          NOT NULL,
    status       VARCHAR2(16)  NOT NULL,
    CONSTRAINT customers_city_fk FOREIGN KEY (city_id) REFERENCES cities(id)
);
CREATE INDEX customers_city_idx ON customers(city_id);

CREATE TABLE products (
    id              NUMBER(10)     PRIMARY KEY,
    code            VARCHAR2(16)   NOT NULL UNIQUE,
    name            VARCHAR2(64)   NOT NULL,
    category        VARCHAR2(32)   NOT NULL,
    price           NUMBER(12, 2)  NOT NULL,
    weight_kg       NUMBER(8, 3),
    in_stock_count  NUMBER(10)     NOT NULL
);

CREATE TABLE orders (
    id            NUMBER(10)     PRIMARY KEY,
    customer_id   NUMBER(10)     NOT NULL,
    order_date    TIMESTAMP      NOT NULL,
    total_amount  NUMBER(12, 2),
    status        VARCHAR2(16)   NOT NULL,
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE INDEX orders_customer_idx ON orders(customer_id);

CREATE TABLE order_items (
    id           NUMBER(10)     PRIMARY KEY,
    order_id     NUMBER(10)     NOT NULL,
    product_id   NUMBER(10)     NOT NULL,
    qty          NUMBER(10)     NOT NULL,
    unit_price   NUMBER(12, 2)  NOT NULL,
    line_total   NUMBER(12, 2)  NOT NULL,
    CONSTRAINT order_items_order_fk   FOREIGN KEY (order_id)   REFERENCES orders(id),
    CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE INDEX order_items_order_idx   ON order_items(order_id);
CREATE INDEX order_items_product_idx ON order_items(product_id);

CREATE TABLE employees (
    id          NUMBER(10)     PRIMARY KEY,
    manager_id  NUMBER(10),
    name        VARCHAR2(64)   NOT NULL,
    role        VARCHAR2(32)   NOT NULL,
    salary      NUMBER(12, 2),
    hire_date   DATE,
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
);
CREATE INDEX employees_manager_idx ON employees(manager_id);

CREATE TABLE events_log (
    id            NUMBER(19)     PRIMARY KEY,
    customer_id   NUMBER(10)     NOT NULL,
    event_type    VARCHAR2(32)   NOT NULL,
    occurred_at   TIMESTAMP      NOT NULL,
    value         NUMBER(12, 2),
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE INDEX events_log_customer_idx ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx ON events_log(occurred_at);

CREATE TABLE routes (
    id            NUMBER(10)     PRIMARY KEY,
    city_from_id  NUMBER(10)     NOT NULL,
    city_to_id    NUMBER(10)     NOT NULL,
    name          VARCHAR2(120)  NOT NULL,
    length_km     NUMBER(8, 2)   NOT NULL,
    geom          SDO_GEOMETRY,
    CONSTRAINT routes_from_fk FOREIGN KEY (city_from_id) REFERENCES cities(id),
    CONSTRAINT routes_to_fk   FOREIGN KEY (city_to_id)   REFERENCES cities(id)
);
CREATE INDEX routes_from_idx ON routes(city_from_id);
CREATE INDEX routes_to_idx   ON routes(city_to_id);

COMMIT;
"""


_MSSQL_SCHEMA = """\
-- Plenora E2E test dataset — schema SQL Server 2017+ (generato)

IF OBJECT_ID('routes',      'U') IS NOT NULL DROP TABLE routes;
IF OBJECT_ID('events_log',  'U') IS NOT NULL DROP TABLE events_log;
IF OBJECT_ID('order_items', 'U') IS NOT NULL DROP TABLE order_items;
IF OBJECT_ID('orders',      'U') IS NOT NULL DROP TABLE orders;
IF OBJECT_ID('products',    'U') IS NOT NULL DROP TABLE products;
IF OBJECT_ID('customers',   'U') IS NOT NULL DROP TABLE customers;
IF OBJECT_ID('cities',      'U') IS NOT NULL DROP TABLE cities;
IF OBJECT_ID('regions',     'U') IS NOT NULL DROP TABLE regions;
IF OBJECT_ID('employees',   'U') IS NOT NULL DROP TABLE employees;

CREATE TABLE regions (
    id        INT          PRIMARY KEY,
    code      VARCHAR(8)   NOT NULL UNIQUE,
    name      VARCHAR(64)  NOT NULL,
    boundary  GEOGRAPHY    NULL
);

CREATE TABLE cities (
    id            INT             PRIMARY KEY,
    region_id     INT             NOT NULL,
    name          VARCHAR(64)     NOT NULL,
    population    INT             NOT NULL,
    founded_year  INT             NULL,
    latitude      FLOAT           NOT NULL,
    longitude     FLOAT           NOT NULL,
    geom          GEOGRAPHY       NULL,
    CONSTRAINT cities_region_fk FOREIGN KEY (region_id) REFERENCES regions(id)
);
CREATE INDEX cities_region_idx ON cities(region_id);

CREATE TABLE customers (
    id           INT          PRIMARY KEY,
    city_id      INT          NULL,
    full_name    VARCHAR(64)  NOT NULL,
    email        VARCHAR(96)  NULL UNIQUE,
    age          INT          NULL,
    signup_date  DATE         NOT NULL,
    status       VARCHAR(16)  NOT NULL,
    CONSTRAINT customers_city_fk FOREIGN KEY (city_id) REFERENCES cities(id)
);
CREATE INDEX customers_city_idx ON customers(city_id);

CREATE TABLE products (
    id              INT             PRIMARY KEY,
    code            VARCHAR(16)     NOT NULL UNIQUE,
    name            VARCHAR(64)     NOT NULL,
    category        VARCHAR(32)     NOT NULL,
    price           DECIMAL(12, 2)  NOT NULL,
    weight_kg       DECIMAL(8, 3)   NULL,
    in_stock_count  INT             NOT NULL
);

CREATE TABLE orders (
    id            INT             PRIMARY KEY,
    customer_id   INT             NOT NULL,
    order_date    DATETIME2       NOT NULL,
    total_amount  DECIMAL(12, 2)  NULL,
    status        VARCHAR(16)     NOT NULL,
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE INDEX orders_customer_idx ON orders(customer_id);

CREATE TABLE order_items (
    id           INT             PRIMARY KEY,
    order_id     INT             NOT NULL,
    product_id   INT             NOT NULL,
    qty          INT             NOT NULL,
    unit_price   DECIMAL(12, 2)  NOT NULL,
    line_total   DECIMAL(12, 2)  NOT NULL,
    CONSTRAINT order_items_order_fk   FOREIGN KEY (order_id)   REFERENCES orders(id),
    CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE INDEX order_items_order_idx   ON order_items(order_id);
CREATE INDEX order_items_product_idx ON order_items(product_id);

CREATE TABLE employees (
    id          INT             PRIMARY KEY,
    manager_id  INT             NULL,
    name        VARCHAR(64)     NOT NULL,
    role        VARCHAR(32)     NOT NULL,
    salary      DECIMAL(12, 2)  NULL,
    hire_date   DATE            NULL,
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
);
CREATE INDEX employees_manager_idx ON employees(manager_id);

CREATE TABLE events_log (
    id            BIGINT          PRIMARY KEY,
    customer_id   INT             NOT NULL,
    event_type    VARCHAR(32)     NOT NULL,
    occurred_at   DATETIME2       NOT NULL,
    value         DECIMAL(12, 2)  NULL,
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE INDEX events_log_customer_idx ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx ON events_log(occurred_at);

CREATE TABLE routes (
    id            INT             PRIMARY KEY,
    city_from_id  INT             NOT NULL,
    city_to_id    INT             NOT NULL,
    name          VARCHAR(120)    NOT NULL,
    length_km     DECIMAL(8, 2)   NOT NULL,
    geom          GEOGRAPHY       NULL,
    CONSTRAINT routes_from_fk FOREIGN KEY (city_from_id) REFERENCES cities(id),
    CONSTRAINT routes_to_fk   FOREIGN KEY (city_to_id)   REFERENCES cities(id)
);
CREATE INDEX routes_from_idx ON routes(city_from_id);
CREATE INDEX routes_to_idx   ON routes(city_to_id);
"""


_SCHEMAS = {
    "postgres": _POSTGRES_SCHEMA,
    "mysql":    _MYSQL_SCHEMA,
    "oracle":   _ORACLE_SCHEMA,
    "mssql":    _MSSQL_SCHEMA,
}


def emit_schema(dialect: str) -> str:
    """Ritorna lo schema DDL (CREATE TABLE + indici) per ``dialect``."""
    if dialect not in _SCHEMAS:
        raise ValueError(f"Unknown dialect: {dialect!r}")
    return _SCHEMAS[dialect]
