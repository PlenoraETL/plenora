# Phase 0.2 — Deploy/Server Probe

**Data**: 2026-04-27
**Stato**: probe locale + probe remoto SSH read-only **eseguiti** (sezioni §3.A-§3.J).
**Vincoli rispettati**: nessun deploy, nessun restart, nessun push, nessun `git pull`, nessun `cp`, nessun `daemon-reload`, nessuna modifica codice. `deploy.sh` non eseguito. SSH solo per comandi read-only. Nessun valore di secret stampato.

**Aggiornamento 2026-04-27 post-probe**: §5, §6, §7 aggiunti con stato server REALE. §1-§4 invariati (probe locale immutato).

---

## 1. Probe locale — risultati osservati

### 1.1 Repo state

```
Branch:        main
HEAD:          c68e42e  "docs: remove archived phase reports" (2026-04-27 12:54)
Upstream:      origin/main (in sync, no ahead/behind)
Working tree:  clean
Tag:           phase-5-complete -> 5e34fd3 "docs: close Phase 5 refactor map"
                (HEAD e` 2 commit oltre il tag)
```

Remote configurati:
- `origin → https://github.com/PlenoraETL/plenora.git` (canonico, post-rename)
- `ultra → C:\Users\Marco\Desktop\int_efm\URBY-ETL-ultra` (path locale **non esistente**, residuo di ultrareview, harmless)

### 1.2 Deploy artefatti nel repo

`backend/deploy/` contiene materiale completo:

