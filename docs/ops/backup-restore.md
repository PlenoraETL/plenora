# Disaster Recovery — Backup & Restore

Runbook per incidenti che richiedono ripristino del DB di produzione,
cambio bucket S3, o escalation AWS. Pensato per l'operatore di turno
alle 3 del mattino: comandi concreti, decisioni chiare, zero teoria.

Se stai cercando operazioni di routine (backup manuale, verifica timer,
troubleshooting tipico), vai su
[backup-runbook.md](../../backend/deploy/backup-runbook.md).
Questo documento si attiva quando **qualcosa e' andato storto**.

---

## 0. Stato del sistema — riferimenti rapidi

| Componente | Dettaglio | Doc |
|---|---|---|
| DB prod | Postgres su `15.161.59.22:5432/plenora`, schema `plenora` | DATABASE_URL su SSM |
| Backup | Daily 02:00 UTC via systemd, dump `--schema=plenora` (schema + dati), non full-DB | `deploy/backup-runbook.md` |
| Test restore | Weekly Sun 03:00 UTC verifica restorability | `deploy/systemd/README.md` |
| Bucket S3 | `plenora-backup-112011134642-eu-south-1-an` (eu-south-1) | — |
| Retention S3 | Standard 30gg → Standard-IA → delete 365gg | lifecycle rule AWS |
| IAM role EC2 | `plenora-backup-role` (account 112011134642) | `deploy/iam/` |
| Secrets | AWS SSM `/plenora/prod/*` SecureString | `deploy/iam/README.md` |

**Punto critico a sapere a memoria**: il backup dump e' **scoped allo
schema applicativo `plenora` via `--schema=plenora` (schema + dati),
non full-DB**. Il target di restore deve avere le estensioni gia'
installate: `postgis`, `postgis_raster`, `postgis_topology`, `h3`,
`h3_postgis`.

---

## 1. Triage — decidi lo scenario

Pima di lanciare qualsiasi comando, capisci **cosa** e' successo:

```
Il DB prod risponde?
├── NO → scenario A (DB totalmente perso o unreachable)
└── SI
    ├── Dati sbagliati/corrotti?
    │   ├── Tutti → scenario A
    │   └── Alcuni → scenario B (restore selettivo)
    └── Solo backup fallisce (prod OK)?
        ├── Timer non parte → scenario C
        └── S3 403/errore → scenario D (bucket/IAM)
```

Scenari dettagliati sotto. Ogni playbook finisce con un **checkpoint
di verifica** che conferma il ripristino.

---

## 2. Scenario A — DB prod da ricostruire da zero

Caso: il DB non risponde, o i dati sono completamente compromessi,
o stai migrando a una nuova istanza.

### A.1 — Precondizioni

1. Hai accesso SSH al server EC2 (o puoi provisionarne uno nuovo)
2. IAM role `plenora-backup-role` attaccato all'istanza (policy
   S3 read + SSM read)
3. Estensioni Postgres disponibili sul target (postgis + h3)
4. Nuovo DB name / host deciso (se stai ricreando, non sovrascrivi)

### A.2 — Download dell'ultimo dump valido

Dal server EC2:

```bash
# Lista dump disponibili, ordina per data
aws s3 ls s3://plenora-backup-112011134642-eu-south-1-an/ \
  --region eu-south-1 | sort -k1,2 | tail -10

# Scarica il piu' recente (sostituisci <timestamp>)
cd /tmp
aws s3 cp s3://plenora-backup-112011134642-eu-south-1-an/plenora_<timestamp>.dump . \
  --region eu-south-1
```

Se il dump e' in **Glacier Deep Archive** (>30 giorni): serve un
restore request con 12-48h di attesa:

```bash
aws s3api restore-object \
  --bucket plenora-backup-112011134642-eu-south-1-an \
  --key plenora_<timestamp>.dump \
  --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Expedited"}}' \
  --region eu-south-1
# Tier=Expedited: 1-5 min (costa di piu'); Standard: 3-5 ore
```

Se non trovi dump < 24h recenti → il backup notturno ha fallito per N
giorni. Vedi scenario C.

### A.3 — Crea il DB target

```bash
# Leggi DATABASE_URL corrente da SSM per riusare host/user/pw
DATABASE_URL=$(aws ssm get-parameter \
  --name /plenora/prod/DATABASE_URL \
  --with-decryption --region eu-south-1 \
  --query 'Parameter.Value' --output text)

# Admin URL: punta al DB 'postgres' per poter fare CREATE DATABASE
ADMIN_URL="${DATABASE_URL%/*}/postgres"

# Nuovo DB target (es. 'plenora_restored' per non rischiare il nome prod)
psql "$ADMIN_URL" -c 'CREATE DATABASE plenora_restored'

# Installa estensioni prima del restore (il dump non le include)
TARGET_URL="${DATABASE_URL%/*}/plenora_restored"
psql "$TARGET_URL" <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS h3;
CREATE EXTENSION IF NOT EXISTS h3_postgis;
SQL
```

