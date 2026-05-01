# Phase 0.5e — Deploy / Ops Second Pass (Decision-ready)

Status: **analysis / proposal only — no code change, no deploy, no SSH write, no restart.**
Scope: rendere `deploy_ops_migration_design_0_5e.md` actionable.
Patch plan, checklist e rollback per Step 0 (deploy.sh hardening).

Read together with:
- `deploy_ops_migration_design_0_5e.md` (design originale + Codex note)
- `URBY-ETL/deploy.sh` (97 LOC esistenti)
- `backend/deploy/systemd/plenora-{backend,celery,beat,backup,backup-test}.service` + 2 timer
- `backend/docs/ops/runtime-prereqs.md`
- `backend/docs/ops/backup-restore.md`
- `backend/.env.example` (314 LOC)
- `backend/app/main.py:308–441` (`/`, `/health`, `/ready`)

---

## 0. Riepilogo decisione

**Decisione finale (anticipata): Step 0 deploy.sh hardening è implementabile subito.**

Razionale sintetico (dettagli §10):
- È limitato a `deploy.sh` (97 LOC) — niente touch ai systemd unit, all'`.env`, all'IaC, alla pipeline GHA.
- Tutte le primitive che servono **esistono già** lato backend:
  - `/health` (PG + Redis) e `/ready` (PG + Redis + pool_headroom + migrations_aligned) — `app/main.py:374, 403`
  - `validate_storage_config()` fail-fast su `STORAGE_POLICY=object_storage` — già chiuso in batch A1 (vedi 0.5e originale §0)
  - `check-deps` mode già wired in `deploy.sh:70–87`
- Riduce 5 dei 15 rischi (R1, R4, R5, R7-checklist) **senza nuova infrastruttura**.
- Rollback = `git revert` di un singolo commit (script-only, nessuna mutazione persistente).

**Restano fuori da Step 0** (richiedono lavoro infrastrutturale): R2 (PEM bus factor), R3 (staging assente), R6 (env asymmetry SSM/.env), R9 (CI deploy gate), R12 (IaC gap), R15 (bus factor 1).

---

## 1. Patch plan preciso — Step 0 deploy.sh hardening

Le modifiche sono raggruppate in 5 patch logiche. Ogni patch è **indipendente e revertable** singolarmente. Ordine raccomandato dal più sicuro al meno (P1 → P5).

### P1. Rimuovere auto-commit + force-push guard

**Cosa cambiare** (linee `deploy.sh:20–28`):
- Eliminare il blocco `if [ -n "$(git status --porcelain)" ]; then git add -A; git commit -m "deploy: $(date)"`.
- Sostituire con un controllo **fail-fast**: se `git status --porcelain` ritorna non-empty → exit con messaggio `"ERRORE: working tree sporco. Committa o stash prima del deploy."`.
- Aggiungere check pre-push: rifiuta se `git rev-parse --abbrev-ref HEAD` non è `main` (deploy parte solo da branch pulito sulla mainline).

**Perché**:
- `git add -A` può catturare file sensibili (chiavi locali, file di test, cred locali) e committarli con messaggio "deploy: TIMESTAMP" — pratica documentata come anti-pattern in 0.5e originale R1.
- Auto-commit mescola code review e deploy — ogni deploy genera un "commit rumore" senza passare da PR/review.
- Branch guard previene deploy accidentali da un branch di feature.

**Rollback**:
- `git revert <patch-commit>` ripristina il blocco originale.
- Se la patch è applicata localmente solo (mai mergeata): `git checkout HEAD -- deploy.sh`.
- Effetto immediato: prossima invocazione di `deploy.sh` torna al comportamento auto-commit.

**Rischi**:
- **Basso**. L'unico cambio comportamentale è "deploy.sh exit 1 se working tree sporco" → l'operatore (Marco oggi) deve committare manualmente prima. Costo: 1 comando in più. Beneficio: niente commit accidentali con file non review-ed.

---

### P2. Pre-deploy validate_storage_config remoto + dipendenze sistema

**Cosa cambiare** (nuova funzione `pre_deploy_check()` da invocare prima di `deploy_backend`):
- SSH al server e lanciare in **modalità read-only**:
  1. `python3 -c "from app.shared.cloud_backends import validate_storage_config; validate_storage_config()"` — già esiste e fail-fast su missing `OBJECT_STORAGE_BUCKET` quando `STORAGE_POLICY=object_storage` (vedi Codex note in 0.5e originale §0).
  2. Riusare `check_deps()` già scritto (`deploy.sh:70–87`) — librerie sistema (libmagic1, libgdal) e python-magic nel venv.
  3. `curl -fsS http://127.0.0.1:5004/health` — verifica baseline pre-deploy (servizio attualmente vivo).
