# Plenora E2E expected results

Risultati attesi delle query/operazioni canoniche sul dataset
`test-e2e/sql/`. I test e2e plenora costruiscono un canvas
equivalente e verificano che la riga prodotta combaci con quanto
documentato qui.

I valori sono stati calcolati a mano sul seed deterministico
condiviso da tutti e 4 i dialetti.

## 1. Estrazione semplice (ESTRAI)

### 1.1 Tutti i prodotti

```
SELECT id, code, name, category, price FROM products ORDER BY id;
```

| id | code    | name             | category    | price   |
| -: | ------- | ---------------- | ----------- | ------: |
| 1  | ELE-001 | Smartphone X1    | ELECTRONICS |  599.00 |
| 2  | ELE-002 | Laptop Pro 14    | ELECTRONICS | 1299.00 |
| 3  | ELE-003 | Wireless Earbuds | ELECTRONICS |   99.00 |
| 4  | FOO-001 | Olive Oil 1L     | FOOD        |   12.50 |
| 5  | FOO-002 | Pasta Pack 500g  | FOOD        |    1.80 |
| 6  | CLO-001 | Cotton T-Shirt   | CLOTHING    |   19.90 |
| 7  | CLO-002 | Denim Jeans      | CLOTHING    |   59.00 |
| 8  | CLO-003 | Wool Sweater     | CLOTHING    |   89.00 |

### 1.2 Filtro WHERE

```
SELECT id, name, price FROM products WHERE category = 'ELECTRONICS' ORDER BY id;
```

3 righe → id 1, 2, 3.

### 1.3 Filtro range numerico

```
SELECT id, name, price FROM products WHERE price BETWEEN 50 AND 200 ORDER BY id;
```

3 righe → id 3 (99.00), 7 (59.00), 8 (89.00).

### 1.4 Filtro NULL

```
SELECT id, name FROM cities WHERE founded_year IS NULL;
```

1 riga → id 6 (Frosinone).

```
SELECT id, full_name FROM customers WHERE city_id IS NULL;
```

1 riga → id 12 (Roberto Costa).

## 2. Aggregazioni (MANIPOLA — group by)

### 2.1 Conteggio per categoria

```
SELECT category, COUNT(*) AS n FROM products GROUP BY category ORDER BY category;
```

| category    | n |
| ----------- | -: |
| CLOTHING    | 3 |
| ELECTRONICS | 3 |
| FOOD        | 2 |

### 2.2 Somma per regione (via JOIN)

```
SELECT r.code, SUM(c.population) AS pop
FROM regions r JOIN cities c ON c.region_id = r.id
GROUP BY r.code ORDER BY r.code;
```

| code | pop      |
| ---- | -------: |
| CAM  | 1121000  |
| LAZ  | 3046000  |
| LOM  | 1714000  |

### 2.3 Media salary per ruolo

```
SELECT role, AVG(salary) AS avg_sal FROM employees GROUP BY role ORDER BY role;
```

| role    | avg_sal  |
| ------- | -------: |
| junior  |  3100.00 |
| manager | 11500.00 |
| senior  |  6766.67 (6766.66... arrotondamento dialect-specific) |

### 2.4 Conteggio ordini per status

```
SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY status;
```

| status    | n |
| --------- | -: |
| cancelled | 2 |
| paid      | 9 |
| pending   | 4 |

## 3. JOIN — i 4 tipi

### 3.1 INNER JOIN

```
SELECT c.id AS cust_id, c.full_name, COUNT(o.id) AS n_orders
FROM customers c INNER JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name ORDER BY c.id;
```

8 righe (solo customer con ordini).

| cust_id | full_name        | n_orders |
| ------: | ---------------- | -------: |
| 1       | Anna Rossi       | 2        |
| 2       | Marco Bianchi    | 2        |
| 4       | Paolo Ferrari    | 1        |
| 5       | Laura Romano     | 1        |
| 6       | Stefano Conti    | 2        |
| 7       | Elena Greco      | 2        |
| 9       | Chiara Esposito  | 2        |
| 10      | Luca Russo       | 1        |
| 11      | Francesca Galli  | 2        |

