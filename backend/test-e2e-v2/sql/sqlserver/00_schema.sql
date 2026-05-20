-- Plenora E2E test dataset — schema SQL Server (≥ 2017)
-- Tipo geography per i punti (WGS84). Niente DROP IF EXISTS prima di 2016.

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
    boundary  GEOGRAPHY    NULL    -- nullable: regions senza boundary nota
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
    email        VARCHAR(96)  NULL UNIQUE,    -- nullable: NULL non viola UNIQUE
    age          INT          NULL,           -- nullable
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
    weight_kg       DECIMAL(8, 3)   NULL,       -- nullable
    in_stock_count  INT             NOT NULL
);

CREATE TABLE orders (
    id            INT             PRIMARY KEY,
    customer_id   INT             NOT NULL,
    order_date    DATETIME2       NOT NULL,
    total_amount  DECIMAL(12, 2)  NULL,         -- nullable
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
    salary      DECIMAL(12, 2)  NULL,           -- nullable
    hire_date   DATE            NULL,           -- nullable
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
);

CREATE INDEX employees_manager_idx ON employees(manager_id);

-- events_log: tabella di volume per stress test (paginazione, fingerprint
-- sample, preview limit, cross_join_oom_guard, performance loader).
CREATE TABLE events_log (
    id            BIGINT          PRIMARY KEY,
    customer_id   INT             NOT NULL,
    event_type    VARCHAR(32)     NOT NULL,    -- 'view' | 'click' | 'purchase' | 'logout'
    occurred_at   DATETIME2       NOT NULL,
    value         DECIMAL(12, 2)  NULL,         -- nullable
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE INDEX events_log_customer_idx ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx ON events_log(occurred_at);

-- routes: geometrie LineString per stress test multi-vertex e cross-meridian.
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
