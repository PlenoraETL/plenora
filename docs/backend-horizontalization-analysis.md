# Analisi orizzontalizzazione backend

Scope: `backend/` senza test (`tests`, `test*`, `__pycache__` esclusi). Data analisi: 2026-05-16.

## Executive summary

Il backend e' gia' orientato al multi-replica su tre assi importanti:

- stato applicativo principale su PostgreSQL (`projects`, `connections`, `files`, `results`, `logs`, `schedules`, `intermediate`, `backup_runs`);
- task asincroni su Celery con Redis broker/result backend;
- result/file_source/eseguibili/IMDF/intermediate artifact migrati o predisposti verso object storage runtime.

I punti ancora da governare operativamente per una vera orizzontalizzazione sono:

1. filesystem locale ancora usato solo come cache o staging ricostruibile (`DATA_DIR/temp`, `FILES_DIR`, `IMDF_BUNDLES_DIR`, `ESEGUIBILI_OUTPUTS_DIR`);
2. fallback in-memory confinati a dev/single-host (`login_rate_limiter`, JWT metrics);
3. lock process-local usati come guard solo intra-processo in singleton/cache non canonici;
4. scheduler beat single-leader gestito via Redis e bloccato fail-closed in produzione;
5. tuning aggregato pool DB/HTTP per numero di pod/processi, per evitare che lo scale-out saturi Postgres o provider esterni;
6. datasource `local` valido solo se montato identicamente su ogni worker.

## Prerequisiti produzione multi-host

- `DATABASE_URL` deve puntare a Postgres condiviso.
- `CELERY_BROKER_URL` e `CELERY_RESULT_BACKEND` devono puntare a Redis condiviso, non localhost.
- `OBJECT_STORAGE_ENABLED=true` e `OBJECT_STORAGE_BUCKET` devono essere configurati per runtime artifact.
- `STORAGE_POLICY=hybrid` o `object_storage`; `db` e' orizzontalizzabile ma scarica tutto su Postgres.
- `DB_POOL_MAX * (uvicorn_workers + celery_concurrency + beat_count)` deve stare sotto il limite reale di Postgres.
- Un solo beat leader logico: Redis leader election attiva e `SCHEDULE_BEAT_LEADER_FAIL_OPEN=false` in cluster.

## Cartelle e file rilevanti

### `backend/app/main.py`

- Buono: composition root stateless; routers e servizi sono costruiti all'avvio del processo.
- Buono: `/ready` controlla PG, Redis, object storage, migrations e headroom pool.
- Punto di scala: ogni worker Uvicorn ha i propri singleton e il proprio PG pool; e' corretto, ma va dimensionato.

### `backend/app/bootstrap.py`, `backend/app/startup_validation.py`

- `bootstrap.py` usa `_BOOT_COMPLETE` process-local: ok per idempotenza per processo, non e' stato applicativo condiviso.
- `startup_validation.py` crea/valida `DATA_DIR`, pulisce `TEMP_DIR` e in dev puo' avviare Redis via WSL. In produzione multi-host questo path deve restare solo validazione, non provisioning runtime.
- `TEMP_DIR` cleanup allo startup puo' impattare solo il nodo locale: ok se e' cache/staging, non ok se ci finisce dato canonico.

### `backend/app/config.py`

- Definisce `DATA_DIR`, `TEMP_DIR`, `RESULTS_DIR`, `FILES_DIR`, `ESEGUIBILI_OUTPUTS_DIR`, `IMDF_BUNDLES_DIR`, `SCHEDULES_DIR`.
- Questi path sono nodo-locali salvo volume condiviso. Per cluster: usarli solo come temp/cache ricostruibile, o montarli su storage condiviso.
- `OBJECT_STORAGE_*`, `STORAGE_POLICY`, soglie S3/DB e readiness sono i principali toggle per orizzontalizzare artifact.

### `backend/app/db/pg_connection.py`