### A.4 — Restore

```bash
pg_restore \
  --dbname "$TARGET_URL" \
  --no-owner --no-privileges \
  --exit-on-error \
  --verbose \
  /tmp/plenora_<timestamp>.dump
```

Tempo atteso: ~10-60s per schema `plenora` da 12 MB (aggiornare se
cresce).

### A.5 — Verifica post-restore

```bash
psql "$TARGET_URL" <<'SQL'
SELECT 'users' AS t, COUNT(*) FROM plenora.users
UNION ALL SELECT 'projects', COUNT(*) FROM plenora.projects
UNION ALL SELECT 'connections', COUNT(*) FROM plenora.connections
UNION ALL SELECT 'schedules', COUNT(*) FROM plenora.schedules;
SQL
```

Controlla che i count siano plausibili (non-zero dove atteso).

### A.6 — Punta il backend al nuovo DB

```bash
# Aggiorna SSM con il nuovo URL
aws ssm put-parameter \
  --name /plenora/prod/DATABASE_URL \
  --value "${DATABASE_URL%/*}/plenora_restored" \
  --type SecureString --overwrite --region eu-south-1

# Restart dei service applicativi perche' rileggano DATABASE_URL
# (uvicorn/celery leggono ancora da .env — aggiorna anche li' fino a
# quando non chiudiamo il migration pending full-stack)
ssh ubuntu@15.160.219.93 "
  sudo sed -i 's|DATABASE_URL=.*|DATABASE_URL=${DATABASE_URL%/*}/plenora_restored|' \
    /home/ubuntu/plenora-etl/backend/.env
  sudo systemctl restart plenora-backend plenora-celery
"
```

### A.7 — Smoke test prod

1. Login come utente reale: `curl -X POST .../auth/login` con credenziali note
2. Lista progetti: deve tornare i progetti attesi
3. Esegui una pipeline piccola: deve completare senza errori
4. Controlla logs per errori: `journalctl -u plenora-backend --since '5 min ago'`

**Checkpoint**: backend risponde, login ok, pipeline ok. **Fine scenario A**.

---

## 3. Scenario B — Restore selettivo (alcuni dati persi)

Caso: il DB prod e' up ma alcune tabelle sono state troncate per
errore, o una migrazione ha corrotto colonne specifiche.

**Strategia**: restore su un DB temporaneo, poi copia selettiva dei
dati mancanti da temp → prod.

### B.1 — Restore su temp DB

Segui A.2 + A.3 + A.4, ma chiama il DB `plenora_temp_restore_<data>`.

### B.2 — Copia selettiva

Esempio: tabella `users` e' stata troncata per errore in prod.

```bash
TEMP_URL="${DATABASE_URL%/*}/plenora_temp_restore_20260417"
PROD_URL="$DATABASE_URL"

# Dump solo users da temp, restore su prod (ATTENZIONE: valuta FK)
pg_dump --dbname "$TEMP_URL" \
  --data-only --table=plenora.users \
  --format=custom --file=/tmp/users.dump

# Check: pre-esistente su prod?
psql "$PROD_URL" -c "SELECT COUNT(*) FROM plenora.users"

# Se serve truncate (NO se FK attive) o merge selettivo via UPSERT:
# pg_restore --data-only fa INSERT — su PK duplicate fallisce.
# Decisione caso per caso.
```

**ATTENZIONE**: questa operazione e' **rischiosa**. Se la tabella ha
FK in uscita (altre tabelle la referenziano), truncate/insert cascade
puo' cancellare dati validi. Se non sei sicuro:

1. Stoppa i worker Celery (niente scritture concorrenti)
2. Backup fresh di prod PRIMA di toccarla
3. Lavora in transazione esplicita (`BEGIN; ... COMMIT/ROLLBACK`)
4. Escalation a DBA se disponibile

**Checkpoint**: conteggi post-restore selettivo = attesi, FK intatte,
pipeline di prod continua a girare.

---

## 4. Scenario C — Backup notturno fallisce da giorni

Caso: il timer gira ma ogni run fallisce. Rischio crescente: giorno
dopo giorno l'RPO si allarga.

### C.1 — Diagnostica immediata

```bash
ssh ubuntu@15.160.219.93
sudo journalctl -u plenora-backup.service --since '7 days ago' --no-pager | \
  grep -E "(ERROR|CRASH|Failed)" | tail -20
```

