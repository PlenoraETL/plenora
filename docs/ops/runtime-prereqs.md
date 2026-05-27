# Runtime Prerequisites — Admin & Backup services

Contratto tra i moduli `app/core/admin_backups/` e `app/core/admin_system_status/` e l'ambiente in cui girano. Elenca cosa deve essere presente sull'host perche' le metriche di stato funzionino — e cosa succede se manca.

Scopo: evitare diagnosi sbagliate ("il dashboard dice `unknown`, il codice e' rotto") quando in realta' manca un binario o un permesso.

> Per installare Plenora-ETL da zero su un host nuovo, vedi
> [install-non-docker.md](install-non-docker.md). Quel runbook è la
> source-of-truth per la procedura passo-passo (host prereq,
> Postgres, Redis, object storage, secrets, systemd, frontend, smoke
> test). Questo documento elenca solo il **contratto runtime**, non la
> procedura di install. Per Docker vedi [install-docker.md](install-docker.md).

---

## Dipendenze Python

| Package | Usato da | Scopo |
|---|---|---|
| `boto3` | `admin_backups.service.list_backups`, `admin_system_status.get_backup_status` (transitive) | Lista oggetti S3 del bucket backup |
| `celery` | `admin_system_status.get_workers_status` | `inspect().active()/stats()/active_queues()` sui worker |

Entrambi sono in `requirements.txt` — se il modulo importa ma la lib manca (venv corrotto), il fallback e' `status="unknown"` + log a livello `error` o `warning`.

**Nota esplicita: NON e' richiesta la AWS CLI (`aws` binary).** Tutte le operazioni cloud passano per `boto3` nativo. Se vedi chiamate `subprocess` nei due moduli, sono a `systemctl`/`journalctl`, mai ad `aws`.

---

## Binari di sistema (host)