- PG pool singleton per processo (`ThreadedConnectionPool`), fork-safe per Celery con `discard_pool_after_fork`.
- Punto di scala: il limite effettivo e' aggregato su processi/pod. Non aumentare repliche senza ridurre `DB_POOL_MAX` o introdurre PgBouncer.
- Nessun `threading.local`: `transaction()` e' alias compatibile di `unit_of_work()` e produce una `PgSession` esplicita.
- `PgSession`/`unit_of_work()` sono il boundary esplicito per il porting Go. I wrapper compat `execute`/`fetchone` passano da `unit_of_work`, ma i nuovi repository dovrebbero ricevere sessione esplicita invece di importare helper globali.

### `backend/app/utils/logger.py`

- Logging persistito su Postgres: buono per multi-replica.
- Handler e logger cache sono process-local: ok.
- Attenzione a volume log: aumentando pod aumenta write pressure su `logs`; retention/archive beat deve essere attivo.

### `backend/workers/celery_app.py`

- Buono: Celery usa Redis, `acks_late`, `reject_on_worker_lost`, `worker_prefetch_multiplier=1`.
- Buono: re-bootstrap post-fork per evitare pool PG ereditato.
- Buono: le code Celery sono ora separate per ruolo operativo:
  `CELERY_QUEUE_DATA_PLANE` per pipeline/preview/carica/enrichment/IMDF,
  `CELERY_QUEUE_CONTROL_PLANE` per backup amministrativi e
  `CELERY_QUEUE_SCHEDULER` per heartbeat scheduler leggero. Anche le pipeline
  schedulate vanno sulla Data Plane queue perche' eseguono pandas/GDAL come
  le run manuali. Questo consente worker
  pool dedicati e autoscaling separato senza far competere job pandas/GDAL con
  task di controllo.
- Punto di scala: concorrenza Celery moltiplica pool PG, HTTP pool, memoria pandas/geopandas.

### `backend/workers/beat_scheduler.py`

- Buono: leader election Redis con chiave `scheduler:beat:leader`, fail-closed di default.
- Buono: heartbeat DB, cleanup, reconciler storage, retention, stuck task cleanup.
- Punto critico: se `SCHEDULE_BEAT_LEADER_FAIL_OPEN=True` in cluster, piu beat possono emettere duplicati durante Redis down.
- Cleanup temp/IMDF/eseguibili e' locale al nodo corrente: non pulisce cache su altri pod se i volumi sono separati. Serve DaemonSet/CronJob per nodo o spostare tutto su object storage con lifecycle.

### `backend/app/core/scheduler/*`

- `concurrency_manager.py`: lock e coda scheduler su Postgres (`scheduler_locks`), buono per multi-worker.
- `schedule_service.py`: gli update read-modify-write del JSON config passano da `db.update_schedule_locked()`, che usa `SELECT ... FOR UPDATE` dentro transazione. Il vecchio `_schedule_locks` process-local e' stato rimosso: la sincronizzazione canonica e' solo DB.
- `db.py`: `update_schedule_locked()` e' il boundary cluster-safe per read-modify-write; `update_schedule()` resta helper low-level per update puntuali gia' materializzati.
- `execution_manager.py`: le run schedulate costruiscono lo stesso `execution_contract` delle run HTTP e chiamano `execute_pipeline_from_contract()`, quindi anche questo path non dipende piu' dal lookup canvas/metadata dentro l'orchestrator.
- `execution_manager.py`: heartbeat lock durante run schedulate, buono. Non materializza piu' result blob in `RESULTS_DIR` per archive: il riferimento canonico resta `execution_info.result_id` nel log schedule.
- `runs_catalog.py`: lista e download run schedulate leggono prima da `schedule_logs.execution_info.result_id` + metadata/blob `results`, quindi il path cluster-safe e' DB/object-storage backed. `run_archive_store.py` e `artifact_store.py` restano compat legacy filesystem per run pre-migrazione gia' archiviate su disco, non piu' source of truth per nuove run.

