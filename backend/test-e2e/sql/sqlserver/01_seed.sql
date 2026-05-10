-- Plenora E2E test dataset — seed SQL Server
-- I POINT sono `geography::Point(lat, lng, 4326)` (NB: lat prima di lng,
-- è la convenzione `geography` di SQL Server, opposta a PostGIS/MySQL).

-- regions ---------------------------------------------------------------------
-- boundary: GEOGRAPHY MultiPolygon (rettangoli ~1° di lato).
-- NB: geography vuole exterior ring in counter-clockwise (left-hand rule).
-- WKT axis order qui è (long, lat) come nelle altre dialetti.
-- CAM ha 2 poligoni: regione + isola di Capri (geometria non-banale).
INSERT INTO regions (id, code, name, boundary) VALUES
    (1, 'LOM', 'Lombardia',
        geography::STGeomFromText('MULTIPOLYGON(((8.5 45.0, 10.5 45.0, 10.5 46.0, 8.5 46.0, 8.5 45.0)))', 4326)),
    (2, 'LAZ', 'Lazio',
        geography::STGeomFromText('MULTIPOLYGON(((12.0 41.0, 14.0 41.0, 14.0 42.5, 12.0 42.5, 12.0 41.0)))', 4326)),
    (3, 'CAM', 'Campania',
        geography::STGeomFromText('MULTIPOLYGON(((14.0 40.5, 15.5 40.5, 15.5 41.5, 14.0 41.5, 14.0 40.5)),((14.20 40.53, 14.30 40.53, 14.30 40.57, 14.20 40.57, 14.20 40.53)))', 4326));

-- cities ----------------------------------------------------------------------
INSERT INTO cities (id, region_id, name, population, founded_year,
                    latitude, longitude, geom) VALUES
    (1, 1, 'Milano',    1396000, -222, 45.4642,  9.1900, geography::Point(45.4642,  9.1900, 4326)),
    (2, 1, 'Bergamo',    122000,  -49, 45.6983,  9.6773, geography::Point(45.6983,  9.6773, 4326)),
    (3, 1, 'Brescia',    196000,  -27, 45.5398, 10.2200, geography::Point(45.5398, 10.2200, 4326)),
    (4, 2, 'Roma',      2873000, -753, 41.9028, 12.4964, geography::Point(41.9028, 12.4964, 4326)),
    (5, 2, 'Latina',     127000, 1932, 41.4676, 12.9038, geography::Point(41.4676, 12.9038, 4326)),
    (6, 2, 'Frosinone',   46000, NULL, 41.6403, 13.3500, geography::Point(41.6403, 13.3500, 4326)),
    (7, 3, 'Napoli',     914000, -470, 40.8518, 14.2681, geography::Point(40.8518, 14.2681, 4326)),
    (8, 3, 'Salerno',    133000, -197, 40.6824, 14.7681, geography::Point(40.6824, 14.7681, 4326)),
    (9, 3, 'Caserta',     74000, 1750, 41.0721, 14.3327, geography::Point(41.0721, 14.3327, 4326));

-- customers -------------------------------------------------------------------
INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    ( 1,    1, 'Anna Rossi',         'anna.rossi@example.com',     34, '2023-01-15', 'active'),
    ( 2,    1, 'Marco Bianchi',      'marco.bianchi@example.com',  41, '2023-02-20', 'active'),
    ( 3,    1, 'Giulia Verdi',       'giulia.verdi@example.com',   28, '2023-03-10', 'pending'),
    ( 4,    3, 'Paolo Ferrari',      'paolo.ferrari@example.com',  52, '2023-04-05', 'active'),
    ( 5,    4, 'Laura Romano',       'laura.romano@example.com',   45, '2023-05-12', 'inactive'),
    ( 6,    4, 'Stefano Conti',      'stefano.conti@example.com',  37, '2023-06-18', 'active'),
    ( 7,    4, 'Elena Greco',        'elena.greco@example.com',    29, '2023-07-22', 'active'),
    ( 8,    5, 'Davide Marini',      'davide.marini@example.com',  60, '2023-08-08', 'inactive'),
    ( 9,    7, 'Chiara Esposito',    'chiara.esposito@example.com',31, '2023-09-14', 'active'),
    (10,    7, 'Luca Russo',         'luca.russo@example.com',     48, '2023-10-25', 'pending'),
    (11,    8, 'Francesca Galli',    'francesca.galli@example.com',39, '2023-11-03', 'active'),
    (12, NULL, 'Roberto Costa',      'roberto.costa@example.com',  55, '2023-12-19', 'active');

