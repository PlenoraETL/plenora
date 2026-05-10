-- Plenora E2E test dataset — seed PostgreSQL
-- Inserisce ~80 righe deterministiche.
-- Le coordinate (latitude, longitude) sono valori reali approssimati
-- delle città italiane corrispondenti, scelti tondi per riproducibilità.

-- regions ---------------------------------------------------------------------
-- boundary: MultiPolygon WGS84 (rettangoli ~1° di lato approssimati).
-- CAM ha 2 poligoni: regione + isola di Capri (geometria non-banale).
INSERT INTO regions (id, code, name, boundary) VALUES
    (1, 'LOM', 'Lombardia',
        ST_GeomFromText('MULTIPOLYGON(((8.5 45.0, 10.5 45.0, 10.5 46.0, 8.5 46.0, 8.5 45.0)))', 4326)),
    (2, 'LAZ', 'Lazio',
        ST_GeomFromText('MULTIPOLYGON(((12.0 41.0, 14.0 41.0, 14.0 42.5, 12.0 42.5, 12.0 41.0)))', 4326)),
    (3, 'CAM', 'Campania',
        ST_GeomFromText('MULTIPOLYGON(((14.0 40.5, 15.5 40.5, 15.5 41.5, 14.0 41.5, 14.0 40.5)),((14.20 40.53, 14.30 40.53, 14.30 40.57, 14.20 40.57, 14.20 40.53)))', 4326));

-- cities ----------------------------------------------------------------------
-- LOM (region_id=1): Milano, Bergamo, Brescia
-- LAZ (region_id=2): Roma, Latina, Frosinone
-- CAM (region_id=3): Napoli, Salerno, Caserta
INSERT INTO cities (id, region_id, name, population, founded_year,
                    latitude, longitude, geom) VALUES
    (1, 1, 'Milano',    1396000, -222, 45.4642, 9.1900,
        ST_SetSRID(ST_MakePoint(9.1900, 45.4642), 4326)),
    (2, 1, 'Bergamo',    122000, -49,  45.6983, 9.6773,
        ST_SetSRID(ST_MakePoint(9.6773, 45.6983), 4326)),
    (3, 1, 'Brescia',    196000, -27,  45.5398, 10.2200,
        ST_SetSRID(ST_MakePoint(10.2200, 45.5398), 4326)),
    (4, 2, 'Roma',      2873000, -753, 41.9028, 12.4964,
        ST_SetSRID(ST_MakePoint(12.4964, 41.9028), 4326)),
    (5, 2, 'Latina',     127000, 1932, 41.4676, 12.9038,
        ST_SetSRID(ST_MakePoint(12.9038, 41.4676), 4326)),
    (6, 2, 'Frosinone',   46000, NULL, 41.6403, 13.3500,
        ST_SetSRID(ST_MakePoint(13.3500, 41.6403), 4326)),
    (7, 3, 'Napoli',     914000, -470, 40.8518, 14.2681,
        ST_SetSRID(ST_MakePoint(14.2681, 40.8518), 4326)),
    (8, 3, 'Salerno',    133000, -197, 40.6824, 14.7681,
        ST_SetSRID(ST_MakePoint(14.7681, 40.6824), 4326)),
    (9, 3, 'Caserta',     74000, 1750, 41.0721, 14.3327,
        ST_SetSRID(ST_MakePoint(14.3327, 41.0721), 4326));

