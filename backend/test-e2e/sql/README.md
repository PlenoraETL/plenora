# Plenora E2E test dataset

Schema portato manualmente su 4 dialetti (PostgreSQL, MySQL, Oracle,
SQL Server) per validare end-to-end ogni operazione che plenora
espone (estrai, manipola, arricchisci, carica).

I file [`expected/`](expected/README.md) documentano i risultati attesi
delle query più importanti — riferimento canonico per i test e2e. La
lista query renderizzata vive in [expected/queries/INDEX.md](expected/queries/INDEX.md).
Il generatore deterministico dei seed vive in
[seed_generator/README.md](seed_generator/README.md).

## Schema

9 tabelle, ~130 righe nel seed (base + edge + NULL) + 10000 righe di
volume in `events_log`. Dati interamente deterministici (nessun random,
nessuna data dinamica).

```
regions  (id, code, name, boundary? MULTIPOLYGON)
   ↑ 1
cities   (id, region_id FK regions, name, population, founded_year,
          latitude, longitude, geometry POINT)
   ↑ 1                        ↑ 2  (city_from / city_to)
customers (id, city_id FK   routes (id, city_from_id FK cities,
           cities, ...)             city_to_id FK cities, name,
   ↑ 1                              length_km, geom LINESTRING)
orders   (id, customer_id FK customers, order_date, total_amount?,
          status)
   ↑ 1
order_items (id, order_id FK orders, product_id FK products, qty,
             unit_price, line_total)
              ↓ N
            products (id, code, name, category, price, weight_kg?,
                      in_stock_count)

employees (id, manager_id FK employees [self], name, role, salary?,
           hire_date?)

events_log (id, customer_id FK customers, event_type, occurred_at,
            value?)   -- 10000 righe (volume)
```

(`?` = nullable)

### Distribuzione

Il seed è suddiviso in tre blocchi:
- **Base** (id contigui 1..N): dataset principale, valori "puliti".
  Risultati attesi nel `expected/README.md` calcolati su questo blocco.
- **Edge** (id discontinui dopo il base): righe con stringhe Unicode,
  apostrofi, valori 0, date estreme, geo edge, gerarchia profonda.