| Path | Contenuto | Stato |
|---|---|---|
| `deploy.sh` (root URBY-ETL) | script user-facing Windows | **disallineato dal repo nuovo** (vedi §1.5) |
| `backend/deploy/migrations/2026-04-18_rename-urby-to-plenora.sh` | migration server urby→plenora (343 righe, idempotente, con rollback automatico) | applicato 2026-04-18, residui dichiarati nel README |
| `backend/deploy/systemd/README.md` | doc + dichiarazione residui post-migration | **autorita` per stato server** |
| `backend/deploy/systemd/plenora-backend.service` | uvicorn 127.0.0.1:5004, 2 worker, EnvironmentFile=`/home/ubuntu/plenora-etl/backend/.env` | canonico repo |
| `backend/deploy/systemd/plenora-celery.service` | celery worker concurrency=4 | canonico repo |
| `backend/deploy/systemd/plenora-beat.service` | celery beat con `-S workers.beat_scheduler.PlenoraBeatScheduler` | canonico repo |
| `backend/deploy/systemd/plenora-backup.service|.timer` | oneshot dump+S3 daily 02:00 UTC | canonico repo |
| `backend/deploy/systemd/plenora-backup-test.service|.timer` | restore test weekly Sun 03:00 UTC | canonico repo |
| `backend/deploy/iam/` | IAM policy JSON (probe-only, non esplorato qui) | n/d |
| `backend/deploy/terraform/` | lifecycle.tf (S3 retention) | n/d |
| `backend/deploy/backup-runbook.md` | runbook DR | n/d |

### 1.3 `.env` e secret handling

| Aspetto | Stato |
|---|---|
| `.env` tracciato in git | NO — `.gitignore:56` lo esclude correttamente |
| `.env.example` tracciato | SI (13KB) |
| `.env` locale presente | SI (576B) |
| `.env` locale ENVIRONMENT | `development` |
| `.env` locale DATABASE_URL | **punta a Postgres produzione** (15.161.59.22:5432/plenora) — sviluppo gira su DB prod |
| `.env` locale PLENORA_ENCRYPTION_KEY | reale (non `_DEV_DEFAULT_KEY`) |
| `.env` locale JWT_SECRET_KEY | reale |
| `.env` locale BACKUP_BUCKET / AWS_REGION | popolate (ma senza credenziali AWS sul desktop boto3 fallisce silente) |
| Crypto dev fallback | `crypto.py:102` `_DEV_DEFAULT_KEY = "urby-etl-default-key-change-in-production"` — **lasciare**: cambiare la stringa romperebbe i dati cifrati locali storici. Marker B intenzionale. |

**Osservazione critica**: lo sviluppo locale parla con il Postgres produzione `15.161.59.22:5432/plenora`. Qualsiasi test write locale tocca dati prod. Non e` un problema di Phase 0.2 (e` setup operatore), ma va annotato.

### 1.4 Residui `urby-*` ancora nel repo (post-rename)

5 punti trovati:

| # | File | Riga | Tipo | Azione consigliata |
|---|---|---:|---|---|
| 1 | `backend/app/shared/crypto.py` | 102 | dev fallback key string | **B — lasciare** (storico necessario per non rompere blob cifrati locali) |
| 2 | `backend/docs/compliance/secrets-management.md` | 27 | doc reference `urby-backend.service` + path sbagliato `/opt/urby-etl/` | **A — doc fix** in batch dedicato |
| 3 | `frontend/src/store/useCanvasStore.js` | 2 | commento header "canvas urby-etl" | **A — cosmetico** |
| 4 | `backend/workers/beat_scheduler.py` | (TBD) | alias retro-compat `UrbyBeatScheduler` (citato in `deploy/systemd/README.md:20-22`) | **B/A** — rimovibile quando l'unit `plenora-beat.service` installato sul server invocera` `PlenoraBeatScheduler` (vedi §2) |
| 5 | `backend/deploy/migrations/2026-04-18_rename-urby-to-plenora.sh` | tutto | migration script storico | **B — tenere** come storico (vale per future migrazioni di server o DR) |

Verifica classe BeatScheduler nel codice:
- `backend/workers/beat_scheduler.py:48` definisce `class PlenoraBeatScheduler(Scheduler):`
- L'alias `UrbyBeatScheduler` non e` stato verificato direttamente in questa passata (grep restituisce solo la classe nuova; l'alias citato nel README potrebbe essere gia` rimosso). **Needs verification** — task Phase 0.4.

### 1.5 `deploy.sh` (root) — analisi rischi

```
PEM      = C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem
SERVER   = ubuntu@15.160.219.93
BACKEND  = /home/ubuntu/plenora-etl/backend/  (path post-migration)
UNITS    = plenora-backend, plenora-celery, plenora-beat
FLOW     = git add -A && git commit "deploy: $(date)" && git push origin main
           → ssh: git pull origin main && pip install -r requirements.txt
                  && systemctl restart plenora-{backend,celery,beat}
FRONTEND = npm run build → ssh rm -rf dist remoto → scp dist/*
```

**Gap rilevati nel `deploy.sh`**:

| # | Gap | Rischio |
|---|---|---|
| G1 | `git add -A` + auto-commit con messaggio generico se working tree sporco | **Alto** — pusha file non voluti (es. `.env` se gitignore fallisce, output debug, file di stato locali) |
| G2 | NON verifica `git remote -v` sul server prima del pull | **Medio** — se il server ha `origin → vecchio repo`, pull arriva da remote sbagliato in silenzio |
| G3 | NON copia `deploy/systemd/*.service` su `/etc/systemd/system/` dopo il pull | **Medio** — modifiche agli unit file nel repo NON arrivano sul server |
| G4 | NON fa `daemon-reload` prima di `restart` (presume nessun cambio unit) | **Medio** — coerente con G3 ma se G3 si fixa, serve anche daemon-reload |
| G5 | NESSUN dry-run / diff preview / approvazione interattiva | **Medio** — operatore non vede cosa sta per essere applicato |
| G6 | NESSUN rollback automatico (a parte quello implicito di `pip install` che fail-fast) | **Basso** — `Restart=always` su systemd unit puo` mascherare crash loop |
| G7 | PEM in OneDrive | **Basso** — sync OneDrive puo` corrompere permessi/path |
| G8 | NO check coerenza `.env.example` ↔ `.env` server (env aggiunte in repo non finiscono nel `.env` server) | **Medio** — feature flag e env nuove rimangono inattive in produzione finche` qualcuno non aggiorna `.env` a mano |

### 1.6 Documentazione contraddittoria nel repo

| Doc | Dichiara | Realta` repo |
|---|---|---|
| `deploy/systemd/README.md:19-22` | "plenora-beat.service installato sul server invoca ancora `UrbyBeatScheduler`" | il file `.service` nel repo invoca **PlenoraBeatScheduler** |
| `deploy/systemd/README.md:23-25` | "Description= dei 3 unit installati sul server dicono ancora URBY-ETL" | i file `.service` nel repo hanno `Description=PLENORA-ETL ...` |
| `deploy/systemd/README.md:165-178` | flusso "Modifiche future": `scp deploy/systemd/*.service` + `daemon-reload` + restart | `deploy.sh` NON include questo flusso |

**Conclusione**: il **server installato** e` rimasto a quanto generato dal `sed` del migration script del 2026-04-18. Tutti i miglioramenti unit file fatti dopo (porta esplicita 127.0.0.1, EnvironmentFile, classe `PlenoraBeatScheduler`, ecc.) sono **solo nel repo**. Il `deploy.sh` corrente non li sincronizza.

---

## 2. Aspettativa server (ipotesi pre-probe SSH)

Basata su `deploy/systemd/README.md`, sul migration script, sul LEDGER e sulla cronologia commit. **Da confermare con probe SSH read-only proposto in §3.**

### 2.1 Stato presunto post-2026-04-18

| Aspetto | Atteso |
|---|---|
| Directory app | `/home/ubuntu/plenora-etl/` (rinominata da `/home/ubuntu/urby-etl/`) |
| Backup vecchi unit | `/etc/systemd/system/*.pre-plenora-rename.bak` (tenuti 7gg → presumibilmente gia` rimossi a 2026-04-27) |
| Unit installati `/etc/systemd/system/` | `plenora-backend.service`, `plenora-celery.service`, `plenora-beat.service` (generati via sed dai vecchi `urby-*`) + `plenora-backup{,.timer}`, `plenora-backup-test{,.timer}` (copiati dal repo) |
| `plenora-beat.service` installato | invoca `UrbyBeatScheduler` (sed non l'ha modificato; `deploy/systemd/README.md` lo dice) — **divergente** dal repo nuovo |
| Description= dei 3 long-running | "URBY-ETL ..." (sed non le ha intercettate) — **divergente** dal repo nuovo |
| venv path | `/home/ubuntu/plenora-etl/backend/venv/` con shebang gia` aggiornati dal migration step 3.5 |
| `.env` server | `/home/ubuntu/plenora-etl/backend/.env` con `PLENORA_ENCRYPTION_KEY` reale, `DATABASE_URL` produzione |

### 2.2 Stato presunto del `git remote` e branch sul server

| Aspetto | Atteso |
|---|---|
| `cd /home/ubuntu/plenora-etl/backend && git remote -v` | **IGNOTO** — potrebbe ancora puntare al vecchio repo `URBY-ETL` su un altro org/account, oppure a un fork interno. Carry-over LEDGER esplicito: "aggiornare deploy/server remote in uno step separato". |
| Branch checked out | presumibilmente `main` |
| Ultimo commit | **IGNOTO** — Last `git pull` server e` di data sconosciuta, sicuramente prima del rename `PlenoraETL/plenora.git`. Probabilmente fermo a uno stato pre-2026-04-23 (data dei commit di archivio root docs). |

### 2.3 Gap atteso server vs repo

| Dimensione | Server (atteso) | Repo (osservato) | Gap |
|---|---|---|---|
| Origin remote | repo vecchio o fork | `PlenoraETL/plenora.git` | da riallineare |
| HEAD | tra 2026-04-18 e 2026-04-23 (presunto) | `c68e42e` 2026-04-27 | ~80+ commit di disallineamento (Phase 1-5 + cleanup) |
| Unit `plenora-beat.service` | `UrbyBeatScheduler` | `PlenoraBeatScheduler` | scheduler class disallineata |
| Unit Description | "URBY-ETL" | "PLENORA-ETL" | cosmetico |
| `.env` server | env minime per girare | `.env.example` ha 13KB di env (~50+ env documentate) | **possibili env nuove non settate** sul server (es. `PLENORA_DISABLE_DEFAULT_KEY`, `JWT_BLACKLIST_*`, `TRUSTED_PROXIES`, `TICKET_ATTACHMENT_MAX_BYTES`, ecc. — tutte hanno default safe ma alcune potrebbero essere security-relevant) |
| Codice Phase 1-5 fix | mancante | applicato | tutti i fix security/refactor non sono in produzione |

---

## 3. Comandi SSH READ-ONLY proposti (NON eseguiti)

**Regola**: ogni comando SSH e` da copiare-incollare manualmente nel terminale dell'operatore, **una sezione alla volta**, leggendo l'output e fermandosi se qualcosa diverge dall'atteso. NESSUN comando di scrittura (`pull`, `restart`, `cp`, `daemon-reload`, `systemctl set-property`, ecc.) e` proposto.

Il PEM e` in `C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem`. Sostituire `$PEM` se la shell lo richiede.

### 3.A Identita` server e accesso

```bash
PEM="C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem"
SERVER="ubuntu@15.160.219.93"

ssh -i "$PEM" "$SERVER" 'hostname && uname -a && uptime && date -u'
```

**Atteso**: hostname EC2, kernel Linux, uptime ragionevole, ora UTC.
**Stop se**: connessione fallisce (PEM permission, IP cambiato, security group).

### 3.B Layout filesystem post-migration

```bash
ssh -i "$PEM" "$SERVER" 'ls -la /home/ubuntu/ | grep -E "(urby|plenora)"; \
                          echo "---"; \
                          ls -la /home/ubuntu/plenora-etl/backend/ 2>&1 | head -30'
```

**Atteso**:
- `plenora-etl/` presente
- `urby-etl/` **assente** (rinominata)
- nel backend: `app/`, `workers/`, `tests2/`, `venv/`, `requirements.txt`, `.env`, `deploy/`

**Stop se**: `urby-etl/` ancora presente (migration non eseguita o rollback subito).

### 3.C Stato git server

```bash
ssh -i "$PEM" "$SERVER" 'cd /home/ubuntu/plenora-etl/backend && \
    echo "=== REMOTE ===" && git remote -v && \
    echo "=== BRANCH ===" && git branch -vv && \
    echo "=== HEAD ===" && git log --oneline -5 && \
    echo "=== STATUS ===" && git status --short && \
    echo "=== TAGS ===" && git tag --list "phase-*" --sort=-creatordate | head -3'
```

**Atteso (probabile)**:
- remote = repo VECCHIO (non `PlenoraETL/plenora.git`)
- HEAD ~80 commit dietro `c68e42e`
- working tree pulito (se sporco → file modificati a mano sul server, da indagare PRIMA di qualsiasi pull)

**Decisione**: questo output decide il piano di riallineamento (riga 4.3 sotto).

### 3.D Working tree server pulito?

```bash
ssh -i "$PEM" "$SERVER" 'cd /home/ubuntu/plenora-etl/backend && \
    git status -uall && \
    echo "---DIFF SUMMARY---" && \
    git diff --stat HEAD && \
    echo "---UNTRACKED FILES (head 30)---" && \
    git ls-files --others --exclude-standard | head -30'
```

**Atteso**: working tree pulito.
**Stop se**: ci sono file modificati o nuovi non tracciati. Significa che qualcuno ha hot-fixato il server senza commit. Quegli edit vanno **letti** prima di applicare il riallineamento.

### 3.E Stato systemd unit

```bash
ssh -i "$PEM" "$SERVER" 'systemctl list-units --all "plenora-*" "urby-*" --no-pager; \
    echo "---"; \
    systemctl list-unit-files "plenora-*" "urby-*" --no-pager; \
    echo "---"; \
    sudo ls -la /etc/systemd/system/ | grep -E "(plenora|urby)"'
```

**Atteso**:
- Unit attivi: `plenora-backend.service`, `plenora-celery.service`, `plenora-beat.service`, `plenora-backup.timer`, `plenora-backup-test.timer`
- Eventuali `*.pre-plenora-rename.bak` (residui migration, harmless)
- Nessun `urby-*.service` `loaded`/`active`

**Stop se**: `urby-*.service` ancora attivi → migration non completata; oppure se mancano alcuni `plenora-*` (timer non installati).

### 3.F Confronto unit file installati vs repo

```bash
ssh -i "$PEM" "$SERVER" 'echo "=== plenora-backend.service ==="; \
    sudo cat /etc/systemd/system/plenora-backend.service; \
    echo "=== plenora-celery.service ==="; \
    sudo cat /etc/systemd/system/plenora-celery.service; \
    echo "=== plenora-beat.service ==="; \
    sudo cat /etc/systemd/system/plenora-beat.service'
```

**Diff atteso vs repo** (basato su `deploy/systemd/README.md:19-25`):
- `Description=` su tutti e 3 dice "URBY-ETL ..." invece di "PLENORA-ETL ..."
- `plenora-beat.service` ExecStart contiene `UrbyBeatScheduler` invece di `PlenoraBeatScheduler`
- Path /home/ubuntu/plenora-etl/ corretto in tutti (sed ha funzionato)

Confrontare a vista con i 3 file in `backend/deploy/systemd/*.service` del repo.

**Output**: tabella diff `installed vs repo` per ogni unit.

### 3.G Stato runtime servizi

```bash
ssh -i "$PEM" "$SERVER" 'for u in plenora-backend plenora-celery plenora-beat; do \
        echo "=== $u ==="; \
        systemctl is-active "$u"; \
        systemctl is-enabled "$u"; \
        sudo systemctl status "$u" --no-pager | head -15; \
    done; \
    echo "=== timers ==="; \
    systemctl list-timers "plenora-*" --no-pager'
```

**Atteso**: 3 long-running `active (running)`, 2 timer `active (waiting)` con next-run prossimo.
**Stop se**: qualunque `failed`, `inactive`, o `not-found`.

### 3.H Verifica `.env` server (NO leak secrets)

```bash
ssh -i "$PEM" "$SERVER" 'sudo ls -la /home/ubuntu/plenora-etl/backend/.env; \
    echo "---KEY NAMES ONLY (no values)---"; \
    sudo grep -oE "^[A-Z_]+=" /home/ubuntu/plenora-etl/backend/.env | sort'
```

**Output atteso**: lista nomi env presenti nel `.env` server.
**Confronto**: contro `backend/.env.example` repo per individuare env nuove non ancora settate sul server.
**NON stampare valori dei secret**.

### 3.I Verifica venv/dipendenze

```bash
ssh -i "$PEM" "$SERVER" 'cd /home/ubuntu/plenora-etl/backend && \
    venv/bin/python --version && \
    venv/bin/pip --version && \
    venv/bin/pip list --format=freeze | wc -l && \
    venv/bin/pip list --format=freeze | grep -iE "(fastapi|celery|psycopg|cryptography|pandas|geopandas|boto3)" | sort'
```

**Atteso**: Python 3.11+, package count plausibile, versioni allineate a `requirements.txt`.

### 3.J Disk + log freshness

```bash
ssh -i "$PEM" "$SERVER" 'df -h / /home; \
    echo "---"; \
    sudo journalctl -u plenora-backend.service --since "1 hour ago" --no-pager | tail -20; \
    echo "---last beat tick---"; \
    sudo journalctl -u plenora-beat.service --since "10 minutes ago" --no-pager | tail -10'
```

**Atteso**: disk libero >20%, log recenti, beat tick visibile (ogni minuto in default).
**Stop se**: `journalctl` muto da ore (servizi forse falliti silenti) o disk sopra 90%.

---

## 4. Findings consolidati

### 4.1 Convergenze tra LEDGER e probe locale

- Server **disallineato** dal repo: confermato dalla doc del repo stesso (`deploy/systemd/README.md` dichiara residui).
- Non era un'ipotesi: era gia` carry-over LEDGER esplicito.

### 4.2 Findings nuovi emersi dal probe locale

| # | Finding | Severita` |
|---|---|---|
| F1 | `deploy.sh` NON copia gli unit file aggiornati dal repo a `/etc/systemd/system/` → server resta indietro su unit file anche dopo deploy "ufficiali" | **Alta** — gap silenzioso |
| F2 | `deploy.sh` auto-commit working tree sporco con messaggio generico = puo` pushare lavoro non finito o file di stato locali | **Alta** — accountability + leak risk |
| F3 | Documentazione interna contraddittoria: `secrets-management.md:27` parla ancora di `urby-backend.service` + path `/opt/urby-etl/` (path mai esistito sul server, anche storicamente era `/home/ubuntu/`) | Media — confonde il prossimo operatore |
| F4 | `.env` locale punta a Postgres prod con `ENVIRONMENT=development` → ogni run locale tocca dati prod | Media — non Phase 0 fix, ma da annotare |
| F5 | Esiste un alias `UrbyBeatScheduler` retro-compat (citato in README) ma non ho verificato direttamente — needs verification | Bassa |

### 4.3 Piano di riallineamento (proposto, NON eseguito)

Ordine raccomandato, **dopo** che §3 ha confermato lo stato server reale:

#### Step 1 — server git remote alignment (read+write, ~5 min)

Solo se §3.C mostra remote diverso da `PlenoraETL/plenora.git`:

```bash
# DA NON ESEGUIRE FINO A CONFERMA UTENTE
ssh -i "$PEM" "$SERVER" 'cd /home/ubuntu/plenora-etl/backend && \
    git remote set-url origin https://github.com/PlenoraETL/plenora.git && \
    git remote -v && \
    git fetch origin && \
    git log --oneline origin/main -5'
```

**Test**: `git fetch` riesce + log mostra commit nuovi. NESSUN `pull`/`merge` qui.

#### Step 2 — preview diff server vs origin/main (read-only, ~5 min)

```bash
# DA NON ESEGUIRE FINO A CONFERMA UTENTE
ssh -i "$PEM" "$SERVER" 'cd /home/ubuntu/plenora-etl/backend && \
    git log --oneline HEAD..origin/main | wc -l && \
    git log --oneline HEAD..origin/main | head -50 && \
    git diff --stat HEAD..origin/main | tail -20'
```

**Output**: numero commit di gap, file toccati (probabilmente migliaia di linee).
**Decisione**: se diff e` enorme (atteso ~80+ commit) → fast-forward in finestra dedicata, NON in mezzo a un workday.

#### Step 3 — finestra deploy controllata (NON Phase 0)

Sequenza atomica (richiede downtime ~2-5 min):

```
1. Backup .env server (cp .env .env.bak.YYYYMMDD)
2. git fetch origin && git merge --ff-only origin/main
3. venv/bin/pip install -q -r requirements.txt
4. Diff .env vs .env.example → eventuali env nuove + decisione
5. Copia file unit aggiornati: sudo cp deploy/systemd/*.service /etc/systemd/system/
6. sudo systemctl daemon-reload
7. sudo systemctl restart plenora-backend plenora-celery plenora-beat
8. Verifica: systemctl status + curl localhost:5004/health
9. Smoke browser su lab.plenora.it
10. Se KO → step di rollback documentato
```

**Phase 0 NON include lo Step 3.** Va in Phase 1 stabilization, in finestra dedicata, con `consolidated.md §"deadline May 5"` come vincolo.

#### Step 4 — refactor `deploy.sh` (Phase 1)

Sostituire `deploy.sh` con `deploy.sh v2` che:
- fail-fast su working tree sporco invece di auto-commit (G1)
- verifica `git remote -v` server prima del pull (G2)
- copia unit file `deploy/systemd/*.service` + `daemon-reload` (G3+G4)
- mostra diff preview e chiede conferma (G5)
- `--dry-run` flag
- separa PEM path da una env var, non hardcoded (G7)
- fa `diff .env .env.example` automatico e segnala nuove env (G8)

---

## 5. Cosa Phase 0.2 ha confermato del consolidated

| Voce consolidated | Esito |
|---|---|
| D1 — server alignment con `PlenoraETL/plenora.git` | **CONFERMATO bloccante**, da risolvere PRIMA di Phase 1 |
| D2 — verifica systemd unit | **CONFERMATO** — unit file divergenti repo vs server, da §3.F |
| D3 — riallineare `deploy.sh` | **CONFERMATO + RAFFORZATO** con 8 gap puntuali (G1-G8) |
| D4 — runbook nuovo | **CONFERMATO**, struttura proposta in §4.3 |
| D5 — auto-commit deploy.sh = pattern rischioso | **CONFERMATO** (G1) |
| D6 — GitHub branch protection / CI / checks | NON probato in 0.2 (fuori scope), candidato Phase 0.3 |

---

## 6. Stop con report no-change

**Modificato in questa passata**: solo `orchestrate/phase_0_consolidation/probe_0_2_deploy.md`.

**NON modificato**:
- nessun file in `URBY-ETL/`
- nessun comando SSH eseguito
- nessun deploy
- nessun restart
- nessun push

**Decisioni richieste all'utente prima di passare a 0.3**:

1. Esegui i comandi §3 (A→J) uno alla volta sul tuo terminale e incollami gli output. Posso cosi` chiudere lo "stato server reale" con dati veri invece di ipotesi LEDGER.
2. Conferma se vuoi che il piano di riallineamento §4.3 step 3 sia un batch dedicato di Phase 0.x o che vada in Phase 1 (preferenza mia: **Phase 1 in finestra dedicata**, non in mezzo al lavoro Phase 0).
3. Conferma se Phase 0.3 deve includere anche:
   - secret/env diff `.env` server vs `.env.example` repo (richiede output §3.H)
   - GitHub branch protection / CI checks audit (D6)
   - secret scan policy (proposto in roadmap consolidated)

**Next batch suggerito**: Phase 0.3 — Security Boundary Baseline (SSRF design + A2/A3 marker safe + env audit), partendo dagli output SSH che mi inviti.

---

**Stato**: Phase 0.2 probe locale **chiusa**. Probe remoto **eseguito** §3.A-§3.J. Vedi §5-§7 sotto.

---

## 5. Server observed state — actual (post-probe SSH 2026-04-27)

### 5.1 Identita` host (3.A)

| Campo | Valore |
|---|---|
| Hostname | `ip-172-31-20-89` (EC2 internal) |
| OS | Ubuntu 24.04 — kernel `6.17.0-1007-aws` |
| Arch | **aarch64** (ARM, NON x86_64 — pip wheels devono avere build ARM) |
| Uptime | 19g 17h al momento del probe (no reboot post-migration) |
| Time | UTC, sincronizzato |

### 5.2 Filesystem (3.B)

```
/home/ubuntu/
└── plenora-etl/             ← migration completata, no urby-etl/
    └── backend/             [working dir]
        ├── .env             (599B, mtime 2026-04-23 08:27)
        ├── .env.bak.od4b.20260423-082646    (untracked, 510B — backup auto)
        ├── .env.example     (6160B — outdated, repo ne ha 13170B)
        ├── .git/            (mtime 2026-04-19 15:06)
        ├── app/             (mtime 2026-04-19 04:23)
        ├── workers/         (mtime 2026-04-19 04:23)
        ├── tests2/          (mtime 2026-04-18 16:48)
        ├── deploy/          (mtime 2026-04-18 13:11)
        ├── scripts/         (mtime 2026-04-18 13:11)
        ├── venv/            (mtime 2026-04-18 06:42)
        ├── data/            (mtime 2026-04-23 08:55)
        ├── reduction_plan_backend.md (19KB — file storico NON presente nel repo nuovo)
        └── genera_doc.py    (5KB — file storico NON presente nel repo nuovo)
```

**Mtime storia**: il server e` stato toccato l'ultima volta intorno al 2026-04-19 (codice) e 2026-04-23 (env + restart). Tutti i fix Phase 5 (2026-04-23 → 2026-04-27) **non sono mai arrivati**.

### 5.3 Git state server (3.C, 3.D)

```
REMOTE  origin = git@github.com:PlenoraETL/Plenora-backend.git  (SSH)
BRANCH  master  ← NON main
HEAD    f82c481  "deploy: 2026-04-19 07:57"
LOG     5 commit consecutivi "deploy: <date>" (auto-commit deploy.sh G1)
STATUS  pulito, eccetto:  ?? .env.bak.od4b.20260423-082646
TAGS    nessuno
```

**Implicazione**: server e` su un **fork divergente** sotto due dimensioni:
1. **URL repo**: `Plenora-backend.git` (only-backend, non monorepo)
2. **Branch**: `master` (non `main`)
3. **Storia**: HEAD ha 5 commit auto-deploy non presenti nel repo nuovo

Fast-forward merge **NON e` possibile**. Serve sostituzione (re-clone), non un pull.

### 5.4 Systemd state (3.E, 3.F, 3.G)

**Plenora units installati e attivi**:

| Unit | State | Description (server) | Note |
|---|---|---|---|
| `plenora-backend.service` | active running, enabled, NRestarts=0 | "PLENORA-ETL Backend (uvicorn)" | identico al repo |
| `plenora-celery.service` | active running, enabled, NRestarts=0 | "PLENORA-ETL Celery Worker" | identico al repo |
| `plenora-beat.service` | active running, enabled, NRestarts=0 | "PLENORA-ETL Celery Beat" | invoca `PlenoraBeatScheduler` ✓ |
| `plenora-backup.timer` | active waiting, last run **oggi 02:02 UTC** ✓ | — | backup giornaliero funziona |
| `plenora-backup-test.timer` | active waiting, last run dom 03:06 UTC ✓ | — | restore weekly funziona |

**Tutti gli unit installati sono identici byte-per-byte ai file nel repo** (verificato via `systemctl cat`). Non c'e` divergenza unit-file repo↔server.

**Urby units residui**: 7 file in `/etc/systemd/system/` (`urby-{backend,celery,beat,backup,backup-test}.service|timer`), tutti `disabled` o `static`. Mtime Apr 10-17, **pre-migration intatti**. NESSUN file `*.pre-plenora-rename.bak` (la doc del README diceva di tenerli 7gg → presumibilmente rimossi a fine periodo).

**ExecMainStartTimestamp** dei 3 long-running: tutti `2026-04-23 08:55:32-34 UTC` → ultimo restart 4 giorni fa. Coerente con `.env.bak.od4b.20260423-082646`.

Health check: `curl -sI http://127.0.0.1:5004/health` → `HTTP 405` (HEAD non ammesso, GET-only — endpoint live).

### 5.5 `.env` server (3.H)

10 env settate sul server (nomi solo, no valori):

```
CELERY_BROKER_URL
CELERY_RESULT_BACKEND
CORS_ORIGINS
DATABASE_URL
ENABLE_ASYNC_ENRICHMENT
ENRICHMENT_POOL_SIZE
ENVIRONMENT
JWT_SECRET_KEY
PLENORA_ENCRYPTION_KEY
URBY_ENCRYPTION_KEY     ← alias deprecato/tombstoned, ancora presente
```

**Findings**:
- **`URBY_ENCRYPTION_KEY` ancora nel `.env` server**: il runtime crypto lo ignora (lockato dal 2026-04-23 dal contract test `test_crypto_key_sourcing_contract.py`), ma e` smell concreto. Se un operatore in futuro modifica solo l'alias per errore, il backend non puo` decifrare blob esistenti.
- **`.env.example` server outdated** (6KB vs 13KB repo): mancano le ~7KB di env documentate post-Phase-5.
- **Env nuove non settate** (tutte con default safe nel codice, ma alcune meritano configurazione esplicita):
  - `PLENORA_DISABLE_DEFAULT_KEY` (suggerito: `=1` per fail-fast in prod)
  - `PLENORA_ALLOW_LOOPBACK_OUTBOUND` (correttamente NON settata in prod)
  - `JWT_BLACKLIST_REDIS_TIMEOUT_SEC`, `JWT_BLACKLIST_STALE_THRESHOLD_SEC`
  - `TICKET_ATTACHMENT_MAX_BYTES`, `TRUSTED_PROXIES`
  - `BACKUP_PG_DUMP_TIMEOUT_SEC`, `BACKUP_S3_UPLOAD_TIMEOUT_SEC`, `BACKUP_SCHEMA`
  - `LOG_ARCHIVE_*`, `AUDIT_RETENTION_DAYS`
  - Env documentate in `runtime-prereqs.md:64-72`
- **Non mancano** `BACKUP_BUCKET`, `AWS_REGION`: arrivano da SSM via IAM role per i service oneshot (`scripts/load_secrets.py`).

### 5.6 Venv + dipendenze (3.I)

| Campo | Valore |
|---|---|
| Python | 3.12.3 |
| pip | 26.0.1 |
| Pkg count | 67 |
| `requirements.txt` server | 41 righe |
| FastAPI | 0.135.3 |
| Celery | 5.6.3 |
| pandas | 3.0.2 |
| geopandas | 1.1.3 |
| cryptography | 46.0.7 |
| pydantic | 2.12.5 |
| redis (client) | 7.4.0 |
| boto3 | 1.42.90 |
| uvicorn | 0.44.0 |
| SQLAlchemy | 2.0.49 |

Nessuna divergenza grossolana. Versioni recenti, coerenti con un install Phase 4.

### 5.7 Disk + log (3.J)

- Disk: `/dev/root` 193G, 4% usato (187G liberi) — nessun problema
- Backend journalctl: vede solo la HEAD probe, no errori 30 min
- Celery + Beat journalctl: muti 30 min (coerente: log Python via `app.utils.logger` finiscono nel modulo `logs/` DB, NON in journalctl)
- **Zero errori `-p err` last 24h** sui 3 unit

---

## 6. Reality check — ipotesi pre-probe vs realta`

| # | Ipotesi (probe_0_2 §2 + LEDGER) | Realta` osservata | Esito |
|---|---|---|---|
| 1 | Server origin diverso da `PlenoraETL/plenora.git` | Si: `git@github.com:PlenoraETL/Plenora-backend.git` (SSH, only-backend, master) | **CONFERMATO + AGGRAVATO**: e` fork divergente, non solo "indietro" |
| 2 | HEAD ~80 commit dietro | HEAD `f82c481` 2026-04-19; repo `c68e42e` 2026-04-27. Non confrontabile per ff (storia divergente) | **CONFERMATO** ma diversa natura |
| 3 | `plenora-beat.service` invoca `UrbyBeatScheduler` | Invoca **`PlenoraBeatScheduler`** ✓ | **SMENTITO** — README outdated |
| 4 | `Description=` dei 3 unit dice "URBY-ETL" | Dice **"PLENORA-ETL"** ✓ | **SMENTITO** — README outdated |
| 5 | `*.pre-plenora-rename.bak` presenti | Nessun file `.bak`. Solo i 7 `urby-*.service` originali lasciati `disabled` | **PARZIALMENTE SMENTITO** (ma residuo equivalente esiste in altra forma) |
| 6 | Server stabile e operativo | NRestarts=0, backup OK, restore weekly OK, health live | **CONFERMATO** |
| 7 | `.env` server ha env nuove non settate | Si, mancano ~10+ env post-Phase-5 (default safe) | **CONFERMATO** |
| 8 | `URBY_ENCRYPTION_KEY` rimosso dal `.env` server | **Ancora presente** come dead duplicate (runtime ignora) | **SMENTITO** |
| 9 | Migration completata 2026-04-18 | Conferma indiretta da mtime + assenza `urby-etl/` dir | **CONFERMATO** |
| 10 | `deploy.sh` auto-commit dirty tree (G1) | 5 commit "deploy: <date>" testimoni evidenti | **CONFERMATO** |

### 6.1 Gap reali vs gap dedotti

**Gap REALI confermati dal probe** (richiedono Phase 1 alignment):

| Gap | Evidenza |
|---|---|
| GR1 | Server `origin → Plenora-backend.git`, repo nuovo `plenora.git` (monorepo) | §5.3 |
| GR2 | Server branch `master`, repo nuovo `main` | §5.3 |
| GR3 | Server HEAD `f82c481` (2026-04-19), repo nuovo `c68e42e` (2026-04-27): tutta Phase 5 mancante (B3-A, B3-B, B4 strutturali + cleanup) | §5.3 |
| GR4 | `URBY_ENCRYPTION_KEY` ancora nel `.env` server come dead duplicate | §5.5 |
| GR5 | `.env.example` server outdated 6KB vs 13KB repo | §5.5 |
| GR6 | 7 unit `urby-*` legacy in `/etc/systemd/system/` (disabled, ma file presenti) | §5.4 |
| GR7 | Server file storici nel repo (`reduction_plan_backend.md` 19KB, `genera_doc.py`) NON nel repo nuovo | §5.2 |

**Gap DEDOTTI ma SMENTITI dal probe** (rimuovere dal piano):

| Gap dedotto | Realta` |
|---|---|
| GD1 (smentito) | `plenora-beat.service` con `UrbyBeatScheduler` | invoca gia` `PlenoraBeatScheduler` |
| GD2 (smentito) | `Description=` dice "URBY-ETL" | dice "PLENORA-ETL" |
| GD3 (smentito) | `*.pre-plenora-rename.bak` presenti | non presenti |
| GD4 (smentito) | Unit file installati divergenti dal repo | identici byte-per-byte |

**Documentazione da aggiornare** (al di la` del piano server):
- `backend/deploy/systemd/README.md` linee 19-25: rimuovere "Residui noti" perche` non sono piu` tali
- `backend/docs/compliance/secrets-management.md:27`: aggiornare riferimento `urby-backend.service` → `plenora-backend.service` e `/opt/urby-etl/` → `/home/ubuntu/plenora-etl/`

---

## 7. Piano Phase 1 — Server alignment (atomic re-clone)

**Importante**: il piano §4.3 dell'iterazione precedente assumeva fast-forward. Il probe ha smentito questa premessa. Riscrittura completa.

### 7.1 Approccio: re-clone + atomic switchover

`git remote set-url + ff merge` non funziona perche`:
- Storia `master` divergente (5 commit auto-deploy assenti dal repo nuovo)
- Repo source different shape (only-backend vs monorepo)
- Branch convention diversa (`master` vs `main`)

Approccio corretto: clonare il monorepo nuovo in directory parallela, copiare `.env`, fare smoke test, switchare atomicamente.

### 7.2 Sequenza proposta (Phase 1, finestra dedicata, NON eseguita ora)

**Pre-requisiti**:
- Finestra di manutenzione annunciata (downtime atteso 3-5 min)
- Operatore connesso via SSH
- Health check baseline pre-cutover registrato

**Step**:

1. **Snapshot pre-cutover** (1 min, no impact)
   ```
   ssh: cd /home/ubuntu/plenora-etl/backend
        cp .env /home/ubuntu/.env.PRE-CUTOVER-$(date +%Y%m%d-%H%M%S)
        git status --short > /home/ubuntu/cutover-pre-status.txt
        git log --oneline -10 > /home/ubuntu/cutover-pre-head.txt
   ```

2. **Clone repo nuovo in directory parallela** (2 min, no impact)
   ```
   ssh: cd /home/ubuntu/
        git clone https://github.com/PlenoraETL/plenora.git plenora-etl-NEW
        # plenora-etl-NEW/ ora e` un monorepo: ha backend/ e frontend/
   ```

3. **Subset checkout: l'app gira da `backend/`**
   - Opzione A (preferita): rinominare/symlink in modo che `plenora-etl-NEW/backend` sia il nuovo working dir, e i unit puntino li`
   - Opzione B: copiare solo `plenora-etl-NEW/backend/` in `plenora-etl-NEW2/backend/` mantenendo struttura attesa

4. **Re-pip install nel nuovo venv** (3-5 min, no impact)
   ```
   ssh: cd /home/ubuntu/plenora-etl-NEW/backend
        python3 -m venv venv
        venv/bin/pip install -r requirements.txt
        venv/bin/pip list --format=freeze > /tmp/new-venv-freeze.txt
   ```

5. **Copia `.env` + diff vs `.env.example`** (1 min, no impact)
   ```
   ssh: cp /home/ubuntu/plenora-etl/backend/.env /home/ubuntu/plenora-etl-NEW/backend/.env
        cd /home/ubuntu/plenora-etl-NEW/backend
        diff <(grep -oE "^[A-Z_]+=" .env | sort -u) \
             <(grep -oE "^[A-Z_]+=" .env.example | sort -u) > /tmp/env-diff.txt
   # Decisione: env nuove safe da aggiungere ora? (PLENORA_DISABLE_DEFAULT_KEY=1 in prod consigliato)
   # Decisione: rimuovere URBY_ENCRYPTION_KEY dal .env (tombstone definitivo, GR4)
   ```

6. **Smoke test sul nuovo venv senza switchover** (2 min, no impact)
   ```
   ssh: cd /home/ubuntu/plenora-etl-NEW/backend
        venv/bin/python -c "import app.main; print('import ok')"
        venv/bin/python -m pytest tests2/ -x -k "smoke or health" --no-header -q 2>&1 | tail -20
   # Stop se import fallisce o smoke test fallisce — il piano si interrompe qui
   ```

7. **Atomic switchover** (10 secondi — DOWNTIME inizia)
   ```
   ssh: sudo systemctl stop plenora-beat plenora-celery plenora-backend
        mv /home/ubuntu/plenora-etl /home/ubuntu/plenora-etl-OLD-$(date +%Y%m%d)
        mv /home/ubuntu/plenora-etl-NEW /home/ubuntu/plenora-etl
        # Fix shebang venv come step 3.5 del migration script (se path nuovo == path vecchio non serve)
   ```

8. **Re-install systemd unit dal nuovo repo** (~30 sec)
   ```
   ssh: cd /home/ubuntu/plenora-etl/backend
        sudo cp deploy/systemd/plenora-{backend,celery,beat,backup,backup-test}.service \
                deploy/systemd/plenora-backup.timer deploy/systemd/plenora-backup-test.timer \
                /etc/systemd/system/
        sudo systemctl daemon-reload
   ```

9. **Restart + health check** (~30 sec — DOWNTIME finisce)
   ```
   ssh: sudo systemctl start plenora-backend plenora-celery plenora-beat
        sleep 5
        for u in plenora-backend plenora-celery plenora-beat; do systemctl is-active $u; done
        curl -s http://127.0.0.1:5004/health
   ```

10. **Cleanup unit `urby-*` legacy** (~30 sec, no impact)
    ```
    ssh: sudo rm /etc/systemd/system/urby-backend.service \
                 /etc/systemd/system/urby-celery.service \
                 /etc/systemd/system/urby-beat.service \
                 /etc/systemd/system/urby-backup{,-test}.service \
                 /etc/systemd/system/urby-backup{,-test}.timer
         sudo systemctl daemon-reload
    ```

11. **Smoke E2E** (~5 min, no impact)
    - Browser su `https://lab.plenora.it`: login, list project, run trivial pipeline
    - Verifica timer: `systemctl list-timers plenora-*` (next-run prossimi)
    - Verifica log application via `app/core/logs/router.py` o DB query

12. **Rollback istantaneo se KO** (~2 min)
    ```
    ssh: sudo systemctl stop plenora-backend plenora-celery plenora-beat
         mv /home/ubuntu/plenora-etl /home/ubuntu/plenora-etl-FAILED-$(date +%Y%m%d-%H%M)
         mv /home/ubuntu/plenora-etl-OLD-* /home/ubuntu/plenora-etl
         sudo systemctl start plenora-backend plenora-celery plenora-beat
    ```

**Tempo totale**: ~15-20 min (di cui ~3-5 min downtime tra step 7 e step 9).

**Conservativo**: NON rimuovere `plenora-etl-OLD-<date>` finche` non sono passati ≥7 giorni di stabilita`.

### 7.3 Decisioni esplicite per Phase 1

1. Path filesystem: confermare se `plenora-etl/backend/` resta la working dir o se si scende a un livello (es. `plenora-app/backend/`). Decisione: **mantenere** `plenora-etl/backend/` per minimizzare diff con systemd unit + venv shebang.
2. Repo reference: dopo cutover, server avra` `origin → https://github.com/PlenoraETL/plenora.git` + branch `main`. **Coerenza** raggiunta.
3. **`URBY_ENCRYPTION_KEY` da rimuovere** dal `.env` server in step 5: il runtime non lo legge, e` solo confusione documentale.
4. **Env nuove safe da aggiungere** in step 5: minimo `PLENORA_DISABLE_DEFAULT_KEY=1` per fail-fast in prod (la `.env` non ha questa env, ma il runtime gia` fa fail-fast in `production` perche` `crypto.py:172-176` rifiuta default key se ENVIRONMENT=production).
5. **Frontend cutover** non in scope Phase 1: il frontend gira come bundle statico in `/home/ubuntu/plenora-etl/frontend/dist/`. Il cutover backend non lo tocca. Frontend dist deploy resta invariato (separato).

### 7.4 Refactor `deploy.sh v2` (Phase 1 separato)

Dopo il cutover, `deploy.sh` va riscritto per non rifare gli stessi errori:
- Rimuovere `git add -A` + auto-commit (G1) → fail se working tree sporco
- Fail se `git remote -v` server diverso da atteso (G2)
- Aggiungere step `cp deploy/systemd/*.service /etc/systemd/system/` + `daemon-reload` (G3+G4)
- `--dry-run` flag con git fetch + diff preview (G5)
- Diff `.env` vs `.env.example` post-pull con warning (G8)
- PEM da env var non hardcoded path OneDrive (G7)

---

## 8. Conclusioni Phase 0.2

### 8.1 Stato server reale

- **Operativo e stabile** (NRestarts=0 da 4 giorni, backup OK, restore weekly OK, health live, disk 4%)
- **Codice fermo al 2026-04-19** — manca tutta Phase 5 (~80 commit)
- **systemd e paths gia` rinominati** (URBY → PLENORA) ✓ — il README repo era outdated
- **Repo source divergente** (only-backend, branch master) — fast-forward non possibile, serve re-clone

### 8.2 Gap reali (consolidati)

GR1-GR7 elencati in §6.1. I 4 gap dedotti dal README (GD1-GD4) sono **smentiti** dal probe → rimossi dal piano.

### 8.3 Piano Phase 1

Atomic re-clone + switchover (~15-20 min, di cui 3-5 min downtime). 12 step in §7.2. Rollback documentato.

### 8.4 Doc da aggiornare in Phase 0.x (no codice)

- `backend/deploy/systemd/README.md:19-25` — rimuovere "Residui noti" ormai falsi
- `backend/docs/compliance/secrets-management.md:27` — fix reference a unit name e path
- `frontend/src/store/useCanvasStore.js:2` — commento header "canvas urby-etl" → "canvas plenora-etl"

### 8.5 Cosa Phase 0.2 NON ha fatto

- Deploy / restart / push / git pull / cp / daemon-reload ✓
- Lettura valori secret ✓
- Esecuzione `deploy.sh` ✓
- Modifica codice/test ✓

**Phase 0.2 chiusa.** In attesa di:
1. Approvazione piano Phase 1 §7.2 (tempi, finestra, downtime tollerato)
2. Decisione su Phase 0.3 scope (proposto: SSRF design + A2/A3 marker safe + secret/env audit dettagliato)
3. Decisione se aprire Phase 0.x doc fix (3 file in §8.4) prima o dopo Phase 0.3
