# Plenora — Test Suite v5

## Struttura

```
tests/
├── conftest.py              ← fixture globali (client HTTP, pg_conn, make_project, ecc.)
├── mock_api_server.py       ← server mock per test ARRICCHISCI e CARICA
├── pytest.ini
│
├── estrai/
│   ├── integration/         ← test E-01→E-17 (richiedono backend + Docker)
│   │   ├── test_E01.py ... test_E17.py
│   └── unit/                ← test unitari puri (no DB, no API, no Celery)
│       ├── conftest.py       ← setup sys.path + builder canvas
│       ├── test_query_generator.py
│       ├── test_type_mapper.py
│       ├── test_executor_helpers.py
│       └── test_query_executor_unit.py
│
├── manipola/
│   ├── integration/         ← test M-01→M-22
│   └── unit/                ← da aggiungere (transforms, executor helpers)
│
├── arricchisci/
│   ├── integration/         ← test A-01→A-74
│   └── unit/                ← da aggiungere (EnrichmentExecutor helpers, api_utils)
│
└── carica/
    ├── integration/         ← test C-01→C-30
    └── unit/                ← da aggiungere (column_mapper, ddl_generator)
```

## Come eseguire

### Solo unit test (veloci, zero infrastruttura)
```bash
pytest -m unit -v
```

### Solo integration test (richiede backend + Docker)
```bash
pytest -m integration -v
# oppure per modulo specifico:
pytest estrai/integration/ -v
```

### Solo ESTRAI unit
```bash
pytest estrai/unit/ -v
```

### Con coverage
```bash
pip install pytest-cov
pytest estrai/unit/ --cov=app.core.estrai --cov=app.db.query_executor --cov-report=term-missing
```

### Con mutation testing (mutmut)
```bash
pip install mutmut
mutmut run --paths-to-mutate app/core/estrai/query_generator.py \
           --tests-dir tests/estrai/unit/
mutmut results
```

## Filosofia della suite

### Integration test (`integration/`)
Testano il sistema **dall'esterno**: chiamano l'API REST, aspettano task Celery,
verificano il risultato finale. Coprono il happy path end-to-end.

**Pro**: realistici, testano l'integrazione tra componenti.  
**Contro**: lenti (~60s/test), richiedono Docker, coprono solo i branch principali.

### Unit test (`unit/`)
Testano le **classi Python direttamente**, senza infrastruttura.

**Pro**: velocissimi (<1ms/test), coprono ogni branch/edge case, uccidono i mutanti.  
**Contro**: non testano l'integrazione.

### Regola d'oro
Ogni bug trovato in produzione → aggiungere un unit test che lo riproduce,
poi verificare che passi dopo il fix. Questo previene le regressioni.

## Metriche target

| Metrica | Stato attuale (solo integration) | Target con unit test |
|---------|-----------------------------------|----------------------|
| Line Coverage ESTRAI | ~45% | >80% |
| Branch Coverage ESTRAI | ~25% | >65% |
| Mutation Score QueryGenerator | ~15% | >60% |

## Aggiungere unit test per un nuovo modulo

1. Copiare `_template_unit_conftest.py` in `{modulo}/unit/conftest.py`
2. Identificare le classi con metodi puri (nessun I/O): queste sono le prime da testare
3. Per ogni metodo: scrivere test per ogni `if/elif/else` + test sui boundary
4. Regola mutation score: ogni assert deve fallire se il codice viene "mutato"
   di poco (es. `>` → `>=`, `and` → `or`, stringa cambiata)