-- products --------------------------------------------------------------------
INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    (1, 'ELE-001', 'Smartphone X1',     'ELECTRONICS',  599.00, 0.180,   50),
    (2, 'ELE-002', 'Laptop Pro 14',     'ELECTRONICS', 1299.00, 1.500,   20),
    (3, 'ELE-003', 'Wireless Earbuds',  'ELECTRONICS',   99.00, 0.050,  200),
    (4, 'FOO-001', 'Olive Oil 1L',      'FOOD',          12.50, 0.920,  500),
    (5, 'FOO-002', 'Pasta Pack 500g',   'FOOD',           1.80, 0.500, 1000),
    (6, 'CLO-001', 'Cotton T-Shirt',    'CLOTHING',      19.90, 0.250,  300),
    (7, 'CLO-002', 'Denim Jeans',       'CLOTHING',      59.00, 0.700,  150),
    (8, 'CLO-003', 'Wool Sweater',      'CLOTHING',      89.00, 0.600,    0);

-- orders ----------------------------------------------------------------------
INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    ( 1,  1, '2024-01-10 09:30:00',  698.00, 'paid'),
    ( 2,  1, '2024-02-15 14:22:00',  118.90, 'paid'),
    ( 3,  2, '2024-01-20 11:00:00', 1299.00, 'paid'),
    ( 4,  2, '2024-03-05 16:45:00',   25.00, 'cancelled'),
    ( 5,  4, '2024-02-28 10:15:00',  239.50, 'paid'),
    ( 6,  5, '2024-03-12 13:00:00',  148.80, 'paid'),
    ( 7,  6, '2024-04-02 09:00:00',   59.00, 'pending'),
    ( 8,  6, '2024-04-18 15:30:00',  199.00, 'paid'),
    ( 9,  7, '2024-05-01 12:00:00',   39.80, 'paid'),
    (10,  7, '2024-05-15 18:20:00',   89.00, 'pending'),
    (11,  9, '2024-06-08 10:30:00', 1318.00, 'paid'),
    (12,  9, '2024-06-22 14:00:00',   24.50, 'cancelled'),
    (13, 10, '2024-07-04 11:45:00',  179.10, 'pending'),
    (14, 11, '2024-07-30 09:00:00',  198.00, 'paid'),
    (15, 11, '2024-08-12 16:15:00',  118.00, 'pending');

-- order_items -----------------------------------------------------------------
INSERT INTO order_items (id, order_id, product_id, qty, unit_price, line_total) VALUES
    ( 1,  1, 1,  1,  599.00,  599.00),
    ( 2,  1, 3,  1,   99.00,   99.00),
    ( 3,  2, 6,  1,   19.90,   19.90),
    ( 4,  2, 7,  1,   59.00,   59.00),
    ( 5,  2, 4,  2,   12.50,   25.00),
    ( 6,  3, 2,  1, 1299.00, 1299.00),
    ( 7,  4, 4,  2,   12.50,   25.00),
    ( 8,  5, 6,  4,   19.90,   79.60),
    ( 9,  5, 7,  1,   59.00,   59.00),
    (10,  5, 8,  1,   89.00,   89.00),
    (11,  5, 5,  7,    1.80,   12.60),
    (12,  6, 7,  2,   59.00,  118.00),
    (13,  6, 4,  1,   12.50,   12.50),
    (14,  6, 5, 10,    1.80,   18.00),
    (15,  6, 3,  0,   99.00,    0.00),
    (16,  7, 7,  1,   59.00,   59.00),
    (17,  8, 1,  1,  199.00,  199.00),
    (18,  9, 6,  2,   19.90,   39.80),
    (19, 10, 8,  1,   89.00,   89.00),
    (20, 11, 2,  1, 1299.00, 1299.00),
    (21, 11, 3,  1,   19.00,   19.00),
    (22, 12, 5,  5,    1.80,    9.00),
    (23, 12, 4,  1,   15.50,   15.50),
    (24, 13, 7,  3,   59.00,  177.00),
    (25, 13, 5,  1,    2.10,    2.10),
    (26, 14, 6,  5,   19.90,   99.50),
    (27, 14, 7,  1,   59.00,   59.00),
    (28, 14, 4,  3,   13.00,   39.00),
    (29, 14, 5,  1,    0.50,    0.50),
    (30, 15, 8,  1,  118.00,  118.00);