(9 righe — Anna ha 2 ordini, e così via. Customer 3, 8, 12 NON ci sono.)

### 3.2 LEFT JOIN (mantiene tutti i customer)

```
SELECT c.id, c.full_name, COUNT(o.id) AS n_orders
FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name ORDER BY c.id;
```

12 righe — i customer 3, 8, 12 hanno `n_orders = 0`.

### 3.3 RIGHT JOIN (cities ↔ customers)

```
SELECT ci.id AS city_id, ci.name AS city, COUNT(cu.id) AS n_cust
FROM customers cu RIGHT JOIN cities ci ON cu.city_id = ci.id
GROUP BY ci.id, ci.name ORDER BY ci.id;
```

9 righe (tutte le cities).
- `n_cust=0` per Bergamo (id=2), Frosinone (6), Caserta (9): 3 città senza customer.
- Roma (4) ha 3 customer; Milano (1) ne ha 3; Napoli (7) ne ha 2.

### 3.4 FULL OUTER JOIN (PostgreSQL/Oracle/SQL Server — MySQL no)

```
SELECT c.id AS cust_id, ci.id AS city_id
FROM customers c FULL OUTER JOIN cities ci ON c.city_id = ci.id
ORDER BY ci.id NULLS LAST, c.id;
```

13 righe: 12 customer (incluso il customer 12 con `city_id NULL`) + 3 città senza customer.

> MySQL non supporta `FULL OUTER JOIN` — emulare con `LEFT UNION RIGHT`.

### 3.5 CROSS JOIN

```
SELECT * FROM regions CROSS JOIN products;
```

3 × 8 = **24 righe**.

### 3.6 SELF JOIN (gerarchia employees)

```
SELECT e.id, e.name AS employee, m.name AS manager
FROM employees e LEFT JOIN employees m ON m.id = e.manager_id
ORDER BY e.id;
```

10 righe. CEO/COO (id 1, 2) hanno `manager = NULL`. I 3 senior puntano ai 2 manager. I 5 junior puntano ai 3 senior.

## 4. Formule (calcolate)

### 4.1 Verifica `line_total = qty * unit_price`

```
SELECT id, qty, unit_price, line_total, qty * unit_price AS expected
FROM order_items WHERE qty * unit_price <> line_total;
```

**0 righe** — il seed è coerente (verifica integrità).

### 4.2 Età al signup (anni dal 2024-01-01)

```
SELECT id, age, signup_date,
       EXTRACT(YEAR FROM signup_date) - (2024 - age) AS birth_year_offset
FROM customers ORDER BY id;
```

`birth_year_offset` è una formula sintetica per testare estrazioni temporali.

### 4.3 Concatenazione (full_name + email dominio)

```
SELECT id, full_name || ' <' || email || '>' AS card FROM customers ORDER BY id LIMIT 3;
```

(`CONCAT(full_name, ' <', email, '>')` su MySQL/SQL Server — Oracle ha
`||` come PostgreSQL.)

### 4.4 CASE WHEN (categoria di età)

```
SELECT id, age,
       CASE
         WHEN age < 30 THEN 'young'
         WHEN age < 50 THEN 'mid'
         ELSE 'senior'
       END AS bucket
FROM customers ORDER BY id;
```

| bucket | n |
| ------ | -: |
| young  | 2 (id 3, 7)   |
| mid    | 6 (id 1, 2, 6, 9, 10, 11) |
| senior | 4 (id 4, 5, 8, 12) |

## 5. Geo (POINT, distanza)

### 5.1 Distanza in km (Roma → Milano)