- Se uno dei 3 fallisce → exit 1 con messaggio specifico, **non procedere al `git pull` né al restart**.

**Perché**:
- `validate_storage_config()` è già chiamata al boot (`app/main.py:47`), ma **scoperta solo a restart effettuato**. Eseguirla pre-deploy evita "restart fallito → backend down → nessun rollback automatico".
- Dipendenze sistema mancanti (es. nuova lib in `requirements.txt` che richiede pkg apt) emergono solo a runtime crash. Check pre-deploy le anticipa.
- Health check pre-deploy è la **baseline**: se il servizio è già malato prima del deploy, l'operatore deve saperlo per non "deploy-su-incidente".

**Rollback**:
- `git revert <patch-commit>` rimuove le 3 chiamate.
- Le tre operazioni sono **read-only** (curl GET, python validate, dpkg -l) — nessun side-effect sul server.

**Rischi**:
- **Basso/medio**. Il check `validate_storage_config()` carica i moduli `app.shared.cloud_backends` e dipendenze (`boto3`, ecc.) — se il venv è broken il check fallisce ma il deploy non è ancora partito → fail-safe.
- Possibile falso positivo: se Redis è temporaneamente down al pre-check, `/health` risponde 503 e il deploy non parte. In quel caso l'operatore deve capire se è blip o problema reale → friction.

---

### P3. Post-restart health gate sui 3 servizi

**Cosa cambiare** (dopo il blocco `git pull && pip install && systemctl restart` esistente):
- Polling con timeout su `curl http://127.0.0.1:5004/ready` (NON `/health`):
  - Attendi 200 OK fino a max 60s, intervallo 2s.
  - `/ready` include migrations_aligned + pool_headroom — più rigoroso.