-- customers -------------------------------------------------------------------
-- 12 customer; 3 città (Bergamo, Frosinone, Caserta) restano senza customer
-- (utile per RIGHT/LEFT JOIN). 1 customer (id=12) ha city_id NULL.
INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    ( 1,    1, 'Anna Rossi',         'anna.rossi@example.com',     34, DATE '2023-01-15', 'active'),
    ( 2,    1, 'Marco Bianchi',      'marco.bianchi@example.com',  41, DATE '2023-02-20', 'active'),
    ( 3,    1, 'Giulia Verdi',       'giulia.verdi@example.com',   28, DATE '2023-03-10', 'pending'),
    ( 4,    3, 'Paolo Ferrari',      'paolo.ferrari@example.com',  52, DATE '2023-04-05', 'active'),
    ( 5,    4, 'Laura Romano',       'laura.romano@example.com',   45, DATE '2023-05-12', 'inactive'),
    ( 6,    4, 'Stefano Conti',      'stefano.conti@example.com',  37, DATE '2023-06-18', 'active'),
    ( 7,    4, 'Elena Greco',        'elena.greco@example.com',    29, DATE '2023-07-22', 'active'),
    ( 8,    5, 'Davide Marini',      'davide.marini@example.com',  60, DATE '2023-08-08', 'inactive'),
    ( 9,    7, 'Chiara Esposito',    'chiara.esposito@example.com',31, DATE '2023-09-14', 'active'),
    (10,    7, 'Luca Russo',         'luca.russo@example.com',     48, DATE '2023-10-25', 'pending'),
    (11,    8, 'Francesca Galli',    'francesca.galli@example.com',39, DATE '2023-11-03', 'active'),
    (12, NULL, 'Roberto Costa',      'roberto.costa@example.com',  55, DATE '2023-12-19', 'active');

-- products --------------------------------------------------------------------
-- 8 prodotti, 3 categorie. Product id=8 ha in_stock_count=0 (per filtri).
INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    (1, 'ELE-001', 'Smartphone X1',     'ELECTRONICS', 599.00, 0.180, 50),
    (2, 'ELE-002', 'Laptop Pro 14',     'ELECTRONICS', 1299.00, 1.500, 20),
    (3, 'ELE-003', 'Wireless Earbuds',  'ELECTRONICS',  99.00, 0.050, 200),
    (4, 'FOO-001', 'Olive Oil 1L',      'FOOD',          12.50, 0.920, 500),
    (5, 'FOO-002', 'Pasta Pack 500g',   'FOOD',           1.80, 0.500, 1000),
    (6, 'CLO-001', 'Cotton T-Shirt',    'CLOTHING',      19.90, 0.250, 300),
    (7, 'CLO-002', 'Denim Jeans',       'CLOTHING',      59.00, 0.700, 150),
    (8, 'CLO-003', 'Wool Sweater',      'CLOTHING',      89.00, 0.600, 0);

-- orders ----------------------------------------------------------------------
-- 15 ordini su 8 customer (i restanti 4 customer non hanno ordini).
-- Mix status: 9 paid, 4 pending, 2 cancelled.
INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    ( 1,  1, TIMESTAMP '2024-01-10 09:30:00',  698.00, 'paid'),
    ( 2,  1, TIMESTAMP '2024-02-15 14:22:00',  118.90, 'paid'),
    ( 3,  2, TIMESTAMP '2024-01-20 11:00:00', 1299.00, 'paid'),
    ( 4,  2, TIMESTAMP '2024-03-05 16:45:00',   25.00, 'cancelled'),
    ( 5,  4, TIMESTAMP '2024-02-28 10:15:00',  239.50, 'paid'),
    ( 6,  5, TIMESTAMP '2024-03-12 13:00:00',  148.80, 'paid'),
    ( 7,  6, TIMESTAMP '2024-04-02 09:00:00',   59.00, 'pending'),
    ( 8,  6, TIMESTAMP '2024-04-18 15:30:00',  199.00, 'paid'),
    ( 9,  7, TIMESTAMP '2024-05-01 12:00:00',   39.80, 'paid'),
    (10,  7, TIMESTAMP '2024-05-15 18:20:00',   89.00, 'pending'),
    (11,  9, TIMESTAMP '2024-06-08 10:30:00', 1318.00, 'paid'),
    (12,  9, TIMESTAMP '2024-06-22 14:00:00',   24.50, 'cancelled'),
    (13, 10, TIMESTAMP '2024-07-04 11:45:00',  179.10, 'pending'),
    (14, 11, TIMESTAMP '2024-07-30 09:00:00',  198.00, 'paid'),
    (15, 11, TIMESTAMP '2024-08-12 16:15:00',  118.00, 'pending');

