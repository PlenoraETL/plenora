# Secrets Management

**Scope**: come URBY-ETL gestisce secret applicativi (credenziali, chiavi di cifratura, token).
**Owner**: piattaforma backend.
**Ultima revisione**: 2026-04-21.

## Secret gestiti

| Secret | Uso | Dove vive ora | Dove andra' | Rotazione |
|---|---|---|---|---|
| `DATABASE_URL` | connection string PostgreSQL | `backend/.env` (file), systemd `EnvironmentFile` | AWS SSM `/plenora/prod/DATABASE_URL` (SecureString) | su compromissione o cambio password DB |
| `PLENORA_ENCRYPTION_KEY` (alias legacy `URBY_ENCRYPTION_KEY`) | chiave derivata SHA-256 per Fernet AES-128-CBC, cifra at-rest password DB / token API / secret OAuth / credenziali SharePoint/FTP/SFTP | `backend/.env` | AWS SSM `/plenora/prod/PLENORA_ENCRYPTION_KEY` (SecureString) | **mai a caldo**: rotare significa re-cifrare tutti i record (`connections.password`, `api_connections.config`, `datasources.config`, `user_mfa.secret_encrypted`); richiede script di re-encryption coordinato con downtime. |
| `JWT_SECRET_KEY` | firma JWT HS256 degli access token (vedi `app/core/auth/auth.py:235` `create_access_token`) | `backend/.env` | AWS SSM `/plenora/prod/JWT_SECRET_KEY` (SecureString) | su incidente: la rotazione invalida TUTTI i token attivi → logout forzato di tutti gli utenti. Playbook: `docs/ops/jwt-incident-response.md`. |

## Secret NON presenti

Per chiarezza: certe decisioni hanno evitato di creare secret applicativi.

- **Redis auth**: il broker Celery/blacklist JWT gira in VPC privato con security-group allowlist, non ha password configurata a livello protocollo. Se in futuro esposto, aggiungere `REDIS_PASSWORD` a questa tabella.
- **SMTP credentials**: non inviamo email; nessun worker SMTP e' configurato.
- **S3 credentials**: credenziali AWS risolte tramite IAM role (EC2 instance profile in prod, `AWS_PROFILE` locale in dev). Nessun access key hardcoded o in env. Vedi `app/shared/cloud_backends.py:112` `AwsSsmSecretsBackend.__init__`.

## Architettura di storage

### Stato attuale (file-based)
1. `backend/.env` presente sulla macchina di deploy, `chmod 0600`, owner dell'unit systemd.
2. systemd unit `urby-backend.service` carica `EnvironmentFile=/opt/urby-etl/backend/.env`.
3. L'app legge `os.getenv(...)` al boot.

Limite: il file `.env` e' presente in clear sul filesystem; l'accesso e' limitato ai permessi, ma una compromissione root sul nodo espone tutti i secret simultaneamente.

### Stato target (SSM-based)
1. I secret vivono in AWS SSM Parameter Store come `SecureString` sotto prefix `/plenora/prod/`. KMS key: `alias/aws/ssm` (default account) oppure CMK dedicata (override via `SSM_KMS_KEY_ID`).
2. systemd unit usano `ExecStart=python backend/scripts/load_secrets.py <target.py>`. Il wrapper:
   - fetcha i param da SSM con `ssm:GetParametersByPath` + `WithDecryption=True`
   - popola `os.environ` con `setdefault` (env esistente vince, utile in dev)
   - `os.execv` del target → stesso PID, `Type=oneshot` pulito
   - codice: `backend/scripts/load_secrets.py`
3. L'app legge `os.getenv(...)` identico al caso precedente (zero cambio applicativo).

Vantaggi del target:
- nessun secret in clear sul nodo
- audit centralizzato (CloudTrail logga ogni `GetParameter`)
- rotazione gestita lato AWS senza toccare il file system
- IAM policy granulare per principal (backup task vs backend app possono avere scope diversi)

### Script di migrazione

`backend/scripts/migrate_env_to_ssm.py` migra i secret dal `.env` a SSM.

