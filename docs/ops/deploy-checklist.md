# Deploy checklist

Checklist operativa pre/post deploy + rollback manuale minimo.

Riferimenti:
- `incident_response.md` — incident framework generale
- `runbook.md` — procedure recovery operative
- `backup-restore.md` — DB backup/restore
- `dr_drill_playbook.md` — DR drill formale

Il deploy attuale usa `deploy.sh` come wrapper parametrico
multi-environment (vedi `deploy/profiles/<name>.conf`). Default profilo
`prod`. Tutte le configurazioni per-environment sono nel profilo
versionato; secret operativi (PEM, AWS) restano per-operatore via env.

## 1. Pre-deploy

### 1.1 Stato locale

- [ ] Working tree clean: `git status` non mostra modifiche o file untracked
- [ ] Su branch corretto: `git rev-parse --abbrev-ref HEAD` = `main` (o branch specifico per profilo non-prod)
- [ ] Commit pushed: `git rev-parse HEAD` = `git rev-parse @{u}`
- [ ] Branch sincronizzato con remote: `git fetch && git status` riporta "up to date"

### 1.2 Test locale

- [ ] Backend pytest verde: `cd backend && pytest tests2/ -c tests2/pytest.ini`
- [ ] Frontend vitest verde: `cd frontend && npm test`
- [ ] (opzionale) B-2 functional manual run via `gh workflow run ci-functional.yml`
  pre-merge per cambi rilevanti su `app/core/arricchisci/`,
  `app/core/scheduler/`, `app/db/`

### 1.3 Pre-flight remoto (read-only)

- [ ] Server raggiungibile: SSH check (no command, solo connectivity)
- [ ] `/health` 200 OK pre-deploy (baseline)
- [ ] `/ready` 200 OK pre-deploy (baseline)
- [ ] Disco libero sul server: `df -h` riporta >=20% libero su mount applicativo
- [ ] PG raggiungibile via app: `/ready` include PG check
- [ ] Redis raggiungibile via app: `/ready` include broker check

### 1.4 Stato infra

- [ ] AWS S3 bucket reachable (object storage default): nessun alarm CloudWatch
- [ ] BACKUP_BUCKET reachable: ultimo backup recente (<24h) presente
- [ ] Nessun incident attivo segnalato in `incident_response.md`

### 1.5 Comunicazione

- [ ] Window di change comunicato: chi deploya + ora inizio + ETA + fallback contact
- [ ] Pause-the-traffic note (se applicabile a release ad alto rischio)

## 2. Esecuzione

### 2.1 Modalità preliminare check-only (raccomandata)

Pre-flight senza side-effect:
```sh
DRY_RUN=1 ./deploy.sh backend
# oppure
./deploy.sh backend --check-only
```

Output atteso: log "Check-only mode" + verifica branch/commit/profile/PEM/health URL/SSH.
Niente push, niente restart, niente modifica server-side.

### 2.2 Deploy effettivo

```sh
./deploy.sh backend
```

Aspettative durante esecuzione:
- Nessun prompt interattivo (script non-interactive)
- Log line `Health gate OK` post-restart
- Exit code 0

Se exit != 0: NON proseguire con post-deploy. Vedi sezione 4 (rollback) +
`runbook.md` (deploy.sh failure modes).

## 3. Post-deploy

### 3.1 Health endpoints

- [ ] `/health` ritorna 200 OK (subito dopo restart)
- [ ] `/ready` ritorna 200 OK (potrebbe richiedere 30-60s per PG pool warm)
- [ ] `/ready` body include `db: ok`, `redis: ok`, schema migrations ok

### 3.2 Servizi systemd

- [ ] `systemctl status plenora-backend` = active (running)
- [ ] `systemctl status plenora-celery` = active (running)
- [ ] `systemctl status plenora-beat` = active (running)
- [ ] Niente "failed" o "activating" residuo
- [ ] Uptime servizi consistente con timestamp deploy

### 3.3 Smoke funzionale

- [ ] Login real-user sull'UI (admin test account) → arrivo a homepage
- [ ] 1 task POST: triggerare un job task piccolo (es. estrai preview piccolo)
- [ ] 1 GET status: verificare polling task arrivato a `success` o `failure` coerente
- [ ] Niente 500 errors visibili in UI durante smoke

### 3.4 Frontend

- [ ] URL frontend accessibile (200 OK al root)
- [ ] Console browser: no JS errors al primo load (open DevTools console)
- [ ] Login + navigazione + logout flow OK
- [ ] Network tab: nessun fetch 5xx persistente

### 3.5 Logs / observability

- [ ] `journalctl -u plenora-backend -n 50` non mostra `[CRASH]` né exceptions ripetute
- [ ] `journalctl -u plenora-celery -n 50` worker registrato + task consumed
- [ ] `journalctl -u plenora-beat -n 50` schedule reload + leader election OK
- [ ] `/api/admin/system-status` (se accesso admin) mostra metriche coerenti

### 3.6 Backup health

- [ ] `backup_runs` ultima riga `success=true` recente (<24h)
- [ ] (settimanale) DR drill o test_restore.py giro corretto

### 3.7 Comunicazione closure

- [ ] Window di change chiuso ufficialmente
- [ ] Eventuali anomalie post-deploy registrate in `incident_response.md`

## 4. Rollback manuale minimo

### 4.1 Backend rollback

Pre-condizione: identificare commit/tag stabile precedente.

```sh
# 1. Pull commit stabile precedente
ssh -i $PLENORA_DEPLOY_PEM <user>@<server> \
    'cd <REMOTE_BACKEND_PATH> && git fetch && git checkout <prev-tag-or-commit>'

# 2. Re-install dipendenze (solo se requirements cambiati)
ssh ... 'cd <path> && pip install -r requirements.txt --quiet'

# 3. Restart servizi
ssh ... 'sudo systemctl restart plenora-backend plenora-celery plenora-beat'

# 4. Verifica health
curl -fsS https://<app-url>/health
curl -fsS https://<app-url>/ready
```

Note: gli endpoint applicativi (`<app-url>`) sono nel profilo `deploy/profiles/<name>.conf`.

### 4.2 Frontend rollback

Pre-PR P4 (atomic frontend swap): rollback richiede ri-build + ri-upload bundle precedente.
Post-PR P4 (atomic swap via symlink): switch symlink al rilascio precedente è atomico.

Lo stato attuale può essere pre o post-P4 a seconda di quando si esegue questa checklist —
verificare in `deploy.sh` se il frontend è gestito con symlink swap (cerca `current` symlink
o `releases/N` directory).

### 4.3 DB rollback

Vedi `backup-restore.md` per procedura completa.

Riassunto:
- Migration **forward-only** non rollbackabili automaticamente
- Restore PG da snapshot pre-deploy se necessario (RPO documentato in DR playbook)
- Identificare last-good `schema_migrations` row prima di restore

### 4.4 Quando NON rollbackare

- Se il bug è già fixato in un commit successivo → forward-fix
- Se rollback rompe migration applicate → restore PG
- Se solo frontend è broken → rollback frontend mantenendo backend nuovo

### 4.5 Post-rollback

- [ ] Health endpoints verde su versione rollback
- [ ] Smoke funzionale (sez. 3.3) verde
- [ ] Incident note in `incident_response.md` con timeline + root cause provvisoria
- [ ] Ticket di fix reintroduzione apertura in tracker
