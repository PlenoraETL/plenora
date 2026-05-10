-- Plenora E2E test dataset — schema Oracle (19c+ con Spatial/Locator)
-- SDO_GEOMETRY è incluso in Oracle Spatial / Locator.
--
-- Esecuzione: sqlplus user/pass@host:1521/FREEPDB1 @00_schema.sql

-- DROP idempotente: Oracle non supporta DROP TABLE IF EXISTS, usiamo EXCEPTION.
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
            IF SQLCODE != -942 THEN -- ORA-00942 table does not exist
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
    boundary  SDO_GEOMETRY    -- nullable: regions senza boundary nota (MultiPolygon, gtype 2007)
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
-- Spatial index richiede metadata in user_sdo_geom_metadata; lo lasciamo
-- come passo opzionale (non bloccante per i test).

CREATE TABLE customers (
    id           NUMBER(10)    PRIMARY KEY,
    city_id      NUMBER(10),
    full_name    VARCHAR2(64)  NOT NULL,
    email        VARCHAR2(96)           UNIQUE,  -- nullable: NULL non viola UNIQUE
    age          NUMBER(10),                      -- nullable
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
    weight_kg       NUMBER(8, 3),                -- nullable
    in_stock_count  NUMBER(10)     NOT NULL
);

CREATE TABLE orders (
    id            NUMBER(10)     PRIMARY KEY,
    customer_id   NUMBER(10)     NOT NULL,
    order_date    TIMESTAMP      NOT NULL,
    total_amount  NUMBER(12, 2),                -- nullable
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
    salary      NUMBER(12, 2),               -- nullable
    hire_date   DATE,                         -- nullable
    CONSTRAINT employees_manager_fk FOREIGN KEY (manager_id) REFERENCES employees(id)
);

CREATE INDEX employees_manager_idx ON employees(manager_id);

-- events_log: tabella di volume per stress test (paginazione, fingerprint
-- sample, preview limit, cross_join_oom_guard, performance loader).
CREATE TABLE events_log (
    id            NUMBER(19)     PRIMARY KEY,
    customer_id   NUMBER(10)     NOT NULL,
    event_type    VARCHAR2(32)   NOT NULL,  -- 'view' | 'click' | 'purchase' | 'logout'
    occurred_at   TIMESTAMP      NOT NULL,
    value         NUMBER(12, 2),             -- nullable
    CONSTRAINT events_log_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE INDEX events_log_customer_idx ON events_log(customer_id);
CREATE INDEX events_log_occurred_idx ON events_log(occurred_at);

-- routes: geometrie LineString (gtype 2002) per stress test multi-vertex
-- e cross-meridian.
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
-- Spatial index opzionale (richiede metadata in user_sdo_geom_metadata).

COMMIT;
