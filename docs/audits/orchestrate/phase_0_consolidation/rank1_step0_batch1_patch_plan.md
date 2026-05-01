# Rank 1 / Step 0 Batch 1 — Patch Plan (NOT applied)

Status: **plan only — no file change, no SSH, no deploy.**

Scope ratificato in `phase_0_5x_final_synthesis.md §10.0`:
- profile loader minimale
- profilo `prod` come unico profilo concreto
- required vars validation
- no auto-commit
- branch guard parametrico
- pre/post health URL dal profilo
- systemd units dal profilo
- PEM via env operatore

Read together with:
- `deploy_ops_second_pass_0_5e.md` §1 (P1–P5 originali)
- `phase_0_5x_final_synthesis.md` §6 (revisione multi-environment) + §10 (next action)

---

## 1. File da modificare / creare

### 1.1 File modificati (1)

| Path | Tipo | Note |
| --- | --- | --- |
| `URBY-ETL/deploy.sh` | modify | da 97 LOC attuali a ~150 LOC stimate. Backward-compat: invocazione senza argomenti continua a funzionare con profilo default `prod` |

### 1.2 File creati (3)

| Path | Tipo | Note |
| --- | --- | --- |
| `URBY-ETL/deploy/profiles/prod.conf` | new | shell-source-able, valori attuali estratti da deploy.sh hardcoded. **Nessun secret**. |
| `URBY-ETL/deploy/profiles/README.md` | new | documenta: come aggiungere profilo, variabili obbligatorie, cosa NON va dentro (no PEM, no AWS keys, no `.env` content) |
| `URBY-ETL/deploy/profiles/.gitignore` | new | `*.local.conf` (per profili locali operatore non versionati, futuro) |

### 1.3 File NON toccati in batch 1

