# Backup & Restore Runbook

Procedure operative essenziali per il sistema di backup Postgres -> S3.
Pensato per l'operatore di turno: comandi diretti, niente teoria.

> **Incidente in corso?** See [DR runbook](../../docs/ops/backup-restore.md)
> (triage scenari, restore full/selettivo, cambio bucket, escalation AWS).
> Questo doc copre operazioni di routine; il DR runbook si attiva quando
> qualcosa e' andato storto.

## Setup corrente

| Componente | Dettaglio |
|---|---|
| **Script backup** | `backend/scripts/backup_db.py` |
| **Script test restore** | `backend/scripts/test_restore.py` (#25-R1) |
| **Schedulazione backup** | `plenora-backup.timer`, daily 02:00 UTC + 5 min jitter |
| **Schedulazione test restore** | `plenora-backup-test.timer`, weekly Sun 03:00 UTC + 10 min jitter |
| **Scope dump** | Solo schema `plenora` (~12 MB compresso), non l'intero DB |
| **Bucket S3** | `plenora-backup-112011134642-eu-south-1-an` (eu-south-1) |
| **Retention** | Standard 30 gg â†’ Standard-IA (lifecycle rule AWS) â†’ delete 365 gg |
| **IAM role** | `plenora-backup-role` attached all'istanza EC2 |
| **Credenziali** | Via IAM role metadata â€” nessuna key statica |

Il test restore settimanale verifica che l'ultimo dump su S3 sia
**effettivamente ripristinabile** â€” non solo sintatticamente valido
(`pg_restore --list`). Crea un DB `plenora_restore_test_<ts>`, ci fa
pg_restore completo, query di sanity su 4 tabelle core, dropd il DB.
Senza di questo, un dump corrotto silenziosamente passa il check TOC
e si scopre il problema solo in disaster recovery.

## 1. Verificare che il backup gira

### Timer schedulato

```bash
ssh ubuntu@15.160.219.93
systemctl list-timers plenora-backup.timer
# Atteso: riga con "NEXT ... plenora-backup.timer -> plenora-backup.service"
```

Se non appare: il timer non e' enabled. Riabilita:

```bash
sudo systemctl enable --now plenora-backup.timer
```

### Ultimo run

```bash
sudo journalctl -u plenora-backup --since '24 hours ago'
# Atteso vedere: pg_dump ok / integrita' ok / upload ok / exit 0
```

### File effettivi su S3

```bash
aws s3 ls s3://plenora-backup-112011134642-eu-south-1-an/ --region eu-south-1
# Oppure dal backend (che ha permessi via IAM role):
python3 -c "
import boto3
c = boto3.client('s3', region_name='eu-south-1')
for o in c.list_objects_v2(Bucket='plenora-backup-112011134642-eu-south-1-an')['Contents']:
    print(o['Key'], o['LastModified'], o['Size']//1024//1024, 'MB')
"
```

## 2. Eseguire un backup manuale (on-demand)

### Via systemd (stesso flusso del timer, log in journalctl)

```bash
sudo systemctl start plenora-backup.service
sudo journalctl -u plenora-backup --since '1 minute ago'
```

### Via UI (superadmin only)

- Login nell'app come superadmin
- Sidebar â†’ **Backup DB**
- Clicca **"Esegui backup ora"**
- La lista si ricarica dopo ~5 secondi

### Via API (superadmin only)

```bash
curl -X POST https://<host>/api/admin/backups/trigger \
  -H "Authorization: Bearer <JWT-superadmin>"
```

## 3. Restore manuale (da S3)

**âš ï¸ Operazione distruttiva se fatta sul DB di produzione**. Esegui
sempre su un DB target separato (es. `plenora_restore_test`) e verifica
prima di puntare il backend.

### Prerequisiti sul target DB

```sql
-- Estensioni necessarie (il dump di 'plenora' non le include):
CREATE EXTENSION postgis;
CREATE EXTENSION postgis_raster;
CREATE EXTENSION postgis_topology;  -- schema dedicato 'topology'
CREATE EXTENSION h3;
CREATE EXTENSION h3_postgis;
```

### Scarica dump da S3

Dal server EC2 (IAM role ha s3:GetObject):

```bash
cd /tmp
aws s3 cp s3://plenora-backup-112011134642-eu-south-1-an/plenora_<timestamp>.dump . --region eu-south-1
# Oppure via boto3:
python3 -c "
import boto3
c = boto3.client('s3', region_name='eu-south-1')
c.download_file('plenora-backup-112011134642-eu-south-1-an', 'plenora_<timestamp>.dump', '/tmp/plenora.dump')
"
```

Se il dump e' in Glacier Deep Archive (>30 gg): serve prima un restore
request (12-48h attesa) via `aws s3api restore-object` â€” vedi AWS docs.

### Verifica integrita' del dump

```bash
pg_restore --list /tmp/plenora.dump | head -20
# Atteso: lista entry TOC (tabelle, sequenze, indici, ecc.)
```

### Restore sul target

Le credenziali DATABASE_URL vivono su AWS SSM (post C1). Dal server
EC2 (IAM role ha gia' i permessi SSM):

```bash
# 1. Leggi DATABASE_URL dalla fonte di verita' (NON da .env)
DATABASE_URL=$(aws ssm get-parameter \
  --name /plenora/prod/DATABASE_URL \
  --with-decryption --region eu-south-1 \
  --query 'Parameter.Value' --output text)

# 2. Componi TARGET_URL sostituendo il DB name di produzione (`plenora`)
#    con un nome temporaneo (`plenora_restore_test`). Host/user/password restano.
TARGET_URL="${DATABASE_URL%/*}/plenora_restore_test"

# 3. Crea il DB target (serve CREATEDB sull'utente â€” vedi test_restore.py)
ADMIN_URL="${DATABASE_URL%/*}/postgres"
psql "$ADMIN_URL" -c 'CREATE DATABASE plenora_restore_test'

# 4. Restore
pg_restore \
  --dbname "$TARGET_URL" \
  --no-owner --no-privileges \
  --verbose \
  /tmp/plenora.dump
```

> **Scorciatoia automatizzata**: se lo scopo e' solo verificare che il
> dump piu' recente sia ripristinabile (non serve tenere il DB per
> ispezione), il timer `plenora-backup-test.timer` fa tutto weekly in
> automatico. Trigger manuale:
> `sudo systemctl start plenora-backup-test.service` (vedi
> `deploy/systemd/README.md`).

### Verifica post-restore

```sql
-- Dal target DB
SELECT COUNT(*) FROM plenora.users;
SELECT COUNT(*) FROM plenora.projects;
SELECT COUNT(*) FROM plenora.connections;
SELECT MAX(updated_at) FROM plenora.users;
```

## 4. Troubleshooting

### Il backup notturno non parte

```bash
# Verifica timer attivo
systemctl is-active plenora-backup.timer   # atteso: active

# Verifica prossimo trigger
systemctl list-timers plenora-backup.timer

# Verifica last run
systemctl status plenora-backup.service --no-pager
```

Se `Result=failed`:

```bash
sudo journalctl -u plenora-backup --since '24 hours ago' --no-pager | tail -30
```

Cause tipiche:
- **pg_dump version mismatch** con server Postgres â†’ reinstalla client giusto
  (vedi docstring di `scripts/backup_db.py` per repo PGDG)
- **DATABASE_URL mancante o invalida** â†’ il service lo legge da AWS SSM
  (`/plenora/prod/DATABASE_URL`), NON da `.env`. Verifica con:
  ```bash
  aws ssm get-parameter --name /plenora/prod/DATABASE_URL \
    --with-decryption --region eu-south-1
  ```
  Se ritorna `ParameterNotFound`: popola (vedi `deploy/iam/README.md`).
  Se `AccessDeniedException`: il role EC2 non ha la policy SSM read
  (vedi `deploy/iam/plenora-backup-ssm-read.json`).
- **S3 upload 403** â†’ IAM role detached o policy modificata; riattacca
  `plenora-backup-role` all'istanza in AWS Console

### pg_dump fallisce per version mismatch

```
error: aborting because of server version mismatch
server version: 17.9
pg_dump version: 16.x
```

Installa client 17:

```bash
sudo sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
sudo apt update
sudo apt install -y postgresql-client-17
pg_dump --version   # atteso: 17.x
```

### IAM role lockato / S3 403

Se il backup e l'API UI ricevono 403 su S3:

1. Console AWS â†’ EC2 â†’ Instance â†’ Actions â†’ Security â†’ Modify IAM role
2. Riattacca `plenora-backup-role`
3. Se il role e' stato cancellato: ricrealo con:
   - policy SSM read: `deploy/iam/plenora-backup-ssm-read.json`
   - policy S3 (PutObject/GetObject/ListBucket sul bucket backup â€”
     non committata nel repo, scrivi a mano in Console limitata al
     bucket specifico)
4. Se i permessi sono insufficienti sul bucket (es. policy bucket
   cambiata): verifica ACL bucket via console

### Bucket cambio (raro)

Se il bucket S3 viene rinominato/sostituito:

1. Aggiorna `BACKUP_BUCKET` (cross-provider) nei systemd unit
   `deploy/systemd/plenora-backup*.service`.
2. Aggiorna policy IAM del role per il nuovo ARN
3. Ridistribuisci lifecycle rule sul nuovo bucket
4. Verifica con `sudo systemctl start plenora-backup.service`

## 5. Miglioramenti opzionali

- **Alerting su fallimento**: oggi nessuno viene avvertito se il backup
  fallisce (solo log journalctl). Da chiudere: systemd `OnFailure=`
  gia' commentato in `plenora-backup.service` e `plenora-backup-test.service`,
  serve una unit notification (es. `plenora-backup-notify.service` che
  fa `curl` su webhook Slack/SMTP) e decommentare gli `OnFailure=`.
- **UI admin: trigger restore test + history**: oggi la pagina Backup
  DB admin mostra lo storico backup. Aggiungere button "Verifica
  ultimo backup (test restore)" + ultima verifica status. Pattern
  equivalente a "Esegui backup ora" esistente. Stima ~2.5h (vedi nota
  in chat del 2026-04-17 opzioni A/B/C).
- **Secret migration full-stack**: uvicorn/worker Dramatiq leggono ancora da
  `backend/.env`. Sostituire con lo stesso pattern C1 (wrapper
  `load_secrets.py` su SSM) per eliminare `.env` completamente dal
  server. Stima ~2-3h.