Confronta con check checklist di `deploy/backup-runbook.md` sezione
Troubleshooting. Le cause tipiche sono:

- pg_dump version mismatch con server Postgres (upgrade server Postgres senza client update)
- IAM role detached dall'istanza
- SSM permission persa (check `aws ssm get-parameter` dal server)
- Disco pieno (dump temp file non scrivibile)

Fix il problema, poi:

```bash
sudo systemctl start plenora-backup.service
# Verifica che ora funzioni
```

### C.2 — Se l'ultimo dump e' >7 giorni

Il rischio e' reale: non hai un RPO rispettabile. Opzioni:

1. **Se riesci a fixare il backup e il prod e' OK**: fai un backup
   manuale ORA e vai avanti.
2. **Se il prod e' anche compromesso**: vai a scenario A col dump
   vecchio (RPO = eta' dump), accetta la perdita dati recenti.
3. **Se il dump piu' recente e' in Glacier (>30gg)**: A.2 restore
   request, aspetta 12-48h, poi scenario A.

---

## 5. Scenario D — Bucket S3 inaccessibile / compromesso

Caso: `aws s3 ls` ritorna 403/AccessDenied, o il bucket e' stato
cancellato per errore, o (peggio) c'e' sospetto di compromise.

### D.1 — Verifica rapida

```bash
# Dal server EC2 (ha IAM role):
aws s3 ls s3://plenora-backup-112011134642-eu-south-1-an/ \
  --region eu-south-1 2>&1 | head

# Dallo stesso server, check identity:
aws sts get-caller-identity
# Verifica che Arn punti ancora a plenora-backup-role
```

Se `AccessDenied` da `sts` → il role e' stato detached. Riattacca in
Console AWS (EC2 → Instance → Security → Modify IAM role).

Se `sts` ok ma `s3 ls` denied → policy bucket cambiata o role policy
alterata. Vedi `deploy/iam/README.md` per ripristinare.

### D.2 — Bucket cancellato per errore

**Importante**: se hai Bucket Versioning attivo e un cancellato entro
90 giorni, AWS permette il recovery via Object Lock / versioni
precedenti. **Fallo IMMEDIATAMENTE** prima che la finestra si chiuda:

1. Console AWS → S3 → Deleted objects → Restore
2. Se versioning non attivo → i dump sono **persi**. Vai a scenario A
   dal dump piu' recente che riesci a trovare (dev locale? backup
   manuale offline? colleghi?).

### D.3 — Sospetto di compromise

1. **NON** scaricare dump dal bucket senza verificare integrita'
2. Ruota le credenziali: `aws ssm put-parameter /plenora/prod/DATABASE_URL`
   con una nuova password (richiedi un reset al DBA prima)
3. Invalida il ruolo compromesso:
   ```bash
   aws iam delete-role-policy --role-name plenora-backup-role \
     --policy-name <policy-in-sospetto>
   ```
4. Escalation a AWS Security — vedi sezione 7

---

## 6. Cambio bucket (pianificato, non emergenza)

Se ti serve migrare su un bucket diverso (nuova regione, nuovo account,
rename) **senza** perdere storicita':

### Steps

1. **Crea nuovo bucket** in Console AWS con lifecycle rule identica
   (Standard 30gg → Standard-IA → delete 365gg), SSE-S3 attivo,
   versioning ON, block public access ON.
2. **Copia dump esistenti** dal vecchio al nuovo:
   ```bash
   aws s3 sync \
     s3://plenora-backup-112011134642-eu-south-1-an/ \
     s3://<nuovo-bucket>/ \
     --region eu-south-1
   ```
3. **Aggiorna IAM policy** del role per includere il nuovo bucket ARN
   (puo' restare anche il vecchio durante la transizione — due
   resource ARN nella stessa policy S3).
4. **Aggiorna env** nei systemd unit:
   - `deploy/systemd/plenora-backup.service` → `Environment=BACKUP_BUCKET=<nuovo>`
   - `deploy/systemd/plenora-backup-test.service` → idem
   - `git commit + push`, poi `sudo cp` sul server, `daemon-reload`
5. **Smoke test**: `sudo systemctl start plenora-backup.service` → verifica
   upload sul nuovo bucket. `sudo systemctl start plenora-backup-test.service`
   → verifica restore dal nuovo bucket.
6. **Cancella il vecchio bucket** solo dopo che il test weekly e' passato
   almeno una volta sul nuovo.

---

## 7. Escalation AWS

### Livelli di supporto AWS

Il piano di supporto dell'account determina SLA:
- **Basic** (default): no support tecnico, solo billing
- **Developer** ($29/mese): email, 12h SLA generale
- **Business** ($100/mese o 3% spesa): 1h SLA se production-down
- **Enterprise**: 15min SLA, TAM dedicato