- `URBY-ETL/backend/deploy/systemd/*.service` — no edit, no copy
- `URBY-ETL/backend/deploy/systemd/*.timer` — no edit
- `URBY-ETL/backend/deploy/iam/*` — no edit
- `URBY-ETL/backend/deploy/terraform/*` — no edit
- `URBY-ETL/backend/.env.example` — no edit (resta come template per-installazione)
- nessun file backend/app/* — Step 0 è pure script-only

---

## 2. Patch outline per file

### 2.1 `deploy/profiles/prod.conf` (nuovo)

Formato shell-source-able (`source "$PROFILE_FILE"` da `deploy.sh`). Variabili **public** estratte dai valori oggi hardcoded.

Outline contenuto (illustrativo, non vincolante):

```
# Public deploy config. NESSUN SECRET.
SERVER="ubuntu@15.160.219.93"
REMOTE_BACKEND_PATH="/home/ubuntu/plenora-etl/backend"
REMOTE_FRONTEND_PATH="/home/ubuntu/plenora-etl/frontend"
APP_HEALTH_URL="http://127.0.0.1:5004/health"
APP_READY_URL="http://127.0.0.1:5004/ready"
APP_BASE_URL="https://lab.plenora.it"
BACKEND_PORT="5004"
SYSTEMD_UNITS="plenora-backend plenora-celery plenora-beat"
ALLOWED_BRANCHES="main"
READY_TIMEOUT_SEC="60"
READY_POLL_INTERVAL_SEC="2"
RETAIN_FRONTEND_VERSIONS="3"
```

Note:
- I valori riprodotto **esattamente** lo stato hardcoded oggi: nessun cambio comportamentale per il profilo `prod`.
- `APP_BASE_URL` letto ma non ancora usato in batch 1 (predisposto per smoke esterni futuri).
- `RETAIN_FRONTEND_VERSIONS` letto solo da batch 2 (P4); presente qui per completezza profilo.

### 2.2 `deploy/profiles/README.md` (nuovo)

Outline sezioni:

1. **Scopo**: profili public per ambiente, selezionabili con `--profile=<name>` o env `PLENORA_DEPLOY_PROFILE`.
2. **Variabili obbligatorie**: `SERVER`, `REMOTE_BACKEND_PATH`, `REMOTE_FRONTEND_PATH`, `APP_HEALTH_URL`, `APP_READY_URL`, `SYSTEMD_UNITS`, `ALLOWED_BRANCHES`. Lo script fallisce esplicitamente se manca una di queste.
3. **Variabili opzionali**: `APP_BASE_URL`, `BACKEND_PORT`, `READY_TIMEOUT_SEC` (default 60), `READY_POLL_INTERVAL_SEC` (default 2), `RETAIN_FRONTEND_VERSIONS` (default 3).
4. **Cosa NON va dentro** (esplicito):
   - PEM path → resta `PLENORA_DEPLOY_PEM` env operatore
   - AWS access keys, credentials → mai nei profili, sempre IAM role / SSM
   - `.env` content (DATABASE_URL, JWT_SECRET_KEY, PLENORA_ENCRYPTION_KEY) → mai nei profili, restano per-installazione sul server target
   - SSM parameter values → mai
5. **Come aggiungere un profilo**: copia `prod.conf` → `<name>.conf`, sostituisci valori, commit. **Non è batch 1**: il primo nuovo profilo concreto richiede infra reale (Step 2).
6. **Come selezionare un profilo**:
   - `bash deploy.sh --profile=staging` (futuro)
   - `PLENORA_DEPLOY_PROFILE=staging bash deploy.sh` (futuro)
   - default `prod` se non specificato
7. **Profili locali (futuro, non batch 1)**: `*.local.conf` ignorati da git per esperimenti operatore.

### 2.3 `deploy/profiles/.gitignore` (nuovo)

```
*.local.conf
```

Predispone, ma irrilevante in batch 1 (nessun profilo locale esiste oggi).

### 2.4 `deploy.sh` (modifiche)

Patch suddivise in 4 commit indipendenti revertable singolarmente, in ordine raccomandato P5 → P1 → P2 → P3.

#### Commit 1 — P5: Profile loader minimale + PEM env

Modifiche outline:
- **Linee 7–12** (oggi: `PEM=...`, `SERVER=...`, `BACKEND_LOCAL=...`, `FRONTEND_LOCAL=...`, `MODE=...`):
  - Sostituire i 2 hardcoded `PEM`/`SERVER` con:
    - `PROFILE="${PLENORA_DEPLOY_PROFILE:-prod}"`
    - parsing flag `--profile=<name>` (override env se passato)
    - `PEM="${PLENORA_DEPLOY_PEM:-C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem}"` (default invariato per compat operatore)
  - `BACKEND_LOCAL` e `FRONTEND_LOCAL` restano (sono path **locali**, non per-environment).
- **Nuova funzione `load_profile()`**:
  - Risolve `PROFILE_FILE="$(dirname "$0")/deploy/profiles/${PROFILE}.conf"`.
  - Check `[ -f "$PROFILE_FILE" ]` → se manca: `echo "ERRORE: profilo '$PROFILE' non trovato in $PROFILE_FILE"; exit 1`.
  - `source "$PROFILE_FILE"` (shell-source-able).
  - **Required vars validation**: per ogni variabile obbligatoria (`SERVER`, `REMOTE_BACKEND_PATH`, `REMOTE_FRONTEND_PATH`, `APP_HEALTH_URL`, `APP_READY_URL`, `SYSTEMD_UNITS`, `ALLOWED_BRANCHES`), check `[ -z "${VAR}" ]` → exit 1 con messaggio specifico.
  - Defaults applicati per opzionali (`READY_TIMEOUT_SEC=60`, `READY_POLL_INTERVAL_SEC=2`, `RETAIN_FRONTEND_VERSIONS=3`).
- **Nuovo check PEM**: `[ -f "$PEM" ]` → se manca: exit 1 con messaggio "PEM non trovato: $PEM. Imposta PLENORA_DEPLOY_PEM env override.".
- **Log iniziale (banner)**: `echo "Deploy verso profilo=$PROFILE server=$SERVER (PEM: $(basename $PEM))"`.
- **Sostituire** ovunque `"$SERVER"` (ssh, scp) e i path remoti hardcoded con le variabili lette dal profilo (`$SERVER`, `$REMOTE_BACKEND_PATH`, `$REMOTE_FRONTEND_PATH`).
- `MODE` parsing aggiornato: lo script accetta `[backend|frontend|all|check-deps]` come oggi, ma anche `--profile=<name>` come prefisso/suffisso → da gestire con un mini parser argomenti (case-by-case bash, niente getopts complicato per restare minimale).

Effort: ~60 min.

#### Commit 2 — P1: No auto-commit + branch guard parametrico

Modifiche outline:
- **Linee 20–28** (oggi: blocco `if [ -n "$(git status --porcelain)" ]; then git add -A; git commit -m "deploy: ..."`):
  - **Eliminare** `git add -A` + `git commit -m "deploy: ..."`.
  - **Sostituire** con check fail-fast:
    ```
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERRORE: working tree sporco. Committa o stash prima del deploy."
        exit 1
    fi
    ```
- **Nuovo branch guard parametrico** (prima di `git push origin main`):
  - `CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)`
  - Verifica `CURRENT_BRANCH` in `ALLOWED_BRANCHES` (split su spazi).
  - Se non in allowlist: exit 1 con messaggio `"ERRORE: branch '$CURRENT_BRANCH' non in ALLOWED_BRANCHES='$ALLOWED_BRANCHES' del profilo '$PROFILE'."`.
- **`git push origin main`** → sostituire con `git push origin "$CURRENT_BRANCH"` per supportare profili che permettono branch != main (futuro). In `prod` resta `main`.

Effort: ~30 min.

#### Commit 3 — P2: Pre-deploy validate + check-deps + health baseline

Modifiche outline:
- **Nuova funzione `pre_deploy_check()`** (chiamata prima di `deploy_backend()` quando `MODE` include backend):
  1. SSH al `$SERVER` ed esegui:
     - `cd "$REMOTE_BACKEND_PATH" && venv/bin/python -c "from app.shared.cloud_backends import validate_storage_config; validate_storage_config()"` → exit 1 se non-zero.
  2. Riusare `check_deps()` esistente (linee 70–87) → invocare e propagare exit code.
  3. `curl -fsS --max-time 5 "$APP_HEALTH_URL"` da locale (se l'health URL è esposto pubblicamente via `APP_BASE_URL` o tunnel) **oppure** via SSH (`ssh ... "curl -fsS --max-time 5 $APP_HEALTH_URL"`). Decisione: usare SSH, perché `APP_HEALTH_URL` nel profilo `prod` è `127.0.0.1:5004` (interno, non esposto). Exit 1 su 503/timeout, log dump del JSON response.
- Tutti i check sono **read-only**: niente mutazione su server. Failure → exit 1 prima del `git pull`.
- `pre_deploy_check()` **non** invocato per `MODE=frontend` (non c'è restart backend a valle) né per `MODE=check-deps`.

Effort: ~75 min.

#### Commit 4 — P3: Post-restart `/ready` gate + systemctl is-active

Modifiche outline:
- **Nuova funzione `post_restart_health_gate()`** (chiamata dopo il blocco `git pull && pip install && systemctl restart` esistente in `deploy_backend()`):
  1. Polling `curl -fsS --max-time 5 "$APP_READY_URL"` via SSH:
     - Loop fino a `READY_TIMEOUT_SEC` con interval `READY_POLL_INTERVAL_SEC`.
     - 200 OK → break, log success.
     - 503 ripetuto fino a timeout → log dump del JSON, exit 1.
  2. Verifica systemd units:
     - `ssh ... "systemctl is-active $SYSTEMD_UNITS"` (passa la lista dal profilo).
     - Tutti i unit devono ritornare `active`. Se uno è `failed` / `inactive` / altro → log specifico, exit 1.
  3. `pre_deploy_check`/`post_restart_health_gate` non rimettono mano a `systemctl` su fail (lasciano lo stato all'operatore per decidere il rollback).
- Exit code distinti per failure type (utile per future GHA workflow):
  - `1` = ready timeout
  - `2` = systemctl unit not active
  - (`0` = success)

Effort: ~75 min.

#### Sintesi commit deploy.sh

| Commit | Patch | LOC delta stimate |
| --- | --- | --- |
| 1 | P5 profile loader + PEM env | +30 / -8 |
| 2 | P1 no auto-commit + branch guard | +12 / -8 |
| 3 | P2 pre_deploy_check | +25 / -0 |
| 4 | P3 post_restart_health_gate | +30 / -2 |
| **Totale** | | **+97 / -18 ≈ 176 LOC finali** |

---

## 3. Comandi di verifica read-only

Tutti **non-mutating** sul server. Da eseguire prima/durante/dopo il primo deploy con il nuovo flow.

### 3.1 Verifica locale (pre-merge)

```
# Profilo letto correttamente (su working tree con patch applicate, prima di commit/push)
PLENORA_DEPLOY_PROFILE=prod bash -n deploy.sh                       # syntax check
bash deploy.sh --help 2>&1 || true                                  # se aggiunto help (opzionale)

# Profilo inesistente fallisce esplicitamente
PLENORA_DEPLOY_PROFILE=ghost bash deploy.sh                         # exit 1 + msg "profilo 'ghost' non trovato"

# PEM mancante fallisce esplicitamente
PLENORA_DEPLOY_PEM=/tmp/nonexistent bash deploy.sh                  # exit 1 + msg "PEM non trovato"

# Working tree sporco fallisce
echo "x" >> /tmp/dirty_marker; cp /tmp/dirty_marker URBY-ETL/dirty_test
bash deploy.sh                                                      # exit 1 + msg "working tree sporco"
rm URBY-ETL/dirty_test

# Branch fuori allowlist fallisce (test su branch feature)
git checkout -b test/branch-guard
bash deploy.sh                                                      # exit 1 + msg "branch 'test/branch-guard' non in ALLOWED_BRANCHES"
git checkout main && git branch -D test/branch-guard

# Required var mancante (simulazione: profilo locale fittizio)
echo 'SERVER=""' > URBY-ETL/deploy/profiles/dryrun.local.conf
PLENORA_DEPLOY_PROFILE=dryrun.local bash deploy.sh                  # exit 1 + msg "SERVER richiesto"
rm URBY-ETL/deploy/profiles/dryrun.local.conf

# Anti-regressione hardcode in deploy.sh (grep manuale)
grep -nE "15\.160\.219\.93|/home/ubuntu/plenora-etl|127\.0\.0\.1:5004|lab\.plenora\.it|plenora-backend|plenora-celery|plenora-beat" URBY-ETL/deploy.sh
# Atteso: ZERO match (tutto deve venire dal profilo)
grep -nE "15\.160\.219\.93|/home/ubuntu/plenora-etl|127\.0\.0\.1:5004|lab\.plenora\.it" URBY-ETL/deploy/profiles/prod.conf
# Atteso: match presenti (è il profilo, non lo script)
```

### 3.2 Pre-flight remoto (read-only, prima di applicare le patch)

Già SSH con il deploy.sh attuale (per misurare baseline pre-cambio):

```
ssh -i $PEM ubuntu@15.160.219.93 "curl -fsS http://127.0.0.1:5004/health"
ssh -i $PEM ubuntu@15.160.219.93 "curl -fsS http://127.0.0.1:5004/ready"
ssh -i $PEM ubuntu@15.160.219.93 "systemctl is-active plenora-backend plenora-celery plenora-beat"
ssh -i $PEM ubuntu@15.160.219.93 "venv/bin/python -c 'from app.shared.cloud_backends import validate_storage_config; validate_storage_config(); print(\"ok\")'"
ssh -i $PEM ubuntu@15.160.219.93 "df -h /home/ubuntu /tmp; free -m"
```

Tutto deve tornare 200 / `active` / `ok` / metriche disponibili. Se non torna sano **prima**, non ha senso testare il batch 1.

### 3.3 Verifica post-merge (dopo applicazione patch, prima del primo deploy reale)

```
# Profilo prod risolto
PLENORA_DEPLOY_PROFILE=prod bash -c 'source URBY-ETL/deploy/profiles/prod.conf; echo "$SERVER $REMOTE_BACKEND_PATH $APP_READY_URL"'

# Banner stampato all'avvio (dry-run interrompendo dopo banner: lasciare PEM mancante)
PLENORA_DEPLOY_PEM=/tmp/nonexistent PLENORA_DEPLOY_PROFILE=prod bash URBY-ETL/deploy.sh
# Atteso: stampa "Deploy verso profilo=prod server=ubuntu@15.160.219.93 (PEM: nonexistent)" + exit 1
```

### 3.4 Verifica primo deploy reale (in finestra controllata)

Sequenza manuale operatore:

```
cd URBY-ETL
git status --porcelain                                              # vuoto
git rev-parse --abbrev-ref HEAD                                     # main
bash deploy.sh                                                      # invocazione default = profilo prod
# Osserva log:
#   - banner profilo + server + PEM
#   - pre_deploy_check passa
#   - git pull / pip install / systemctl restart
#   - post_restart_health_gate /ready 200 entro READY_TIMEOUT_SEC
#   - systemctl is-active OK su 3 unit
```

Post-deploy: checklist completa `phase_0_5x_final_synthesis.md §10.3`.

---

## 4. Rollback plan

### 4.1 Rollback per-commit (granulare)

Ogni commit P5/P1/P2/P3 è revertable indipendentemente:

```
git revert <commit-sha-P3>      # rimuove post_restart_health_gate
git revert <commit-sha-P2>      # rimuove pre_deploy_check
git revert <commit-sha-P1>      # ripristina auto-commit + rimuove branch guard
git revert <commit-sha-P5>      # ripristina hardcoded SERVER/PEM
```

Il revert di P5 rimuove anche la dipendenza dal profilo. I file `deploy/profiles/prod.conf`, `README.md`, `.gitignore` restano sul disco ma diventano dead code finché P5 non è ri-applicato. Cleanup opzionale: `git rm` di quei file in un commit separato. **Non urgente**.

### 4.2 Rollback aggregato (full revert batch 1)

Se serve tornare al `deploy.sh` originale in un colpo solo:

```
git revert <commit-P3> <commit-P2> <commit-P1> <commit-P5>
# (in quest'ordine; revert in ordine inverso applicazione)
# Oppure, se i commit sono consecutivi:
git revert <oldest-sha>..<newest-sha>
```

### 4.3 Rollback runtime (deploy fallito)

Se il primo deploy con il nuovo `deploy.sh` fallisce a metà:

| Stato | Azione |
| --- | --- |
| `pre_deploy_check` exit 1 | nessun side-effect server (read-only); operatore investiga lato server, fix, ri-prova |
| `git pull` riuscito ma `pip install` fallisce | `ssh ... "cd $REMOTE_BACKEND_PATH && git reset --hard <SHA-PRE>"`; **non** restart (i servizi girano ancora con vecchio venv ma vecchio codice → coerente) |
| Restart riuscito ma `post_restart_health_gate` exit 1 | rollback manuale (vedi `phase_0_5x_final_synthesis.md` rollback procedure): `ssh ... "cd $REMOTE_BACKEND_PATH && git reset --hard <SHA-PRE> && venv/bin/pip install -q -r requirements.txt && sudo systemctl restart $SYSTEMD_UNITS"` + verifica `/ready` |

Tempo target: < 5 min dal momento decisione rollback.

### 4.4 Rollback config profilo

Se il contenuto di `deploy/profiles/prod.conf` ha valori errati (es. typo SERVER):
- Edit locale del file, commit, push, riprova deploy.
- **Niente coordinamento server-side**: il file vive nel repo client-side, non sul server target.

---

## 5. Rischi residui

### 5.1 Rischi noti del batch 1

| Rischio | Probabilità | Impatto | Mitigazione |
| --- | --- | --- | --- |
| Profilo `prod.conf` con valore errato nel primo commit (typo, copia incompleta) | bassa | medio (deploy fallisce subito su `pre_deploy_check` o `post_restart_health_gate`) | Comparison riga-per-riga col `deploy.sh` attuale prima del merge. Verifica grep §3.1 |
| `validate_storage_config` fallisce in `pre_deploy_check` su un setup oggi sano | molto bassa | basso (operatore investiga, non c'è side-effect) | il check è già parte del boot backend; se fallisce a freddo → c'era già un problema |
| `READY_TIMEOUT_SEC=60` insufficiente per cold-start uvicorn | bassa | medio (deploy.sh exit 1 falsamente) | timeout è configurabile per profilo. Se 60s insufficiente → bumpare in `prod.conf`, no patch al codice |
| `systemctl is-active` ritorna `activating` durante restart (race) | bassa | basso (exit 2) | il polling `/ready` precede il check; quando `/ready` è 200, i unit sono già `active` |
| Bash parser argomenti `--profile=` interagisce male con MODE esistente (`backend|frontend|all|check-deps`) | media | basso (parsing CLI ambiguo) | mini parser custom, non `getopts`. Test §3.1 specifici per ognuna delle 4 mode |
| File `prod.conf` shell-source-able può contenere comandi shell (security) | bassa | medio (chi committa il file può iniettare bash) | review manuale del file in PR. Il pattern shell-source è scelto per minimalismo (no nuove dipendenze yq/jq); review di PR `deploy/profiles/*` resta gate manuale |
| Ambiente Windows (`bash` su Git Bash / Cygwin) parser path differente | media | basso | i path nei `.conf` sono Unix-side server (`/home/ubuntu/...`); il PEM resta Windows-side via env. Da testare prima |
| Operatore (Marco) confuso dal nuovo banner / messaggi | molto bassa | trascurabile | banner spiega tutto; rollback per-commit possibile in <5 min |

### 5.2 Rischi non gestiti in batch 1 (esplicito)

Questi rimangono **fuori dal batch 1** e sono noti:

- Bus factor 1 totale (Marco) — solo P5 lo riduce parzialmente abilitando un secondo PEM via env, ma il setup organizzativo non è batch 1
- Niente alerting su deploy fail (operatore deve guardare l'output dello script)
- Niente notifica utenza durante deploy
- Nessuna conferma esplicita prima del `git push` (fail-fast su working tree sporco è il guard più stretto)
- Rollback non automatico su `/ready` 503: operatore decide
- Migrazione DB pendente non gate'd: se la nuova release ha migration, il backend la applica al boot e `/ready` può essere 503 per `migrations_aligned` finché il runner finisce. `READY_TIMEOUT_SEC=60` può essere insufficiente per migration grandi → operatore aumenta timeout nel profilo se necessario

---

## 6. Conferma out-of-scope

Il batch 1 **non include**:

- ✗ P4 atomic frontend swap (separato in batch 2, richiede precondizione server `mv dist dist-initial && ln -s`)
- ✗ Profili concreti oltre `prod` (no `staging.conf`, no `client-x.conf` in questo batch — solo `README.md` come template)
- ✗ Framework deploy complesso (Ansible, Fabric, custom orchestrator)
- ✗ Nuove dipendenze tool: nessun yq, jq, python wrapper. Profilo `.conf` resta shell-source-able
- ✗ Modifiche systemd unit file
- ✗ Modifiche `.env` server (resta per-installazione, mai automatico)
- ✗ AWS SSM sync automatico
- ✗ GitHub Actions workflow
- ✗ Staging environment setup concreto
- ✗ IaC esteso (Terraform import EC2, RDS, IAM)
- ✗ Refactor backend o frontend
- ✗ Test contract anti-regressione hardcode (citato in `phase_0_5x_final_synthesis.md §10.5` ma rinviato post-batch 1)
- ✗ Multi-target parallelo (un deploy = un profilo per invocazione)
- ✗ Secret management nei profili (resta IAM role + SSM + env operatore)
- ✗ Automazione cleanup dist directory (lato batch 2 con P4)

Tutti questi punti restano disponibili come **incrementi successivi** (Step 1+, batch 2+, Phase 1+) con i prerequisiti naturali documentati in `phase_0_5x_final_synthesis.md §6.3`.

---

## 7. Stato

**Status: piano patch documentato, NON applicato.**

Output:
- 1 file modify (`deploy.sh`)
- 3 file create (`deploy/profiles/prod.conf`, `README.md`, `.gitignore`)
- 4 commit P5 → P1 → P2 → P3 con outline incremento +97/-18 LOC stimate
- 8 comandi di verifica read-only locale + 6 pre-flight remoti + 3 post-merge + sequenza primo deploy
- Rollback per-commit + aggregato + runtime + config
- 8 rischi residui noti + 8 rischi non gestiti dichiarati
- Out-of-scope esplicito (14 voci ✗)

Effort totale stimato: **~4h dev** + **~30 min review** + **~30 min smoke deploy controllato** = ~5h end-to-end.

Decisione esplicita richiesta dall'operatore prima dell'applicazione:
1. PR target branch (`main` direttamente o branch dedicato `step0/batch1`)
2. Finestra di smoke deploy
3. Conferma valori `prod.conf` (estratti uguali al hardcoded oggi, ma vale review umano)

Nessun comando esegutito. Nessun file modificato.

---

*End of Rank 1 / Step 0 Batch 1 Patch Plan.*
