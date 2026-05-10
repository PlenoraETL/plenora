# Performance Queries

Query read-only per analizzare i marker di performance salvati nella tabella `logs` di PostgreSQL.

## Uso

Eseguire le query da `psql`, DBeaver, pgAdmin o da un pannello SQL di osservabilita'.

```powershell
psql -d <database> -f q_perf_1_s3_download_p50_p95.sql
```

Le query non scrivono dati e non richiedono estensioni custom.

## Inventario

| File | Scopo |
| --- | --- |
| `q_perf_1_s3_download_p50_p95.sql` | latenza p50/p95 download object storage |
| `q_perf_2_storage_backend_distribution.sql` | distribuzione backend storage e durata media |
| `q_perf_3_record_final_snapshot_top_db_ms.sql` | snapshot piu' lenti per tempo DB |
| `q_perf_4_load_source_project_p95.sql` | p95 caricamento project source |
| `q_perf_6_full_artifact_artifact_ms.sql` | distribuzione persistenza full artifact |

## Personalizzazione finestra temporale

Le query usano una finestra recente, di norma `NOW() - INTERVAL '7 days'`. Cambiala nel `WHERE` quando serve un range diverso.

## Vincoli

- solo `SELECT`;
- nessuna DDL/DML;
- nessun valore sensibile hardcoded;
- compatibile con PostgreSQL 12+.

## Fonte dati

I marker arrivano dai logger applicativi nel campo JSONB `logs.details`. Quando aggiungi un nuovo marker di performance, aggiungi una query solo se serve a una decisione operativa reale.