### `backend/app/core/orchestrator/*`

- `task_service.py`: pending metadata (`task_id -> project_id/task_type`) e' persistito tramite `task_metadata_store.py` su Redis usando `CELERY_BROKER_URL`, con TTL allineato a `CELERY_RESULT_EXPIRES`. Il default runtime e' Redis-only: lo store in-memory resta solo test double esplicito, non fallback automatico dev/prod.
- `execution_contract.py`: il contratto API Control Plane -> Worker Data Plane e' ora un builder esplicito, non JSON assemblato nel router. Include `schema_version`, `project_id`, `project_type`, `group_id`, `canvas`, `columns_metadata`, `artifact_refs` estratti dal canvas (`file_sources`, `project_sources`, `result_refs`) e retention opzionale. Questo rende il futuro Control Plane Go compatibile con gli stessi worker Python senza importare logica Python di progetto.
- `PipelineTaskPayload` puo' trasportare un `execution_contract` gia' risolto dal control plane. Quando presente, `workers.tasks.pipeline_task` chiama `OrchestratorService.execute_pipeline_from_contract()` e non rilegge metadata/canvas progetto dal DB: questo e' il primo boundary Control Plane (Go-ready) -> Data Plane Python.
- `router.py`: download progetto usa solo result_id/blob/object storage; il fallback path su `RESULTS_DIR` e' rimosso dal comportamento API.
- `service.py`: orchestration stateless; ok.

### `backend/app/core/results/*`

- `blob_writer.py`: buon punto di orizzontalizzazione. Salva in DB o object storage e registra upload intent per reconciliation.
- `pipeline_result.py`: per `gpkg`/`shp` usa temp locale solo per serializzare, poi salva bytes come result blob. Ok se il temp e' effimero.
- `legacy_file_store.py`, `temp_paths.py`, `result_reader.py`: compat filesystem/temp confinata ad adapter interni. I router non usano piu' `RESULTS_DIR` come fallback download/input operativo.
- `db.py` e `cleanup_repository.py`: repository result migrati a `unit_of_work()`/`PgSession` espliciti, senza import diretto dei wrapper legacy `execute/fetchone/fetchall`.
- `cleanup.py`: usa `FOR UPDATE` e reconciliation, buono per concorrenza.

### `backend/app/core/files/*`

- `storage_dispatch.py`, `db.py`, `source_blob.py`: file_source in DB/object storage; buono.
- `source_loading.py`: materializza file_source in cache stabile sotto `FILES_DIR` con `.tmp + os.replace`. In cluster ogni nodo si scalda la propria cache: ok se cache ricostruibile, non ok per dati non presenti in DB/object storage.
- `upload.py`, `multi_layer_upload.py`, `preview_temp.py`: temp locali durante upload/preview; ok come staging.
- `service.py`: `file_source_service` singleton per processo, ma stateless rispetto ai dati canonici.

### `backend/app/core/intermediate/*`

- `artifact_writer.py`: full artifact su object storage, buono.
- `policy.py`: `_validated_once` process-local: ok come boot/cache, ma se env cambia runtime le repliche non convergono finche' non restartano.
- `runs_db.py`, `node_snapshots_db.py`: Postgres-backed; ok.
- `artifact_reader.py`, `preview_parser.py`: leggere preview puo' materializzare bytes/temp; attenzione RAM su molte repliche.

### `backend/app/core/resources/*`

- `registry.py`: singleton process-local del Resource Engine, ok per routing interno.
- `loaders/result_blob.py`, `loaders/file_source_blob.py`: storage-aware con bounded read; buono.
- `storage_reconciliation.py`: outbox/reconciliation su Postgres + object storage; punto forte per cluster. Dipende dal beat hook: in produzione serve beat leader vivo e monitorato.

### `backend/app/infrastructure/storage/cloud_backends.py`

