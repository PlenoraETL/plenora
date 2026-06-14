# Functional Tests

`tests2/functional/` ospita i test che esercitano il backend con processi veri:

- server FastAPI
- worker Dramatiq (`python -m dramatiq workers.dramatiq_app`)
- eventuale beat APScheduler
- fake services esterni

Questa cartella NON sostituisce `tests2/*/unit/`.
Serve a coprire i flussi reali fra moduli quando i soli unit/contract test
non bastano piu'.

## Obiettivi

- verificare i flussi end-to-end piu' rischiosi
- validare l'integrazione fra router, service, worker e result store
- simulare sistemi esterni in modo deterministico
- testare lifecycle completo: boot, ready, run, teardown, cleanup

## Struttura

```text
tests2/functional/
  conftest.py
  harness/
    celery.py        # bootstrap worker/beat (Plenora 2.0: spawn Dramatiq, non Celery)
    http.py
    processes.py
    ports.py
  fakes/
    api_connector_fake.py
  api_connector/
  orchestrator/
  scheduler/
```

## Regole

- pochi scenari, alto ROI
- niente dipendenze da internet o credenziali reali
- usare sempre fake services locali per `api_connector`
- teardown robusto: nessun processo o file temporaneo deve restare vivo
- i test funzionali devono poter essere lanciati in subset

## Roadmap minima

1. `api_connector/`
   server fake HTTP con endpoint JSON, errori e file download
2. `orchestrator/`
   1 happy path sync e 1 async con worker
3. `scheduler/`
   1 flow con run archiviata e download/listing

## Note

- Questa cartella e' preparata adesso come harness base.
- L'harness worker (Dramatiq su RabbitMQ) e' stato predisposto ma non ancora
  usato nei test.
- I primi test funzionali veri verranno aggiunti modulo per modulo.