`(45.4642, 9.1900)` ↔ `(41.9028, 12.4964)` → ≈ **478 km**
(WGS84 great-circle).

I dialetti calcolano differentemente (PostGIS `ST_Distance` su
geography ritorna metri; MySQL `ST_Distance_Sphere`; SQL Server
`geom1.STDistance(geom2)` su geography ritorna metri; Oracle
`SDO_GEOM.SDO_DISTANCE(geom1, geom2, tol)`).

Tolleranza accettata nei test e2e: ± 1 km.

### 5.2 Punti dentro un buffer (Lazio cluster)

Buffer 50 km centrato su `(41.9028, 12.4964)` (Roma) include:
- id 4 (Roma) — distanza 0
- id 5 (Latina) — distanza ≈ 60 km → **fuori buffer 50 km** (al
  netto della tolleranza, considerare 70 km come buffer di
  sicurezza per includerla)
- id 6 (Frosinone) — ≈ 75 km → fuori 50 km

→ buffer 50 km: **1 città** (Roma).
→ buffer 100 km: **3 città** (Roma + Latina + Frosinone).

## 6. Casi limite per regression

- `order_items.id = 15` ha `qty = 0` e `line_total = 0.00`: utile per
  testare aggregazioni con valori zero.
- `customers.id = 12` ha `city_id NULL`: per LEFT vs INNER join.
- `cities.id = 6` (Frosinone) ha `founded_year NULL`.
- `products.id = 8` (Wool Sweater) ha `in_stock_count = 0`: filtro
  "in stock" deve escluderlo.
- `customer_id = 9` (Chiara) ha 1 ordine paid (1318) + 1 cancelled (24.5).
  Le aggregazioni filtrate per `status = 'paid'` devono ritornare 1318.
- `order 4` ha `total_amount = 25.00` ma è `cancelled`: somma per
  `status` deve assegnare 25.00 a `cancelled`, non a `paid`.

## 7. Totali di sanity

I valori della sezione 1-6 sono calcolati sul **solo blocco base**
(id contigui 1..N). Le sezioni 8 (edge NULL) e 9 (volume) coprono
gli aggiuntivi.

| metrica                                | valore  |
| -------------------------------------- | ------: |
| Numero righe `regions` (base)          |       3 |
| Numero righe `cities` (base)           |       9 |
| Numero righe `customers` (base)        |      12 |
| Numero righe `products` (base)         |       8 |
| Numero righe `orders` (base)           |      15 |
| Numero righe `order_items` (base)      |      30 |
| Numero righe `employees` (base)        |      10 |
| Somma `total_amount` (base)            | 4773.70 |
| Somma `total_amount` solo `paid`       | 4456.20 |
| Somma `total_amount` solo `pending`    |  445.10 |
| Somma `total_amount` solo `cancelled`  |   49.50 |
| Somma `qty` su tutti gli `order_items` |      63 |
| Somma `line_total`                     | 4773.70 |

## 8. Edge NULL handling

Conteggi totali (base + edge + NULL, escluso `events_log`):

| Tabella       | Total rows |
| ------------- | ---------: |
| `regions`     |          4 |
| `cities`      |         18 |
| `customers`   |         22 |
| `products`    |         13 |
| `orders`      |         21 |
| `order_items` |         35 |
| `employees`   |         17 |

### 8.1 Customers — `age` NULL

```
SELECT COUNT(*)        AS n_total,
       COUNT(age)      AS n_age,
       AVG(age)        AS avg_age,
       SUM(age)        AS sum_age
FROM customers;
```

| n_total | n_age | sum_age | avg_age (non-null) |
| ------: | ----: | ------: | -----------------: |
|      22 |    21 |     795 |  37.857... ≈ 37.86 |

`AVG`/`SUM` ignorano NULL: il risultato è calcolato sui 21 non-null.
`COUNT(*)` conta tutte le righe (22), `COUNT(age)` solo i non-null (21).