- Buona astrazione S3/MinIO e capability `OBJECT_STORAGE_ENABLED`.
- La config storage passa da `StorageBackendConfig` popolato tramite `Settings`; il modulo non legge piu' direttamente `os.getenv`, mentre i validator fail-fast usano `Settings.get_raw` per distinguere unset da malformed.
- Punto di scala: `_client()` crea client boto3 per chiamata. Funziona, ma a throughput alto conviene client cache per processo con timeout/retry espliciti anche runtime, non solo readiness.
- `validate_storage_config` richiede ancora bucket backup legacy in alcuni casi hybrid: verificare che `OBJECT_STORAGE_BUCKET` sia il canonico runtime.

### `backend/app/core/eseguibili/*`

- `storage.py`: output su object storage, buono.
- `output_store.py`: owner sidecar dei task su object storage only; se object storage non e' disponibile il salvataggio fallisce loud. Il filesystem locale resta solo cleanup di residui legacy, non read/write fallback di ownership.
- `router.py`: upload temporanei in `DATA_DIR/temp/eseguibili_uploads`; ok se immediatamente passati a Celery e cleanup beat e' per-nodo. Meglio evitare path locali fra API e worker se worker sta su altro host.

### `backend/app/core/imdf/*`

- `storage.py`: salva zip/meta su object storage e usa `IMDF_BUNDLES_DIR` come cache locale. Buono per multi-host con cache miss hydration.
- `router.py`, `bundle_access.py`, `bundle_store.py`: hydration locale da S3. Ok, ma cleanup cache e size calcolabile sono nodo-locali.
- Requisito: object storage attivo; senza, bundle generati su un worker non sono visibili alle altre repliche.

### `backend/app/core/datasources/*`

- `local.py`: rischio principale. Path locali sono accessibili solo al nodo corrente; il codice emette advisory ma consente l'uso. In cluster usare solo UNC/NFS/EFS montato identico, oppure SFTP/FTP/SharePoint.
- `service.py`: `scan_state` e processed state sono su PostgreSQL (`datasources.scan_state`). Il fallback legacy su `DATA_DIR/datasources_scan/*.json` e' stato rimosso: il datasource service non tocca piu' filesystem locale per lo stato di scan.
- `ftp.py`, `sftp.py`, `sharepoint.py`: naturalmente multi-host se credenziali e rete sono uguali.

### `backend/app/core/auth/*`, `backend/app/shared/login_rate_limiter.py`, `backend/app/shared/token_blacklist.py`

- JWT e utenti su DB; ok.
- `login_rate_limiter.py`: Redis-backed; il fallback in-memory resta solo dev/single-host perche' production/staging/cluster richiedono `LOGIN_RL_REDIS_REQUIRED=true` via startup gate.
- `rate_limit_fallback.py`: `_STORE` process-local; solo fallback non-prod.
- `token_blacklist.py`: blacklist su Redis, ma read fail-open e metriche in-process. Logout write fallisce loud, ma check token con Redis down non revoca. Valutare fail-closed per ruoli admin/superadmin.
- MFA su DB; ok.

### `backend/app/core/arricchisci/*`

- `enrichment_service.py`: cache dedup, stats e rate limiter sono per istanza/task/processo. Non garantiscono rate limit globale verso API esterne.
- `_rate_limiter.py`: token bucket locale resta default dev, ma production/staging/cluster richiedono `ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED=true` e `ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED=true`, quindi le quote verso provider esterni sono globali tra pod/worker.
- `api_connection_rest_file.py`: molti path sono stati refactorati verso in-memory/object storage; residui `mkdtemp` sono staging locale.
- `parallel.py`, `enrichment_execution_engines.py`: thread locali per parallelismo intra-task; ok ma moltiplicano consumo HTTP/RAM.

### `backend/app/core/manipola/*`

