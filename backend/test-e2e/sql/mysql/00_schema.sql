-- Plenora E2E test dataset — schema MySQL (≥ 8.0)
-- POINT richiede MySQL 8 per SRID nativo.

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
    boundary  MULTIPOLYGON SRID 4326   -- nullable: regions senza boundary nota
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
    email        VARCHAR(96)           UNIQUE,  -- nullable: NULL non viola UNIQUE
    age          INT,                            -- nullable
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
    weight_kg       DECIMAL(8, 3),               -- nullable
    in_stock_count  INT             NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
    id            INT             PRIMARY KEY,
    customer_id   INT             NOT NULL,
    order_date    DATETIME        NOT NULL,
    total_amount  DECIMAL(12, 2),              -- nullable
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
    salary      DECIMAL(12, 2),              -- nullable
    hire_date   DATE,                         -- nullable
    INDEX employees_manager_idx (manager_id),
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- events_log: tabella di volume per stress test (paginazione, fingerprint
-- sample, preview limit, cross_join_oom_guard, performance loader).
CREATE TABLE events_log (
    id            BIGINT          PRIMARY KEY,
    customer_id   INT             NOT NULL,
    event_type    VARCHAR(32)     NOT NULL,  -- 'view' | 'click' | 'purchase' | 'logout'
    occurred_at   DATETIME        NOT NULL,
    value         DECIMAL(12, 2),             -- nullable
    INDEX events_log_customer_idx (customer_id),
    INDEX events_log_occurred_idx (occurred_at),
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- routes: geometrie LineString per stress test multi-vertex e cross-meridian.
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
