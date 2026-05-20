-- Plenora E2E test dataset — schema PostgreSQL
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
    boundary  geometry(MultiPolygon, 4326)        -- nullable: regions senza boundary nota
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
    email        VARCHAR(96)          UNIQUE,  -- nullable: NULL non viola UNIQUE
    age          INTEGER,                       -- nullable
    signup_date  DATE        NOT NULL,
    status       VARCHAR(16) NOT NULL  -- 'active' | 'inactive' | 'pending'
);

CREATE INDEX customers_city_idx ON customers(city_id);

CREATE TABLE products (
    id              INTEGER        PRIMARY KEY,
    code            VARCHAR(16)    NOT NULL UNIQUE,
    name            VARCHAR(64)    NOT NULL,
    category        VARCHAR(32)    NOT NULL, -- 'ELECTRONICS' | 'FOOD' | 'CLOTHING'
    price           DECIMAL(12, 2) NOT NULL,
    weight_kg       DECIMAL(8, 3),            -- nullable
    in_stock_count  INTEGER        NOT NULL
);

CREATE TABLE orders (
    id            INTEGER        PRIMARY KEY,
    customer_id   INTEGER        NOT NULL REFERENCES customers(id),
    order_date    TIMESTAMP      NOT NULL,
    total_amount  DECIMAL(12, 2),           -- nullable
    status        VARCHAR(16)    NOT NULL  -- 'paid' | 'pending' | 'cancelled'
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
    role        VARCHAR(32)    NOT NULL,  -- 'manager' | 'senior' | 'junior'
    salary      DECIMAL(12, 2),           -- nullable
    hire_date   DATE                      -- nullable (employee "ex" senza data nota)
);

CREATE INDEX employees_manager_idx ON employees(manager_id);

-- events_log: tabella di volume per stress test (paginazione, fingerprint
-- sample, preview limit, cross_join_oom_guard, performance loader).
CREATE TABLE events_log (
    id            BIGINT       PRIMARY KEY,
    customer_id   INTEGER      NOT NULL REFERENCES customers(id),
    event_type    VARCHAR(32)  NOT NULL,  -- 'view' | 'click' | 'purchase' | 'logout'
    occurred_at   TIMESTAMP    NOT NULL,
    value         DECIMAL(12, 2)          -- nullable
);

CREATE INDEX events_log_customer_idx   ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx   ON events_log(occurred_at);

-- routes: geometrie LineString per stress test multi-vertex e cross-meridian.
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