-- order_items -----------------------------------------------------------------
-- 30 righe; ogni order ha 1-3 line_items. line_total = qty * unit_price (PRE-CALCOLATO).
INSERT INTO order_items (id, order_id, product_id, qty, unit_price, line_total) VALUES
    ( 1,  1, 1, 1,  599.00,  599.00),
    ( 2,  1, 3, 1,   99.00,   99.00),
    ( 3,  2, 6, 1,   19.90,   19.90),
    ( 4,  2, 7, 1,   59.00,   59.00),
    ( 5,  2, 4, 2,   12.50,   25.00),
    ( 6,  3, 2, 1, 1299.00, 1299.00),
    ( 7,  4, 4, 2,   12.50,   25.00),
    ( 8,  5, 6, 4,   19.90,   79.60),
    ( 9,  5, 7, 1,   59.00,   59.00),
    (10,  5, 8, 1,   89.00,   89.00),
    (11,  5, 5, 7,    1.80,   12.60),
    (12,  6, 7, 2,   59.00,  118.00),
    (13,  6, 4, 1,   12.50,   12.50),
    (14,  6, 5, 10,   1.80,   18.00),
    (15,  6, 3, 0,   99.00,    0.00), -- riga "stub" qty=0 (per testare aggregazioni)
    (16,  7, 7, 1,   59.00,   59.00),
    (17,  8, 1, 1,  199.00,  199.00),  -- prezzo applicato 199 (sconto), price 599
    (18,  9, 6, 2,   19.90,   39.80),
    (19, 10, 8, 1,   89.00,   89.00),
    (20, 11, 2, 1, 1299.00, 1299.00),
    (21, 11, 3, 1,   19.00,   19.00),  -- sconto cumulato
    (22, 12, 5, 5,    1.80,    9.00),
    (23, 12, 4, 1,   15.50,   15.50),
    (24, 13, 7, 3,   59.00,  177.00),
    (25, 13, 5, 1,    2.10,    2.10),
    (26, 14, 6, 5,   19.90,   99.50),
    (27, 14, 7, 1,   59.00,   59.00),
    (28, 14, 4, 3,   13.00,   39.00),
    (29, 14, 5, 1,    0.50,    0.50),
    (30, 15, 8, 1,  118.00,  118.00);

-- employees -------------------------------------------------------------------
-- Gerarchia: 2 manager top, 3 senior, 5 junior. Manager top hanno manager_id NULL.
INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    ( 1, NULL, 'Alice CEO',         'manager', 12000.00, DATE '2018-01-01'),
    ( 2, NULL, 'Bruno COO',         'manager', 11000.00, DATE '2018-03-15'),
    ( 3,    1, 'Carla Senior Dev',  'senior',   6500.00, DATE '2019-05-20'),
    ( 4,    1, 'Diego Senior PM',   'senior',   6800.00, DATE '2019-07-10'),
    ( 5,    2, 'Eva Senior Sales',  'senior',   7000.00, DATE '2020-02-03'),
    ( 6,    3, 'Franco Junior Dev', 'junior',   3200.00, DATE '2021-09-01'),
    ( 7,    3, 'Greta Junior Dev',  'junior',   3300.00, DATE '2022-01-15'),
    ( 8,    4, 'Hans Junior PM',    'junior',   3100.00, DATE '2022-06-20'),
    ( 9,    5, 'Irene Junior Sales','junior',   3000.00, DATE '2023-03-10'),
    (10,    5, 'Jacopo Junior Sales','junior',  2900.00, DATE '2023-08-25');

-- =============================================================================
-- Edge cases (id discontinui rispetto al seed base, scelti tondi).
-- Stessi blocchi presenti negli altri dialetti, sintassi adattata.
-- =============================================================================

INSERT INTO regions (id, code, name, boundary) VALUES
    (4, 'VEN', 'Veneto', NULL); -- regione SENZA cities né boundary (LEFT JOIN edge)

