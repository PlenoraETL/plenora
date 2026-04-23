# systemd unit files

Unit file systemd per il backup automatico del DB su S3 + verifica
restore settimanale. Specchio di quelli installati su
`/etc/systemd/system/` nell'istanza EC2 di produzione. Committati per
disaster recovery: se si perde il server o si migra region, questi file
riportano il servizio allo stato live senza riscriverli a memoria.

## Rebrand urby-* → plenora-* (completato 2026-04-18)

Migrazione server eseguita via `deploy/migrations/2026-04-18_rename-urby-to-plenora.sh`:
- Directory: `/home/ubuntu/urby-etl` → `/home/ubuntu/plenora-etl`
- Unit installate: `plenora-backend`, `plenora-celery`, `plenora-beat`,
  `plenora-backup{,.timer}`, `plenora-backup-test{,.timer}`
- Backup dei vecchi unit file: `/etc/systemd/system/*.pre-plenora-rename.bak`
  (safe da rimuovere dopo qualche giorno di stabilita')
- Venv shebangs aggiornati (19 script + `pyvenv.cfg`)

Residui noti:
- `plenora-beat.service` invoca ancora la classe `UrbyBeatScheduler` tramite
  l'alias retro-compat in `workers/beat_scheduler.py`. Quando si aggiornera'
  il service a `PlenoraBeatScheduler` l'alias puo' essere rimosso.
- Le `Description=` nei 3 unit installati sul server dicono ancora
  "URBY-ETL ..." (il `sed` del migration non le ha intercettate). I file
  nel repo hanno Description corretta; la prossima re-install allinea.

## Modello credenziali (C1) + astrazione cloud

I service **oneshot di backup** (`plenora-backup`, `plenora-backup-test`)
NON dipendono da `backend/.env`. I secret (`DATABASE_URL`) vivono in un
SecretsBackend configurabile:
  - AWS SSM Parameter Store su `/plenora/prod/*` (SecureString) — default
  - Azure Key Vault (future, `CLOUD_PROVIDER=azure`)
  - GCP Secret Manager (future, `CLOUD_PROVIDER=gcp`)

Il dispatch e' nella factory `app/shared/cloud_backends.py` — i consumer
(`backup_db.py`, `test_restore.py`, `load_secrets.py`) parlano solo al
Protocol `SecretsBackend` / `ObjectStorage`, mai direttamente a boto3.

I service **long-running** (`plenora-backend`, `plenora-celery`,
`plenora-beat`) INVECE usano `EnvironmentFile=/home/ubuntu/plenora-etl/backend/.env`
per la configurazione runtime — inclusa `PLENORA_ENCRYPTION_KEY`.
Migrazione SSM anche per questi service = scope futuro (vedi ADR-0003
§Consequences e `runtime_config_audit.md §4`).
Migrazione cross-cloud: aggiungi una classe nel file `cloud_backends.py`
+ aggiorna la IAM policy del nuovo cloud, zero change negli altri file.

Ogni `ExecStart=` invoca lo wrapper `scripts/load_secrets.py` che:

1. Fetcha i parametri via il backend configurato
2. Popola `os.environ` (setdefault: env pre-esistente ha priorita')
3. `os.execv` il target script (backup_db.py o test_restore.py)

systemd `Type=oneshot` vede lo stesso PID dopo `execv` e gestisce
correttamente l'exit code.

Prerequisito IAM sul role EC2 (`plenora-backup-role`): permessi
`ssm:GetParametersByPath` + `kms:Decrypt` condizionato
`kms:ViaService: ssm.<region>.amazonaws.com`. Policy JSON e istruzioni
one-shot in `backend/deploy/iam/`.

## File

- `plenora-backend.service` — long-running uvicorn (FastAPI), porta 5004
  locale. Restart=always.
- `plenora-celery.service` — long-running Celery worker, concurrency=4.
- `plenora-beat.service` — Celery beat scheduler (schedule dal DB).
- `plenora-backup.service` — oneshot. Invoca `backup_db.py` via wrapper
  SSM. Timeout 2h.
- `plenora-backup.timer` — schedula il service ogni giorno alle 02:00 UTC
  con `RandomizedDelaySec=300` (jitter 5 min). `Persistent=true`
  recupera il run al boot se il server era spento.
- `plenora-backup-test.service` — oneshot. Invoca `test_restore.py` via
  wrapper SSM (stesso modello credenziali): download ultimo dump,
  `CREATE DATABASE` usa-e-getta, `pg_restore` completo, sanity count
  su tabelle core, `DROP DATABASE`. Timeout 2h. Senza questo, il
  backup nightly verifica solo il TOC del dump e non prova un restore
  reale (#25-R1 / "un backup mai ripristinato non e' un backup").
- `plenora-backup-test.timer` — weekly domenica 03:00 UTC, dopo che il
  dump delle 02:00 UTC e' su S3. `Persistent=true`,
  `RandomizedDelaySec=600`.

### Alerting su failure (opzionale, non attivo)

I service hanno `OnFailure=` commentato. Per ricevere un alert
(Slack/SMTP) su backup o restore failure: crea una unit notification
(es. `plenora-backup-notify.service` che fa `curl` su un webhook Slack) e
decommenta l'`OnFailure=` in `.service`. Attualmente gli errori
finiscono solo nel journal
(`journalctl -u plenora-backup -u plenora-backup-test`).

## Install su nuovo server

Prerequisiti:
- `/home/ubuntu/plenora-etl/backend/` con `scripts/backup_db.py`,
  `scripts/test_restore.py`, `scripts/load_secrets.py`
- `/usr/bin/python3` con `boto3` installato (stesso venv dei backup)
- `pg_dump`, `pg_restore`, `psql` versione compatibile col server
  Postgres nel PATH
- Istanza EC2 con IAM role che ha:
  - `s3:PutObject` + `s3:GetObject` + `s3:ListBucket` sul bucket backup
  - `ssm:GetParametersByPath` su `/plenora/prod/*` + `kms:Decrypt`
    (vedi `backend/deploy/iam/plenora-backup-ssm-read.json`)
- SSM Parameter Store popolato con `/plenora/prod/DATABASE_URL`
  come SecureString

Install:

```bash
# 1. Copia gli unit file (backup nightly + test restore weekly +
#    backend/celery/beat long-running)
sudo cp plenora-backup.service plenora-backup.timer \
        plenora-backup-test.service plenora-backup-test.timer \
        plenora-backend.service plenora-celery.service plenora-beat.service \
        /etc/systemd/system/

# 2. Reload systemd
sudo systemctl daemon-reload

# 3. Abilita timer + long-running
sudo systemctl enable --now plenora-backup.timer plenora-backup-test.timer
sudo systemctl enable --now plenora-backend.service plenora-celery.service plenora-beat.service

# 4. Verifica
systemctl list-timers plenora-backup.timer plenora-backup-test.timer
systemctl status plenora-backend plenora-celery plenora-beat
```

Prerequisito aggiuntivo per `plenora-backup-test`: l'utente Postgres in
`DATABASE_URL` deve avere `CREATEDB`. Verifica:

```bash
# Nota: da shell interattiva DATABASE_URL non e' in env (e' su SSM).
# Esportalo dal param store per il check manuale:
export DATABASE_URL=$(aws ssm get-parameter \
  --name /plenora/prod/DATABASE_URL \
  --with-decryption --region eu-south-1 \
  --query 'Parameter.Value' --output text)
psql "$DATABASE_URL" -c "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user;"
# Output atteso: rolcreatedb = t
```

Se manca, `GRANT CREATE` su DB non basta — serve attributo ruolo:
```sql
ALTER ROLE <username> CREATEDB;
```

## Test manuale (post-install)

```bash
# Daily backup
sudo systemctl start plenora-backup.service
sudo journalctl -u plenora-backup.service --since '5 minutes ago' --no-pager

# Weekly test restore
sudo systemctl start plenora-backup-test.service
sudo journalctl -u plenora-backup-test.service --since '5 minutes ago' --no-pager
```

Output atteso (backup): SSM fetch → pg_dump → integrity ok → upload ok → exit 0.
Output atteso (test-restore): SSM fetch → download → createdb → pg_restore ok →
sanity ok → dropdb → exit 0.

## Modifiche future

Se aggiorni i file qui nel repo, dopo il deploy sincronizza sul server:

```bash
ssh ubuntu@<host>
cd /home/ubuntu/plenora-etl/backend/deploy/systemd
sudo cp plenora-backup.service plenora-backup.timer \
        plenora-backup-test.service plenora-backup-test.timer \
        plenora-backend.service plenora-celery.service plenora-beat.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
# Se il timer era gia' attivo, daemon-reload basta.
# Se hai modificato solo il .service, non serve ricaricare il .timer.
```
