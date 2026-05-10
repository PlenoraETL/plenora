# perf_queries — Observability baseline (read-only)

Saved queries SQL per aggregare i marker `[PERF]` emessi dal backend
nella tabella `logs` (PostgreSQL JSONB).

Tutte read-only (SELECT-only). Nessuna scrittura, nessuna DDL, niente
funzioni custom, niente extension. Compatibili PG 12+.

I marker `[PERF]` sono già emessi dal backend in 7 file:
- `app/utils/dataframe.py`
- `app/utils/result_store.py`
- `app/shared/cloud_backends.py`
- `app/core/intermediate/artifacts/resolver.py`
- `app/core/intermediate/snapshot.py`
- `app/db/results_db_service.py`
- `app/core/arricchisci/api_connection_source_loading.py`

I record arrivano in `logs.details` JSONB tramite `PostgresLogHandler`
(vedi `app/utils/logger.py` + whitelist `_BULK_TELEMETRY_EXTRA_WHITELIST`).

## Inventario

| File | Scopo |
| --- | --- |
| `q_perf_1_s3_download_p50_p95.sql` | p50/p95 latenza `s3_download op=get_bytes` ultimi 7 giorni |
| `q_perf_2_storage_backend_distribution.sql` | Conteggio + media `duration_ms` per `backend=object_storage` vs `db_legacy` |
| `q_perf_3_record_final_snapshot_top_db_ms.sql` | Top 20 `record_final_snapshot` per `db_ms` (slow path detection) |
| `q_perf_4_load_source_project_p95.sql` | `load_source_project` p95 per `project_id` (HAVING count >=5) |
| `q_perf_5_intermediate_run_report.md` | Riferimento a `intermediate_run_report.py` (script Python, non SQL) |
| `q_perf_6_full_artifact_artifact_ms.sql` | Distribution `artifact_ms` per `persistence_mode=full_artifact` |

## Come usare

### Esecuzione manuale (psql / DBeaver / pgAdmin)

Le query sono **incollabili così come sono**: nessun parametro
posizionale (`$1`/`$2`). La finestra temporale è hardcoded a `NOW() -
INTERVAL '7 days'` con commento esplicito per personalizzare.

Esempio (psql interactive su host che ospita il DB):

```sh
psql -d <database> -f q_perf_1_s3_download_p50_p95.sql
```

### Esecuzione via tool BI (Grafana SQL panel / Metabase question)

Copia-incolla il contenuto del file. Il sink (Grafana / Metabase /
DBeaver shared workspace) non è imposto da queste query.

### Personalizzazione finestra temporale

Tutti i file usano `WHERE ts >= NOW() - INTERVAL '7 days'`. Per finestre
diverse:

- Ultimo giorno: `INTERVAL '1 day'`
- Ultimo mese: `INTERVAL '30 days'`
- Range esplicito: `WHERE ts BETWEEN '2026-01-01' AND '2026-01-07'`

## Vincoli operativi

- ✅ Read-only (SELECT puro)
- ✅ Nessuna `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`
- ✅ Nessuna `pg_read_file`, `COPY FROM`, `dblink` o function privilegiata
- ✅ Nessun JOIN cross-database
- ✅ Nessun valore di credenziali / hostname / IP hardcoded
- ✅ Compatibile PG 12+

## Nota su `Q-PERF-5`

`Q-PERF-5` non è una query SQL ma un riferimento allo script Python
esistente `backend/scripts/intermediate_run_report.py`. Vedi
`q_perf_5_intermediate_run_report.md` per dettagli.

## Out-of-scope

- Setup dashboard infra (Grafana / Metabase)
- Esecuzione queries su prod DB
- Automatic alerting / scheduled report
- Dashboard panel embedding

## Riferimenti

- `backend/app/utils/logger.py` (PostgresLogHandler + whitelist details)
- `backend/scripts/perf_bulk_verdict.py` (template verdict-on-rates)
- `backend/scripts/intermediate_run_report.py` (drift detection)