- **Idempotente**: read-before-write. Se la param esiste con lo stesso valore → SKIP (`already_in_sync`). Se esiste con valore diverso → WARN + SKIP, richiede `--force` esplicito (evita rotazioni accidentali).
- **Whitelist**: solo i 3 secret sopra, hardcoded in `SECRET_ENV_SPECS`. Variabili di configurazione (DB_SCHEMA, DB_POOL_MIN/MAX, UVICORN_WORKERS, ecc.) NON vengono migrate.
- **No logging del valore**: solo fingerprint SHA-256 primi 8 char (`fp=9ae6be55`) per audit cross-run senza esporre il secret.
- **Dry-run**: `--dry-run` stampa il piano (read incluso, nessuna write).
- **Permessi AWS richiesti**: `ssm:GetParameter`, `ssm:PutParameter`. La KMS key deve permettere `kms:Encrypt` al principal che esegue lo script.

Uso:

```bash
# dry-run
python backend/scripts/migrate_env_to_ssm.py --env-file backend/.env --dry-run

# migrazione iniziale (CREATE)
python backend/scripts/migrate_env_to_ssm.py --env-file backend/.env

# rotazione JWT_SECRET_KEY (il .env ha il nuovo valore)
python backend/scripts/migrate_env_to_ssm.py --env-file backend/.env --force
```

## Procedure

### Onboarding nuovo deploy
1. Creare `.env` locale con i 3 secret valorizzati.
2. `python scripts/migrate_env_to_ssm.py --env-file .env --dry-run` → verificare piano.
3. `python scripts/migrate_env_to_ssm.py --env-file .env` → CREATE.
4. Cancellare o rinominare `.env` sul nodo (tenere solo gli EnvironmentFile con param non-secret).
5. Configurare systemd unit con `load_secrets.py` wrapper.

### Rotazione `JWT_SECRET_KEY`
Vedi `docs/ops/jwt-incident-response.md`. In sintesi: (a) aggiornare valore in `.env` locale; (b) `migrate_env_to_ssm.py --force`; (c) restart `urby-backend.service`; (d) comunicare agli utenti il logout forzato.

### Rotazione `PLENORA_ENCRYPTION_KEY`
**Non supportata a caldo**. Richiede uno script one-shot di re-encryption su tutti i record cifrati (`connections.password`, `api_connections.config.auth.*`, `datasources.config.auth.*`, `user_mfa.secret_encrypted`). Pianificare con downtime. Rotazione non automatica by design: la chain di crypto e' documentata in `app/shared/crypto.py:18-31`.

### Incidente (sospetta compromissione)
1. Rotare `JWT_SECRET_KEY` immediatamente (procedura sopra).
2. Ruotare `DATABASE_URL` (password PG): coordinare con DBA, update in SSM con `--force`, restart backend + Celery + beat.
3. Se compromissione include nodo: rotare anche `PLENORA_ENCRYPTION_KEY` con script dedicato (downtime richiesto).
4. Revisione CloudTrail log `ssm:GetParameter` negli ultimi 30 giorni per identificare accessi non autorizzati.

## Audit & logging

- Accessi a SSM: CloudTrail `ssm:GetParameter` / `ssm:PutParameter` (retention standard: 90gg).
- Accessi applicativi ai secret: l'app legge `os.getenv()` al boot, non ri-legge a runtime. Un log applicativo `logger.info("Secret loaded")` non e' presente di proposito (evitare fingerprint-by-timing su scheduled task).
- Hash dei valori SSM: tenuti nei log di `migrate_env_to_ssm.py` (stdout, catturato dai deploy log) per verifica cross-run. Non reversibile.

## Riferimenti
- Source: `backend/app/shared/crypto.py` (chain chiave, Fernet)
- Source: `backend/app/shared/cloud_backends.py:46` `SecretsBackend` Protocol + AwsSsm implementation
- Source: `backend/scripts/load_secrets.py` (wrapper systemd)
- Source: `backend/scripts/migrate_env_to_ssm.py` (migration tool)
- Ops: `backend/docs/ops/jwt-incident-response.md` (playbook rotazione JWT)
- ADR: `backend/docs/architecture/decisions/0003-*.md` (Fernet crypto at-rest), `backend/docs/architecture/decisions/0004-*.md` (JWT HS256 + TTL 8h), `backend/docs/architecture/decisions/0009-*.md` (blacklist Redis)
