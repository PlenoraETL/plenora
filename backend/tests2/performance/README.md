# tests2/performance — stream-perf (Fase 1)

Benchmark e correctness test per le ottimizzazioni PERF di Fase 1.

## Struttura

- `conftest.py` — fixture di generazione dataset sintetici (df/gdf), helper di timing
- `test_baseline_*.py` — baseline measurement dello stato attuale (previous implementation)
- `test_bench_*.py` — benchmark post-refactor (speedup vs baseline, target >=3x)
- `test_correctness_*.py` — invariant preservation (output identico pre/post)

## Marker pytest

- `perf_baseline` — run slow baseline; skip di default, attiva con `-m perf_baseline`
- `perf_bench` — benchmark post-optimization; skip di default, attiva con `-m perf_bench`
- `perf_correctness` — test correttezza, eseguiti in CI

## Timing

Non dipendiamo da `pytest-benchmark` in Batch 0: usiamo `time.perf_counter`. La dep
`pytest-benchmark` sara' richiesta via `STREAM_REPORT.md` per Batch 1+ quando serve
stat-significance su run multipli.

## DB integration

I baseline bulk (COPY vs executemany) funzionano in-memory:
- la parte CSV-serialization e params-list-construction e' misurabile senza DB
- la parte network+DB e' gated da `PERF_PG_TEST_URL` env var; skip se assente

## Dataset synthetics

I generatori vivono in `conftest.py`:
- `make_pandas_df(n)` — DataFrame con colonne int/str/float/date
- `make_geo_points_gdf(n)` — GeoDataFrame di punti (EPSG:3857)
- `make_geo_polygons_gdf(n)` — GeoDataFrame di poligoni con overlap parziali
- `make_diff_inputs(n, changed_ratio)` — coppia (old, new) per op_table_diff