| Binario | Usato da | Path tipico | Fallback se assente |
|---|---|---|---|
| `pg_dump`, `pg_restore` | `scripts/backup_db.py`, `scripts/test_restore.py` | `/usr/bin/pg_dump` (Ubuntu 24.04 apt fornisce 16.x; se server Postgres e' 17.x serve repo PGDG: `postgresql-client-17`) | Script exit code 1 |

**Admin dashboard e container**: lo stato backup/verify-restore e' letto dalle tabelle DB `plenora.backup_runs` e `plenora.verify_runs`, non da `systemctl`/`journalctl`. Significa:
- Il dashboard admin funziona **identico** su host systemd e in container — zero dipendenza dai binari systemd
- Le righe sono scritte a fine job dai rispettivi script; se lo script non parte (timer non configurato, cron non attivato, ecc.) il dashboard mostra "Nessuna verifica registrata" / "Nessun run registrato"
- `/api/admin/backups/schedule` ritorna metadati statici dalla env var `BACKUP_SCHEDULE_CRON` (default `0 2 * * *`), non interroga il runtime schedulatore

**Quello che resta host-local** (fuori scope observability, da governare nel deployment):
- `POST /api/admin/backups/trigger` acquisisce il lock distribuito su Postgres (`admin_backup_manual_locks`) e accoda `workers.tasks.admin_backup_task.run_manual_backup_task` su Celery. L'API pod non lancia piu' `backup_db.py` via `subprocess.Popen` locale. In Kubernetes si puo' sostituire il worker Celery con un K8s Job mantenendo lo stesso lock/idempotency boundary.
- La schedulazione reale dei job (systemd timer oggi, Celery beat / K8s CronJob in container) e' questione di deployment, non di codice

---

## Service units systemd

Attesi installati in `/etc/systemd/system/`:

| Unit | Tipo | Scopo |
|---|---|---|
| `plenora-backend.service` | `simple` | FastAPI backend (uvicorn) |
| `plenora-celery.service` | `simple` | Worker Celery |
| `plenora-beat.service` | `simple` | Celery beat scheduler |
| `plenora-backup.service` | `oneshot` | Dump schema `plenora` + upload S3 |
| `plenora-backup.timer` | `timer` | Trigger daily 02:00 UTC (+ RandomizedDelaySec=300) |
| `plenora-backup-test.service` | `oneshot` | Restore di prova weekly (Sun 03:00 UTC) |
| `plenora-backup-test.timer` | `timer` | Trigger weekly |

File sorgente in `deploy/systemd/`. Dopo modifica: `sudo systemctl daemon-reload && sudo systemctl restart <unit>`.

---

## Variabili d'ambiente

Lette da `.env` via systemd unit wrapper o SSM secrets (vedi `deploy/iam/README.md`).

| Variabile | Default | Consumato da |
|---|---|---|
| `STORAGE_POLICY` | `hybrid` (`object_storage` raccomandato in prod cloud) | `app/core/results/blob_writer.py`, `app/infrastructure/storage/cloud_backends.py` |
| `OBJECT_STORAGE_ENABLED` | `true` | Gate capability object storage runtime; con `STORAGE_POLICY=object_storage` deve essere true |
| `OBJECT_STORAGE_BUCKET` | -- (obbligatoria in `object_storage`) | Bucket runtime per result, file sources e artifact intermedi |
| `PLENORA_ARTIFACT_BUCKET` | alias di `OBJECT_STORAGE_BUCKET` | Alias operativo per artifact runtime |
| `BACKUP_BUCKET` | `plenora-backup-112011134642-eu-south-1-an` | Nome canonico bucket backup |
| `DATABASE_URL` | — (obbligatoria) | `scripts/backup_db.py`, `scripts/test_restore.py`, `app/db/*` |
| `BACKUP_BUCKET` | `plenora-backup-bucket` | `admin_backups.service`, `scripts/backup_db.py` |
| `BACKUP_S3_REGION` | `eu-south-1` | come sopra |
| `BACKUP_SCHEMA` | `plenora` | `scripts/backup_db.py` (vuoto → full DB dump) |
| `BACKUP_PG_DUMP_TIMEOUT_SEC` | `3600` | `scripts/backup_db.py` |
| `BACKUP_S3_UPLOAD_TIMEOUT_SEC` | `1800` | `scripts/backup_db.py` |
| `CLOUD_PROVIDER` | `aws` | `app/infrastructure/storage/cloud_backends.py` factory (dispatch backend object storage + secrets) |
| `IMDF_BUNDLES_DIR` | — (config `settings`) | Cache locale bundle IMDF (TTL 48h via beat scheduler) |
| `IMDF_BUNDLE_TTL_HOURS` | `48` | Cleanup cache locale IMDF |

---

## IAM / Credenziali cloud

**Modello**: IAM role attached all'istanza EC2 (`plenora-backup-role`, account `112011134642`). `boto3` raccoglie credenziali automaticamente dal metadata endpoint EC2 — nessuna chiave statica in `.env`.

Permessi richiesti sul bucket `plenora-backup-112011134642-eu-south-1-an`:
- `s3:ListBucket`
- `s3:GetObject`
- `s3:PutObject`
- `s3:DeleteObject` (per eager cleanup post-download IMDF + eseguibili)

Permessi SSM per secrets (`/plenora/prod/*`):
- `ssm:GetParameter`, `ssm:GetParametersByPath`
- `kms:Decrypt` sulla KMS key che cifra i SecureString

**Se le credenziali mancano o sono invalide**: `boto3.client("s3")` crea il client senza errore, ma `list_objects_v2` solleva `ClientError` → caught da `list_backups` → log `error` + return `[]`. Dashboard: card backup in `status="error"` con messaggio "Nessun backup trovato su S3".

---

## Bucket S3 e convenzioni di prefix

`OBJECT_STORAGE_BUCKET` e' il bucket runtime per artefatti applicativi. Deve
restare distinto da `BACKUP_BUCKET`: in `STORAGE_POLICY=object_storage` il boot
fallisce se e' configurato solo il bucket backup.

| Bucket | Prefix | TTL | Uso |
|---|---|---|---|
| `plenora-backup-...` | `plenora_` | Standard 30gg → Standard-IA → delete 365gg | Dump DB daily |
| `plenora-runtime-artifacts-...` | `results/` | Retention TBD | Blob dei result pipeline |
| `plenora-runtime-artifacts-...` | `file-sources/` | Retention TBD | File source sopra soglia / object storage |
| `plenora-runtime-artifacts-...` | `intermediate/` | Retention TBD | Artifact intermedi `full_artifact` |
| `plenora-runtime-artifacts-...` | `eseguibili-outputs/` | 7gg delete (safety net sopra eager cleanup) | Output eseguibili workflow |
| `plenora-runtime-artifacts-...` | `imdf-bundles/` | 7gg delete (safety net sopra eager cleanup) | Bundle IMDF (zip + meta.json) |

Lifecycle rule gestite via Terraform (`deploy/terraform/lifecycle.tf`), state su `s3://plenora-terraform-state-112011134642/plenora-etl/lifecycle.tfstate`.

---

## Dipendenze runtime non-AWS

| Servizio | Endpoint | Usato da | Fallback |
|---|---|---|---|
| Postgres | `15.161.59.22:5432/plenora` (via `DATABASE_URL`) | tutto il backend + script backup | Senza DB: backend non parte. `admin_system_status.get_schedules_status` → `status="unknown"` con error |
| Redis | env `CELERY_BROKER_URL` (variabile **canonica unica**) | `workers.celery_app` (broker), `workers.beat_scheduler` (leader lock), `admin_system_status` (queue depth / infra status), `app.core.security.token_blacklist` (JWT revocation) | Redis down: `inspect()` timeout 2s → `nodes=0`, card workers vuota; `/auth/logout` → 503 (fail-loud write); `is_jti_blacklisted` → False (fail-open read) |

### Redis — perché una sola variabile

Tutta l'app parla con **una sola istanza Redis**. Non abbiamo (e non vogliamo) una `REDIS_URL` separata: `CELERY_BROKER_URL` fa da source-of-truth e copre TUTTI i consumer Redis. Motivazione:

- Single-host deploy: lo stesso `redis-server` serve broker Celery, backend Celery (DB 1), beat leader lock e blacklist JWT.
- Due variabili sinonime invitano a configurazioni incoerenti (es. `CELERY_BROKER_URL=redis://a` e `REDIS_URL=redis://b` per errore).
- Convivenza di keyspace: Celery usa chiavi tipo `celery`, `_kombu.binding.*`, `unacked*`. Blacklist JWT usa prefix `plenora:jwt_blacklist:*`. Nessuna collisione.

Se un giorno servisse separare la blacklist su un'altra istanza/DB (es. Redis dedicato high-availability per auth) → introdurre una nuova env var **esplicita** (`JWT_BLACKLIST_REDIS_URL`) e documentare il deliberato deviation. Finché non ce n'è bisogno, `CELERY_BROKER_URL` basta.

### JWT revocation (blacklist)

| Aspetto | Valore |
|---|---|
| Trigger | `POST /auth/logout` con bearer token |
| Chiave Redis | `plenora:jwt_blacklist:{jti}` (dove `jti` è claim `uuid4.hex` emessa al login) |
| TTL | `exp - now` del token (auto-cleanup a scadenza naturale) |
| Check | `get_current_user` consulta `EXISTS` prima della query DB |
| Fail policy write (`/logout`) | 503 se Redis down — il client deve sapere se la revoca è effettiva |
| Fail policy read (`get_current_user`) | Allow (JWT `exp`+signature restano enforced) — evita blast radius se Redis flappa |
| Token senza `jti` | `is_jti_blacklisted(None)` → False → passa normalmente |
| Health check | `admin_system_status.get_blacklist_status()` → card "JWT Blacklist" dashboard (EXISTS sentinel + contatori + alert "inoperante da X min") |
| Env `JWT_BLACKLIST_REDIS_TIMEOUT_SEC` | Timeout connessione/socket Redis del client blacklist (default `1` sec; alzare con Redis remoto) |
| Env `JWT_BLACKLIST_STALE_THRESHOLD_SEC` | Soglia alert "blacklist inoperante" se nessuna op reale riesce da X sec (default `1800` = 30 min) |
| Incident runbook | [jwt-incident-response.md](jwt-incident-response.md) (token compromesso, Redis down, rotazione SECRET_KEY, fleet-wide revoke) |
| Test | unit `tests2/security/unit/test_jwt_logout_blacklist.py`, integration `tests2/integration/test_jwt_logout_blacklist_integration.py` |

---

## Policy di graceful degradation

`admin_system_status/service.py` e `admin_backups/service.py` hanno un contratto comune: **mai sollevare, sempre ritornare un dict con `status`/`result`**.

| Input | Output |
|---|---|
| DB irraggiungibile (`plenora.backup_runs`/`verify_runs`) | `status="unknown"`, `error`/`error_message` popolato |
| Tabella `backup_runs`/`verify_runs` vuota (nessun run) | `status="unknown"`, message `"Nessun backup/verifica registrata"` |
| S3 403 / bucket inesistente | `list_backups()` → `[]`, card backup `status="error"` |
| `boto3` non installato | `list_backups()` → `[]` |
| Redis irraggiungibile (read) | `inspect()` timeout → `nodes=0`, card workers zeri; `is_jti_blacklisted` → False (fail-open) |
| Redis irraggiungibile (write `/auth/logout`) | 503 "Servizio di revoca token non disponibile, riprova" (fail-loud) |
| Query tickets fallita | Card "Ticket" → `status="unknown"`, message "Query ticket fallita", dashboard continua a funzionare per le altre card |
| DB query schedules fallita | `status="unknown"`, `error` campo troncato a 200 char |
| Celery app non importabile | `status="unknown"`, message `"Celery app non importabile"` |

Questo permette al dashboard admin di funzionare parzialmente anche in ambienti parziali (sviluppatore locale senza DB popolato, container senza AWS creds, broker down, ecc.) — le card in `unknown` sono neutre, non rompono la pagina.

---

## Ticketing (tech_debt + user_support)

Tabelle in `plenora.*` tracciate dalla dashboard `DB Schema` (vedi `_CRITICAL_TABLES` in `admin_system_status/service.py`):

| Tabella | Contenuto | Volume atteso | Cleanup |
|---|---|---|---|
| `tickets` | row principali (kind, status, severity, assignee, group_id, ...) | ~centinaia/anno | nessun cleanup automatico |
| `ticket_comments` | thread commenti utente↔admin | ~migliaia/anno | cascade delete su `ticket` |
| `ticket_attachments` | link `file_source_id` con uploader e timestamp. Il blob reale vive in `file_sources` | ~centinaia MB/anno | cascade delete su `ticket`; cleanup blob sottostante best-effort via `file_service` |

Categorie `tickets.category` sono enum **applicativo** (non DB CHECK) → estensibili senza migration. Vedi `docs/ops/tickets.md` per come aggiungere una categoria.

Allegati: allowlist MIME (immagini + log/txt) + cap 10MB enforced dal router. Se il threshold S3 globale (`FILE_SOURCE_S3_THRESHOLD_BYTES`) scende sotto 10MB, gli allegati finiscono su S3 trasparentemente (stesso dispatch pattern di `file_sources`).

Retention non presente. Se emerge necessità (GDPR, spazio), task Celery proposto:
```sql
DELETE FROM tickets
WHERE status = 'closed' AND updated_at < NOW() - INTERVAL '2 years';
-- comments + attachments cascade-deleted
```

Notifiche email: hook `TicketNotifier` predisposto, default no-op. Vedi `tickets.md#notifiche-hook-email-ready` per il wiring.

---

## Link

- Clean install (non-Docker, single-machine): [install-non-docker.md](install-non-docker.md)
- Clean install (Docker, status / target shape): [install-docker.md](install-docker.md)
- Backup runbook: [../../backend/deploy/backup-runbook.md](../../backend/deploy/backup-runbook.md)
- DR runbook: [backup-restore.md](backup-restore.md)
- JWT incident response: [jwt-incident-response.md](jwt-incident-response.md)
- Trusted proxies: [trusted-proxies.md](trusted-proxies.md)
- Ticketing system: [tickets.md](tickets.md)
- Systemd units: [../../backend/deploy/systemd/](../../backend/deploy/systemd/)
- IAM setup: [../../backend/deploy/iam/](../../backend/deploy/iam/)
- Cloud abstraction: [../../backend/app/infrastructure/storage/cloud_backends.py](../../backend/app/infrastructure/storage/cloud_backends.py)
