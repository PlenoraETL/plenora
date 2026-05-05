"""
Configurazione PLENORA-ETL — solo DEFAULTS + variabili d'ambiente, niente config.json.

Priorità:
    1. Variabile d'ambiente (es. export CELERY_BROKER_URL=...)
    2. File backend/.env (caricato all'import di questo modulo — SOLO in development)
    3. DEFAULTS della classe

In production/staging il file backend/.env viene ignorato: i secret devono
arrivare da variabili d'ambiente iniettate da systemd o da un secret manager.
Se il file è presente in prod/staging viene loggato un warning.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Env names che disabilitano il caricamento di backend/.env dal disco.
# In questi ambienti i secret devono arrivare da env var di sistema o
# da un secret manager, mai da un file sul filesystem che potrebbe
# essere sopravvissuto a un rollback, finire in un backup, o essere
# stato copiato per errore dal deploy.
_DOTENV_DISABLED_ENVS = ("production", "prod", "staging", "stage")


def _should_load_dotenv() -> bool:
    """True se l'ambiente corrente consente la lettura di backend/.env dal disco.

    L'ambiente è letto da `ENVIRONMENT` (fallback `ENV`, default `development`).
    Ambienti in `_DOTENV_DISABLED_ENVS` restituiscono False.
    """
    env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    return env_name not in _DOTENV_DISABLED_ENVS


# Carica backend/.env il prima possibile, prima di qualunque os.getenv del
# resto dell'app. Tutti i moduli che leggono variabili d'ambiente (incluso
# app.shared.crypto) vedranno così le variabili definite nel .env. Senza
# questo, l'app userebbe la chiave di cifratura di default e fallirebbe a
# decifrare qualsiasi record salvato con la chiave reale.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        if _should_load_dotenv():
            load_dotenv(_env_path)
        else:
            logger.warning(
                "backend/.env presente ma ENVIRONMENT indica prod/staging: "
                "file ignorato. I secret devono arrivare da env var di sistema."
            )
except ImportError:
    # python-dotenv non installato — l'app continua a funzionare con le sole
    # variabili d'ambiente esportate dal sistema.
    pass

# =============================================================================
# CLASSE SETTINGS
# =============================================================================

class Settings:
    """
    Configurazione singleton per PLENORA-ETL.

    Accesso:
        from app.config import settings
        settings.CELERY_BROKER_URL
        settings.get("API_TIMEOUT", 30)
    """

    DEFAULTS = {
        # ==================== DIRECTORY ====================
        "DATA_DIR": "./data",

        # ==================== LOG DB ====================
        # Legacy post-PG (O-ARC 2026-04-21): keys stale. LOG_DB_FILE/MAX_ROWS
        # lette da utils/logger.py:250-251 ma non consumate (SQLiteLogHandler
        # dead; sink canonico = PostgresLogHandler su plenora.logs). CONNECT/
        # BUSY_TIMEOUT orfani. Mantenute per .env retro-compat + Settings init.
        "LOG_DB_FILE":              "log.db",
        "LOG_DB_MAX_ROWS":          100_000,
        "LOG_DB_CONNECT_TIMEOUT":   5,
        "LOG_DB_BUSY_TIMEOUT":      3000,
        "RUNS_PAGE_SIZE":           50,
        "RUNS_LOG_LIMIT":           200,

        # ==================== CORS ====================
        "CORS_ORIGINS": "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",

        # ==================== DATABASE ====================
        "DB_CONNECT_TIMEOUT": 10,
        "DB_QUERY_TIMEOUT": 30,
        "SUPPORTED_DB_TYPES": ["postgres", "mssql", "mysql", "oracle", "sqlite"],

        # ==================== CELERY (Redis) ====================
        "CELERY_BROKER_URL":            "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND":        "redis://localhost:6379/0",
        "CELERY_TASK_TIMEOUT":          3600,
        "CELERY_TASK_HARD_LIMIT":       3900,
        "CELERY_PREVIEW_TIMEOUT":       300,
        "CELERY_PREVIEW_HARD_LIMIT":    360,
        "CELERY_SCHEDULE_TIMEOUT":      3500,
        "CELERY_SCHEDULE_HARD_LIMIT":   3600,
        # C3 fix 4: TTL corto + heartbeat periodico dal worker durante
        # l'esecuzione. Se il worker muore SIGKILL il lock scade in
        # SCHEDULE_LOCK_TTL_SECONDS invece di restare appeso fino al
        # hard limit Celery. HEARTBEAT_INTERVAL << TTL/2 per margine.
        "SCHEDULE_LOCK_TTL_SECONDS":                600,
        "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS": 180,
        # C3 follow-up: leader election fail-closed di default.
        # Se Redis e' unavailable il beat va in standby invece di
        # diventare leader by default (evita doppio enqueue in caso
        # di flap Redis con due beat attivi). Override a True per
        # deployment single-instance dove nessun altro beat gira mai.
        "SCHEDULE_BEAT_LEADER_FAIL_OPEN":           False,
        "CELERY_MAX_RETRIES":           3,
        "TASK_PROGRESS_POLL_INTERVAL":  1,
        "CELERY_RESULT_EXPIRES":        86400,

        # ==================== AUTH ====================
        # Durata del token JWT in ore. Migrato a Settings da `BE-CONFIG-001B`
        # (era direct `os.getenv` in `app.shared.jwt_token`). Override env
        # `JWT_EXPIRE_HOURS` continua a funzionare via type coercion `int`.
        "JWT_EXPIRE_HOURS": 8,
        # Login rate limit (R-01 + ADR-0021). Migrati a Settings da
        # `BE-CONFIG-001C` (erano direct `os.getenv` import-time in
        # `app/core/auth/auth.py`). Default identici al pre-001C: 10
        # tentativi falliti in 60s -> lockout 300s. I test esistenti
        # `tests2/security/unit/test_login_rate_limit_per_ip.py`
        # monkeypatch-ano i moduli `LOGIN_*` symbol; preservato.
        "LOGIN_MAX_ATTEMPTS": 10,
        "LOGIN_WINDOW_SECONDS": 60,
        "LOGIN_LOCKOUT_SECONDS": 300,
        # Timeout Redis per i lookup di rate limit / blacklist. Default 1s
        # (single-host) — ops puo' alzarlo per Redis remoto/latente. Su
        # timeout: fallback in-memory (login RL) o fail-open (blacklist
        # read, ADR-0009). Migrati a Settings da `BE-CONFIG-001C`.
        "LOGIN_RL_REDIS_TIMEOUT_SEC": 1,
        "JWT_BLACKLIST_REDIS_TIMEOUT_SEC": 1,
        # Soglia "stale" per la blacklist JWT mostrata nella dashboard
        # admin (`admin_system_status/service.py`). Sopra questa soglia
        # l'ultima sync e' considerata stantia e segnalata come warning.
        # Default 1800s (30 min). Migrato a Settings da `BE-CONFIG-001C`.
        "JWT_BLACKLIST_STALE_THRESHOLD_SEC": 1800,

        # ==================== FEATURE FLAG + TUNING (001D) ====================
        # Migrati da direct `os.getenv` in `BE-CONFIG-001D`. Default
        # tipati identici al pre-001D. Override env funziona via
        # `Settings._converti_tipo_env`.
        # FEATURE_FLAG (invariants.md §3): kill-switch / dialect-tuning.
        # `MSSQL_FAST_EXECUTEMANY` accetta on|off|true|false|0|1|...; il
        # caller fa il match insensitive. Default `"on"` = abilita
        # `fast_executemany` per pyodbc/MSSQL.
        "MSSQL_FAST_EXECUTEMANY": "on",
        # `CARICA_BULK_INSERT` accetta on|off|auto|true|false|...
        # Post 2026-04-23 e' funzionalmente no-op (entrambi i rami
        # producono executemany), preservato come scaffolding op.
        "CARICA_BULK_INSERT": "auto",
        # `ENABLE_ASYNC_ENRICHMENT` kill-switch routing async/sync
        # enrichment. Default `False` (sync legacy); ops abilita in
        # prod con `true`/`1`/`yes`.
        "ENABLE_ASYNC_ENRICHMENT": False,
        # OPERATIONAL_TUNING:
        # `ENRICHMENT_POOL_SIZE` semaforo concorrenza async + httpx
        # pool size. Default 50, clamp [5, 200] applicato dai consumer.
        "ENRICHMENT_POOL_SIZE": 50,
        # `TICKET_ATTACHMENT_MAX_BYTES` cap upload attachments tickets.
        # Default 10 MB. Sotto questa soglia il blob va in DB BYTEA;
        # sopra dovrebbe andare object storage (separate path).
        "TICKET_ATTACHMENT_MAX_BYTES": 10 * 1024 * 1024,
        # Soglie display dashboard admin per la dimensione dello schema
        # PG. Float in GB. Puro display, non influenza retention.
        "DB_SCHEMA_WARN_GB": 1.0,
        "DB_SCHEMA_ERROR_GB": 5.0,

        # ==================== API ESTERNE ====================
        "API_TIMEOUT": 30,
        "API_DEFAULT_RATE_LIMIT": 5,

        # ==================== FORMATI OUTPUT ====================
        "SUPPORTED_OUTPUT_FORMATS": [
            "csv", "json", "xlsx", "parquet",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "DEFAULT_OUTPUT_FORMAT": "csv",

        # ==================== FILE ESTERNI ====================
        "SUPPORTED_FILE_FORMATS": [
            "csv", "xlsx", "json",
            "geojson", "gpkg", "shp", "gdb"
        ],
        "MAX_FILE_SIZE": 100 * 1024 * 1024,  # 100 MB

        # ==================== TEMP CLEANUP ====================
        # TTL in ore per i file temp orfani sotto `DATA_DIR/temp/`.
        # Beat scheduler (`workers/beat_scheduler:_cleanup_temp_orphans`)
        # rimuove le directory piu' vecchie di N ore. Default 24h.
        # Migrato a Settings da `BE-CONFIG-001B`.
        "TEMP_ORPHAN_CLEANUP_HOURS": 24,

        # ==================== STORAGE POLICY (A1) ====================
        # Policy unica top-level per persistenza di result pipeline e
        # file_source utente. Vedi orchestrate/phase_1_stabilization/a1_storage_policy.md.
        #   - "db"             -> sempre BYTEA in DB, nessun bucket richiesto
        #   - "object_storage" -> sempre bucket, DB tiene solo metadata
        #   - "hybrid"         -> dispatch per soglia (default retro-compat)
        # Validazione coerenza all'avvio in app/shared/cloud_backends.validate_storage_config.
        "STORAGE_POLICY": "hybrid",
        # Hard cap per payload persistiti come BYTEA (mode "db" + ramo DB di "hybrid").
        # Default 500 MB: limite pratico di un BYTEA PG senza toast tuning.
        # Sopra cap: ResultSaveError al save (caller deve failare).
        "RESULT_DB_HARD_CAP_BYTES": 500 * 1024 * 1024,
        # Soglie dispatch DB-vs-object-storage in policy=hybrid (ADR-0011 + 0015).
        # Per-dominio (intenzionalmente separate, vedi ADR-0011 + ADR-0021):
        # ops puo' tunare result e file_source in modo indipendente perche'
        # la distribuzione di size differisce. Default 50 MB per entrambi
        # = break-even empirico DB BYTEA vs roundtrip object storage.
        # Migrato a Settings da `BE-CONFIG-001B` (era direct `os.getenv`).
        "RESULT_S3_THRESHOLD_BYTES": 50 * 1024 * 1024,
        "FILE_SOURCE_S3_THRESHOLD_BYTES": 50 * 1024 * 1024,
        # MinIO provider (A1-B2, ADR-0015). Required-if CLOUD_PROVIDER=minio,
        # pattern analogo a LOG_ARCHIVE_BUCKET: validazione fail-fast a boot
        # in app/shared/cloud_backends.validate_storage_config.
        "MINIO_ENDPOINT_URL": None,
        "MINIO_ACCESS_KEY": None,
        "MINIO_SECRET_KEY": None,

        # ==================== LOG ARCHIVE (O-ARC Batch 2, ADR-0014) ====================
        # Master switch archive+purge per plenora.logs e plenora.audit_actions.
        # Con enabled=false (default) validate_storage_config salta tutti i check
        # LOG_ARCHIVE_*; il retention esistente (_cleanup_old_logs) continua a
        # girare come oggi. L'hook archive nel beat arriva in Batch 3
        # (ADR-0014 §A4: silent pass anche con altre LOG_ARCHIVE_* settate).
        "LOG_ARCHIVE_ENABLED": False,
        # Prefisso deterministico delle object key archive. Default senza
        # auto-derivazione <env>: ops override esplicito in .env per separare
        # prod/staging/dev (ADR-0014 §A2). Formato: "" oppure endswith "/".
        "LOG_ARCHIVE_KEY_PREFIX": "logs-archive/",
        # Righe per batch iterazione SELECT->upload->DELETE del writer. Cap
        # pratico RAM per batch. Nome canonico (ADR-0014 §A1): supersede
        # LOG_ARCHIVE_BATCH_SIZE citato nell'ADR originale Consequences/Neutral.
        "LOG_ARCHIVE_BATCH_ROWS": 10_000,
        # Giorni di retention per plenora.audit_actions. 0 = retention
        # disabilitata (simmetrico a SCHEDULE_LOGS_RETENTION_DAYS). Default
        # 365gg: baseline ISO 27001 A.12.4.1 per event log. Consumato dal
        # beat in Batch 3 come cutoff archive+purge.
        "AUDIT_RETENTION_DAYS": 365,
        # NB: LOG_ARCHIVE_BUCKET e' intenzionalmente fuori da DEFAULTS —
        # e' required-if-enabled con validazione fail-fast a boot
        # (ADR-0014 §A3: distinto da BACKUP_BUCKET/BACKUP_S3_BUCKET).

        # ==================== PROGETTI ====================
        "SUPPORTED_PROJECT_TYPES": ["estrai", "manipola", "arricchisci", "salva"],
        "DEFAULT_GROUP": "default",

        # ==================== LIMITI ====================
        "MAX_PROJECT_NAME_LENGTH": 100,
        "MAX_CONNECTION_NAME_LENGTH": 100,
        "MAX_PREVIEW_ROWS": 1000,
        # ==================== MEMORY / OOM PREVENTION ====================
        # BE-GO-READY-MEMORY-OOM-001-LOAD-FILE-PREFLIGHT-GUARD: cap massimo
        # in byte per il file caricato sincrono via
        # `load_dataframe_from_file`. Sopra il cap il guard solleva
        # `MemoryLimitExceededError` mappata a 413 envelope dual-shape
        # `memory_limit_exceeded`. Default conservativo 200 MB: bilancia
        # uso routine (CSV/JSON < 50 MB) e protezione worker contro file
        # multi-GB che rieducherebbero il process al SIGKILL del kernel.
        # Override via env `LOAD_FILE_MAX_BYTES` per ambienti con worker
        # piu' grossi.
        "LOAD_FILE_MAX_BYTES": 200 * 1024 * 1024,
        # BE-GO-READY-MEMORY-OOM-002-WORKER-RSS-SAMPLING: cap RSS del
        # processo worker prima di stage pesanti (pipeline executor).
        # Default 1.5 GB. Se il RSS corrente supera il cap, il guard
        # solleva MemoryLimitExceededError mappata a 503 (worker overloaded).
        # Cap <= 0 disabilita il guard (escape hatch).
        # Override via env `WORKER_RSS_MAX_BYTES`.
        "WORKER_RSS_MAX_BYTES": 1_500_000_000,
        # BE-GO-READY-TRANSFORMATION-ENGINE-001-CROSS-JOIN-GUARD: cap
        # massimo per il numero di righe di output di un cross-join
        # (`op_cross_join` in `app.core.manipola.transforms.joins`).
        # Il preflight stima `len(left) * len(right)` PRIMA di
        # `pd.merge(how="cross")`. Sopra il cap solleva
        # `MemoryLimitExceededError` (stage `manipola_cross_join`)
        # mappata a 413 envelope dual-shape `memory_limit_exceeded`
        # (Payload Too Large: input troppo grandi per il cross). Default
        # 50_000_000 righe: copre la routine multi-tabella tipica
        # (1k x 10k = 10M, 10k x 1k = 10M) ma blocca esplosioni 100M+
        # che certamente fanno SIGKILL del worker. Cap <= 0 disabilita
        # il guard (escape hatch testing/admin).
        # Override via env `CROSS_JOIN_MAX_OUTPUT_ROWS`.
        "CROSS_JOIN_MAX_OUTPUT_ROWS": 50_000_000,
        # BE-GO-READY-TRANSFORMATION-ENGINE-002-MULTI-JOIN-ROW-ESTIMATE:
        # cap massimo righe stimate per step dal multi-join sequenziale
        # (`_esegui_multi_join` in `app.core.manipola.executor`). Il
        # guard stima la cardinalita' output del prossimo `pd.merge`
        # dalle frequenze delle join key PRIMA di materializzare il
        # DataFrame. Se la stima supera il cap solleva
        # `MemoryLimitExceededError` (stage `manipola_multi_join`).
        # Reuse contratto Memory/OOM (status 413, codice
        # `memory_limit_exceeded`).
        # Default 50_000_000 righe (stesso budget del cross-join: stessa
        # semantica "row count che il worker puo' tenere in RAM"). Cap
        # <= 0 disabilita il guard.
        # La stima usa value-counts sulle join key: costo O(N+M), ma
        # molto piu' sicuro del worst-case cartesian
        # (`len(result) * len(next)`) e meno rischioso di lasciare che
        # il primo merge esploda in memoria.
        # Override via env `MULTI_JOIN_MAX_OUTPUT_ROWS`.
        "MULTI_JOIN_MAX_OUTPUT_ROWS": 50_000_000,

        # BE-GO-READY-STORAGE-ENGINE-006-RECONCILIATION-BEAT-HOOK:
        # Tunables del reconciler beat hook che processa le entry
        # pending in `plenora.storage_reconciliation_log`. Tutti
        # operational tuning (ADR-0021), override env consentito.
        # - STORAGE_RECONCILE_INTERVAL_SECONDS: cadence gate del hook
        #   nel `PlenoraBeatScheduler.tick()`. Default 900s (15 min)
        #   (tradeoff tra latenza orphan-recovery e load sul DB /
        #   object storage). Cap <= 0 -> hook disabilitato (escape
        #   hatch ops).
        # - STORAGE_RECONCILE_BATCH_SIZE: max righe pending lette per
        #   ciascun tick. Default 50: bound al numero di
        #   `compensating_delete` per tick.
        # - STORAGE_RECONCILE_MAX_ATTEMPTS: oltre questo numero di
        #   tentativi falliti l'entry transitiona a `lost` (review
        #   ops). Default 5.
        # - STORAGE_RECONCILE_BACKOFF_BASE_SECONDS: base in secondi
        #   per il backoff esponenziale
        #   `next_retry_at = now + base * 2**(attempts-1)`, exponent
        #   capped a 8 (~4.3h max con base 60s). Default 60s.
        "STORAGE_RECONCILE_INTERVAL_SECONDS": 900,
        "STORAGE_RECONCILE_BATCH_SIZE": 50,
        "STORAGE_RECONCILE_MAX_ATTEMPTS": 5,
        "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS": 60,
        # BE-GO-READY-STORAGE-ENGINE-008-RECONCILIATION-OPERATIONAL-GUARDRAILS
        # Operational tuning (ADR-0021 OPERATIONAL_TUNING) per evitare
        # che `plenora.storage_reconciliation_log` diventi una discarica
        # silenziosa di terminali. Tutto override env consentito.
        # - STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS: giorni di retention
        #   per le righe terminali `done` / `orphan_deleted` (audit
        #   trail). Sopra il cutoff il beat hook ne rimuove fino a
        #   `STORAGE_RECONCILE_PRUNE_BATCH_SIZE` per tick. Default 30
        #   giorni: bilancia visibilita' forensic vs storage cost.
        #   Cap <= 0 -> prune disabilitato (la tabella cresce; ops
        #   accetta esplicitamente).
        # - STORAGE_RECONCILE_PRUNE_BATCH_SIZE: cap righe potate per
        #   tick. Default 500: bound deliberatamente alto rispetto al
        #   normale write-rate per assorbire backlog accumulato senza
        #   richiedere intervention. Cap <= 0 -> prune disabilitato.
        "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS": 30,
        "STORAGE_RECONCILE_PRUNE_BATCH_SIZE": 500,

        # ==================== TASK ARRICCHIMENTO ====================
        "ENRICHMENT_BATCH_SIZE": 100,
        "ENRICHMENT_MAX_WORKERS": 5,
        "ENRICHMENT_CACHE_TTL": 3600,

        # ==================== CARICA ====================
        # Soglia righe oltre la quale TRUNCATE_INSERT + ALL_OR_NOTHING usa
        # stage+swap invece di DELETE+INSERT atomico. Sotto soglia: atomic
        # (zero overhead, no privilegi DDL). Sopra soglia: stage+swap
        # (safe su dataset enormi, richiede CREATE TABLE; se il grant
        # manca il writer fa fallback automatico ad atomic con WARNING).
        "CARICA_STAGE_SWAP_THRESHOLD": 500_000,

        # ==================== ESTRAI ====================
        # C11: hard cap righe ritornate da una query ESTRAI. Se un
        # chunk_fetchmany porta il totale oltre questo limite, la
        # query fallisce loud con suggerimento (aggiungi WHERE, usa
        # LIMIT, oppure alza il cap). Protezione anti-OOM: fino a
        # C11 il chunking era cosmetico (fetchmany ma poi accumulo
        # in memoria + DataFrame intero). Default 5M: ragionevole per
        # ETL tipico PLENORA-ETL (centinaia-migliaia di righe), lascia
        # margine per operazioni admin occasionali da 1-5M.
        "ESTRAI_MAX_ROWS": 5_000_000,

        # C11: cap righe serializzate in intermediate.runs snapshot.
        # Sopra questa soglia il writer scrive solo metadata (no
        # df.to_dict rows) per evitare double-materialization della
        # query output nell'intermediate DB. Default 100k: l'intermediate
        # serve per debug UI, non per re-esecuzione integrale.
        "INTERMEDIATE_SNAPSHOT_MAX_ROWS": 100_000,

        # ==================== INTERMEDIATE PERSISTENCE POLICY ====================
        # Phase 6 product-grade Batch 1 (2026-04-28). Nuova policy
        # globale che separa "persistenza righe" (UI/debug) da "change
        # detection" (hash). Tre mode implementati in Batch 1:
        #   - "metadata_only": runs + node_meta + node_hash, zero
        #     node_rows. Massima velocita'. Diff L2 non disponibile.
        #   - "preview_only": metadata + hash + prime
        #     INTERMEDIATE_PREVIEW_ROWS righe in node_rows. Default
        #     consigliato in produzione.
        #   - "full_pg": comportamento legacy, tutte le righe in
        #     node_rows. Compat/debug temporaneo.
        # Mode "full_artifact": persiste data.parquet + manifest.json
        # su runtime object storage (richiede OBJECT_STORAGE_ENABLED=true
        # + OBJECT_STORAGE_BUCKET configurato). Zero `node_rows`,
        # `node_meta.artifact_uri/_status/_format` valorizzati.
        # Validazione fail-fast in
        # `app/core/intermediate/policy.validate_intermediate_settings`.
        # Default produzione: metadata_only (Batch object storage
        # intermedi, 2026-04-28). Postgres resta catalogo leggero.
        # Per audit/debug: preview_only (legacy) o full_artifact.
        "INTERMEDIATE_PERSISTENCE_MODE": "metadata_only",
        # Numero righe persistite quando MODE=preview_only. Tagliato
        # dalle prime N (df.head). UI mostra "preview NN su YYY".
        "INTERMEDIATE_PREVIEW_ROWS": 500,
        # Formato artifact full quando MODE=full_artifact. Dichiarativo
        # (`artifact_writer` oggi serializza Parquet hardcoded e popola
        # `node_meta.artifact_format` con questo valore). Future-proof
        # per supporto multi-formato senza schema change.
        "INTERMEDIATE_FULL_ARTIFACT_FORMAT": "parquet",

        # Bug #24 (perf, 2026-04-28): mode di calcolo del node_hash
        # del DataFrame al checkpoint/final snapshot.
        #   - "sample": hash di (schema, len(df), df.head(N)). Costo
        #     ~10-20 ms anche su DataFrame da 275k righe (vs 1.0-1.5 s
        #     del full-DF su DF grandi). Trade-off: cambi che toccano
        #     SOLO righe oltre la N-esima possono non essere rilevati
        #     dal change-detection L1; accettabile per audit alto
        #     livello (default operativo).
        #   - "full": hash di tutto il DataFrame via pd.util.hash_
        #     pandas_object. Lento sui DF grandi ma esatto. Per audit
        #     forte su run specifici si puo' temporaneamente alzare
        #     la mode a "full" via env senza redeploy.
        # node_hash NON e' usato da alcun branch di correttezza pipeline
        # (transform/join/save non lo leggono); serve solo a UI run/diff
        # e a future feature di change-detection. Default = sample.
        "INTERMEDIATE_FINGERPRINT_MODE": "sample",
        # Cap righe usate dal mode sample. >0. Default 1000: copre la
        # head dei dataset reali e mantiene il costo nell'ordine dei ms.
        # Rendere troppo alto vanifica il vantaggio del sample.
        "INTERMEDIATE_FINGERPRINT_SAMPLE_ROWS": 1000,

        # ==================== CHECKPOINT POLICY PER MODULO ====================
        # Granularita' selezione nodi che producono snapshot in MANIPOLA.
        # Policy applicata al layer `intermediate.policy`, non agli
        # executor (Batch 2). Valori:
        #   - "none": solo final, nessun nodo intermedio
        #   - "critical": nodi critici (join/aggregate/filter/...) + final
        #   - "marked": solo nodi con flag canvas save_intermediate=true
        #     + final
        #   - "critical_or_marked": critici O marcati + final
        #   - "all": tutti i nodi (debug/legacy)
        # Vedi `manipola_checkpoint_policy.md` per la classifica
        # transform-by-transform.
        "MANIPOLA_INTERMEDIATE_CHECKPOINTS": "critical",
        # ESTRAI: solo final output del modulo (no checkpoint per
        # query intermedie). Solo "final_only" ammesso in Batch 1.
        "ESTRAI_INTERMEDIATE_CHECKPOINTS": "final_only",
        # ARRICCHISCI: solo final output del modulo (no snapshot per
        # nodo api_enrich). Solo "final_only" ammesso in Batch 1.
        "ARRICCHISCI_INTERMEDIATE_CHECKPOINTS": "final_only",

        # ==================== ADMIN BACKUPS POLICY (BE-GO-READY-CONFIG-BOUNDARY-001) ====================
        # Backup amministrativi: cron schedule + descrizione UI-readable.
        # Classificate APP_POLICY/OPERATIONAL_TUNING in ADR-0021 §"Config
        # boundary": stringhe consumate da `get_schedule_info()` per
        # l'endpoint admin di metadata. La schedule reale e' gestita a
        # livello deployment (systemd timer / Celery beat / K8s CronJob);
        # questi default sono i valori esposti al FE quando l'env e'
        # unset, e devono essere overridabili via env per allineare il
        # display alla schedule effettivamente configurata sul deployment.
        # NOTA: `BACKUP_BUCKET`, `BACKUP_S3_BUCKET`, `AWS_REGION` restano
        # DEPLOY_INFRA env-only (gia' centralizzati in
        # `cloud_backends._resolve_backup_bucket()` / `_build_object_storage`).
        "BACKUP_SCHEDULE_CRON": "0 2 * * *",
        "BACKUP_SCHEDULE_DESCRIPTION": "Daily 02:00 UTC (+ jitter fino a 5 min)",

        # ==================== ARTIFACT STORE ====================
        # Bucket dedicato runtime artifact intermedi (full_artifact).
        # Separato da BACKUP_BUCKET (IAM boundary distinto) e dal
        # terraform-state. Required quando MODE=full_artifact +
        # OBJECT_STORAGE_ENABLED=true. Funziona come fallback retro-
        # compat dietro a OBJECT_STORAGE_BUCKET in
        # `cloud_backends._resolve_runtime_bucket`. None ammesso se
        # OBJECT_STORAGE_BUCKET e' settato.
        "PLENORA_ARTIFACT_BUCKET": None,
        # Prefix logico dentro il bucket. Layout completo:
        #   <prefix>/group=.../project=.../run=.../module=.../node=...
        # Default "intermediate". Trim slash a destra.
        "INTERMEDIATE_ARTIFACT_PREFIX": "intermediate",

        # ==================== OBJECT STORAGE CAPABILITY (Batch 1.5) ====================
        # Capability globale: se False, NESSUN modulo runtime (result/
        # file_source/eseguibili/IMDF/intermediate) deve tentare upload o
        # download via object storage. La factory `get_runtime_object_storage`
        # solleva `ObjectStorageDisabledError`; i caller che hanno fallback
        # locale (IMDF list_bundles cache locale) degradano graceful, gli
        # altri sollevano errori operativi chiari.
        # Backup amministrativo (`get_backup_object_storage`) e log archive
        # (`get_log_archive_storage`) seguono la stessa capability: se
        # False, admin_backups list ritorna feature-disabled invece di
        # importare boto3 e fare list_objects_v2 diretta.
        # Default True per non rompere deployment esistenti che usano
        # CLOUD_PROVIDER=aws + BACKUP_BUCKET. Per disabilitare: setta
        # OBJECT_STORAGE_ENABLED=false in env.
        "OBJECT_STORAGE_ENABLED": True,
        # Backend logico. "s3"=AWS, "minio"=S3-compatible, "local"=futuro
        # filesystem-backed (dev), "webdav"=futuro, "none"=esplicitamente
        # disattivato (equivalente a OBJECT_STORAGE_ENABLED=False).
        # Default None = derivato da CLOUD_PROVIDER (aws->s3, minio->minio,
        # none->none).
        "OBJECT_STORAGE_BACKEND": None,
        # Bucket runtime per result/file_source/eseguibili/IMDF. Distinto
        # da BACKUP_BUCKET per design (lifecycle, IAM boundary diverso).
        # Se None, fallback su PLENORA_ARTIFACT_BUCKET; se anche quello
        # None, fallback retro-compat su BACKUP_BUCKET (con warning) per
        # non rompere deployment legacy. Configurazione canonica futura:
        # OBJECT_STORAGE_BUCKET per runtime, BACKUP_BUCKET per backup,
        # LOG_ARCHIVE_BUCKET per log archive.
        "OBJECT_STORAGE_BUCKET": None,
    }

    def __init__(self):
        pass  # Niente da caricare — tutto da DEFAULTS + env

    # ==================== ACCESSO VALORI ====================

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
        os.environ[key] = str(value)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self.get(name)

    # ==================== DIRECTORY ====================

    def _directory(self, sottopercorso: str, base: Path = None, create: bool = True) -> Path:
        if base is None:
            base = Path(self.get("DATA_DIR"))
        percorso = base / sottopercorso
        if create:
            percorso.mkdir(parents=True, exist_ok=True)
        return percorso

    @property
    def CORS_ORIGINS_LIST(self) -> list:
        """Restituisce la lista delle origini CORS da stringa CSV."""
        raw = self.get("CORS_ORIGINS", "")
        return [o.strip() for o in raw.split(",") if o.strip()]

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

    # ==================== LOGGING ====================
    # Livello minimo dei log che finiscono nella console (stdout/journald).
    @property
    def LOG_LEVEL_CONSOLE(self) -> str:
        return (self.get("LOG_LEVEL_CONSOLE", "INFO") or "INFO").upper()

    # Livello minimo dei log persistiti nel DB (tabella plenora.logs).
    # Default WARNING: in prod le righe INFO sono rumore e gonfiano la tabella
    # senza fornire valore diagnostico proporzionale al costo.
    @property
    def LOG_LEVEL_DB(self) -> str:
        return (self.get("LOG_LEVEL_DB", "WARNING") or "WARNING").upper()

    # Giorni di retention per plenora.logs. Record piu' vecchi vengono eliminati
    # dal beat scheduler (PlenoraBeatScheduler._cleanup_old_logs).
    @property
    def LOG_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("LOG_RETENTION_DAYS", 30))
        except (TypeError, ValueError):
            return 30

    # Giorni di retention per plenora.schedule_logs (storia esecuzioni
    # scheduler). Default 90gg — finestra utile per debug/audit di catene
    # ricorrenti, oltre la quale il dettaglio dei singoli step non serve.
    # Eliminati da PlenoraBeatScheduler._cleanup_old_schedule_logs.
    # Mettere 0 per disabilitare la retention.
    @property
    def SCHEDULE_LOGS_RETENTION_DAYS(self) -> int:
        try:
            return int(self.get("SCHEDULE_LOGS_RETENTION_DAYS", 90))
        except (TypeError, ValueError):
            return 90

    # ==================== LOG ARCHIVE (O-ARC Batch 2, ADR-0014) ====================
    # Master switch pipeline archive+purge (beat hook in Batch 3). False =
    # validate_storage_config salta i check LOG_ARCHIVE_*; il retention
    # esistente gira come oggi. Silent pass anche con altre LOG_ARCHIVE_*
    # settate nel .env (pattern preparazione pre-flip, ADR-0014 §A4).
    @property
    def LOG_ARCHIVE_ENABLED(self) -> bool:
        return bool(self.get("LOG_ARCHIVE_ENABLED", False))

    # Bucket object storage dedicato per archive log. Separato da
    # BACKUP_BUCKET: IAM boundary distinto (ADR-0014 §A3). Unset di
    # default; required-if-enabled con validazione fail-fast a boot in
    # app/shared/cloud_backends.validate_storage_config.
    @property
    def LOG_ARCHIVE_BUCKET(self) -> Optional[str]:
        return self.get("LOG_ARCHIVE_BUCKET")

    # Prefisso deterministico delle object key archive. Ops override in
    # .env per separare prod/staging/dev (ADR-0014 §A2). Formato: vuoto
    # oppure terminante con "/" (invariante consumato da
    # app/services/log_archive._build_object_key).
    @property
    def LOG_ARCHIVE_KEY_PREFIX(self) -> str:
        return self.get("LOG_ARCHIVE_KEY_PREFIX", "logs-archive/") or "logs-archive/"

    # Righe per batch iterazione SELECT->upload->DELETE del writer
    # LogArchiveWriter. Cap pratico RAM per batch. Nome canonico
    # (ADR-0014 §A1): supersede LOG_ARCHIVE_BATCH_SIZE citato nell'ADR
    # originale. Consumato dal beat in Batch 3 per istanziare il writer.
    @property
    def LOG_ARCHIVE_BATCH_ROWS(self) -> int:
        try:
            return int(self.get("LOG_ARCHIVE_BATCH_ROWS", 10_000))
        except (TypeError, ValueError):
            return 10_000

    # Giorni di retention per plenora.audit_actions. 0 = retention
    # disabilitata (simmetrico a SCHEDULE_LOGS_RETENTION_DAYS). Default
    # 365gg: baseline ISO 27001 A.12.4.1 per event log. Consumato dal
    # beat in Batch 3 come cutoff archive+purge.
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

    # ==================== HELPER PERCORSI ====================

    def get_project_output_dir(self, project_id: str) -> Path:
        return self._directory(project_id, base=self.RESULTS_DIR)

    def get_connection_file(self, connection_id: str) -> Path:
        return self.CONNECTIONS_DIR / f"{connection_id}.json"

    def get_file_source_dir(self, file_id: str) -> Path:
        return self._directory(file_id, base=self.FILES_DIR)

    def get_task_dir(self, task_id: str) -> Path:
        return self._directory(task_id, base=self.TASKS_DIR)

    # ==================== REDIS AUTO-START ====================

    def _ensure_redis(self, errors: list, warnings: list) -> None:
        """Verifica che Redis sia raggiungibile sul broker URL configurato.

        Su dev Windows: fallback opportunistico con avvio via WSL
        (convenienza dev locale). Su Linux/server/container o su
        ambienti hardened (`production`/`prod`/`staging`/`stage`):
        fail-fast con messaggio operativo che cita il broker URL
        configurato. Nessun riferimento a WSL nel path server: WSL
        e' un dettaglio del dev locale e non deve apparire nel log
        di un servizio che gira sotto systemd o container.
        """
        import socket
        from urllib.parse import urlparse

        broker_url = self.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
        parsed = urlparse(broker_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379

        def _redis_ping() -> bool:
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                return False

        if _redis_ping():
            logger.info("Redis già attivo su %s:%s", host, port)
            return

        if self._wsl_redis_fallback_allowed():
            self._ensure_redis_wsl_fallback(_redis_ping, errors, warnings)
            return

        errors.append(
            f"Redis non raggiungibile su {broker_url}. "
            "Avvia il servizio Redis configurato in CELERY_BROKER_URL "
            "prima di avviare l'app."
        )

    @staticmethod
    def _wsl_redis_fallback_allowed() -> bool:
        """True solo su dev Windows. Gating del fallback WSL.

        Dev = `ENVIRONMENT` (fallback `ENV`) non in
        `_DOTENV_DISABLED_ENVS` (production/prod/staging/stage).
        Windows = `sys.platform` inizia con `win`.
        """
        import sys

        env_name = os.getenv(
            "ENVIRONMENT", os.getenv("ENV", "development")
        ).lower()
        is_dev = env_name not in _DOTENV_DISABLED_ENVS
        is_windows = sys.platform.startswith("win")
        return is_dev and is_windows

    def _ensure_redis_wsl_fallback(
        self, redis_ping, errors: list, warnings: list
    ) -> None:
        """Avvio opportunistico di Redis via WSL — solo dev Windows."""
        import subprocess
        import time

        logger.info("Redis non raggiungibile — avvio via WSL (dev Windows)...")
        try:
            subprocess.Popen(
                ["wsl", "redis-server", "--daemonize", "yes"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(10):
                time.sleep(0.5)
                if redis_ping():
                    logger.info("Redis avviato con successo via WSL")
                    return
            warnings.append("Redis avviato via WSL ma non risponde dopo 5s")
        except FileNotFoundError:
            errors.append(
                "Redis non attivo e WSL non disponibile. "
                "Avvia Redis manualmente: wsl redis-server --daemonize yes"
            )
        except Exception as e:
            errors.append(f"Impossibile avviare Redis via WSL: {e}")

    # ==================== VALIDAZIONE AVVIO ====================

    def validate_startup(self) -> None:
        errors = []
        warnings = []

        try:
            data_dir = self.DATA_DIR
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            errors.append(f"DATA_DIR '{self.get('DATA_DIR')}' non scrivibile: {e}")

        # Pulizia cartelle temp residue da sessioni precedenti
        try:
            import shutil, stat
            def _on_error(func, fpath, exc_info):
                try:
                    os.chmod(fpath, stat.S_IWRITE)
                    func(fpath)
                except Exception as e:
                    logger.debug("Cleanup temp: impossibile rimuovere %s: %s", fpath, e)
            temp_dir = self.TEMP_DIR
            if temp_dir.exists():
                for entry in temp_dir.iterdir():
                    if entry.is_dir():
                        shutil.rmtree(entry, onerror=_on_error)
                    else:
                        try:
                            entry.unlink()
                        except Exception as e:
                            logger.debug("Cleanup temp: impossibile rimuovere %s: %s", entry, e)
        except Exception as e:
            logger.debug("Pulizia cartelle temp fallita (non critico): %s", e)

        try:
            import shutil as _shutil
            for legacy_dir in ["files", "results", "File", "Results"]:
                legacy_path = self.DATA_DIR / legacy_dir
                if legacy_path.exists() and legacy_path.is_dir():
                    _shutil.rmtree(legacy_path, ignore_errors=True)
        except Exception as e:
            logger.debug("Rimozione cartelle legacy fallita (non critico): %s", e)

        # ── Redis: verifica che sia attivo. Su dev Windows fallback WSL,
        # altrove fail-fast operativo con messaggio sul broker URL. ──
        self._ensure_redis(errors, warnings)

        for w in warnings:
            print(f"⚠️  PLENORA-ETL: {w}")

        if errors:
            print("\n❌ PLENORA-ETL — Configurazione non valida, avvio interrotto:\n")
            for i, e in enumerate(errors, 1):
                print(f"  {i}. {e}")
            raise SystemExit(1)

    # ==================== DEBUG ====================

    def print_config(self):
        print("=" * 60)
        print("⚙️  CONFIGURAZIONE PLENORA-ETL")
        print("=" * 60)
        print("\n📂 DIRECTORY")
        print(f"  DATA_DIR:            {self.DATA_DIR}")
        print(f"  LOGS_DIR:            {self.LOGS_DIR}")
        print(f"  CONNECTIONS_DIR:     {self.CONNECTIONS_DIR}")
        print(f"  RESULTS_DIR:         {self.RESULTS_DIR}")
        print("\n⏱️  TIMEOUT")
        print(f"  DB_QUERY_TIMEOUT:    {self.DB_QUERY_TIMEOUT}s")
        print(f"  API_TIMEOUT:         {self.API_TIMEOUT}s")
        print(f"  CELERY_TASK_TIMEOUT: {self.CELERY_TASK_TIMEOUT}s")
        print("\n🗄️  CELERY (Redis)")
        print(f"  BROKER_URL:          {self.CELERY_BROKER_URL}")
        print(f"  RESULT_BACKEND:      {self.CELERY_RESULT_BACKEND}")
        print("=" * 60)


# Singleton
settings = Settings()

if __name__ == "__main__":
    settings.print_config()