### 8.2 Customers — `email` NULL e UNIQUE

```
SELECT COUNT(*) AS n_total, COUNT(email) AS n_email FROM customers;
```

| n_total | n_email |
| ------: | ------: |
|      22 |      21 |

Il vincolo `UNIQUE` su `email` resta in vigore: NULL non viola
UNIQUE su PostgreSQL, MySQL, Oracle, SQL Server.

> Nota Oracle: la stringa vuota `''` è equivalente a NULL.
> Il customer `id=20` ('Empty Status') ha `status=''` → in Oracle
> diventa NULL, impatto sui filtri `status = ''` (non match) vs
> `status IS NULL` (match).

### 8.3 Products — `weight_kg` NULL

```
SELECT COUNT(*) AS n_total, COUNT(weight_kg) AS n_w,
       AVG(weight_kg) AS avg_w, SUM(weight_kg) AS sum_w
FROM products;
```

| n_total | n_w | sum_w | avg_w  |
| ------: | --: | ----: | -----: |
|      13 |  12 | 5.901 | 0.4918 (≈ 0.49) |

### 8.4 Orders — `total_amount` NULL

```
SELECT COUNT(*) AS n_total, COUNT(total_amount) AS n_amt,
       SUM(total_amount) AS sum_amt
FROM orders;
```

| n_total | n_amt | sum_amt |
| ------: | ----: | ------: |
|      21 |    20 | 5023.70 |

(Base 4773.70 + edge `0+50+100+75+25` = 5023.70 totale; ordine 21
con `total_amount NULL` ignorato dalla SUM.)

### 8.5 Employees — `salary` e `hire_date` NULL

```
SELECT COUNT(*)         AS n_total,
       COUNT(salary)    AS n_sal,
       COUNT(hire_date) AS n_hire,
       AVG(salary)      AS avg_sal,
       SUM(salary)      AS sum_sal
FROM employees;
```

| n_total | n_sal | n_hire | sum_sal  | avg_sal |
| ------: | ----: | -----: | -------: | ------: |
|      17 |    16 |     16 | 90500.00 | 5656.25 |

### 8.6 Coalesce / NULL guard

```
SELECT id, COALESCE(age, -1) AS age_safe
FROM customers WHERE id IN (21, 22) ORDER BY id;
```

| id | age_safe |
| -: | -------: |
| 21 |       -1 |
| 22 |       33 |

```
SELECT id, COALESCE(email, '<no-email>') AS email_safe
FROM customers WHERE id IN (21, 22) ORDER BY id;
```

| id | email_safe              |
| -: | ----------------------- |
| 21 | null.age@example.com    |
| 22 | <no-email>              |

### 8.7 NULL nei JOIN

```
SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id;
```