- Trasformazioni prevalentemente in-memory pandas/geopandas: orizzontalizzabili via Celery workers, ma non condividono stato.
- `project_source_runtime.py`: catena esecuzione migrata a `ContextVar`; niente `threading.local` nel runtime Manipola.
- `plugin_executor.py`: singleton executor process-local; ok se i plugin sono stateless. Se plugin scrivono su disco, vanno auditati separatamente.
- `ManipolaExecutor` riceve ora un `group_id_resolver` iniettabile: la risoluzione tenant resta un boundary esplicito e testabile, non un lookup nascosto hardcoded nel core trasformazioni.
- `source_loading.load_project_source_artifact()` confina la dipendenza da `Path` nell'adapter pandas/geopandas. I caller lavorano con resource reference; la materializzazione finale resta in un solo boundary.
- Rischio principale: memoria per worker, non coerenza distribuita.

### `backend/app/core/estrai/*`, `backend/app/db/query_executor.py`

- Query execution stateless rispetto al backend; dati da DB esterni.
- `query_executor.py`: per CSV/Parquet streaming migliora scalabilita; per formati non streamable materializza tutto in RAM fino a `ESTRAI_MAX_ROWS`.
- La persistenza query e' dietro `QueryPersistenceProtocol`, iniettabile nel costruttore. Lo streaming CSV/Parquet e' dietro `QueryStreamingWriter`, quindi l'executor non possiede piu' direttamente il writer filesystem.
- `query_engines.py`: SQLAlchemy pool per engine; moltiplica connessioni verso sorgenti esterne quando aumentano worker.

### `backend/app/core/carica/*`

- Scrittura verso DB/REST/ArcGIS esterni. Orizzontalizzabile a livello task, ma attenzione alle destinazioni condivise.
- `writer_replace_strategies.py`, `writer_transaction_exec.py`, DDL/swap/truncate: concorrenza sullo stesso target table dipende dal DB destinazione, non dal backend. Per cluster serve lock per destination/schema/table se due task possono scrivere la stessa tabella.
- `carica_task.py`: usa Celery, chiude connessioni PG in finally; ok.

### `backend/app/core/features/*`, `backend/app/shared/connections/*`

- ArcGIS client/service sono singleton per processo ma stateless rispetto al dato canonico.
- OAuth token fetch usa lock in-process; non c'e' token cache distribuita. Se molti pod fanno refresh insieme, possibile thundering herd.
- `features/routes.py` non accetta piu' `input_path` locale: le scritture ArcGIS devono passare da `project_id`/result blob.

### `backend/app/core/projects/*`

- Dati progetto/canvas su Postgres; ok.
- `projects/db.py` contiene update canvas con `SELECT ... FOR UPDATE`, buono per concorrenza.
- `schema_inference.py` usa cache per istanza, non globale; ok.
- `canvas_models.py` ha default mutabili in Pydantic (`{}`, `[]`) da verificare, ma non e' un problema di orizzontalizzazione se Pydantic copia correttamente.

### `backend/app/core/connections/*`

- Connessioni su DB, credenziali cifrate; ok.
- Router/service singleton process-local; ok.
- Test connessioni e browse moltiplicano carico verso DB esterni/ArcGIS con le repliche.

### `backend/app/core/admin_backups/*`, `backend/scripts/backup_db.py`, `backend/deploy/systemd/*`

- Stato run backup su DB e lista backup da object storage: buono.
- `admin_backups/service.py` usa lock manuale su Postgres (`admin_backup_manual_locks`), quindi due API pod non possono avviare backup manuali concorrenti. Il trigger manuale non usa piu' `subprocess.Popen` dal pod API: se `ADMIN_BACKUP_MANUAL_TRIGGER_ENABLED=true`, accoda `workers.tasks.admin_backup_task.run_manual_backup_task` su Celery e il worker rilascia il lock a fine run.
- Follow-up deployment: in Kubernetes si puo' sostituire il worker Celery con K8s Job dedicato mantenendo lo stesso lock/idempotency boundary.
- systemd units sono single-host. In Kubernetes sostituire con CronJob/Job e non usare `systemctl/journalctl` come fonte di verita.

