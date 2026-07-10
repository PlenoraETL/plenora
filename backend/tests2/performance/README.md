# Performance Tests

Test di correttezza e benchmark controllati per i percorsi dati sensibili.

## Esecuzione

Correctness test, adatti alla CI:

```powershell
cd backend
python -m pytest tests2/performance -c tests2/pytest.ini -m perf_correctness
```

Benchmark manuali:

```powershell
python -m pytest tests2/performance -c tests2/pytest.ini -m perf_bench -s
```

Benchmark RSS mirato al preflight dei join MANIPOLA:

```powershell
python -m pytest tests2/performance/test_bench_manipola_join_memory.py -c tests2/pytest.ini -m perf_bench -s
```

Il benchmark esegue legacy e implementazione corrente in processi separati e
confronta il picco RSS su due DataFrame sintetici larghi.

## Marker

- `perf_correctness`: invarianti di output, eseguibili in CI.
- `perf_bench`: benchmark locali, sensibili alla macchina.
- `perf_baseline`: misure comparative da eseguire solo quando servono.

## Regole

- I correctness test devono essere deterministici.
- I benchmark non devono essere usati come unica prova di correttezza.
- Se una libreria nativa rende instabile un path di benchmark, isolare il benchmark o marcarlo con skip motivato.
- Le soglie devono essere conservative e giustificate da un rischio operativo reale.