Check account corrente:
```bash
aws support describe-trusted-advisor-checks --language en 2>&1 | head
# Se AccessDeniedException → piano Basic, no support API disponibile
```

### Quando escalare

| Situazione | Azione |
|---|---|
| Bucket cancellato, versioning off, dati critici persi | **P0** — contatta Business/Enterprise support via Console AWS → Support Center → Create case → **"Production system down"** |
| Role IAM compromesso, possibile intrusione | **P0** — AWS Security: `aws-security@amazon.com` + Case support urgente |
| Region outage (servizi AWS non rispondono) | **P1** — check `status.aws.amazon.com`; nessuna action se outage generale, aspetta il ripristino AWS |
| Quote SSM/S3 raggiunte | **P2** — Service Quotas request via Console |

### Contatti interni

Stato 2026-04-17: tutti i ruoli sono coperti da **Marco Bonamente**
(single point of contact — quando il team cresce, sostituire
qui con i nuovi owner).

| Ruolo | Contatto | Quando chiamare |
|---|---|---|
| DBA principal | Marco Bonamente — `+39 338 492 5661` / `marco.bonamente@gmail.com` | Restore selettivo rischioso, corruption |
| AWS admin account | Marco Bonamente — `+39 338 492 5661` / `marco.bonamente@gmail.com` | IAM changes, policy modifiche, support case |
| Product owner | Marco Bonamente — `+39 338 492 5661` / `marco.bonamente@gmail.com` | Comunicazione verso utenti in caso di downtime |

Per emergenze P0 (production-down, compromise sospetto): **telefonare**
direttamente, non email — l'email puo' non essere vista in tempo utile.

---

## 8. Test DR (consigliato trimestrale)

Un DR plan che non viene mai testato non e' un DR plan. Ogni 3 mesi
eseguire il drill end-to-end seguendo il playbook dedicato:

- Procedura ripetibile: [`dr_drill_playbook.md`](dr_drill_playbook.md)
- Log esecuzioni: [`dr_drill_log.md`](dr_drill_log.md)

Il playbook copre: download dump da S3, restore su DB vergine, verifica
estensioni (postgis/h3), smoke applicativo (login + healthcheck),
misurazione RPO/RTO reali.

### Target RPO / RTO dichiarati

| Metrica | Target | Razionale |
|---|---|---|
| **RPO** | ≤ 24h | Backup giornaliero `plenora-backup.timer` alle 02:00 UTC |
| **RTO** | ≤ 2h | dump `--schema=plenora` ~12 MB (schema + dati, non full-DB), restore osservato 10-60s + buffer per estensioni + smoke |

Soglie di alert: `RPO_measured > 36h` o `RTO_measured > 2h` apre P1
(vedi [`incident_response.md §1`](incident_response.md)).

Il timer weekly `plenora-backup-test.timer` copre il **restore
restorability** automaticamente, ma NON il full DR (non restarta
backend, non aggiorna SSM, ecc.). E' un subset, utile ma non
sostitutivo del drill trimestrale.

---

## 9. Post-DR checklist

Dopo ogni intervento DR:

- [ ] Backend prod rispondente (HTTP 200 su `/health`)
- [ ] Login funzionante (test con utente reale)
- [ ] Celery worker attivo (`systemctl status plenora-celery`)
- [ ] Beat scheduler attivo (`systemctl status plenora-beat`)
- [ ] Timer backup riattivo (`systemctl list-timers plenora-backup.timer`)
- [ ] Un backup fresco su S3 (manuale via `sudo systemctl start plenora-backup.service`)
- [ ] Stakeholder informati dello stato ripristinato
- [ ] Post-mortem scritto (cosa e' successo, come l'abbiamo fixato,
      cosa cambiamo per prevenire)
- [ ] Follow-up items creati dove il DR ha rivelato gap

---

## Riferimenti

- Operazioni normali: [backup-runbook.md](../../backend/deploy/backup-runbook.md)
- DR drill ripetibile: [dr_drill_playbook.md](dr_drill_playbook.md)
- DR drill log: [dr_drill_log.md](dr_drill_log.md)
- Incident response generale: [incident_response.md](incident_response.md)
- Unit systemd + install: [deploy/systemd/README.md](../../backend/deploy/systemd/README.md)
- IAM policies + SSM setup: [deploy/iam/README.md](../../backend/deploy/iam/README.md)
- Script di backup: [scripts/backup_db.py](../../backend/scripts/backup_db.py)
- Script di verifica restore: [scripts/test_restore.py](../../backend/scripts/test_restore.py)
- Astrazione cloud: [app/shared/cloud_backends.py](../../backend/app/shared/cloud_backends.py)