-- employees -------------------------------------------------------------------
INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    ( 1, NULL, 'Alice CEO',          'manager', 12000.00, '2018-01-01'),
    ( 2, NULL, 'Bruno COO',          'manager', 11000.00, '2018-03-15'),
    ( 3,    1, 'Carla Senior Dev',   'senior',   6500.00, '2019-05-20'),
    ( 4,    1, 'Diego Senior PM',    'senior',   6800.00, '2019-07-10'),
    ( 5,    2, 'Eva Senior Sales',   'senior',   7000.00, '2020-02-03'),
    ( 6,    3, 'Franco Junior Dev',  'junior',   3200.00, '2021-09-01'),
    ( 7,    3, 'Greta Junior Dev',   'junior',   3300.00, '2022-01-15'),
    ( 8,    4, 'Hans Junior PM',     'junior',   3100.00, '2022-06-20'),
    ( 9,    5, 'Irene Junior Sales', 'junior',   3000.00, '2023-03-10'),
    (10,    5, 'Jacopo Junior Sales','junior',   2900.00, '2023-08-25');

-- =============================================================================
-- Edge cases (id discontinui rispetto al seed base).
-- NB: in `geography::Point(lat, lng, 4326)` l'ordine è (lat, lng).
-- =============================================================================

INSERT INTO regions (id, code, name, boundary) VALUES
    (4, 'VEN', 'Veneto', NULL); -- regione SENZA cities né boundary (LEFT JOIN edge)

INSERT INTO cities (id, region_id, name, population, founded_year,
                    latitude, longitude, geom) VALUES
    (10, 1, 'Milano Centro',  5000, NULL, 45.4660,   9.1908, geography::Point(45.4660,   9.1908, 4326)),
    (11, 1, 'Mêntina',        8000, 1245, 45.5000,   9.5000, geography::Point(45.5000,   9.5000, 4326)),
    (12, 3, 'Roma',           1500, NULL, 40.8000,  14.5000, geography::Point(40.8000,  14.5000, 4326)),
    (13, 3, 'D''Amato',       2300, NULL, 40.9000,  14.4000, geography::Point(40.9000,  14.4000, 4326)),
    (14, 3, 'Tokyo Outpost',  1000, NULL, 35.6762, 139.6503, geography::Point(35.6762, 139.6503, 4326)),
    (15, 3, 'NullIsland',      100, NULL,  0.0000,   0.0000, geography::Point( 0.0000,   0.0000, 4326)),
    -- SQL Server geography rifiuta lat=±90 esatto: usiamo 89.9999.
    (16, 3, 'PoleStation',      50, NULL, 89.9999,   0.0000, geography::Point(89.9999,   0.0000, 4326)),
    (17, 3, 'DateLineSpot',     50, NULL,  0.0000, 180.0000, geography::Point( 0.0000, 180.0000, 4326)),
    (18, 2, 'Roma Bis',          1, NULL, 41.9028,  12.4964, geography::Point(41.9028,  12.4964, 4326));

INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    (13,    1, 'O''Brien Patrick',  'patrick.obrien@example.com', 36, '2024-01-10', 'active'),
    (14,    1, 'Müller Hans',       'hans.muller@example.com',    44, '2024-01-15', 'active'),
    (15,    1, '北京 用户',            'beijing.user@example.com',   29, '2024-02-01', 'active'),
    (16,    1, '  Anna Rossi  ',    'anna.spazi@example.com',     34, '2024-02-10', 'active'),
    (17,    4, 'anna ROSSI',        'anna.case@example.com',      34, '2024-02-15', 'active'),
    (18,    1, 'Anna Rossi',        'anna.rossi.dup@example.com', 34, '2024-02-20', 'active'),
    (19,    4, 'EmojiUser 🎉',      'emoji.user@example.com',     22, '2024-03-01', 'active'),
    (20, NULL, 'Empty Status',      'empty.status@example.com',   30, '2024-03-15', '');

INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    ( 9, 'BOO-001', 'O''Reilly Programming Book', 'BOOKS',     0.00, 0.500,  10),
    (10, 'FOO-VOID','Mystery Item',                'FOOD',  9999.99, 0.001,   1),
    (11, 'CLO-LRG', 'Wool Sweater XL\Special',     'CLOTHING', 89.00, 0.600,   5),
    (12, 'FOO-Q',   'A''B''C',                     'FOOD',     1.00, 0.100,   1);

INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    (16,  1, '2023-12-31 23:59:00',    0.00, 'paid'),
    (17,  1, '2024-02-29 00:00:00',   50.00, 'paid'),
    (18,  1, '2025-01-01 03:30:00',  100.00, 'paid'),
    (19,  1, '1999-06-15 14:00:00',   75.00, 'paid'),
    (20,  1, '2024-12-31 23:59:59',   25.00, 'pending');

INSERT INTO order_items (id, order_id, product_id, qty, unit_price, line_total) VALUES
    (31, 16,  9,    1,     0.00,    0.00),
    (32, 17, 10,    1,  9999.99, 9999.99),
    -- INCONSISTENTE: 1 * 59 != 99
    (33, 18,  7,    1,    59.00,   99.00),
    (34, 19,  5, 1000,     0.05,   50.00),
    (35, 20, 11,    1,    25.00,   25.00);

INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    (11,    6, 'Kira Helper',     'helper',   2000.00, '2024-01-15'),
    (12,   11, 'Lia Intern',      'intern',   1000.00, '2024-06-01'),
    (13, NULL, 'Mario Boss-Lone', 'manager', 15000.00, '2017-05-10'),
    (14,    2, 'Néstor López',    'senior',   7200.00, '2021-11-15'),
    (15,   14, 'O''Sullivan Tom', 'junior',   3500.00, '2023-04-20');

-- =============================================================================
-- NULL cases (id discontinui rispetto a base+edge).
-- 1 customer per colonna nullabile, FK rispettate (customer prima dell'order).
-- =============================================================================

INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    (21,    1, 'Null Age User',    'null.age@example.com', NULL, '2024-04-01', 'active'),
    (22,    1, 'Null Email User',  NULL,                     33, '2024-04-05', 'active');

INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    (13, 'WGT-NULL', 'Weightless Item', 'FOOD', 5.00, NULL, 100);

INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    (21, 21, '2024-04-10 10:00:00', NULL, 'pending');

INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    (16,    1, 'Null Salary Emp',  'senior', NULL,    '2022-01-10'),
    (17,    1, 'Null HireDate Emp','junior', 3000.00, NULL);

-- =============================================================================
-- routes (geometrie LineString) — 4 deterministiche tra città del seed
-- + 1 edge cross-meridian (NullIsland → DateLineSpot).
-- length_km calcolato a mano e arrotondato.
-- WKT axis order (long, lat) coerente con gli altri dialetti.
-- =============================================================================

INSERT INTO routes (id, city_from_id, city_to_id, name, length_km, geom) VALUES
    (1, 1, 4, 'Milano - Roma',
        478.00,
        geography::STGeomFromText('LINESTRING(9.1900 45.4642, 12.4964 41.9028)', 4326)),
    (2, 3, 1, 'Brescia - Milano',
         80.00,
        geography::STGeomFromText('LINESTRING(10.2200 45.5398, 9.1900 45.4642)', 4326)),
    (3, 4, 7, 'Roma - Napoli',
        188.00,
        geography::STGeomFromText('LINESTRING(12.4964 41.9028, 14.2681 40.8518)', 4326)),
    (4, 7, 8, 'Napoli - Salerno',
         50.00,
        geography::STGeomFromText('LINESTRING(14.2681 40.8518, 14.7681 40.6824)', 4326)),
    -- Edge: cross-meridian, da NullIsland (0,0) → DateLineSpot (180,0).
    -- Distanza great-circle ≈ 20015 km; SQL Server geography rifiuta segmenti
    -- antipodali esatti, quindi questo segmento può richiedere split a 90E.
    (5, 15, 17, 'NullIsland - DateLine (cross-meridian)',
      20015.00,
        geography::STGeomFromText('LINESTRING(0.0000 0.0000, 90.0000 0.0000, 180.0000 0.0000)', 4326));