→ 21 righe (tutti gli ordini hanno un `customer_id` valido, anche
l'order 21 che ha `total_amount NULL` ma `customer_id = 21` non NULL).

```
SELECT COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
WHERE o.id IS NULL;
```

→ 12 righe (customer senza ordini):
- base: id 3, 8, 12 (3 customer)
- edge: id 13, 14, 15, 16, 17, 18, 19, 20 (8 customer, nessun ordine punta a loro)
- NULL: id 22 (customer 21 ha l'ordine 21, customer 22 no)

## 9. Volume — `events_log`

Generato da `02_volume.sql` con 10000 righe deterministiche.

### 9.1 Conteggi

| metrica                     | valore |
| --------------------------- | -----: |
| `COUNT(*)`                  |  10000 |
| `COUNT(value)` (non-NULL)   |   8000 |
| `COUNT(*) - COUNT(value)`   |   2000 |

(Ogni 5° riga ha `value NULL` → 10000/5 = 2000 NULL.)

### 9.2 Distribuzione `event_type`

| event_type | n     |
| ---------- | ----: |
| view       | 2500 (id divisibile per 4 → 2500) |
| click      | 2500 (id MOD 4 = 1) |
| purchase   | 2500 (id MOD 4 = 2) |
| logout     | 2500 (id MOD 4 = 3) |

### 9.3 Distribuzione `customer_id`

10000 / 12 = 833 con resto 4 → 4 customer (id 1..4) hanno 834 eventi,
gli altri 8 (id 5..12) hanno 833 eventi. Totale = 4×834 + 8×833 = 10000.

I customer con `id > 12` (13..22) NON hanno eventi.

### 9.4 SUM / AVG di `value`

Per `id = 1..10000`, `value = id * 0.10` se `id % 5 != 0`, altrimenti NULL.

Sum di `id` da 1 a 10000 = `10000 * 10001 / 2 = 50005000`.
Sum di `id` con `id % 5 = 0` (i NULL) = 5 + 10 + ... + 10000 =
2000 * (5 + 10000) / 2 = 10005000.
Sum di `id` non-NULL = 50005000 - 10005000 = 40000000.
Sum di `value` = 40000000 * 0.10 = **4000000.00**.
Count non-NULL = 8000.
AVG = 4000000 / 8000 = **500.00**.

### 9.5 Date range `occurred_at`

`'2024-01-01' + (id MOD 365) days` → range
`[2024-01-01, 2024-12-30]` (id MOD 365 ∈ {0, 1, ..., 364}).
10000 / 365 ≈ 27.4 → ogni giorno ha 27 o 28 eventi.

## 10. Geometrie non-Point (`routes`, `regions.boundary`)

Schema esteso con LineString su `routes` e MultiPolygon su
`regions.boundary`. 4 dialetti coprono PostGIS, MySQL ≥ 8, Oracle
Spatial e SQL Server `geography`. Calcoli portabili → tutto in
gradi (deg² per area, gradi per length); le funzioni dialect-specific
sono note tra parentesi.

### 10.1 COUNT routes (totale)

```
SELECT COUNT(*) FROM routes;
```

→ **5 righe** (4 base + 1 edge cross-meridian).

### 10.2 Route più lunga

```
SELECT id, name, length_km
FROM routes
ORDER BY length_km DESC
LIMIT 1;
```

| id | name                                       | length_km |
| -: | ------------------------------------------ | --------: |
| 5  | NullIsland - DateLine (cross-meridian)     |  20015.00 |

Escludendo l'edge cross-meridian:

```
SELECT id, name, length_km
FROM routes
WHERE id <= 4
ORDER BY length_km DESC
LIMIT 1;
```

| id | name          | length_km |
| -: | ------------- | --------: |
| 1  | Milano - Roma |    478.00 |

Sum length_km tutte le routes = 478 + 80 + 188 + 50 + 20015 = **20811.00**.
Escluso edge: 478 + 80 + 188 + 50 = **796.00**.

### 10.3 Boundary regions con area approssimata (deg²)

`regions.boundary` è MultiPolygon SRID 4326. Per portabilità i test
usano area in gradi-quadrati (rettangoli di lato semplice), che è
quanto restituiscono `ST_Area(geometry)` in PostGIS, `ST_Area(g)` in
MySQL, e l'analogo in SQL Server con `geometry::STArea` (per
`geography::STArea` il risultato è in metri quadrati).

| code | n_polygons | area_deg2 (approx) |
| ---- | ---------: | -----------------: |
| LOM  |          1 |               2.00 (2.0 long × 1.0 lat) |
| LAZ  |          1 |               3.00 (2.0 long × 1.5 lat) |
| CAM  |          2 |               1.5040 (1.5 + 0.10×0.04 = 1.5 + 0.004) |
| VEN  |        N/A | NULL (boundary IS NULL) |

```
SELECT COUNT(*) FROM regions WHERE boundary IS NULL;
```

→ **1 riga** (id=4, VEN).

```
SELECT COUNT(*) FROM regions WHERE boundary IS NOT NULL;
```

→ **3 righe** (LOM, LAZ, CAM).

### 10.4 LEFT JOIN regions ↔ boundary

`regions LEFT JOIN cities ON cities.region_id = regions.id WHERE
boundary IS NULL` → restituisce VEN (1 riga, perché VEN ha
`boundary NULL` e nessuna city).

### 10.5 ST_Within: città dentro la boundary di Lombardia

```
-- PostGIS / MySQL / SQL Server (sintassi varia, semantica equivalente).
SELECT COUNT(*)
FROM cities c
JOIN regions r ON r.code = 'LOM'
WHERE ST_Within(c.geom, r.boundary);
```

Boundary LOM = rettangolo `[long 8.5..10.5] × [lat 45.0..46.0]`.

Cities del seed con coordinate dentro questo rettangolo:

| id | name           | longitude | latitude | in LOM? |
| -: | -------------- | --------: | -------: | :------ |
|  1 | Milano         |   9.1900  | 45.4642  | sì      |
|  2 | Bergamo        |   9.6773  | 45.6983  | sì      |
|  3 | Brescia        |  10.2200  | 45.5398  | sì      |
| 10 | Milano Centro  |   9.1908  | 45.4660  | sì      |
| 11 | Mêntina        |   9.5000  | 45.5000  | sì      |

→ **5 cities** (3 base + 2 edge) dentro la boundary di LOM.

Tutte le cities di `region_id = 1` ricadono dentro la boundary
disegnata: il vincolo logico region-cities è coerente con la geometria.

### 10.6 ST_Within sulla regione CAM (MultiPolygon, isola di Capri)

Boundary CAM = MultiPolygon con 2 poligoni:
- main: `[14.0..15.5] × [40.5..41.5]`
- Capri: `[14.20..14.30] × [40.53..40.57]`

Cities del seed con coordinate dentro CAM (uno qualsiasi dei poligoni):

| id | name      | longitude | latitude | in CAM? |
| -: | --------- | --------: | -------: | :------ |
|  7 | Napoli    |   14.2681 |  40.8518 | sì (main) |
|  8 | Salerno   |   14.7681 |  40.6824 | sì (main) |
|  9 | Caserta   |   14.3327 |  41.0721 | sì (main) |
| 12 | Roma      |   14.5000 |  40.8000 | sì (main, ma è "Roma" duplicata in CAM) |
| 13 | D'Amato   |   14.4000 |  40.9000 | sì (main) |

→ **5 cities** dentro CAM. Le altre cities `region_id = 3` (Tokyo,
NullIsland, PoleStation, DateLineSpot) sono fuori dal rettangolo
principale.

Il poligono "Capri" non contiene nessuna city del seed: serve a
verificare che la query gestisca correttamente MultiPolygon con
componenti multiple (l'`ST_Within` deve restituire `true` se il punto
è dentro **almeno uno** dei sub-poligoni).

### 10.7 Note dialect-specific

- **PostGIS**: `ST_Within(point, polygon)`, `ST_Area(geometry)` ritorna
  deg², `ST_Length(geometry)` ritorna deg. Usare `::geography` per
  metri.
- **MySQL ≥ 8**: `ST_Within(g1, g2)`. Su geometrie SRID 4326 con
  `axis-order=long-lat`, attenzione all'ordine asse di default
  (lat-long) di MySQL: forzato negli `ST_GeomFromText` del seed.
- **Oracle**: `SDO_RELATE(geom, poly, 'mask=INSIDE+COVEREDBY')` o
  `SDO_INSIDE`. `SDO_GEOM.SDO_AREA(geom, tol)` per l'area.
- **SQL Server**: `point.STWithin(polygon) = 1`. `boundary.STArea()` in
  metri quadrati (geography). Le LineString cross-meridiano richiedono
  un vertice intermedio (es. 90E) per evitare errore "antipodal points".
