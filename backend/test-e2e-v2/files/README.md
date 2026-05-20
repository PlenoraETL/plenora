# Plenora E2E test fixtures — `files/`

Sample files in every format Plenora ingests (upload + parse). Used as
fixtures for end-to-end tests that compare **upload-via-file** with
**load-via-DB**.

## Coherence with the SQL seed

All identifiers, names, geographic coordinates and relational keys mirror
the base block of `test-e2e-v2/sql/postgres/01_seed.sql`:

- `regions` ids 1..3 (LOM, LAZ, CAM)
- `cities` ids 1..9 (base) + ids 10..18 (geographic edges, used only in
  the geo-edge fixtures)
- `customers` ids 1..12 (base) + ids 13/14/15/19 (Unicode/emoji edges,
  used only in `customers_unicode.csv`)
- `products` ids 1..8 (base set)
- `orders` ids 1..15 (base set)

Identifiers in CSV/JSON/Shapefile/GPKG are guaranteed to coincide with
the corresponding rows in the SQL seed, so a parser test can compare
"the row I uploaded" against "the row I would have read from the DB".

## Index

### `csv/` — CSV variants

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `customers_basic.csv`      | RFC 4180 baseline — comma separator, UTF-8, no BOM, header + 12 rows     |
| `customers_semicolon.csv`  | `;` separator (Italian/European locale)                                  |
| `customers_tab.tsv`        | Tab-separated values                                                     |
| `customers_utf8_bom.csv`   | UTF-8 with leading BOM (`EF BB BF`) — parser must strip it               |
| `customers_iso8859.csv`    | ISO-8859-1 (Latin-1) encoding; only Latin-1-safe rows included           |
| `customers_quoted.csv`     | RFC 4180 quoting: comma inside quoted field (`"Rossi, Anna"`) + escaped quotes |
| `customers_unicode.csv`    | CJK (`北京 用户`), emoji (`🎉`), apostrophe (`O'Brien`), umlaut (`Müller`)|
| `customers_with_nulls.csv` | Empty cells = NULL semantics on `age`, `signup_date`, `status`            |
| `empty.csv`                | Header only, zero data rows                                              |
| `truly_empty.csv`          | 0-byte file                                                              |
| `header_only.csv`          | Header line, no trailing newline                                         |
| `corrupted_quoting.csv`    | **Intentionally invalid** — unbalanced double-quote (parser must error)  |
| `mixed_types.csv`          | Numeric `age` column with one stringy value (`thirty`) — type inference  |

### `xlsx/` — Excel (Office Open XML)

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `multi_sheet.xlsx`         | 3 sheets: `customers`, `products`, `orders` (sheet selection)            |
| `single_sheet.xlsx`        | 1 sheet `customers`                                                      |
| `with_formulas.xlsx`       | Contains `=SUM(E2:E9)` and `=SUM(D2:D10)` (formula evaluation/skip)      |
| `with_merged_cells.xlsx`   | Merged title across `A1:G1` above the header row                          |
| `large_one_sheet.xlsx`     | `events_log` sheet with 5000 data rows (~135 KB) — bulk read path        |

### `xls/` — Legacy Excel 97-2003

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `customers_legacy.xls`     | BIFF8 (xlwt) — only present if `xlwt` is installable; otherwise skipped  |

> If `xlwt` is unavailable on the build machine the generator skips this
> file and prints a warning. All other fixtures remain valid.

### `json/` — JSON & NDJSON

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `customers.json`           | Top-level array of 12 customer objects                                   |
| `customers.ndjson`         | Newline-delimited JSON, one customer per line                            |
| `nested.json`              | Nested `address: {street, city, zip}` (flatten / nested-extract)         |
| `with_nulls.json`          | 5 customers; some fields explicitly `null` (incl. `city_id`)             |
| `malformed.json`           | **Intentionally invalid** — trailing comma before `]` (parser must error)|
| `single_object.json`       | A single object (not an array) — auto-detection of single-vs-array       |
| `cities_export.json`       | 9 cities with numeric `latitude`/`longitude` (non-formal geo)            |

### `geojson/` — GeoJSON FeatureCollection