### `backend/app/core/admin_system_status/*`, `backend/app/observability/*`

- Molti check sono cluster-aware (DB, Redis, object storage, beat heartbeat).
- Metriche host (`psutil`, disco `DATA_DIR`) sono per-pod/nodo; la dashboard deve etichettarle come "questa replica" oppure aggregarle.
- `audit_action.py` ha metriche in-process: ok per health locale, non per audit storico. Audit vero deve restare su DB/log.

### `backend/app/infrastructure/http/*`

- `session.py`: requests Session singleton per processo con pool; ok ma tuning per pod.
- `async_client.py`: httpx AsyncClient singleton per processo; ok. Pool globale effettivo = pool_size * processi.

### `backend/app/infrastructure/security/crypto.py`

- Fernet cache process-local: ok. Requisito cluster: stessa chiave/secret su tutte le repliche.

### `backend/deploy/terraform/*`, `backend/Dockerfile`, `backend/requirements*`

- Terraform/containers sono compatibili con scale-out se runtime env e bucket sono condivisi.
- Le systemd unit e runbook restano single-node operational model.

## Stato aggiornato guardrail Go-ready (2026-05-16)

### Closed

- `app/main.py` non legge piu' direttamente `os.getenv`: delega ad `app.app_factory.create_app` e mantiene solo il path storico `app.main:app` con re-export dei runtime service.
- `app/app_factory.py` e' ora l'app composition boundary; le due letture raw env per docs/redoc sono allowlistate e documentate come boot-time boundary.
- Lo scanner `os.getenv` ora intercetta anche alias (`import os as ...`) e import diretti (`from os import getenv`), quindi nuovi raw env read fuori allowlist non possono bypassare il lock.
- La baseline `threading.local` e' zero nel backend applicativo: il DB adapter non mantiene piu' transaction state thread-local.
- `OrchestratorService` richiede executor espliciti dalla runtime composition factory: non esistono piu' default builder nel service e anche il worker CARICA passa da `build_orchestrator_stack()`.
- La baseline router -> `app.db.pg_connection` resta chiusa a zero import diretti.
- `app.runtime.services` resta la composition boundary per i servizi gia' migrati: `main.py` consuma il container e non costruisce inline i service condivisi.
- `cloud_backends.py` consuma config tramite `StorageBackendConfig` e `Settings`; il raw access per i validator fail-fast e' centralizzato in `Settings.get_raw`.
- `TaskService` non usa piu' `_task_project_cache`: il metadata dei task pending e' su Redis-only nel default runtime, coperto da contract test.
- Il contratto di esecuzione pipeline e' stato estratto in `app.core.orchestrator.execution_contract`: l'endpoint HTTP costruisce un payload self-contained e il worker lo valida/preserva con `schema_version`, `columns_metadata` e `artifact_refs`.
- Celery ha code configurabili per Data Plane, Control Plane e Scheduler, quindi i workload pesanti pandas/GDAL possono essere scalati separatamente dai task amministrativi e dal beat/scheduler.
- Il trigger backup manuale passa da Celery task: l'API pod non avvia piu' subprocess locali.
- `os.getenv` e' confinato a `app/config.py` e al boundary `app/environment.py`; i moduli bootstrap/security/auth/infra passano dal boundary comune.

### Remaining

- I residui multi-host rimasti sono operativi: cache/staging locale ricostruibile e cleanup per nodo. Non restano fallback API basati su filesystem locale.

## Priorita consigliate

P2:

- Aggiungere lifecycle object storage per IMDF/eseguibili/intermediate/result.
- Aggiungere PgBouncer o ridurre pool per pod.
- Rendere admin status esplicitamente multi-replica/cluster-aware.