INSERT INTO cities (id, region_id, name, population, founded_year,
                    latitude, longitude, geom) VALUES
    -- vicinissima a Milano (id=1) — distanza < 1 km
    (10, 1, 'Milano Centro', 5000, NULL, 45.4660,   9.1908,
        ST_SetSRID(ST_MakePoint(  9.1908, 45.4660), 4326)),
    -- nome con accento Unicode
    (11, 1, 'Mêntina',       8000, 1245, 45.5000,   9.5000,
        ST_SetSRID(ST_MakePoint(  9.5000, 45.5000), 4326)),
    -- DUPLICATO logico di name 'Roma' in regione diversa
    (12, 3, 'Roma',          1500, NULL, 40.8000,  14.5000,
        ST_SetSRID(ST_MakePoint( 14.5000, 40.8000), 4326)),
    -- nome con apostrofo
    (13, 3, 'D''Amato',      2300, NULL, 40.9000,  14.4000,
        ST_SetSRID(ST_MakePoint( 14.4000, 40.9000), 4326)),
    -- punto MOLTO lontano (Tokyo)
    (14, 3, 'Tokyo Outpost', 1000, NULL, 35.6762, 139.6503,
        ST_SetSRID(ST_MakePoint(139.6503, 35.6762), 4326)),
    -- origin (0, 0)
    (15, 3, 'NullIsland',     100, NULL,  0.0000,   0.0000,
        ST_SetSRID(ST_MakePoint(  0.0000,  0.0000), 4326)),
    -- polo nord
    (16, 3, 'PoleStation',     50, NULL, 90.0000,   0.0000,
        ST_SetSRID(ST_MakePoint(  0.0000, 90.0000), 4326)),
    -- antimeridiano
    (17, 3, 'DateLineSpot',    50, NULL,  0.0000, 180.0000,
        ST_SetSRID(ST_MakePoint(180.0000,  0.0000), 4326)),
    -- coincidente con Roma (id=4): stesso point esatto
    (18, 2, 'Roma Bis',         1, NULL, 41.9028,  12.4964,
        ST_SetSRID(ST_MakePoint( 12.4964, 41.9028), 4326));

INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    (13,    1, 'O''Brien Patrick',  'patrick.obrien@example.com', 36, DATE '2024-01-10', 'active'),
    (14,    1, 'Müller Hans',       'hans.muller@example.com',    44, DATE '2024-01-15', 'active'),
    (15,    1, '北京 用户',           'beijing.user@example.com',   29, DATE '2024-02-01', 'active'),
    (16,    1, '  Anna Rossi  ',    'anna.spazi@example.com',     34, DATE '2024-02-10', 'active'),
    (17,    4, 'anna ROSSI',        'anna.case@example.com',      34, DATE '2024-02-15', 'active'),
    -- DUPLICATO logico (stesso name+city del customer 1, email diversa)
    (18,    1, 'Anna Rossi',        'anna.rossi.dup@example.com', 34, DATE '2024-02-20', 'active'),
    (19,    4, 'EmojiUser 🎉',      'emoji.user@example.com',     22, DATE '2024-03-01', 'active'),
    -- status STRINGA VUOTA (NOT NULL ma '' è ammesso)
    (20, NULL, 'Empty Status',      'empty.status@example.com',   30, DATE '2024-03-15', '');

INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    -- categoria SOLITARIA (1 prodotto in 'BOOKS')
    ( 9, 'BOO-001', 'O''Reilly Programming Book', 'BOOKS',      0.00, 0.500,  10),
    (10, 'FOO-VOID','Mystery Item',                'FOOD',   9999.99, 0.001,   1),
    -- backslash nel name
    (11, 'CLO-LRG', 'Wool Sweater XL\Special',     'CLOTHING',  89.00, 0.600,   5),
    -- apostrofi multipli
    (12, 'FOO-Q',   'A''B''C',                     'FOOD',      1.00, 0.100,   1);

INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    -- anno 2023, end-of-day
    (16,  1, TIMESTAMP '2023-12-31 23:59:00',    0.00, 'paid'),
    -- 29 febbraio 2024 (leap day), midnight
    (17,  1, TIMESTAMP '2024-02-29 00:00:00',   50.00, 'paid'),
    -- 2025 (futuro), notte profonda
    (18,  1, TIMESTAMP '2025-01-01 03:30:00',  100.00, 'paid'),
    -- 1999 (legacy)
    (19,  1, TIMESTAMP '1999-06-15 14:00:00',   75.00, 'paid'),
    -- last-second 2024
    (20,  1, TIMESTAMP '2024-12-31 23:59:59',   25.00, 'pending');

