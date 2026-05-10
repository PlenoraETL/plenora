# Seed generator deterministico — `test-e2e/sql/seed_generator/`

Generatore Python (no dipendenze esterne) che produce schema, seed e
volume SQL multi-dialect per i test E2E plenora.

I file `01_seed.sql` e `02_volume.sql` esistenti in
`test-e2e/sql/{postgres,mysql,oracle,sqlserver}/` restano la
**baseline pinnata** dei test. Questo generatore e' una **fonte di
verita' alternativa**, utile per:

- generare seed con **shape diversa** (es. 1M righe in `events_log`,
  blocchi edge esclusi, customer base ridotti)
- estendere lo schema con tabelle nuove senza dover scrivere SQL a
  mano per 4 dialetti
- garantire che i 4 file `01_seed.sql` restino **dato-equivalenti**
  fra loro (stessi id, stessi valori) — il generator e' la single
  source of truth.

## Layout moduli

| File              | Ruolo                                                                  |
|-------------------|------------------------------------------------------------------------|
| `models.py`       | Dataclass dialect-neutral (`Region`, `City`, `Customer`, ...)          |
| `fixtures.py`     | Dataset baseline (BASE / EDGE / NULL blocks) + generator events_log    |
| `dialect_emit.py` | Render INSERT statements per dialect (PostGIS / MySQL / SDO / GEOG)    |
| `schema_emit.py`  | Render schema DDL (CREATE TABLE + indici) per dialect                  |
| `generator.py`    | CLI                                                                    |

## CLI — esempi d'uso

```sh
# seed PostgreSQL su stdout
python3 -m seed_generator.generator --dialect postgres --output seed

# schema MySQL su file
python3 -m seed_generator.generator --dialect mysql --output schema --out-dir ./out

# volume Oracle a 1M righe (test stress paginazione/perf)
python3 -m seed_generator.generator \
    --dialect oracle --output volume --rows-events 1000000 --out-dir ./out

# tutti i dialetti, output completo (schema + seed + volume) a 10k righe
python3 -m seed_generator.generator \
    --dialect all --output all --rows-events 10000 --out-dir ./out

# solo seed senza edge-cases ne' null cases
python3 -m seed_generator.generator \
    --dialect postgres --output seed --no-edge --no-null --no-routes
```

Output con `--out-dir`:

```
out/
  postgres/{00_schema.sql,01_seed.sql,02_volume.sql}
  mysql/{...}
  oracle/{...}
  sqlserver/{...}
```

(Il dialect interno e' `mssql`; la directory di output e' `sqlserver/`
per coerenza coi file esistenti.)

## Parametri

