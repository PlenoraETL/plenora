# Deploy profiles

Profili **public** per ambiente di deploy. Selezionati da `deploy.sh` via env
`PLENORA_DEPLOY_PROFILE` o flag `--profile=<name>`. Default: `prod`.

Lo scopo e' rendere `deploy.sh` un wrapper parametrico: stesso script, stessa
logica, parametri per-environment. Cosi' lo stesso codice puo' deployare
verso `prod`, `staging`, o un futuro ambiente cliente senza modifiche.

## Profili presenti

| Profilo | File | Descrizione |
| --- | --- | --- |
| `prod` | `prod.conf` | Server di produzione (lab.plenora.it). Unico profilo concreto in batch 1. |

## Selezione profilo

```bash
# Default (prod)
bash deploy.sh

# Tramite env
PLENORA_DEPLOY_PROFILE=prod bash deploy.sh

# Tramite flag CLI
bash deploy.sh --profile=prod
```

L'ordine di precedenza: `--profile=` flag > env `PLENORA_DEPLOY_PROFILE` > default `prod`.

## Guardie pre-deploy (applicate a `backend|frontend|all`)

Prima di qualunque deploy reale o dry-run su un mode che pubblica codice
o build, lo script applica un set di guardie locali. Tutte read-only,
exit 1 al primo fail.

1. **Working tree pulito** — niente file modificati o untracked in
   `backend/`. Mai auto-commit.
2. **Branch allowlist** — il branch corrente deve essere in
   `ALLOWED_BRANCHES` del profilo (es. `main` per `prod`).
3. **Upstream-sync** — il branch corrente deve essere allineato col
   suo upstream, e l'upstream deve essere esattamente
   `origin/<branch>`:
   - manca upstream → `git push -u origin <branch>` esplicito;
   - upstream ≠ `origin/<branch>` (es. fork personale, mirror, altro
     remote) → exit 1, riconfigura con
     `git branch --set-upstream-to=origin/<branch> <branch>`;
   - branch ahead di `origin/<branch>` → `git push origin <branch>`
     esplicito;
   - branch behind → `git pull --rebase origin <branch>`;
   - branch divergente → risolvi con rebase + push esplicito.

   Il vincolo `upstream == origin/<branch>` evita drift fra il ref
   verificato dalla guard e il ref usato runtime: `deploy_backend` fa
   `git pull origin <branch>` sul server, quindi qualunque upstream
   diverso renderebbe la guard inconsistente con il deploy reale.

   La guard esegue un `git fetch --quiet origin
   +refs/heads/<branch>:refs/remotes/origin/<branch>` (refspec esplicito
   per aggiornare `origin/<branch>`, non solo `FETCH_HEAD`). Il fetch
   è read-only: niente merge, niente rebase, niente modifiche al
   working tree. **Nessun `git push` automatico** — la pubblicazione
   resta a carico dell'operatore. Questo evita che
   `bash deploy.sh frontend` pubblichi un `dist/` costruito da codice
   locale non ancora su GitHub.

`check-deps` non applica queste guardie (e' solo un probe SSH read-only
sul server, niente push, niente restart, niente swap frontend).

## Dry-run / check-only

Prima di un deploy reale, **sempre** verificare la configurazione con
una passata dry-run. In quella modalita' lo script:

- carica e valida il profilo + required vars
- verifica `PLENORA_DEPLOY_PEM` (env + file esistente)
- applica branch + working-tree guard per i mode che deployano
- stampa cosa farebbe (banner + comandi remoti pianificati con prefisso `[DRY-RUN] would run:`)
- **NON esegue** ssh, scp, npm run build, git push, systemctl restart, rm sul server

Selezione dry-run (entrambe equivalenti):

```bash
# Flag CLI
bash deploy.sh --check-only backend
bash deploy.sh --dry-run backend     # alias

# Env var
DRY_RUN=1 bash deploy.sh backend
```

Anche in dry-run i guard locali sono pienamente attivi: working tree
sporco o branch fuori `ALLOWED_BRANCHES` -> `exit 1` come in modalita'
reale. Questo significa che il dry-run e' anche il modo sicuro per
testare i guard del nuovo `deploy.sh` senza rischiare side-effect
remoti (vedi
[`incident_note_step0_unintended_restart.md`](../../../orchestrate/phase_0_consolidation/incident_note_step0_unintended_restart.md)
per il razionale).

**Regola operativa**: qualunque modifica futura a `deploy.sh` deve
essere testata via dry-run su `prod` (o profilo dedicato) prima di un
deploy reale. Il banner mostra esplicitamente `[DRY-RUN: nessun comando
remoto verra' eseguito]` quando il flag e' attivo.