| File                                 | What it tests                                                        |
| ------------------------------------ | -------------------------------------------------------------------- |
| `cities_points.geojson`              | 9 base Point cities, EPSG:4326                                       |
| `cities_with_geo_edges.geojson`      | 18 cities incl. Tokyo Outpost / NullIsland / PoleStation / DateLineSpot |
| `mixed_geometries.geojson`           | Point + LineString (Roma↔Latina) + Polygon (Milano square)           |
| `empty_features.geojson`             | `{"type":"FeatureCollection","features":[]}` (empty collection)      |
| `null_geometry.geojson`              | A feature with `geometry: null` next to a normal Point                |

### `shp/` — Shapefile (zipped)

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `cities_points.zip`        | `.shp + .shx + .dbf + .cpg + .prj` for the 9 base cities, EPSG:4326      |
| `cities_with_data.zip`     | Same geometry but extra attribute columns (`pop`, `lat`, `lng`, `category`, `is_capital`) |

DBF stores `founded_year` as int with sentinel `-9999` where the seed has `NULL`
(shapefile DBF cannot represent SQL NULL on integer columns natively).

### `gpkg/` — GeoPackage

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `cities.gpkg`              | Single layer `cities` (Point, EPSG:4326) — 9 base rows                   |
| `multi_layer.gpkg`         | Two layers: `cities` (Point) + `customers` (attribute-only, no geometry) |

### `parquet/` — Apache Parquet (8)

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `customers.parquet`        | 12 base customers — `id, city_id int64`, `age, in_stock_count int32`, `signup_date date32`, strings; row 12 has `city_id = NULL` (matches seed) |
| `cities.parquet`           | 9 base cities — `id, region_id, population int64`, `founded_year int32` (NULL on Frosinone), `latitude/longitude float64` |
| `products.parquet`         | 8 products — `price decimal128(12,2)` (exact decimal preservation), `weight_kg float64`, `in_stock_count int32` |
| `large.parquet`            | 5000 deterministic rows shaped like `events_log` (id, customer_id, event_type, `occurred_at timestamp[us]` naive UTC, `value decimal128(12,2)` with NULL every 5th row) |
| `with_nulls.parquet`       | 5 rows with NULL spread across `city_id`, `age`, `signup_date`, `status` (PyArrow null-handling) |
| `nested.parquet`           | Complex types: `address struct<street,city,zip>`, `tags list<string>`, `metadata map<string,string>` (4 rows, includes empty list and null-in-struct) |
| `compressed_snappy.parquet`| Same data as `customers.parquet`, written with explicit `compression='snappy'` |
| `compressed_gzip.parquet`  | Same data as `customers.parquet`, written with `compression='gzip'` (different page-data block) |

> All timestamps are stored as `timestamp[us]` without timezone (naive UTC),
> matching the SQL seed's `TIMESTAMP` (no `WITH TIME ZONE`). Decimals use
> `decimal128(12,2)` to mirror `DECIMAL(12,2)` in the schema.

### `zip/` — Composite archives

| File                       | What it tests                                                            |
| -------------------------- | ------------------------------------------------------------------------ |
| `mixed_archive.zip`        | ZIP containing `customers.csv` + `cities.geojson` (multi-format unwrap)  |
| `nested_zip.zip`           | ZIP containing another ZIP (`inner.zip`) — depth/recursion limit         |

## Files that MUST fail parsing

These fixtures are intentionally malformed and exist to validate error
handling. A passing parser MUST raise/return a structured error:

- `csv/corrupted_quoting.csv` — unbalanced `"` (CSV strict parser fails)
- `json/malformed.json` — trailing comma before `]` (strict JSON parser fails)

The other "edge" files (`empty.csv`, `truly_empty.csv`,
`header_only.csv`, `geojson/empty_features.geojson`) are STRUCTURALLY
VALID but semantically empty — parsers should accept them and return an
empty dataset.

## Library notes

The fixtures were generated with a single Python script (`/tmp/gen_fixtures.py`
during construction) that uses:

- builtin `csv`, `json`, `zipfile`, `io` (CSV/TSV/JSON/NDJSON/GeoJSON/ZIP)
- `openpyxl` (XLSX)
- `xlwt` (XLS legacy — best-effort, may be absent)
- `geopandas` + `pyogrio` + `shapely` (Shapefile, GeoPackage)
- `pyarrow` (Parquet)

If `xlwt` is not available on a build machine the legacy `.xls`
fixture is skipped; everything else is reproducible.

## Size budget

Every individual file is well below the 5 MB ceiling. The largest files
are `xlsx/large_one_sheet.xlsx` (~135 KB, 5000 rows) and
`parquet/large.parquet` (~58 KB, 5000 rows). Total tree ≈ 575 KB.