| Flag                     | Default | Effetto                                                  |
|--------------------------|---------|----------------------------------------------------------|
| `--dialect`              | (req)   | `postgres` | `mysql` | `oracle` | `mssql` | `sqlserver` | `all` |
| `--output`               | (req)   | `schema` | `seed` | `volume` | `all`                |
| `--rows-events`          | 10000   | Righe in events_log                                       |
| `--rows-customers-base`  | 12      | Solo descrittivo (al momento il blocco BASE e' fisso)     |
| `--no-edge`              | off     | Esclude EDGE_<table> (id discontinui)                    |
| `--no-null`              | off     | Esclude NULL_<table>                                      |
| `--no-routes`            | off     | Esclude la tabella routes                                 |
| `--out-dir`              | (none)  | Directory di output (richiesta per `all`)                 |

## Equivalenza con `01_seed.sql` esistenti

Per le **9 tabelle baseline** (regions, cities, customers, products,
orders, order_items, employees, routes, events_log) il generator
riproduce gli stessi:

- **id** (stessi PK su tutti e 4 i dialetti)
- **valori scalari** (nome, email, popolazione, prezzo, stipendio, ...)
- **geometrie** (medesimi vertici / WKT — render idiomatico per dialect)
- **distribuzione `events_log`** deterministica via formula
  (`customer_id = ((id-1) % 12) + 1`, `value = NULL ogni 5°`, ...)

Differenze di **formattazione** rispetto ai file pinnati:

- multi-row INSERT: il generator produce un singolo
  `INSERT ... VALUES (..),(..)` per tabella (Oracle: una INSERT per
  riga, perche' Oracle non supporta multi-row VALUES).
- spaziatura/allineamento e commenti inline: il generator non
  riproduce i commenti italiani inline degli `01_seed.sql` originali
  (i commenti restano nei file pinnati).
- `02_volume.sql`: i file esistenti usano CTE ricorsiva /
  `generate_series` / `CONNECT BY LEVEL` per generare le righe
  server-side. Il generator emette VALUES espliciti (client-side),
  funzionalmente identici ma piu' portabili (dataset risultante
  byte-equivalente sul server).

Se serve **byte-match** rigoroso con i file esistenti, usa i file
pinnati. Il generator copre tutti i casi in cui la baseline non
basta (volume diverso, shape custom, dialetto aggiuntivo).

## Estendere il generator

### Aggiungere una nuova tabella (es. `invoices`)

1. **`models.py`** — definisci la dataclass:

   ```python
   @dataclass(frozen=True)
   class Invoice:
       id: int
       order_id: int
       issued_at: datetime
       amount: Decimal
   ```

   Aggiungi `invoices: list[Invoice]` a `SeedDataset`.

2. **`fixtures.py`** — definisci i blocchi `BASE_INVOICES`,
   eventualmente `EDGE_INVOICES`, e includili in `build_dataset()`.

3. **`dialect_emit.py`** — aggiungi `emit_invoices(records, dialect)`
   sul modello di `emit_orders` (gia' esistente).

4. **`schema_emit.py`** — aggiungi il `CREATE TABLE invoices` nelle
   constanti `_POSTGRES_SCHEMA`, `_MYSQL_SCHEMA`, `_ORACLE_SCHEMA`,
   `_MSSQL_SCHEMA`.

5. **`generator.py::emit_seed`** — aggiungi la chiamata a
   `dialect_emit.emit_invoices(...)` nell'ordine FK-safe.

### Aggiungere un dialetto nuovo (es. `clickhouse`)

1. **`dialect_emit.py`** — aggiungi `clickhouse` a `DIALECTS` e i
   branch in `_emit_point` / `_emit_multipolygon` /
   `_emit_linestring` / `_date_lit` / `_ts_lit`.

2. **`schema_emit.py`** — aggiungi `_CLICKHOUSE_SCHEMA` e il mapping
   in `_SCHEMAS`.

3. **`generator.py`** — aggiungi `clickhouse` nei `choices` di
   `--dialect` e in `_DIR_NAME`.

### Generare un seed personalizzato (es. 1M righe per perf test)

```sh
python3 -m seed_generator.generator \
    --dialect postgres --output volume \
    --rows-events 1000000 \
    --out-dir ./out-perf
```

Il file generato sara' `~120 MB` (1M righe * ~120 byte/riga). Per
caricarlo in PostgreSQL, attenzione a `max_wal_size` /
`maintenance_work_mem`. In MySQL, `max_allowed_packet` deve essere
> della singola INSERT chunk (il generator emette chunk da 1000
righe).

## Pinning seed corrente vs personalizzato

| Use case                                | File da usare                              |
|-----------------------------------------|--------------------------------------------|
| Test E2E standard (CI, dev locale)      | `test-e2e/sql/<dialect>/01_seed.sql` (pinnati) |
| Test perf, scenari custom               | output del generator                        |
| Aggiungere righe/tabelle alla baseline  | modificare `fixtures.py` + rigenerare       |

Il generator NON sovrascrive automaticamente i file pinnati. Per
**rigenerare** i seed di baseline (es. dopo aggiunta tabella):

```sh
cd test-e2e/sql
python3 -m seed_generator.generator \
    --dialect all --output all \
    --rows-events 10000 \
    --out-dir .
```

(commit il diff per review prima del merge.)