## Variabili obbligatorie

Lo script fallisce esplicitamente se una di queste manca nel profilo:

| Variabile | Esempio | Note |
| --- | --- | --- |
| `SERVER` | `ubuntu@host.example.com` | Target SSH |
| `REMOTE_BACKEND_PATH` | `/home/ubuntu/plenora-etl/backend` | Path checkout backend sul server |
| `REMOTE_FRONTEND_PATH` | `/home/ubuntu/plenora-etl/frontend` | Path checkout frontend sul server |
| `APP_HEALTH_URL` | `http://127.0.0.1:5004/health` | Endpoint liveness (PG + Redis) |
| `APP_READY_URL` | `http://127.0.0.1:5004/ready` | Endpoint readiness rigoroso |
| `SYSTEMD_UNITS` | `plenora-backend plenora-celery plenora-beat` | Space-separated, restart + is-active |
| `ALLOWED_BRANCHES` | `main` | Allowlist branch deploy permessi |

## Variabili opzionali

| Variabile | Default | Note |
| --- | --- | --- |
| `APP_BASE_URL` | — | URL pubblico (per smoke esterni futuri, non usato in batch 1) |
| `BACKEND_PORT` | — | Solo informativo |
| `READY_TIMEOUT_SEC` | `60` | Timeout polling `/ready` post-restart |
| `READY_POLL_INTERVAL_SEC` | `2` | Intervallo polling |
| `RETAIN_FRONTEND_VERSIONS` | `3` | Retain N versioni `dist-{sha}/` (usato da batch 2 P4) |

## Cosa NON va dentro un profilo

I file `<profile>.conf` sono **versionati** e **public**. Non devono mai contenere:

- **PEM path** → resta env `PLENORA_DEPLOY_PEM` per-operatore (vedi sotto)
- **AWS access keys / credentials** → mai. Il server usa IAM role attached, gli script backup usano SSM Parameter Store
- **Contenuto `.env`** (`DATABASE_URL`, `JWT_SECRET_KEY`, `PLENORA_ENCRYPTION_KEY`, ecc.) → resta sul server target in `${REMOTE_BACKEND_PATH}/.env`
- **Valori SSM Parameter Store** → mai
- **Token o secret di terze parti** → mai

Review della PR di un profilo deve verificare l'assenza di queste classi.

## PEM SSH per-operatore

`deploy.sh` non contiene **alcun path PEM hardcoded** e non usa default
nello script. La variabile `PLENORA_DEPLOY_PEM` e' **obbligatoria**: se
manca o il file non esiste, lo script esce con `exit 1` prima di toccare
qualunque server.

### Configurazione operatore

Esempio bash / WSL / Linux / macOS:

```bash
export PLENORA_DEPLOY_PEM="$HOME/secrets/plenora.pem"
chmod 600 "$PLENORA_DEPLOY_PEM"
```

Esempio Windows (Git Bash):

```bash
export PLENORA_DEPLOY_PEM="/c/Users/<utente>/secrets/plenora.pem"
```

Persistenza tra shell: aggiungere la riga `export ...` al proprio
`~/.bashrc` / `~/.zshrc` (o `.bash_profile` su Git Bash). Il file PEM
**non va mai committato** nel repo, mai condiviso fra operatori senza
canale sicuro, e dovrebbe vivere fuori dalla working copy del repo.

### Multi-operatore

Operatori diversi possono avere path PEM diversi senza modificare lo
script: ognuno esporta la propria variabile. Non c'e' alcun valore
"di default" che privilegi un singolo operatore.

## Come aggiungere un profilo

```bash
cp deploy/profiles/prod.conf deploy/profiles/<nuovo>.conf
# Edita SERVER / path / URL / ALLOWED_BRANCHES per il nuovo ambiente
git add deploy/profiles/<nuovo>.conf
```

Il primo profilo aggiuntivo richiede infra reale (EC2, DNS, DB, bucket S3, SSM
namespace) — non e' parte di Step 0 batch 1. Vedi
`orchestrate/phase_0_consolidation/phase_0_5x_final_synthesis.md` §6.3 (Step 2).

## Profili locali (non versionati)

I file `*.local.conf` sono ignorati da git (vedi `.gitignore`). Possono essere
usati da un operatore per esperimenti locali (es. simulare un target offline).
Non sostituiscono mai un profilo pubblico per deploy reali.

## Format

I profili sono **shell-source-able** (`source <file>`). Niente nuove dipendenze
tool (no yq, jq, python wrapper). Sintassi: `KEY="value"` per riga, commenti
con `#`. Validazione in `deploy.sh:load_profile`.