- Se 60s timeout o response 503 ripetuto → log `[FAIL]` con dump del JSON response, exit 1 **senza** rimettere mano a systemctl (lascia il servizio nello stato in cui si trova, l'operatore decide il rollback).
- Verifica anche `systemctl is-active plenora-celery plenora-beat` per i 2 worker → se uno non è `active` → exit 1.

**Perché**:
- L'attuale deploy.sh "fire-and-forget" del restart non aspetta che il servizio sia tornato up → l'operatore vede "DEPLOY COMPLETATO" in script anche se il backend è 503.
- `/ready` rigoroso (vs `/health`) cattura anche migrations pendenti — comune fonte di "deploy applicato ma DB non allineato".
- Worker silently down non sono catturati da `/ready` (testano backend solo) → check `systemctl is-active` separato per celery e beat.

**Rollback**:
- `git revert <patch-commit>` riporta al "fire-and-forget".
- Nessuna mutazione persistente sul server: il polling è solo letture HTTP/systemctl.

**Rischi**:
- **Basso**. L'unico effetto avverso è "deploy.sh exit 1 quando backend non torna ready in 60s". 60s è soglia generosa per cold-start uvicorn (oggi 2 worker, no startup pesante). Da regolare se servisse più tempo. Caveat: se `/ready` diventa flaky per dependency check fallaci (es. `migrations_aligned` introspection), il deploy scatta su falso positivo.

---

### P4. Frontend atomic swap via symlink + retain N versions

**Cosa cambiare** (sostituisce `deploy.sh:47–67`):
- Lato server, struttura `/home/ubuntu/plenora-etl/frontend/`:
  - `dist-{commit-sha}/` directory per ogni release.
  - `dist` symlink → ultima `dist-{sha}/`.
- Pipeline:
  1. Crea `dist-{NEW_SHA}/` su server.
  2. `scp -r dist/*` nella nuova directory.
  3. Atomic swap: `ln -sfn dist-{NEW_SHA} dist.tmp && mv -Tf dist.tmp dist`.
  4. Mantieni le ultime N=3 directory (cleanup `ls -t dist-* | tail -n +4 | xargs rm -rf`).

**Perché**:
- Pre-swap distruttivo (`rm -rf /dist/*` + `scp` lento) crea finestra in cui la dist è parziale → utenti vedono 404/500 sui chunk Vite con hash nuovo.
- Atomic symlink swap = transition istantaneo a livello filesystem (single inode swap).
- Retain N=3 abilita rollback istantaneo (`ln -sfn dist-{OLD_SHA} dist`) senza ri-build.

**Rollback**:
- `git revert <patch-commit>` rimette `rm -rf` + `scp -r`.
- Sul server, le `dist-*` directory create restano fino al cleanup manuale (basso costo: ~10 MB cad.).

**Rischi**:
- **Medio**. Richiede precondizione sul server: verificare permessi su `/home/ubuntu/plenora-etl/frontend/`, che `dist` non sia oggi una directory standalone (sì, lo è). Prima del primo deploy con P4, l'operatore deve fare manualmente:
  ```
  mv dist dist-initial && ln -s dist-initial dist
  ```
  per non perdere lo stato corrente.
- Webserver (probabilmente nginx davanti a uvicorn, da verificare in `runtime-prereqs.md`) deve seguire i symlink — comportamento default OK con nginx, ma confermare.

---

### P5. Variabili PEM/SERVER promosse a env override

**Cosa cambiare** (linee `deploy.sh:7–8`):
- Default invariati: `PEM="${PLENORA_DEPLOY_PEM:-C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem}"`, `SERVER="${PLENORA_DEPLOY_SERVER:-ubuntu@15.160.219.93}"`.
- Aggiungere check `[ -f "$PEM" ] || { echo "PEM non trovato: $PEM"; exit 1; }` come precondizione.
- Aggiungere log iniziale `echo "Deploy verso: $SERVER (PEM: $(basename $PEM))"` per audit.

**Perché**:
- Hardcoded `OneDrive-Marco/.../plenora.pem` è R2 (bus factor): un secondo operatore con PEM in path diverso non può lanciare deploy.sh senza patch.
- Env override lascia il default invariato (Marco continua a deployare come oggi) ma sblocca un secondo operatore con `PLENORA_DEPLOY_PEM=/path/al/suo/pem bash deploy.sh`.
- Check `[ -f "$PEM" ]` previene la situazione "PEM non c'è → ssh fallisce con messaggio criptico → operatore non capisce".

**Rollback**:
- `git revert <patch-commit>` rimette i path hardcoded.

**Rischi**:
- **Trascurabile**. Default invariati = comportamento user-facing identico per Marco. L'unico cambiamento è una env override opzionale e un check in più.

---

### Sintesi P1–P5

| Patch | Effort stimato | Rischio | Riduce rischio originale |
| --- | ---:| --- | --- |
| P1 auto-commit guard + branch guard | ~30 min | basso | R1 |
| P2 pre-deploy validate + check-deps + /health baseline | ~60 min | basso/medio | R7 (downgrade) + R4 parz. |
| P3 post-restart health gate `/ready` + worker is-active | ~60 min | basso | R4 |
| P4 atomic frontend swap via symlink | ~90 min | medio (precondizione server) | R5 |
| P5 PEM/SERVER env override + check | ~20 min | trascurabile | R2 parz. |
| **Totale** | **~4–4.5h** | basso aggregato | 5/15 rischi |

Tutto resta **limitato a `deploy.sh`** (script single file, 97 → ~150 LOC stimati). Niente touch a systemd unit, `.env`, app code, IaC.

---

## 2. Checklist pre-deploy manuale

Lista ad alto livello che l'operatore (Marco oggi) può seguire **anche prima** di applicare le patch P1–P5. Catturarla in un documento separato (es. `backend/docs/ops/deploy-checklist.md`) è un quick win indipendente.

### 2.1 Stato locale

- [ ] `git status --porcelain` ritorna empty (working tree pulito)
- [ ] `git rev-parse --abbrev-ref HEAD` = `main`
- [ ] `git log -1 --oneline` mostra l'ultimo commit atteso
- [ ] `git log @{u}..` mostra eventuali commit non pushati
- [ ] `git diff main..origin/main` empty (locale e remoto allineati o ahead)

### 2.2 Test locale

- [ ] `pytest backend/tests2/` verde (o subset critico se scoping)
- [ ] Frontend: `npm run build` completa senza warning critici
- [ ] Nessun file `*.env`, `*.pem`, `secrets.json` accidentalmente staged

### 2.3 Pre-flight remoto (read-only)

- [ ] `ssh ... "curl -fsS http://127.0.0.1:5004/health"` → 200 (servizio attuale healthy)
- [ ] `ssh ... "curl -fsS http://127.0.0.1:5004/ready"` → 200 (idem rigoroso)
- [ ] `ssh ... "systemctl is-active plenora-backend plenora-celery plenora-beat"` → tutti `active`
- [ ] `ssh ... "df -h /home/ubuntu /tmp"` → `Use%` < 90% su `/` e `/tmp`
- [ ] `ssh ... "free -m"` → memoria libera ≥ 500 MB (cold start uvicorn)
- [ ] `ssh ... "venv/bin/python -c 'from app.shared.cloud_backends import validate_storage_config; validate_storage_config()'"` → exit 0
- [ ] `bash deploy.sh check-deps` → libmagic1 OK, python-magic OK, libgdal OK (rispetto a quanto richiesto da `requirements.txt` corrente)

### 2.4 Stato infra

- [ ] `BACKUP_BUCKET` raggiungibile (verifica indiretta tramite ultimo run di `plenora-backup.timer` < 26h fa via dashboard admin / `journalctl -u plenora-backup.service --since yesterday`)
- [ ] `OBJECT_STORAGE_BUCKET` head-bucket OK (se policy=object_storage)
- [ ] Redis raggiungibile (lo conferma `/health` sopra, ma se è degradato = blocker)
- [ ] DB raggiungibile (idem)

### 2.5 Comunicazione

- [ ] Avvisato l'utenza (canale interno) se il deploy ha rischio impatto user-visible
- [ ] Finestra di deploy fuori da picco di utilizzo (se conosciuto)

---

## 3. Checklist post-deploy manuale

Eseguita **dopo** che `deploy.sh` ritorna `=== DEPLOY COMPLETATO ===`. Indipendente da P1–P5.

### 3.1 Health endpoints

- [ ] `curl https://lab.plenora.it/health` → 200 con `{"status":"ok","checks":{"postgres":{"ok":true},"redis":{"ok":true}}}`
- [ ] `curl https://lab.plenora.it/ready` → 200 con tutti i 4 check `ok=true` (postgres, redis, pool_headroom, migrations)
- [ ] `curl https://lab.plenora.it/` → 200 banner `{"status":"ok","service":"PLENORA-ETL API"}`

### 3.2 Servizi systemd

- [ ] `ssh ... "systemctl is-active plenora-backend"` → `active`
- [ ] `ssh ... "systemctl is-active plenora-celery"` → `active`
- [ ] `ssh ... "systemctl is-active plenora-beat"` → `active`
- [ ] `ssh ... "systemctl is-enabled plenora-backup.timer plenora-backup-test.timer"` → entrambi `enabled`
- [ ] `ssh ... "journalctl -u plenora-backend -n 50 --no-pager"` → no `[CRASH]` recenti, no traceback

### 3.3 Smoke funzionale

- [ ] Login UI con utente di test → token JWT ricevuto
- [ ] Dashboard `/` carica le 4 sezioni (estrai/manipola/arricchisci/eseguibili) — `loadData` Promise.all 8 endpoint
- [ ] Apri un progetto Estrai esistente → canvas render OK
- [ ] (Opzionale) Esegui un piccolo task estrai noto (≤100 righe) e verifica result_id + download

### 3.4 Frontend

- [ ] Hard refresh browser (Ctrl+Shift+R) → bundle hash nuovi caricati
- [ ] Console DevTools → no 404 sui chunk JS/CSS
- [ ] Versione frontend visibile in UI (banner / footer / DevTools console "build hash") aggiornata

### 3.5 Logs / observability

- [ ] `ssh ... "journalctl -u plenora-celery -n 100 --no-pager"` → worker ready, listening su queue
- [ ] DB `SELECT MAX(ts) FROM logs` → log applicativi recenti (last write < 5 min) — conferma che PostgresLogHandler scrive
- [ ] `intermediate_run_report.py` su una run di test (opzionale) → drift = 0

### 3.6 Backup health

- [ ] `ssh ... "systemctl status plenora-backup.timer"` → `Active`, `Trigger: <prossima esecuzione>`
- [ ] Dashboard admin `/admin/system-status` → card backup `status="ok"` con timestamp ultimo run

---

## 4. Rollback manuale minimo

Se il deploy fallisce (post-deploy checklist § 3.1 o 3.2 in errore), l'operatore deve poter tornare allo stato precedente con il **minimo numero di passi**.

### 4.1 Backend rollback

```
ssh -i $PEM $SERVER
cd /home/ubuntu/plenora-etl/backend
# 1. Identifica commit precedente
git log --oneline -5
# 2. Reset al commit pre-deploy (sostituisci <SHA>)
git reset --hard <SHA-PRE-DEPLOY>
# 3. Re-sync venv (se requirements.txt era diverso)
venv/bin/pip install -q -r requirements.txt
# 4. Restart
sudo systemctl restart plenora-backend plenora-celery plenora-beat
# 5. Verifica
curl -fsS http://127.0.0.1:5004/ready
```

**Precondizione**: il commit pre-deploy deve essere ancora identificabile in `git log` (non force-pushed). Per questo P1 vieta force-push.

**Tempo target**: < 5 min dal momento in cui l'operatore decide il rollback.

### 4.2 Frontend rollback

**Pre-P4 (oggi)**: rebuild della versione precedente da locale + redeploy.
- Tempo: ~5 min (npm build) + ~2 min (scp).
- Friction: serve checkout locale del commit pre-deploy.

**Post-P4**: instantaneo via symlink swap.
- `ssh ... "cd /home/ubuntu/plenora-etl/frontend && ln -sfn dist-{OLD_SHA} dist.tmp && mv -Tf dist.tmp dist"`.
- Tempo: < 5 secondi. Niente rebuild.

### 4.3 DB rollback

**Non automatico**, richiede playbook DR.
- Vedi `backend/docs/ops/backup-restore.md` Scenario A (DB completo) o B (restore selettivo).
- Tempo: ~30 min – 2h a seconda dello scenario.
- Mitigation: se il deploy include una migration, **eseguire migration in dry-run su staging prima** (richiede staging — non disponibile oggi, vedi §8).

### 4.4 Cosa NON è rollback automatico

- Modifiche `.env` sul server (non versionate). Se l'operatore modifica `.env` come parte del deploy, deve tracciare il cambio fuori-script (idealmente un changelog operativo manuale).
- AWS SSM parameters (mai modificati da deploy.sh).
- IAM role / policies (out of scope deploy).
- Bucket S3 lifecycle (Terraform, separato).

---

## 5. Env required checklist post-object_storage

Lista delle variabili che **devono** essere presenti su `/home/ubuntu/plenora-etl/backend/.env` (lette da `EnvironmentFile=` in plenora-backend, plenora-celery, plenora-beat units) per produzione cloud (`STORAGE_POLICY=object_storage`).

Sorgenti: `runtime-prereqs.md` §Variabili d'ambiente + `.env.example` + `validate_storage_config` semantica.

### 5.1 Mandatory (boot fail-fast se mancanti)

| Variabile | Note | Verifica |
| --- | --- | --- |
| `ENVIRONMENT=production` | Attiva fail-fast su altri secret | `grep '^ENVIRONMENT=' .env` |
| `DATABASE_URL` | Postgres connection string | `python -c "from app.config import settings; print('ok')"` |
| `PLENORA_ENCRYPTION_KEY` | Fernet key per crypto at-rest | idem |
| `JWT_SECRET_KEY` | JWT signing | idem |
| `CELERY_BROKER_URL` | Redis broker (single Redis canonical) | `/health` Redis check |
| `CELERY_RESULT_BACKEND` | Redis backend (= broker URL) | idem |
| `STORAGE_POLICY=object_storage` | Policy storage prod cloud | `validate_storage_config` boot |
| `OBJECT_STORAGE_ENABLED=true` | Capability gate | idem |
| `OBJECT_STORAGE_BUCKET` (o `PLENORA_ARTIFACT_BUCKET`) | Runtime bucket distinto da BACKUP_BUCKET | idem |
| `CLOUD_PROVIDER=aws` | Factory cloud_backends | startup log |

### 5.2 Recommended (warn ma non blocca boot)

| Variabile | Note |
| --- | --- |
| `BACKUP_BUCKET` | Per dashboard admin + admin_backups list |
| `BACKUP_S3_BUCKET` | Alias legacy |
| `BACKUP_S3_REGION=eu-south-1` | |
| `AWS_REGION=eu-south-1` | |
| `CORS_ORIGINS` | hostname frontend prod |
| `LOG_LEVEL_CONSOLE=INFO` | |
| `LOG_LEVEL_DB=WARNING` | |
| `LOG_RETENTION_DAYS=30` | |
| `JWT_BLACKLIST_REDIS_TIMEOUT_SEC=1` | (default 1, alzare se Redis remoto) |
| `SCHEDULE_LOCK_TTL_SECONDS=600` | |
| `SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS=180` | |

### 5.3 Optional (default dell'app sufficienti)

`DB_POOL_MIN`, `DB_POOL_MAX`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`, `LOGIN_MAX_ATTEMPTS`, `RESULT_S3_THRESHOLD_BYTES`, `FILE_SOURCE_S3_THRESHOLD_BYTES`, `RESULT_DB_HARD_CAP_BYTES`, `BACKUP_PG_DUMP_TIMEOUT_SEC`, `BACKUP_RESTORE_TIMEOUT_SEC`, `IMDF_BUNDLES_DIR`, `IMDF_BUNDLE_TTL_HOURS`, ...

### 5.4 Mai in `.env` (sempre via SSM, oggi solo backup unit)

- `DATABASE_URL` (oggi è in `.env` per runtime services — asymmetry R6 da chiudere in step futuro)
- `PLENORA_ENCRYPTION_KEY` (idem)
- `JWT_SECRET_KEY` (idem)

> Nota: oggi `.env` contiene questi 3 secret per i runtime services (backend/celery/beat). Solo i 2 oneshot backup units passano via SSM. Vedi 0.5e originale §1.4 per gap di asymmetry.

### 5.5 Pre-flight check raccomandato

Lato server, **read-only** (incorporato in P2):

```
diff <(grep -oE '^[A-Z_]+=' .env | sort -u) <(cat <<EOF | sort
ENVIRONMENT=
DATABASE_URL=
PLENORA_ENCRYPTION_KEY=
JWT_SECRET_KEY=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=
STORAGE_POLICY=
OBJECT_STORAGE_ENABLED=
OBJECT_STORAGE_BUCKET=
CLOUD_PROVIDER=
EOF
)
```

Differenze in output = variabili mandatory mancanti.

---

## 6. Health/Readiness endpoints da usare

### 6.1 Endpoints già disponibili (`backend/app/main.py`)

| Endpoint | Linee | Cosa verifica | Codice ritorno |
| --- | ---:| --- | --- |
| `GET /` | 308–311 | Banner — sempre 200 (non è health check vero) | sempre 200 |
| `GET /health` | 374–400 | **liveness**: PG `SELECT 1` + Redis `PING` | 200 ok / 503 unhealthy |
| `GET /ready` | 403–441 | **readiness rigoroso**: PG + Redis + pool_headroom (≥1 slot libero) + migrations_aligned (no pending) | 200 ready / 503 not_ready |

Tutti gli endpoint ritornano JSON con `checks: {<name>: {ok: bool, detail: str}}` per diagnostica.

### 6.2 Quando usare quale

| Contesto | Endpoint | Perché |
| --- | --- | --- |
| Pre-deploy baseline (P2) | `/health` | Verifica che il servizio **attuale** sia healthy prima di deployare sopra |
| Post-restart health gate (P3) | `/ready` | Più rigoroso: include migrations (se la nuova release ha migration da applicare e il runner non l'ha ancora applicata, `/ready` è 503) |
| Post-deploy smoke esterno (§3.1) | `/health` + `/ready` entrambi | Conferma sia liveness sia readiness post-deploy |
| Load balancer / nginx upstream check | `/health` | Liveness sufficiente; `/ready` può flaky-out su `migrations_aligned` durante deploy → falsi 503 |
| K8s readinessProbe (futuro) | `/ready` | K8s vuole readiness rigoroso per rimuovere pod dal pool durante drift |
| K8s livenessProbe (futuro) | `/health` | Restart pod solo su liveness fail |

### 6.3 Gap noti

- **No `/health` per Celery worker**: `is-active` via systemctl è il check disponibile. Non c'è endpoint HTTP.
- **No `/health` per beat scheduler**: idem. Esiste `/api/scheduler/healthcheck` (`scheduler/router.py:62`) ma è auth-gated → meno comodo per script deploy.
- **`/ready` `migrations_aligned`**: introspection sul runner — se il runner stesso fallisce, il check ritorna `ok=true` con detail "check skipped" (`main.py:351`). Falso negativo possibile.
- **Pool headroom check** (`main.py:355`) usa attributi privati `pool._used` / `pool._pool` — fragile a upgrade psycopg2.

### 6.4 Endpoint da NON usare in deploy.sh

- `GET /` banner (line 308) — sempre 200, non dice nulla sullo stato reale.
- Endpoint authenticated (`/api/admin/*`, `/api/users/*`) — richiedono token, complicano script.

---

## 7. Cosa resta manuale anche dopo Step 0

Step 0 (P1–P5) è uno script-level hardening. Non risolve:

### 7.1 Operativo

- **Aggiornamento `.env` sul server**: ogni nuova env var richiesta (es. dopo merge di una feature che aggiunge `MY_NEW_FLAG`) richiede SSH + edit manuale + restart. Nessuna automation.
- **Migration DB**: `migrations_runner` esiste ma il deploy.sh non lo invoca esplicitamente. Le migration partono al boot del backend (auto-apply on startup, da verificare); fail in migrazione = backend non parte.
- **AWS SSO / credential refresh**: non automatizzato (BUG.md #26 chiuso refresh manuale).
- **Bucket policy / IAM**: cambio richiede Terraform (`backend/deploy/terraform/`) + `terraform apply` manuale.
- **systemd unit edit**: P1–P5 non toccano i `.service` file. Modifiche richiedono `scp` + `systemctl daemon-reload` + `systemctl restart` manuali.

### 7.2 Comunicazione

- **Notifiche utenza**: nessun canale automatico per "deploy in corso" / "rolled back".
- **Audit trail deploy**: `git log` + `journalctl` + dashboard admin sono l'unica traccia. Nessun deploy log centralizzato.
- **Bus factor 1**: anche con P5 (PEM env override), è solo l'abilitatore tecnico. La conoscenza pratica del deploy resta nelle mani di Marco.

### 7.3 Scenari edge

- **Deploy con migration distruttiva**: Step 0 non ha gate per "questa release introduce migration → vuoi davvero?". Manuale.
- **Deploy hot-fix da branch != main**: P1 lo blocca. Per emergenza, l'operatore deve checkout su main + cherry-pick + push, oppure bypassare temporaneamente il guard (manuale).
- **Server EC2 reboot post-deploy**: timer e service partono al boot grazie a `systemctl enable`, ma nessuna verifica automatica post-reboot in deploy.sh.
- **Deploy multi-target** (futuro staging + prod): richiede env var per scegliere target. P5 è il primo step ma non sufficiente: serve `--env staging|prod` flag in CLI.

### 7.4 Performance / observability

- **Log aggregation**: log restano su journalctl + DB locale. Nessun ship verso esterno.
- **Alerting su deploy fail**: nessun webhook Slack/email in script.
- **Rollback automatico su `/ready` 503**: P3 fa exit 1 ma non triggera rollback automatico. L'operatore decide.

---

## 8. Cosa serve per arrivare a GitHub Actions / staging

Lista ordinata di prerequisiti. Ogni voce è un mini-progetto separato — non parte di Step 0.

### 8.1 Per GitHub Actions deploy

1. **Repository segregato `prod` vs `dev`**: oggi tutto è su `main` di `URBY-ETL`. GHA deploy può triggerarsi su `push main` (rischio: ogni merge = deploy) o su `tag vX.Y.Z` (raccomandato: deploy esplicito).
2. **AWS OIDC role** (no static credentials): GitHub Actions assume role tramite `aws-actions/configure-aws-credentials@v4` con OIDC trust policy. Effort: ~3h IAM + Terraform.
3. **Secrets management**: `GHA secrets` non sono il path — i secret applicativi devono restare su SSM. GHA deve solo avere il role per orchestrare.
4. **deploy.yml workflow**: equivalente di deploy.sh in GHA syntax. Niente PEM hardcoded; SSH via `ssh-agent` + chiave privata in GHA secret (OK come pre-staging) o via SSM/SSH bastion.
5. **Health gate in workflow**: equivalente di P3 ma in azione.
6. **Approval gate** (manual approval per prod): GitHub environments con required reviewer.
7. **Post-deploy smoke test job**: equivalente §3 in CI.

Effort stimato totale: **~2–3 giorni** developer-time per primo workflow funzionante.

### 8.2 Per staging environment

1. **EC2 staging instance** (oggi inesistente): tipologia identica a prod (t3.medium ?), stesso security group pattern.
2. **DNS staging**: `staging.plenora.it` o sottodominio simile, certificato Let's Encrypt.
3. **DB staging**: opzioni:
   - RDS instance separata (costo +$30/mese ~) — pulita.
   - Schema `plenora_staging` su RDS prod (più economico, rischio cross-contamination).
4. **Bucket S3 staging**: `plenora-runtime-artifacts-staging-...` separato. Lifecycle aggressivo (delete dopo 7gg).
5. **SSM namespace staging**: `/plenora/staging/*` parallelo a `/plenora/prod/*`.
6. **systemd unit parametrizzati**: oggi i `.service` files hanno path hardcoded `/home/ubuntu/plenora-etl/backend`. Variabilizzare via template + `systemd-tmpfiles` o copia parallelo.
7. **deploy.sh `--env` flag**: `bash deploy.sh --env=staging` vs `--env=prod`.
8. **Promotion path**: tag staging → tag prod manuale (no auto-promote).

Effort stimato: **~5–7 giorni** developer-time + ~$50/mese costi infra.

### 8.3 Cosa NON serve per Step 0

Step 0 (P1–P5) **non richiede né** GHA **né** staging. È deliberatamente uno script-level hardening sull'EC2 prod esistente. Le voci §8.1, §8.2 sono progetti separati e successivi.

---

## 9. Decisione finale

### 9.1 Step 0 (P1–P5) implementabile subito? **Sì, con caveat.**

**Motivazioni per il sì**:
1. Tutti i prerequisiti backend esistono già (`/health`, `/ready`, `validate_storage_config`, `check-deps`). Niente da costruire lato app.
2. Tutto è confinato a `deploy.sh`. Niente touch a systemd, env, IaC, app code.
3. Rollback è banale (`git revert` di 1 commit per patch, totale 5 patch indipendenti).
4. Riduce 5/15 rischi originali (R1, R4, R5, R7-checklist, R2-parz).
5. Effort totale ~4–4.5h.
6. Rischio aggregato basso: P1, P2, P3, P5 sono trascurabili; P4 ha solo precondizione server (one-shot).

**Caveat (cosa va fatto prima/durante)**:
1. **P4 ha precondizione**: prima del primo deploy con symlink swap, l'operatore deve fare manualmente `mv dist dist-initial && ln -s dist-initial dist` sul server. Se non fatto, lo swap atomico non parte. Da documentare in commit message di P4.
2. **P2 dipende dal venv funzionante** sul server: se il venv è broken il check `validate_storage_config()` fallisce. Mitigazione: il check precede il `pip install` → fallisce su venv pre-deploy, segnalando un problema preesistente all'operatore.
3. **P3 timeout 60s**: vale per il setup attuale (uvicorn 2 worker, no startup pesante). Da rivedere se cold-start cresce (es. caricamento modelli ML ipotetici futuri).
4. **Test del deploy.sh modificato**: il primo run dovrebbe essere su una window operativa controllata (non venerdì sera). Ma è comunque safer del deploy attuale (no health gate).

### 9.2 Ordine raccomandato di applicazione

| Sequenza | Patch | Razionale |
| ---:| --- | --- |
| 1 | P5 (env override + check PEM) | Bassissimo rischio, abilita futuri deploy da operatori multipli |
| 2 | P1 (auto-commit guard + branch guard) | Bassissimo rischio, riduce R1 immediatamente |
| 3 | P2 (pre-deploy validate + check-deps + /health baseline) | Read-only checks, niente side-effect; cattura problemi prima del restart |
| 4 | P3 (post-restart /ready gate) | Read-only check post-restart, non muta nulla; rivela deploy falliti |
| 5 | P4 (atomic frontend swap) | Richiede precondizione server (dist → symlink); applicare per ultimo |

Ogni patch può essere applicata e validata isolatamente. Gap fra una patch e la successiva non degrada nulla — il deploy continua a funzionare con un mix di patch applicate e non.

### 9.3 Cosa NON aprire ora

- **Step 1+ (GHA, staging, IaC completo)**: progetti separati, vedi §8. Non parte di Step 0.
- **Modifica systemd unit**: out of scope per Step 0.
- **Migrazione `.env` → SSM per runtime services** (chiusura R6/R8): richiede migrazione dati + test → progetto separato.
- **Atomic swap backend** (zero-downtime via reverse proxy switch): non c'è infrastruttura per farlo (un solo EC2). Out of scope.

### 9.4 Verifica post-Step 0

Dopo aver applicato P1–P5 e fatto il primo deploy con il nuovo flow:

- Ri-eseguire la checklist § 3 (post-deploy manuale).
- Confermare che P3 ha bloccato correttamente un eventuale fail (test manuale opzionale: stoppare Redis, lanciare deploy, verificare exit 1).
- Confermare che P4 ha lasciato N=3 dist directory sul server.
- Aggiornare `incident_response.md` con la nuova procedura di rollback frontend (symlink swap).

---

## 10. Stato Phase 0.5e Second Pass

**Status: analysis only. No code change. No deploy. No SSH write. No restart.**

Output prodotti:
- patch plan P1–P5 con cosa cambia, perché, rollback, rischi
- pre-deploy checklist (5 sezioni)
- post-deploy checklist (6 sezioni)
- rollback manuale minimo (backend, frontend pre/post-P4, DB, fuori-scope)
- env required checklist post-object_storage (mandatory + recommended + optional)
- health/readiness endpoints già disponibili + uso raccomandato
- cosa resta manuale post-Step 0 (operativo, comunicazione, edge cases, observability)
- cosa serve per GHA + staging (con effort stimato)
- decisione finale: **Step 0 implementabile subito (sì)**, con ordine raccomandato P5→P1→P2→P3→P4

Decisione esplicita richiesta dall'utente (§9):
> Step 0 deploy.sh hardening è implementabile subito. Effort ~4–4.5h. Rischio aggregato basso. Rollback banale. Tutti i prerequisiti backend esistono già.

Quello che NON è stato fatto deliberatamente:
- nessuna modifica a `deploy.sh`
- nessun comando SSH eseguito
- nessuna mutazione su file server
- nessuna proposta di GHA / staging come parte di Step 0 (sono progetti separati)
- nessuna scadenza temporale

---

*End of Phase 0.5e — Second Pass (Decision-ready).*