- **NULL** (id discontinui dopo l'edge): righe con NULL nelle colonne
  rese nullabili (`age`, `email`, `weight_kg`, `total_amount`,
  `salary`, `hire_date`).

Il file `02_volume.sql` aggiunge 10000 righe in `events_log` per
test di paginazione, fingerprint sample, preview limit,
cross_join_oom_guard, performance loader.

| Tabella       | Base | + Edge | + NULL | Caratteristiche utili al test |
| ------------- | ---: | -----: | -----: | ----------------------------- |
| `regions`     |    3 |     +1 |      0 | base: LOM/LAZ/CAM con `boundary` MultiPolygon (CAM ha 2 poligoni: regione + isola di Capri). Edge: VEN senza cities né boundary (LEFT JOIN edge + `boundary IS NULL`) |
| `cities`      |    9 |     +9 |      0 | base: 3 per regione. Edge: vicinissima (<1 km), Unicode (`Mêntina`), duplicato del name `Roma` in regione diversa, apostrofo (`D'Amato`), Tokyo (geo lontano), origin `(0,0)`, polo nord, antimeridiano, point coincidente con Roma (id=4) |
| `customers`   |   12 |     +8 |     +2 | base: 4 città senza customer + 1 `city_id NULL`. Edge: apostrofo (`O'Brien`), Unicode latino (`Müller`), CJK (`北京 用户`), whitespace, case mismatch, duplicato logico, emoji, `status=''`. NULL: `age NULL` (id 21), `email NULL` (id 22) |
| `products`    |    8 |     +4 |     +1 | base: 3 categorie. Edge: `BOOKS` solitario (1 prodotto, per `HAVING COUNT > 1`), `price=0.00`, `price=9999.99`, backslash, apostrofi multipli. NULL: `weight_kg NULL` (id 13) |
| `orders`      |   15 |     +5 |     +1 | base: tutti nel 2024. Edge: 1999, 2023 end-of-day, `2024-02-29` (leap), 2025, midnight, `total_amount=0`. NULL: `total_amount NULL` (id 21) |
| `order_items` |   30 |     +5 |      0 | base: 1 riga `qty=0`. Edge: 1 riga con `line_total ≠ qty*unit_price` (integrity check), `qty=1000`, prezzo 9999.99 |
| `employees`   |   10 |     +5 |     +2 | base: 3 livelli. Edge: 4° (`helper`) e 5° (`intern`) livello, manager senza sottoposti, Unicode, apostrofo. NULL: `salary NULL` (id 16), `hire_date NULL` (id 17) |
| `routes`      |    4 |     +1 |      0 | base: 4 LineString tra città del seed (Milano-Roma, Brescia-Milano, Roma-Napoli, Napoli-Salerno). Edge: 1 LineString cross-meridian `(0,0) → (180,0)` per stress test antimeridiano |
| `events_log`  |    0 |      0 |      0 | volume: 10000 righe deterministiche (cycle 12 customer × 4 event_type × 365 giorni × NULL ogni 5). Per stress test paginazione/preview/fingerprint/performance loader |

### Tipi rilevanti

- **Numerico**: `INTEGER`, `DECIMAL(12,2)`, `DOUBLE PRECISION` per
  `latitude`/`longitude`.
- **Testuale**: `VARCHAR(N)` con N in chiaro (no `TEXT` a causa di
  Oracle CLOB).
- **Date**: `DATE` per ricorrenze, `TIMESTAMP` per `order_date`.
- **Boolean / piccoli flag**: codifica `INTEGER` 0/1 (Oracle non ha
  BOOLEAN nativo, MySQL lo serializza come TINYINT, manteniamo
  l'equivalenza esplicita).
- **Geografia**: punti via coordinate `latitude`/`longitude`
  (`DOUBLE PRECISION`) + colonna `geom` con tipo nativo per dialetto:
  - PostgreSQL → `geometry(Point, 4326)` (PostGIS)
  - MySQL ≥ 8 → `POINT` con SRID 4326
  - Oracle → `SDO_GEOMETRY`
  - SQL Server → `geography`
- **Geometrie non-Point**:
  - `routes.geom` = LineString SRID 4326 (PostGIS `geometry(LineString, 4326)`,
    MySQL `LINESTRING SRID 4326`, Oracle `SDO_GEOMETRY` gtype 2002,
    SQL Server `geography`).
  - `regions.boundary` = MultiPolygon SRID 4326, **nullable** (PostGIS
    `geometry(MultiPolygon, 4326)`, MySQL `MULTIPOLYGON SRID 4326`,
    Oracle `SDO_GEOMETRY` gtype 2007, SQL Server `geography`). Per la
    region edge `VEN` (id=4) il valore è NULL: utile per testare LEFT
    JOIN / `IS NULL` sulla geometria.

## Importazione

Ogni sotto-cartella contiene gli script DDL + seed + volume nel dialetto
corrispondente. Ordine di esecuzione: `00_schema.sql` → `01_seed.sql`
→ `02_volume.sql` (opzionale, popola solo `events_log`).

### PostgreSQL (container `plenora-test-postgres`)

```fish
docker exec -i plenora-test-postgres psql -U plenora -d plenora_test < test-e2e/sql/postgres/00_schema.sql
docker exec -i plenora-test-postgres psql -U plenora -d plenora_test < test-e2e/sql/postgres/01_seed.sql
docker exec -i plenora-test-postgres psql -U plenora -d plenora_test < test-e2e/sql/postgres/02_volume.sql
```

Richiede l'estensione PostGIS attiva:
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

### MySQL

```fish
docker exec -i <mysql_container> mysql -u root -p<password> e2e_test < test-e2e/sql/mysql/00_schema.sql
docker exec -i <mysql_container> mysql -u root -p<password> e2e_test < test-e2e/sql/mysql/01_seed.sql
docker exec -i <mysql_container> mysql -u root -p<password> e2e_test < test-e2e/sql/mysql/02_volume.sql
```

Richiede MySQL ≥ 8 per il supporto SRID nativo + CTE ricorsiva.
Lo script `02_volume.sql` imposta `cte_max_recursion_depth = 10000`
in modo SESSION, quindi non serve modificare la config globale.

### Oracle

```fish
sqlplus user/pass@host:1521/FREEPDB1 @test-e2e/sql/oracle/00_schema.sql
sqlplus user/pass@host:1521/FREEPDB1 @test-e2e/sql/oracle/01_seed.sql
sqlplus user/pass@host:1521/FREEPDB1 @test-e2e/sql/oracle/02_volume.sql
```

Richiede Oracle Spatial / Locator (incluso in Oracle 19c+).

### SQL Server

```fish
sqlcmd -S host,1433 -U sa -P <password> -d e2e_test -i test-e2e/sql/sqlserver/00_schema.sql
sqlcmd -S host,1433 -U sa -P <password> -d e2e_test -i test-e2e/sql/sqlserver/01_seed.sql
sqlcmd -S host,1433 -U sa -P <password> -d e2e_test -i test-e2e/sql/sqlserver/02_volume.sql
```

## Convenzioni

- Tutti i nomi tabella/colonna in `lower_snake_case` per uniformare
  cross-dialect (Oracle è case-insensitive in upper, gli altri
  preservano lower).
- Niente identificatori riservati come nome di colonna.
- Le righe sono inserite in ordine deterministico: gli `id` espliciti
  sono `INTEGER` 1..N (no auto-increment) perché i test e2e si
  appoggiano agli ID per asserire i risultati.
- Le date sono in `YYYY-MM-DD`. I timestamp in `YYYY-MM-DD HH:MM:SS`
  UTC senza timezone (semplifica il confronto cross-dialect).
- I prezzi sono numeri tondi (es. `19.90`) per evitare drift di
  floating-point fra DB.

## Cosa testare

`expected/README.md` elenca le query canoniche e i risultati attesi
(JOIN, aggregazioni, ordinamenti, formule). I test e2e plenora
costruiscono un canvas equivalente e verificano che il risultato
combaci con quanto documentato lì.
