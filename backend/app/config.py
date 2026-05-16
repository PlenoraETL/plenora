"""
Configurazione PLENORA-ETL — solo DEFAULTS + variabili d'ambiente, niente config.json.

Priorità:
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# API contract version esposta nel response header X-API-Version. Bumpare
# qui al cambio breaking del contratto FE↔BE (Pydantic model shape, endpoint
# rinominati, response envelope ridisegnato). Il FE compila contro
# EXPECTED_API_VERSION in frontend/src/api/config.js e logga warning su
# mismatch — durante un rolling deploy questo evidenzia se il browser sta
# parlando con un BE su versione diversa da quella per cui il bundle e'
# stato costruito.
API_VERSION = "1.0"

# Carica backend/.env il prima possibile, prima di qualunque os.getenv del
# resto dell'app. Tutti i moduli che leggono variabili d'ambiente (incluso
# app.infrastructure.security.crypto) vedranno così le variabili definite nel .env. Senza
# questo, l'app userebbe la chiave di cifratura di default e fallirebbe a
# decifrare qualsiasi record salvato con la chiave reale
# CLASSE SETTINGS

class Settings:
    """
    Configurazione singleton per PLENORA-ETL.

    Accesso:
        from app.config import settings
        settings.CELERY_BROKER_URL
        settings.get("API_TIMEOUT", 30)
    """

    DEFAULTS = {
        "DATA_DIR": "./data",

        "LOG_DB_FILE":              "log.db",
        "LOG_DB_MAX_ROWS":          100_000,
        "LOG_DB_CONNECT_TIMEOUT":   5,
        "LOG_DB_BUSY_TIMEOUT":      3000,
        "RUNS_PAGE_SIZE":           50,
        "RUNS_LOG_LIMIT":           200,

        "CORS_ORIGINS": "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",

        "DB_CONNECT_TIMEOUT": 10,
        "DB_QUERY_TIMEOUT": 30,
        "SUPPORTED_DB_TYPES": ["postgres", "mssql", "mysql", "oracle", "sqlite"],

        "CELERY_BROKER_URL":            "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND":        "redis://localhost:6379/0",
        # Production gates: dev/test restano single-node per default.
        # Imposta uno di questi flag a true per forzare le stesse regole
        # di production/staging anche in env non-prod (cluster/multi-pod).
        "CLUSTER_MODE":                 False,
        "PLENORA_CLUSTER_MODE":         False,
        "MULTI_POD_MODE":               False,
        "PLENORA_MULTI_POD":            False,
        "CELERY_TASK_TIMEOUT":          3600,
        "CELERY_TASK_HARD_LIMIT":       3900,
        "CELERY_PREVIEW_TIMEOUT":       300,
        "CELERY_PREVIEW_HARD_LIMIT":    360,
        "CELERY_SCHEDULE_TIMEOUT":      3500,
        "CELERY_SCHEDULE_HARD_LIMIT":   3600,
        # Scheduler locks use a short TTL plus worker heartbeat. If the worker
        # dies by SIGKILL, the lock expires in
        # SCHEDULE_LOCK_TTL_SECONDS invece di restare appeso fino al
        # hard limit Celery. HEARTBEAT_INTERVAL << TTL/2 per margine
        "SCHEDULE_LOCK_TTL_SECONDS":                600,
        "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS": 180,
        # Leader election is fail-closed by default: if Redis is unavailable,
        # beat goes standby instead of
        # diventare leader by default (evita doppio enqueue in caso
        # di flap Redis con due beat attivi). Override a True per
        # deployment single-instance dove nessun altro beat gira mai
        "SCHEDULE_BEAT_LEADER_FAIL_OPEN":           False,
        "CELERY_MAX_RETRIES":           3,
        "TASK_PROGRESS_POLL_INTERVAL":  1,
        "CELERY_RESULT_EXPIRES":        86400,

        # Durata del token JWT in ore. Override env via type coercion `int`.
        "JWT_EXPIRE_HOURS": 8,
        # Login rate limit (R-01 + ADR-0021): 10
        # tentativi falliti in 60s -> lockout 300s. I test esistenti
        # `tests2/security/unit/test_login_rate_limit_per_ip.py`
        # monkeypatch-ano i moduli `LOGIN_*` symbol; preservato
        "LOGIN_MAX_ATTEMPTS": 10,
        "LOGIN_WINDOW_SECONDS": 60,
        "LOGIN_LOCKOUT_SECONDS": 300,
        # Timeout Redis per i lookup di rate limit / blacklist. Default 1s
        # single-host) — ops puo' alzarlo per Redis remoto/latente. Su
        # timeout: fallback in-memory (login RL) o fail-open (blacklist
        # read, ADR-0009).
        "LOGIN_RL_REDIS_TIMEOUT_SEC": 1,
        # Default retrocompatibile: se Redis non e' disponibile, il
        # login rate limiter usa ancora il fallback in-memory single-node.
        # In prod/cluster impostare True per fail-closed (nessun login
        # se il bucket distribuito non e' consultabile).
        "LOGIN_RL_REDIS_REQUIRED": False,
        "JWT_BLACKLIST_REDIS_TIMEOUT_SEC": 1,
        # Soglia "stale" per la blacklist JWT mostrata nella dashboard
        # admin (`admin_system_status/service.py`). Sopra questa soglia
        # l'ultima sync e' considerata stantia e segnalata come warning
        # Default 1800s (30 min).
        "JWT_BLACKLIST_STALE_THRESHOLD_SEC": 1800,

        # `MSSQL_FAST_EXECUTEMANY` accetta on|off|true|false|0|1|...; il
        # caller fa il match insensitive. Default `"on"` = abilita
        # `fast_executemany` per pyodbc/MSSQL
        "MSSQL_FAST_EXECUTEMANY": "on",
        # OPERATIONAL_TUNING
        # `ENRICHMENT_POOL_SIZE` httpx async pool size. Default 50,
        # clamp [5, 200] applicato dai consumer
        "ENRICHMENT_POOL_SIZE": 50,
        # `TICKET_ATTACHMENT_MAX_BYTES` cap upload attachments tickets
        # Default 10 MB. Sotto questa soglia il blob va in DB BYTEA
        # sopra dovrebbe andare object storage (separate path
        "TICKET_ATTACHMENT_MAX_BYTES": 10 * 1024 * 1024,
        # Soglie display dashboard admin per la dimensione dello schema
        # PG. Float in GB. Puro display, non influenza retention
        "DB_SCHEMA_WARN_GB": 1.0,
        "DB_SCHEMA_ERROR_GB": 5.0,

        "API_TIMEOUT": 30,
        "API_DEFAULT_RATE_LIMIT": 5,
        # Rate limiter distribuito opzionale per arricchisci. Disabled di
        # default per preservare il throttling locale esistente; se enabled
        # usa Redis via CELERY_BROKER_URL per quote globali per API/scope.
        # REQUIRED=True rende il limiter fail-closed invece di degradare al
        # fallback locale quando Redis non e' disponibile.
        "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED": False,
        "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED": False,
        "ARRICCHISCI_RATE_LIMIT_REDIS_TIMEOUT_SEC": 1,
        "ARRICCHISCI_RATE_LIMIT_REDIS_KEY_PREFIX": "plenora:arricchisci:rl",
        # Cache distribuita opzionale per token OAuth/API provider. Usa Redis
        # via CELERY_BROKER_URL quando disponibile; se Redis non c'e', i
        # caller degradano alla cache locale storica.
        "OAUTH_TOKEN_CACHE_REDIS_TIMEOUT_SEC": 1,
        "OAUTH_TOKEN_CACHE_LOCK_TTL_SEC": 30,
        "OAUTH_TOKEN_CACHE_WAIT_TIMEOUT_SEC": 5,

        "SUPPORTED_OUTPUT_FORMATS": [
            "csv", "json", "xlsx", "parquet",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "DEFAULT_OUTPUT_FORMAT": "csv",

        "SUPPORTED_FILE_FORMATS": [
            "csv", "xlsx", "json",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "MAX_FILE_SIZE": 100 * 1024 * 1024,  # 100 MB

        # TTL in ore per i file temp orfani sotto `DATA_DIR/temp/`
        # Beat scheduler (`workers/beat_scheduler:_cleanup_temp_orphans`
        # rimuove le directory piu' vecchie di N ore. Default 24h
        "TEMP_ORPHAN_CLEANUP_HOURS": 24,

        "STORAGE_POLICY": "hybrid",
        # Hard cap per payload persistiti come BYTEA (mode "db" + ramo DB di "hybrid"
        # Default 500 MB: limite pratico di un BYTEA PG senza toast tuning
        # Sopra cap: ResultSaveError al save (caller deve failare
        "RESULT_DB_HARD_CAP_BYTES": 500 * 1024 * 1024,
        # Soglie dispatch DB-vs-object-storage in policy=hybrid.
        # Per-dominio (intenzionalmente separate):
        # ops puo' tunare result e file_source in modo indipendente perche'
        # la distribuzione di size differisce. Default 50 MB per entrambi
        # = break-even empirico DB BYTEA vs roundtrip object storage
        "RESULT_S3_THRESHOLD_BYTES": 50 * 1024 * 1024,
        "FILE_SOURCE_S3_THRESHOLD_BYTES": 50 * 1024 * 1024,
        # MinIO provider (A1-B2, ADR-0015). Required-if CLOUD_PROVIDER=minio
        # pattern analogo a LOG_ARCHIVE_BUCKET: validazione fail-fast a boot
        # in app/shared/cloud_backends.validate_storage_config
        "MINIO_ENDPOINT_URL": None,
        "MINIO_ACCESS_KEY": None,
        "MINIO_SECRET_KEY": None,

        "LOG_ARCHIVE_ENABLED": False,
        # Prefisso deterministico delle object key archive. Default senza
        # auto-derivazione <env>: ops override esplicito in .env per separare
        # prod/staging/dev (ADR-0014 §A2). Formato: "" oppure endswith "/"
        "LOG_ARCHIVE_KEY_PREFIX": "logs-archive/",
        # Righe per SELECT->upload->DELETE del writer. Cap pratico RAM per batch.
        "LOG_ARCHIVE_BATCH_ROWS": 10_000,
        "AUDIT_RETENTION_DAYS": 365,
        # NB: LOG_ARCHIVE_BUCKET e' intenzionalmente fuori da DEFAULTS —
        # e' required-if-enabled con validazione fail-fast a boot
        # ADR-0014 §A3: distinto da BACKUP_BUCKET/BACKUP_S3_BUCKET

        "SUPPORTED_PROJECT_TYPES": ["estrai", "manipola", "arricchisci", "salva"],
        "DEFAULT_GROUP": "default",

        "MAX_PROJECT_NAME_LENGTH": 100,
        "MAX_CONNECTION_NAME_LENGTH": 100,
        "MAX_PREVIEW_ROWS": 1000,
        # in byte per il file caricato sincrono via
        # `load_dataframe_from_file`. Sopra il cap il guard solleva
        # `MemoryLimitExceededError` mappata a 413 envelope dual-shape
        # `memory_limit_exceeded`. Default conservativo 200 MB: bilancia
        # uso routine (CSV/JSON < 50 MB) e protezione worker contro file
        # multi-GB che rieducherebbero il process al SIGKILL del kernel
        # Override via env `LOAD_FILE_MAX_BYTES` per ambienti con worker
        # piu' grossi
        "LOAD_FILE_MAX_BYTES": 200 * 1024 * 1024,
        # processo worker prima di stage pesanti (pipeline executor
        # Default 1.5 GB. Se il RSS corrente supera il cap, il guard
        # solleva MemoryLimitExceededError mappata a 503 (worker overloaded
        # Cap <= 0 disabilita il guard (escape hatch
        # Override via env `WORKER_RSS_MAX_BYTES`
        "WORKER_RSS_MAX_BYTES": 1_500_000_000,
        # massimo per il numero di righe di output di un cross-join
        # `op_cross_join` in `app.core.manipola.transforms.joins`
        # Il preflight stima `len(left) * len(right)` PRIMA di
        # `pd.merge(how="cross")`. Sopra il cap solleva
        # `MemoryLimitExceededError` (stage `manipola_cross_join`
        # mappata a 413 envelope dual-shape `memory_limit_exceeded`
        # Payload Too Large: input troppo grandi per il cross). Default
        # 50_000_000 righe: copre la routine multi-tabella tipica
        # 1k x 10k = 10M, 10k x 1k = 10M) ma blocca esplosioni 100M+
        # che certamente fanno SIGKILL del worker. Cap <= 0 disabilita
        # il guard (escape hatch testing/admin
        # Override via env `CROSS_JOIN_MAX_OUTPUT_ROWS`
        "CROSS_JOIN_MAX_OUTPUT_ROWS": 50_000_000,
        # cap massimo righe stimate per step dal multi-join sequenziale
        # `_esegui_multi_join` in `app.core.manipola.executor`). Il
        # guard stima la cardinalita' output del prossimo `pd.merge`
        # dalle frequenze delle join key PRIMA di materializzare il
        # DataFrame. Se la stima supera il cap solleva
        # `MemoryLimitExceededError` (stage `manipola_multi_join`
        # Reuse contratto Memory/OOM (status 413, codice
        # `memory_limit_exceeded`
        # Default 50_000_000 righe (stesso budget del cross-join: stessa
        # semantica "row count che il worker puo' tenere in RAM"). Cap
        # <= 0 disabilita il guard
        # La stima usa value-counts sulle join key: costo O(N+M), ma
        # molto piu' sicuro del worst-case cartesian
        # `len(result) * len(next)`) e meno rischioso di lasciare che
        # il primo merge esploda in memoria
        # Override via env `MULTI_JOIN_MAX_OUTPUT_ROWS`
        "MULTI_JOIN_MAX_OUTPUT_ROWS": 50_000_000,
        # massimo (byte) per un singolo blob letto via Resource Engine
        # `ResultBlobLoader` mode='bytes'/'path' + future kinds
        # file_source_blob, intermediate_artifact, eseguibili_output
        # Default 200 MB : allineato a
        # `LOAD_FILE_MAX_BYTES` (200 MB) -- un singolo blob letto sotto
        # cap non puo' eccedere quello che il path di upload sincrono
        # gia' tollera. Sotto `WORKER_RSS_MAX_BYTES` (1.5 GB) di un
        # ordine di grandezza, quindi N download concorrenti restano
        # nel budget RSS del worker anche su host piccoli. Cap <= 0
        # disabilita il guard. Override via env `RESOURCE_MAX_BYTES`.
        # La wiring consumer-side (cap
        # pre-return in `get_result_data` / `_dispatch_by_id`) e'
        # deferita: il loader stesso non importa `app.config` per
        # anti-god-engine; il guard vivra' a livello caller (es
        # repository risultati o `cloud_backends`).
        "RESOURCE_MAX_BYTES": 200 * 1024 * 1024,
        # cap dedicato
        # per la preview HTTP di intermediate artifacts (Parquet) e
        # per qualunque preview-side path che oggi materializza bytes
        # in `BytesIO` prima di passarli a pandas/pyarrow
        # Separato da `RESOURCE_MAX_BYTES` (200 MB post MEMORY-005
        # perche' la preview vive sul thread-pool della API e ha un
        # budget RAM drasticamente piu' stretto di un download/job
        # worker
        # una richiesta preview puo' essere concorrente (N richieste
        # da N tab/utenti contemporanei). Anche con cap 200 MB un
        # solo refresh di un dataset grande consumerebbe ~1/8 del
        # `WORKER_RSS_MAX_BYTES` (1.5 GB) dell'API process; con N=4
        # tab si saturerebbe il budget
        # la preview restituisce solo `df.head(limit)` (default 100
        # righe): leggere centinaia di MB di Parquet per renderne 100
        # righe e' uno spreco strutturale. La cap reale dovra'
        # diventare row-group streaming (residual: `pyarrow.fs`
        # Default 25 MB: bilancia
        # dataset reali small-medium (CSV / Parquet < 25 MB e' la
        # stragrande maggioranza dei file di test users uploaded
        # margine sopra `LOAD_FILE_MAX_BYTES` per artifact-side
        # compressione Parquet (Parquet 25 MB ~ CSV 100-200 MB
        # sotto la soglia che farebbe un singolo refresh API
        # pesare > 1/50 del budget RSS del processo
        # Cap <= 0 disabilita il guard (la preview ricade sul cap
        # generico `RESOURCE_MAX_BYTES`). Override via env
        # `ARTIFACT_PREVIEW_MAX_BYTES`. Wiring: il reader
        # `app/core/intermediate/artifact_reader.py`) legge questa
        # entry via `os.environ` (mirror dello stesso pattern
        # anti-god-engine usato da `result_blob.py`). Streaming
        # Parquet pyarrow.fs e' lavoro futuro residual
        "ARTIFACT_PREVIEW_MAX_BYTES": 25 * 1024 * 1024,

        # Tunables del reconciler beat hook che processa le entry
        # pending in `plenora.storage_reconciliation_log`. Tutti
        # operational tuning (ADR-0021), override env consentito
        # STORAGE_RECONCILE_INTERVAL_SECONDS: cadence gate del hook
        # nel `PlenoraBeatScheduler.tick`. Default 900s (15 min
        # tradeoff tra latenza orphan-recovery e load sul DB /
        # object storage). Cap <= 0 -> hook disabilitato (escape
        # hatch ops
        # STORAGE_RECONCILE_BATCH_SIZE: max righe pending lette per
        # ciascun tick. Default 50: bound al numero di
        # `compensating_delete` per tick
        # STORAGE_RECONCILE_MAX_ATTEMPTS: oltre questo numero di
        # tentativi falliti l'entry transitiona a `lost` (review
        # ops). Default 5
        # STORAGE_RECONCILE_BACKOFF_BASE_SECONDS: base in secondi
        # per il backoff esponenziale
        # `next_retry_at = now + base * 2**(attempts-1)`, exponent
        # capped a 8 (~4.3h max con base 60s). Default 60s
        "STORAGE_RECONCILE_INTERVAL_SECONDS": 900,
        "STORAGE_RECONCILE_BATCH_SIZE": 50,
        "STORAGE_RECONCILE_MAX_ATTEMPTS": 5,
        "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS": 60,
        # Operational tuning (ADR-0021 OPERATIONAL_TUNING) per evitare
        # che `plenora.storage_reconciliation_log` diventi una discarica
        # silenziosa di terminali. Tutto override env consentito
        # STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS: giorni di retention
        # per le righe terminali `done` / `orphan_deleted` (audit
        # trail). Sopra il cutoff il beat hook ne rimuove fino a
        # `STORAGE_RECONCILE_PRUNE_BATCH_SIZE` per tick. Default 30
        # giorni: bilancia visibilita' forensic vs storage cost
        # Cap <= 0 -> prune disabilitato (la tabella cresce; ops
        # accetta esplicitamente
        # STORAGE_RECONCILE_PRUNE_BATCH_SIZE: cap righe potate per
        # tick. Default 500: bound deliberatamente alto rispetto al
        # normale write-rate per assorbire backlog accumulato senza
        # richiedere intervention. Cap <= 0 -> prune disabilitato
        "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS": 30,
        "STORAGE_RECONCILE_PRUNE_BATCH_SIZE": 500,
        # Tunables del reconciler beat hook per il branch
        # `operation='upload_intent'`.
        # STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS: finestra
        # tra `created_at` e ora corrente prima che un upload_intent
        # sia considerato stantio. Deve essere >= max realistic
        # upload+commit time. Default 600s (10 min): generoso ma
        # bounded. Cap <= 0 disabilita il branch (escape hatch ops
        # il reconciler delete-side resta attivo
        # STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE: max righe
        # `upload_intent` pending lette per tick. Default 50
        # stesso ordine del delete branch. Cap <= 0 disabilita
        "STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS": 600,
        "STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE": 50,
        # Threshold per il readiness check `reconciliation_backlog`
        # esposto in `app.observability.health.gather_readiness`
        # Sopra il threshold il check ritorna `(False, ...)` e /ready
        # va a 503; sotto resta `(True, "<count> entries")`. Cap <= 0
        # disabilita il singolo branch (escape hatch ops). Liveness
        # NON e' impattata
        # PENDING_503: numero massimo di righe `state='pending'`
        # tollerate. Default 1000 (con beat ogni 15min e batch
        # 50 → ~200/h smaltite; 1000 = ~5h di accumulo). Alza per
        # ambienti high-throughput
        # LOST_503: numero massimo di righe `state='lost'` (max
        # attempts exceeded → ops review necessaria). Default 10
        # ogni `lost` indica un orphan blob non rimosso, dovrebbe
        # essere raro
        "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD": 1000,
        "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD": 10,
        # Threshold dedicato al backlog `pending` di
        # `operation='upload_intent'` (vedi
        # `count_pending_by_operation`). Distingue il W2 race
        # backlog dal W1 race delete-backlog tracciato dal
        # PENDING_503_THRESHOLD globale
        # Default 0 = OPT-IN: il branch e' disabilitato e il
        # backlog upload_intent contribuisce solo al detail string
        # del check (no 503 dedicato). Operators possono attivare
        # il branch impostando un cap > 0 (es. 200) quando
        # vogliono un alert specifico W2
        # Cap <= 0 disabilita il branch (escape hatch ops
        "STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD": 0,
        # Massima eta' (secondi) tollerata su `plenora.beat_heartbeat.ts`
        # prima che il check `beat_heartbeat` di `gather_readiness`
        # vada a 503. Lo heartbeat e' scritto da
        # `PlenoraBeatScheduler._write_heartbeat` ad ogni tick
        # max_interval=60s default). Threshold tipico: 2-3x il tick
        # interval per assorbire jitter / failover leader
        # Default 0 = OPT-IN disabled: dev/test/CLI environment senza
        # beat scheduler attivo NON triggerano 503. Operators di
        # production setano un valore > 0 (es. 180-300s) per alertare
        # quando il reconciler beat non e' running. Cap <= 0 disabilita
        # il branch (escape hatch ops
        "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS": 0,

        # Timeout (secondi) per la PING Redis dentro check_redis
        # Applicato sia a `socket_connect_timeout` (TCP handshake
        # che a `socket_timeout` (operazione PING dopo handshake
        # Senza socket_timeout, una Redis che accetta la connessione
        # ma stalla nel rispondere bloccherebbe /health e /ready oltre
        # il connect_timeout. Default 2s mantiene il comportamento
        # storico; operatori possono ridurlo (es. 1s) in deploy
        # k8s con SLO stretti, o alzarlo (es. 5s) in reti rumorose
        # Cap <= 0 cade al fallback hardcoded di 2s (non disabilitabile
        # un /health senza timeout sarebbe un footgun ops
        "READINESS_REDIS_TIMEOUT_SECONDS": 2,

        # Timeout (secondi) per `SELECT 1` dentro check_postgres
        # Applicato a livello di statement via `SET LOCAL
        # statement_timeout = Nms` sulla transaction del probe
        # Senza questo bound una PG che accetta la connessione TCP
        # ma stalla nell'esecuzione (lock, backend esauriti) bloccherebbe
        # /health e /ready oltre il TCP keepalive del kernel
        # Lo `SET LOCAL` non altera lo statement_timeout di altre query
        # del pool: il bound resta confinato al readiness probe
        # Cap <= 0 cade al fallback hardcoded di 2s (stesso footgun
        # rationale del Redis timeout
        "READINESS_PG_TIMEOUT_SECONDS": 2,

        # Timeout (secondi) per la probe `readiness_probe` dentro
        # check_object_storage. Applicato sia a `connect_timeout`
        # TCP handshake) che a `read_timeout` (operazione list_objects
        # del client boto3 one-shot creato dal probe; il client usa
        # anche `retries.max_attempts=1` per evitare il retry
        # exponenziale (3 tentativi) che sommerebbe ~360s worst case
        # su un endpoint hung. Default 3s = leggermente sopra il PG/Redis
        # 2s per assorbire la latenza S3 regionale
        # Cap <= 0 cade al fallback hardcoded di 3s
        "READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS": 3,

        "ENRICHMENT_BATCH_SIZE": 100,
        "ENRICHMENT_MAX_WORKERS": 5,
        "ENRICHMENT_CACHE_TTL": 3600,

        # Soglia righe oltre la quale TRUNCATE_INSERT + ALL_OR_NOTHING usa
        # stage+swap invece di DELETE+INSERT atomico. Sotto soglia: atomic
        # zero overhead, no privilegi DDL). Sopra soglia: stage+swap
        # safe su dataset enormi, richiede CREATE TABLE; se il grant
        # manca il writer fa fallback automatico ad atomic con WARNING
        "CARICA_STAGE_SWAP_THRESHOLD": 500_000,

        # Hard cap righe ritornate da una query ESTRAI. Se un
        # chunk_fetchmany porta il totale oltre questo limite, la
        # query fallisce loud con suggerimento (aggiungi WHERE, usa
        # LIMIT, oppure alza il cap). Protezione anti-OOM: fino a
        # Default 5M: ragionevole per
        # ETL tipico PLENORA-ETL (centinaia-migliaia di righe), lascia
        # margine per operazioni admin occasionali da 1-5M
        "ESTRAI_MAX_ROWS": 5_000_000,

        # Cap righe serializzate in intermediate.runs snapshot.
        # Sopra questa soglia il writer scrive solo metadata (no
        # df.to_dict rows) per evitare double-materialization della
        # query output nell'intermediate DB. Default 100k: l'intermediate
        # serve per debug UI, non per re-esecuzione integrale
        "INTERMEDIATE_SNAPSHOT_MAX_ROWS": 100_000,

        # product-grade . Nuova policy
        # globale che separa "persistenza righe" (UI/debug) da "change
        # detection" (hash). Tre mode implementati in
        # "metadata_only": runs + node_meta + node_hash, zero
        # node_rows. Massima velocita'. Diff L2 non disponibile
        # "preview_only": metadata + hash + prime
        # INTERMEDIATE_PREVIEW_ROWS righe in node_rows. Default
        # consigliato in produzione
        # "full_pg": tutte le righe in node_rows per compat/debug.
        # Mode "full_artifact": persiste data.parquet + manifest.json
        # su runtime object storage (richiede OBJECT_STORAGE_ENABLED=true
        # + OBJECT_STORAGE_BUCKET configurato). Zero `node_rows`
        # `node_meta.artifact_uri/_status/_format` valorizzati
        # Validazione fail-fast in
        # `app/core/intermediate/policy.validate_intermediate_settings`
        # Default produzione: metadata_only (storage
        # intermedi, 2026-04-28). Postgres resta catalogo leggero
        # Per audit/debug: preview_only o full_artifact
        "INTERMEDIATE_PERSISTENCE_MODE": "metadata_only",
        # Numero righe persistite quando MODE=preview_only. Tagliato
        # dalle prime N (df.head). UI mostra "preview NN su YYY"
        "INTERMEDIATE_PREVIEW_ROWS": 500,
        # Formato artifact full quando MODE=full_artifact. Dichiarativo
        # `artifact_writer` oggi serializza Parquet hardcoded e popola
        # `node_meta.artifact_format` con questo valore). Future-proof
        # per supporto multi-formato senza schema change
        "INTERMEDIATE_FULL_ARTIFACT_FORMAT": "parquet",

        # TODO: tune node_hash calculation mode if exact run diffs become required.
        # del DataFrame al checkpoint/final snapshot
        # "sample": hash di (schema, len(df), df.head(N)). Costo
        # ~10-20 ms anche su DataFrame da 275k righe (vs 1.0-1.5 s
        # del full-DF su DF grandi). Trade-off: cambi che toccano
        # SOLO righe oltre la N-esima possono non essere rilevati
        # dal change-detection L1; accettabile per audit alto
        # livello (default operativo
        # "full": hash di tutto il DataFrame via pd.util.hash_
        # pandas_object. Lento sui DF grandi ma esatto. Per audit
        # forte su run specifici si puo' temporaneamente alzare
        # la mode a "full" via env senza redeploy
        # node_hash NON e' usato da alcun branch di correttezza pipeline
        # transform/join/save non lo leggono); serve solo a UI run/diff
        # e a future feature di change-detection. Default = sample
        "INTERMEDIATE_FINGERPRINT_MODE": "sample",
        # Cap righe usate dal mode sample. >0. Default 1000: copre la
        # head dei dataset reali e mantiene il costo nell'ordine dei ms
        # Rendere troppo alto vanifica il vantaggio del sample
        "INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS": 1000,

        "MANIPOLA_INTERMEDIATE_CHECKPOINTS": "critical",
        "ESTRAI_INTERMEDIATE_CHECKPOINTS": "final_only",
        "ARRICCHISCI_INTERMEDIATE_CHECKPOINTS": "final_only",

        # Backup amministrativi: cron schedule + descrizione UI-readable
        # Classificate APP_POLICY/OPERATIONAL_TUNING in ADR-0021 §"Config
        # boundary": stringhe consumate da `get_schedule_info` per
        # l'endpoint admin di metadata. La schedule reale e' gestita a
        # livello deployment (systemd timer / Celery beat / K8s CronJob
        # questi default sono i valori esposti al FE quando l'env e'
        # unset, e devono essere overridabili via env per allineare il
        # display alla schedule effettivamente configurata sul deployment
        # NOTA: `BACKUP_BUCKET`, `BACKUP_S3_BUCKET`, `AWS_REGION` restano
        # DEPLOY_INFRA env-only, ma i consumer passano da Settings/storage
        # boundary (`cloud_backends._resolve_backup_bucket`,
        # `_build_object_storage`, admin backup service).
        "BACKUP_SCHEDULE_CRON": "0 2 * * *",
        "BACKUP_SCHEDULE_DESCRIPTION": "Daily 02:00 UTC (+ jitter fino a 5 min)",
        # Trigger manuale legacy eseguito localmente dal pod API tramite
        # subprocess. Comodo in dev/single-host, ma da disabilitare in
        # production/staging/cluster: il backup deve partire da scheduler
        # deployment-level, Celery worker dedicato o K8s Job.
        "ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED": True,

        # Bucket dedicato runtime artifact intermedi (full_artifact
        # Separato da BACKUP_BUCKET (IAM boundary distinto) e dal
        # terraform-state. Required quando MODE=full_artifact +
        # OBJECT_STORAGE_ENABLED=true. Used as fallback behind OBJECT_STORAGE_BUCKET in
        # `cloud_backends._resolve_runtime_bucket`. None ammesso se
        # OBJECT_STORAGE_BUCKET e' settato
        "PLENORA_ARTIFACT_BUCKET": None,
        # Prefix logico dentro il bucket. Layout completo
        # <prefix>/group=.../project=.../run=.../module=.../node=
        # Default "intermediate". Trim slash a destra
        "INTERMEDIATE_ARTIFACT_PREFIX": "intermediate",

        # Capability globale: se False, NESSUN modulo runtime (result/
        # file_source/eseguibili/IMDF/intermediate) deve tentare upload o
        # download via object storage. La factory `get_runtime_object_storage`
        # solleva `ObjectStorageDisabledError`; i caller che hanno fallback
        # locale (IMDF list_bundles cache locale) degradano graceful, gli
        # altri sollevano errori operativi chiari
        # Backup amministrativo (`get_backup_object_storage`) e log archive
        # `get_log_archive_storage`) seguono la stessa capability: se
        # False, admin_backups list ritorna feature-disabled invece di
        # importare boto3 e fare list_objects_v2 diretta
        # Default True per non rompere deployment esistenti che usano
        # CLOUD_PROVIDER=aws + BACKUP_BUCKET. Per disabilitare: setta
        # OBJECT_STORAGE_ENABLED=false in env
        "OBJECT_STORAGE_ENABLED": True,
        # Backend logico. "s3"=AWS, "minio"=S3-compatible, "local"=futuro
        # filesystem-backed (dev), "webdav"=futuro, "none"=esplicitamente
        # disattivato (equivalente a OBJECT_STORAGE_ENABLED=False
        # Default None = derivato da CLOUD_PROVIDER (aws->s3, minio->minio
        # none->none
        "OBJECT_STORAGE_BACKEND": None,
        # Bucket runtime per result/file_source/eseguibili/IMDF. Distinto
        # da BACKUP_BUCKET per design (lifecycle, IAM boundary diverso
        # Se None, fallback su PLENORA_ARTIFACT_BUCKET; se anche quello
        # None, fallback con warning su BACKUP_BUCKET per installazioni
        # non ancora separate. Configurazione canonica: OBJECT_STORAGE_BUCKET
        # per runtime, BACKUP_BUCKET per backup, LOG_ARCHIVE_BUCKET per log.
        "OBJECT_STORAGE_BUCKET": None,
    }

    def __init__(self):
        pass  # Niente da caricare — tutto da DEFAULTS + env


    def get(self, key: str, default: Any = None) -> Any:
        """
        Ottieni un valore di configurazione.

        Priorità:
            1. Variabile d'ambiente
            2. DEFAULTS della classe
            3. default parametro
        """
        env_value = os.getenv(key)
        if env_value is not None:
            return self._converti_tipo_env(key, env_value)
        return self.DEFAULTS.get(key, default)

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Return the raw environment value without DEFAULTS/type coercion.

        Boot validators use this when they must distinguish unset from
        malformed values while keeping raw env access inside Settings.
        """
        return os.getenv(key, default)

    def _converti_tipo_env(self, key: str, env_value: str) -> Any:
        default_value = self.DEFAULTS.get(key)
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes")
        elif isinstance(default_value, int):
            try:
                return int(env_value)
            except ValueError:
                return default_value
        elif isinstance(default_value, float):
            try:
                return float(env_value)
            except ValueError:
                return default_value
        return env_value

    def set(self, key: str, value: Any, auto_save: bool = True):
        """Set in-memory (runtime only)."""
        _ = auto_save  # retained for compatibility with the historical setter signature
        os.environ[key] = str(value)

    def _directory(self, sottopercorso: str, base: Path = None, create: bool = True) -> Path:
        if base is None:
            base = Path(self.get("DATA_DIR"))
        percorso = base / sottopercorso
        if create:
            percorso.mkdir(parents=True, exist_ok=True)
        return percorso

    def _int_setting(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def _str_setting(self, key: str, default: str) -> str:
        value = self.get(key, default)
        return default if value is None else str(value)

    @property
    def CORS_ORIGINS_LIST(self) -> list:
        """Restituisce la lista delle origini CORS da stringa CSV."""
        raw = self.get("CORS_ORIGINS", "")
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def LOG_DB_FILE(self) -> str:
        return self._str_setting("LOG_DB_FILE", "log.db")

    @property
    def LOG_DB_MAX_ROWS(self) -> int:
        return self._int_setting("LOG_DB_MAX_ROWS", 100_000)

    @property
    def RUNS_PAGE_SIZE(self) -> int:
        return self._int_setting("RUNS_PAGE_SIZE", 50)

    @property
    def RUNS_LOG_LIMIT(self) -> int:
        return self._int_setting("RUNS_LOG_LIMIT", 200)

    @property
    def DB_QUERY_TIMEOUT(self) -> int:
        return self._int_setting("DB_QUERY_TIMEOUT", 30)

    @property
    def API_TIMEOUT(self) -> int:
        return self._int_setting("API_TIMEOUT", 30)

    @property
    def CELERY_BROKER_URL(self) -> str:
        return self._str_setting("CELERY_BROKER_URL", "redis://localhost:6379/0")

    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return self._str_setting("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

    @property
    def CELERY_TASK_TIMEOUT(self) -> int:
        return self._int_setting("CELERY_TASK_TIMEOUT", 3600)

    @property
    def CELERY_TASK_HARD_LIMIT(self) -> int:
        return self._int_setting("CELERY_TASK_HARD_LIMIT", 3900)

    @property
    def CELERY_PREVIEW_TIMEOUT(self) -> int:
        return self._int_setting("CELERY_PREVIEW_TIMEOUT", 300)

    @property
    def CELERY_PREVIEW_HARD_LIMIT(self) -> int:
        return self._int_setting("CELERY_PREVIEW_HARD_LIMIT", 360)

    @property
    def CELERY_SCHEDULE_TIMEOUT(self) -> int:
        return self._int_setting("CELERY_SCHEDULE_TIMEOUT", 3500)

    @property
    def CELERY_RESULT_EXPIRES(self) -> int:
        return self._int_setting("CELERY_RESULT_EXPIRES", 86400)

    @property
    def CELERY_MAX_RETRIES(self) -> int:
        return self._int_setting("CELERY_MAX_RETRIES", 3)

    @property
    def MAX_PREVIEW_ROWS(self) -> int:
        return self._int_setting("MAX_PREVIEW_ROWS", 1000)

    @property
    def CELERY_SCHEDULE_HARD_LIMIT(self) -> int:
        return self._int_setting("CELERY_SCHEDULE_HARD_LIMIT", 3600)

    @property
    def SCHEDULE_LOCK_TTL_SECONDS(self) -> int:
        return self._int_setting("SCHEDULE_LOCK_TTL_SECONDS", 600)

    @property
    def SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS(self) -> int:
        return self._int_setting("SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS", 180)

    @property
    def API_DEFAULT_RATE_LIMIT(self) -> int:
        return self._int_setting("API_DEFAULT_RATE_LIMIT", 5)

    @property
    def DEFAULT_GROUP(self) -> str:
        return self._str_setting("DEFAULT_GROUP", "default")

    @property
    def MAX_FILE_SIZE(self) -> int:
        return self._int_setting("MAX_FILE_SIZE", 100 * 1024 * 1024)

    @property
    def LOAD_FILE_MAX_BYTES(self) -> int:
        return self._int_setting("LOAD_FILE_MAX_BYTES", 200 * 1024 * 1024)

    @property
    def CARICA_STAGE_SWAP_THRESHOLD(self) -> int:
        return self._int_setting("CARICA_STAGE_SWAP_THRESHOLD", 500_000)

    @property
    def ESTRAI_MAX_ROWS(self) -> int:
        return self._int_setting("ESTRAI_MAX_ROWS", 5_000_000)

    @property
    def DATA_DIR(self) -> Path:
        return Path(self.get("DATA_DIR"))

    @property
    def TEMP_DIR(self) -> Path:
        return self._directory("temp")

    @property
    def CONNECTIONS_DIR(self) -> Path:
        return self._directory("connections", create=False)

    @property
    def RESULTS_DIR(self) -> Path:
        return self._directory("results", create=False)

    @property
    def ESEGUIBILI_OUTPUTS_DIR(self) -> Path:
        return self._directory("eseguibili_outputs", create=False)

    @property
    def IMDF_BUNDLES_DIR(self) -> Path:
        """Cartella dove vengono persistiti i bundle IMDF estratti dopo
        l'arricchimento (GDB + renderers + geojson). I bundle rimangono
        disponibili per la pubblicazione ArcGIS on-demand (nodo imdf_publisher)
        finche' non vengono puliti dal beat scheduler (TTL da IMDF_BUNDLE_TTL_HOURS)."""
        return self._directory("imdf_bundles", create=False)

    @property
    def IMDF_BUNDLE_TTL_HOURS(self) -> int:
        """Ore di retention per i bundle IMDF su disco. Default 48h:
        copre un weekend lungo e lascia tempo per pubblicare manualmente
        senza che il bundle venga rimosso sotto i piedi dell'utente."""
        try:
            return int(self.get("IMDF_BUNDLE_TTL_HOURS", 48))
        except (TypeError, ValueError):
            return 48

    # Livello minimo dei log che finiscono nella console (stdout/journald
    @property
    def LOG_LEVEL_CONSOLE(self) -> str:
        return (self.get("LOG_LEVEL_CONSOLE", "INFO") or "INFO").upper()

    # Livello minimo dei log persistiti nel DB (tabella plenora.logs
    # Default WARNING: in prod le righe INFO sono rumore e gonfiano la tabella
    # senza fornire valore diagnostico proporzionale al costo
    @property
    def LOG_LEVEL_DB(self) -> str:
        return (self.get("LOG_LEVEL_DB", "WARNING") or "WARNING").upper()

    # Giorni di retention per plenora.logs. Record piu' vecchi vengono eliminati
    # dal beat scheduler (PlenoraBeatScheduler._cleanup_old_logs
    @property
    def LOG_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("LOG_RETENTION_DAYS", 30))
        except (TypeError, ValueError):
            return 30

    # Giorni di retention per plenora.schedule_logs (storia esecuzioni
    # scheduler). Default 90gg — finestra utile per debug/audit di catene
    # ricorrenti, oltre la quale il dettaglio dei singoli step non serve
    # Eliminati da PlenoraBeatScheduler._cleanup_old_schedule_logs
    # Mettere 0 per disabilitare la retention
    @property
    def SCHEDULE_LOGS_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("SCHEDULE_LOGS_RETENTION_DAYS", 90))
        except (TypeError, ValueError):
            return 90

    @property
    def LOG_ARCHIVE_ENABLED(self) -> bool:
        return bool(self.get("LOG_ARCHIVE_ENABLED", False))

    # Bucket object storage dedicato per archive log. Separato da
    # BACKUP_BUCKET: IAM boundary distinto (ADR-0014 §A3). Unset di
    # default; required-if-enabled con validazione fail-fast a boot in
    # app/shared/cloud_backends.validate_storage_config
    @property
    def LOG_ARCHIVE_BUCKET(self) -> Optional[str]:
        return self.get("LOG_ARCHIVE_BUCKET")

    # Prefisso deterministico delle object key archive. Ops override in
    # env per separare prod/staging/dev (ADR-0014 §A2). Formato: vuoto
    # oppure terminante con "/" (invariante consumato da
    # app/core/scheduler/log_archive._build_object_key
    @property
    def LOG_ARCHIVE_KEY_PREFIX(self) -> str:
        return self.get("LOG_ARCHIVE_KEY_PREFIX", "logs-archive/") or "logs-archive/"

    # Righe per SELECT->upload->DELETE del LogArchiveWriter. Cap RAM pratico.
    @property
    def LOG_ARCHIVE_BATCH_ROWS(self) -> int:
        try:
            return int(self.get("LOG_ARCHIVE_BATCH_ROWS", 10_000))
        except (TypeError, ValueError):
            return 10_000

    @property
    def AUDIT_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("AUDIT_RETENTION_DAYS", 365))
        except (TypeError, ValueError):
            return 365

    @property
    def UPLOADS_DIR(self) -> Path:
        return self._directory("uploads", create=False)

    @property
    def FILES_DIR(self) -> Path:
        """Cache dir per file_source blob materializzati.

        I file_source sono salvati nel DB (source of truth). Questa dir
        e' solo una cache filesystem popolata on-demand da
        `api_connector._resolve_file_source_id_path` quando un workflow
        deve dare a fiona/GDAL un path concreto. I file qui possono
        essere eliminati in qualsiasi momento — vengono ri-estratti dal
        DB al prossimo accesso.
        """
        return self._directory("files", create=False)

    @property
    def CATALOG_DIR(self) -> Path:
        return self._directory("catalog", create=False)

    @property
    def LOGS_DIR(self) -> Path:
        return self.DATA_DIR  # data/log.db direttamente in data/

    @property
    def TASKS_DIR(self) -> Path:
        return self._directory("tasks", create=False)

    @property
    def SCHEDULES_DIR(self) -> Path:
        return self._directory("schedules", create=False)

    @property
    def ARCGIS_CONNECTIONS_DIR(self) -> Path:
        return self._directory("arcgis_connections", create=False)

    def get_file_source_dir(self, file_id: str) -> Path:
        return self._directory(file_id, base=self.FILES_DIR)

    def print_config(self):
        from app.config_display import print_config

        print_config(self)


# Singleton
settings = Settings()

if __name__ == "__main__":
    settings.print_config()
