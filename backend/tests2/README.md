# Backend Test Suite

`backend/tests2` e' la suite backend 1.0.

## Obiettivo

La suite copre contratti pubblici, invarianti di sicurezza, boundary architetturali e regressioni su bug reali. I test devono proteggere comportamento utile, non dettagli temporanei di implementazione.

## Esecuzione

```powershell
cd backend
python -m pytest tests2 -c tests2/pytest.ini
```

Subset frequenti:

```powershell
python -m pytest tests2/shared -c tests2/pytest.ini
python -m pytest tests2/security -c tests2/pytest.ini
python -m pytest tests2/manipola -c tests2/pytest.ini
```

## Struttura

```text
tests2/
  shared/        guard architetturali e contratti cross-cutting
  security/      auth, ruoli, tenant, segreti
  arricchisci/   enrichment e provider REST
  carica/        scrittura su destinazioni
  estrai/        estrazione e query
  features/      ArcGIS/features
  intermediate/  run, snapshot, retention, artifact
  manipola/      trasformazioni
  orchestrator/  pipeline/task service
  scheduler/     schedule e beat
  functional/    test con processo backend reale
  integration/   test con servizi persistenti reali
  performance/   correctness e benchmark controllati
```

## Regole

- Preferire assert su contratti osservabili.
- Evitare patch su dettagli privati quando esiste un boundary pubblico.
- Ogni test di regressione deve descrivere il comportamento corretto atteso.
- I guard architetturali devono fallire su nuove violazioni, non documentare debito indefinito.
- I test con DB o servizi reali devono essere marcati e skippabili quando l'ambiente non e' disponibile.

## Quando aggiungi codice

1. aggiungi test unit/contract vicino al dominio interessato;
2. aggiorna i guard solo se cambia intenzionalmente una boundary;
3. evita nuovi allowlist senza removal condition;
4. esegui almeno il subset del dominio e i guard `tests2/shared`.