INSERT INTO order_items (id, order_id, product_id, qty, unit_price, line_total) VALUES
    (31, 16,  9,    1,     0.00,    0.00),
    (32, 17, 10,    1,  9999.99, 9999.99),
    -- INCONSISTENTE: 1 * 59 dovrebbe essere 59, line_total = 99
    (33, 18,  7,    1,    59.00,   99.00),
    -- qty grande, prezzo decimale piccolo
    (34, 19,  5, 1000,     0.05,   50.00),
    (35, 20, 11,    1,    25.00,   25.00);

INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    -- helper sotto Franco (Junior Dev) → 4° livello (CEO→senior→junior→helper)
    (11,    6, 'Kira Helper',     'helper',   2000.00, DATE '2024-01-15'),
    -- intern sotto Kira → 5° livello
    (12,   11, 'Lia Intern',      'intern',   1000.00, DATE '2024-06-01'),
    -- manager top SENZA sottoposti (LEFT JOIN employees↔employees edge)
    (13, NULL, 'Mario Boss-Lone', 'manager', 15000.00, DATE '2017-05-10'),
    -- senior con accento Unicode
    (14,    2, 'Néstor López',    'senior',   7200.00, DATE '2021-11-15'),
    -- junior con apostrofo
    (15,   14, 'O''Sullivan Tom', 'junior',   3500.00, DATE '2023-04-20');

-- =============================================================================
-- NULL cases (id discontinui rispetto a base+edge).
-- 1 customer per colonna nullabile, FK rispettate (customer prima dell'order).
-- =============================================================================

INSERT INTO customers (id, city_id, full_name, email, age, signup_date, status) VALUES
    -- customer con age NULL (testa AVG/SUM/MIN/MAX che ignorano NULL)
    (21,    1, 'Null Age User',   'null.age@example.com',       NULL, DATE '2024-04-01', 'active'),
    -- customer con email NULL (NULL non viola UNIQUE su tutti e 4 i dialetti)
    (22,    1, 'Null Email User', NULL,                           33, DATE '2024-04-05', 'active');

INSERT INTO products (id, code, name, category, price, weight_kg, in_stock_count) VALUES
    -- product con weight_kg NULL
    (13, 'WGT-NULL', 'Weightless Item', 'FOOD', 5.00, NULL, 100);

INSERT INTO orders (id, customer_id, order_date, total_amount, status) VALUES
    -- order con total_amount NULL (ordine in elaborazione, totale non ancora calcolato)
    (21, 21, TIMESTAMP '2024-04-10 10:00:00', NULL, 'pending');

INSERT INTO employees (id, manager_id, name, role, salary, hire_date) VALUES
    -- employee con salary NULL (stipendio confidenziale / non disponibile)
    (16,    1, 'Null Salary Emp', 'senior', NULL, DATE '2022-01-10'),
    -- employee con hire_date NULL (employee "ex" senza data nota)
    (17,    1, 'Null HireDate Emp','junior',  3000.00, NULL);

-- =============================================================================
-- routes (geometrie LineString) — 4 deterministiche tra città del seed
-- + 1 edge cross-meridian (NullIsland → DateLineSpot).
-- length_km calcolato a mano e arrotondato.
-- =============================================================================

INSERT INTO routes (id, city_from_id, city_to_id, name, length_km, geom) VALUES
    (1, 1, 4, 'Milano - Roma',
        478.00,
        ST_GeomFromText('LINESTRING(9.1900 45.4642, 12.4964 41.9028)', 4326)),
    (2, 3, 1, 'Brescia - Milano',
         80.00,
        ST_GeomFromText('LINESTRING(10.2200 45.5398, 9.1900 45.4642)', 4326)),
    (3, 4, 7, 'Roma - Napoli',
        188.00,
        ST_GeomFromText('LINESTRING(12.4964 41.9028, 14.2681 40.8518)', 4326)),
    (4, 7, 8, 'Napoli - Salerno',
         50.00,
        ST_GeomFromText('LINESTRING(14.2681 40.8518, 14.7681 40.6824)', 4326)),
    -- Edge: cross-meridian, da NullIsland (0,0) → DateLineSpot (180,0).
    -- Distanza great-circle = mezzo equatore ≈ 20015 km (metà di 40030).
    (5, 15, 17, 'NullIsland - DateLine (cross-meridian)',
      20015.00,
        ST_GeomFromText('LINESTRING(0.0000 0.0000, 180.0000 0.0000)', 4326));
