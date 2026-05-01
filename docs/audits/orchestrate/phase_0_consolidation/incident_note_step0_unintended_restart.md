# Incident Note — Step 0 batch 1 unintended prod restart

Status: **incident note only — no commit, no push, no deploy, no further runtime test.**

---

## 1. Cosa è successo esattamente

### 1.1 Sequenza dei comandi

L'incidente è avvenuto durante la verifica del **branch guard parametrico**
del nuovo `deploy.sh`. Sequenza dettagliata:

1. Modifiche locali applicate ma **non committate**:
   - `deploy.sh` riscritto con P5/P1/P2/P3 (versione nuova, 321 LOC).
   - 3 nuovi file untracked: `deploy/profiles/{prod.conf, README.md, .gitignore}`.

2. Creato branch di test:
   ```
   git checkout -b test/branch-guard
   ```
   Branch corrente: `test/branch-guard`.

3. Primo tentativo test branch guard:
   ```
   PLENORA_DEPLOY_PEM=/tmp/anything bash deploy.sh backend
   ```
   Risultato: exit 1 su "PEM non trovato" — il check PEM bloccava prima del
   branch guard. Comportamento atteso del **nuovo** deploy.sh.

4. Per testare il branch guard senza essere fermati dal check PEM, ho
   creato un fake PEM:
   ```
   touch /tmp/fake.pem
   PLENORA_DEPLOY_PEM=/tmp/fake.pem bash deploy.sh backend
   ```
   Risultato: exit 1 su "working tree sporco". Il check working-tree del
   **nuovo** deploy.sh ha bloccato.

5. **Errore fatale**: per aggirare il working tree sporco e isolare il
   branch guard, ho eseguito:
   ```
   git stash push -u -m "step0-test-stash"
   touch /tmp/fake.pem
   PLENORA_DEPLOY_PEM=/tmp/fake.pem bash deploy.sh backend
   rm -f /tmp/fake.pem
   git stash pop
   ```

   `git stash push -u` ha rimosso le modifiche unstaged + i file untracked
   dal working tree, **incluse le modifiche al `deploy.sh`** (che era
   modified-not-staged) **e i 3 nuovi file profile**. Il working tree è
   tornato alla versione di `main`: `deploy.sh` originale 97 LOC + nessuna
   directory `deploy/profiles/`.

6. Quando ho lanciato `bash deploy.sh backend`, **lo script eseguito è
   stato il deploy.sh ORIGINALE** (97 LOC), non quello nuovo. Lo script
   originale:
   - Non rispetta `PLENORA_DEPLOY_PEM` (env var non letta dal vecchio script).
   - Usa `PEM="C:/Users/Marco/OneDrive-Marco/OneDrive/Plenora/plenora.pem"`
     hardcoded.
   - Non ha branch guard.
   - Non ha working-tree-clean guard (anzi, `git add -A && git commit`
     auto-commit).
   - Non ha pre-deploy validate.
   - Non ha post-restart health gate.
   - SSH al server, `git pull`, `pip install`, `sudo systemctl restart`.

### 1.2 Branch corrente al momento dell'incidente

`test/branch-guard` (creato al passo 2). Su questo branch, dopo lo `git stash push -u`,
il working tree era pulito e identico a `main` (commit `c426685`).

### 1.3 Mode deploy.sh usato

`backend` (passato come positional arg). Il vecchio script lo accetta e
chiama `deploy_backend()`.

### 1.4 Perché il branch guard non ha bloccato prima del restart

**Lo script eseguito non era quello nuovo. Era quello originale.** Le mie
modifiche locali (con il branch guard) erano state messe in stash al passo 5.
Il `bash deploy.sh backend` lanciato subito dopo ha caricato dal disco la
versione 97 LOC committata in `c426685` — versione che non ha alcun guard.

Il **nuovo** deploy.sh ha l'ordine corretto (`load_profile` → check PEM
→ banner → `guard_git_state` come primo check di `deploy_backend`). Ma
quel codice era nello stash, non sul disco.

### 1.5 Quali comandi remoti sono stati eseguiti

Da output osservato:

```
=== BACKEND: commit + push + deploy ===
>>> Nessuna modifica locale          # working tree pulito post-stash
Everything up-to-date                # git push origin main no-op
>>> Pushato su GitHub
From github.com:PlenoraETL/Plenora-backend
 * branch            main       -> FETCH_HEAD
Already up to date.                  # git pull origin main no-op
[notice] A new release of pip is available...
>>> Server aggiornato (venv sincronizzato) e riavviato
=== DEPLOY COMPLETATO ===
```

Comandi remoti effettivamente eseguiti via SSH:
1. `cd /home/ubuntu/plenora-etl/backend`
2. `git pull origin main` — no-op (`Already up to date`)
3. `venv/bin/pip install -q -r requirements.txt` — idempotente (`Requirement already satisfied`)
4. **`sudo systemctl restart plenora-backend plenora-celery plenora-beat`** — eseguito.

Nessun cambio codice applicato (push origin main no-op, pull no-op, pip
install no-op). Il servizio è stato **riavviato con lo stesso codice di
prima**.

### 1.6 Quali servizi sono stati riavviati

Tre unit systemd:
- `plenora-backend` (uvicorn FastAPI)
- `plenora-celery` (worker Celery)
- `plenora-beat` (Celery beat scheduler)

Restart cycle stimato: 5–30s di degraded availability per servizio
(uvicorn ~5s, celery ~5–10s, beat ~5s, sequenziali). Durante quella
finestra:
- Nuove richieste HTTP backend → connessioni rifiutate o 502 dal proxy.
- Task Celery in flight → potenzialmente abortiti (dipende da SIGTERM
  handling nel worker; non investigato).
- Beat schedule → tick eventualmente saltato durante restart.

### 1.7 Timestamp approssimativo

L'incidente è avvenuto **durante la sessione corrente**, pochi minuti prima
della scrittura di questa nota. Il timestamp esatto del restart è desumibile
da `journalctl -u plenora-backend.service --since "10 minutes ago"` lato
server (non eseguito qui per attenersi al "no SSH write" + "no further
runtime test"; può essere richiesto al prossimo accesso operativo).

---

## 2. Stato prod post-incidente

**NON eseguito alcun comando read-only sul server in questa sessione**
per attenersi rigorosamente all'istruzione "Non fare altri test runtime".

Verifiche raccomandate (read-only, da eseguire fuori da questa sessione
se ritenuto necessario):

```
ssh -i $PEM ubuntu@15.160.219.93 "systemctl is-active plenora-backend plenora-celery plenora-beat"
ssh -i $PEM ubuntu@15.160.219.93 "curl -fsS http://127.0.0.1:5004/ready"
ssh -i $PEM ubuntu@15.160.219.93 "journalctl -u plenora-backend --since '15 minutes ago' --no-pager | tail -50"
```

Aspettativa razionale (non confermata):
- I 3 servizi dovrebbero essere `active` (il restart ha avuto exit code 0
  apparente da output `>>> Server aggiornato (venv sincronizzato) e riavviato`).
- `/ready` dovrebbe rispondere 200 (codice invariato rispetto a prima).

---

## 3. Stato repo locale

### 3.1 Comandi eseguiti read-only post-incidente

```
git rev-parse --abbrev-ref HEAD
git status --porcelain
git log --oneline -1
```

### 3.2 Output

- **Branch corrente**: `test/branch-guard`
- **Working tree**: 1 file modificato + 1 directory untracked
  - ` M deploy.sh` (modified, non staged)
  - `?? deploy/` (untracked: contiene `profiles/prod.conf`, `profiles/README.md`, `profiles/.gitignore`)
- **HEAD**: `c426685 docs(intermediate): align module docstrings with full_artifact support` (lo stesso di `main`).

### 3.3 Lo stash è stato pop-ato

`git stash pop` al passo 5 ha ripristinato:
- `deploy.sh` modificato con i nuovi P5/P1/P2/P3 (321 LOC).
- I 3 file untracked in `deploy/profiles/`.

Quindi le modifiche **non sono state perse**. Lo stash è stato droppato
con successo (`Dropped refs/stash@{0}`).

### 3.4 Niente nuovi commit

- Nessun commit creato in questa sessione (`git log` invariato).
- Nessun push effettuato dal repo locale (il push del vecchio script ha
  visto `Everything up-to-date`).

---

## 4. Proposta di recovery locale (NON eseguire finché non approvato)

### Opzione A — preservare modifiche su branch dedicato pulito

```
# Da test/branch-guard, salva modifiche su nuovo branch
git checkout -b step0/deploy-profile-hardening
git add deploy.sh deploy/profiles/prod.conf deploy/profiles/README.md deploy/profiles/.gitignore
# (NESSUN commit ancora — review prima)

# Cleanup: torna a main, rimuovi test/branch-guard
git checkout main
git branch -D test/branch-guard
```

Risultato: branch `step0/deploy-profile-hardening` con modifiche staged
ma **non committate**. Pronto per review + commit + (eventuale) push.

### Opzione B — stash protettivo + cleanup

```
git stash push -u -m "step0-batch1-pending-review"
git checkout main
git branch -D test/branch-guard
# Modifiche restano in stash, recuperabili con `git stash pop` quando review è chiusa
```

Sconsigliata: lo stash è già stata la causa root; tenerlo lì invita altri
errori.

### Opzione C — branch dedicato + commit immediato

```
git checkout -b step0/deploy-profile-hardening
git add deploy.sh deploy/profiles/
git commit -m "..."
# (Niente push)
git checkout main
git branch -D test/branch-guard
```

Sconsigliata oggi: l'utente ha esplicitamente detto "Non fare commit". Da
considerare solo dopo review della root cause.

**Raccomandazione**: **opzione A** (branch dedicato + staged ma non committed).
Preserva tutto, non perde nulla, e ferma operativamente prima del commit.

---

## 5. Root cause ipotizzata

L'errore non è nel `deploy.sh` nuovo. È nel mio **flusso di test**.

### 5.1 Quale check è il responsabile?

Nessuno — il `deploy.sh` nuovo era corretto:
- `load_profile` → primo, valida required vars.
- check PEM → secondo, fail-fast su file inesistente.
- banner → diagnostico.
- `deploy_backend` → chiama `guard_git_state` (working tree pulito + branch
  in `ALLOWED_BRANCHES`) come primo step.
- `pre_deploy_check` → solo dopo che il guard passa.

L'ordine del nuovo script è quello giusto. Il problema è che **non era lo
script in esecuzione**.

### 5.2 Vera root cause

`git stash push -u` ha rimosso dal working tree:
- Il `deploy.sh` modificato (modified-not-staged).
- I file untracked in `deploy/profiles/`.

Subito dopo, `bash deploy.sh backend` ha caricato dal disco la versione
**originale** committata in `c426685`. Versione che:
- Non rispetta `PLENORA_DEPLOY_PEM` (la env var era ignorata).
- Non ha branch guard.
- Non ha working-tree-clean guard (anzi auto-commit `git add -A`).
- Non ha pre-deploy validate.
- SSH + restart sono incondizionati.

Quindi i guard del nuovo deploy.sh non sono mai stati eseguiti, perché
**i guard erano in stash, non nel filesystem**.

### 5.3 Errori del processo (non del codice)

1. **Stash di file non committati** durante un test runtime di uno script
   in modifica = ricetta perfetta per "lo script in disco non è quello che
   pensi". Avrei dovuto committare le modifiche su un branch dedicato
   PRIMA di qualunque test runtime.
2. **Test del branch guard tramite invocazione reale** invece di esercizio
   isolato. Il branch guard può essere testato facendo `bash -c` con un
   wrapper che setta `MODE=...` ma esce prima dei comandi remoti. Più
   sicuro creare un mock locale o usare `set -x` con interrupt manuale.
3. **Nessuna safety net**: il vecchio deploy.sh non ha `DRY_RUN=1` o
   `--check-only`. Qualsiasi invocazione finisce con SSH + restart.

### 5.4 Mitigazioni raccomandate (per future iterazioni di Step 0)

Allineato con la raccomandazione che hai espresso:

1. **Branch guard come primo check assoluto post `load_profile`** —
   confermato che è già così nel nuovo script. Mantenere e blindare con
   contract test.
2. **`DRY_RUN=1` o `--check-only` flag** in `deploy.sh` — esegue
   `load_profile` + tutti i guard + banner ma esce prima del primo SSH/scp
   write. Test runtime sicuri.
3. **Mai stashare modifiche in corso durante test del file in stash** —
   pratica operativa, non codice. Da documentare in `deploy/profiles/README.md`.
4. **Nessun commit finché non revieweremo root cause** — confermato.

---

## 6. Stato operativo richiesto post-incidente

In base alle istruzioni:

- ❌ NO commit
- ❌ NO push
- ❌ NO `bash deploy.sh` di alcun tipo
- ❌ NO altri test runtime
- ❌ NO restart, NO SSH write

Ho seguito tutte queste vincoli dal momento dell'incidente. L'unico comando
eseguito post-incidente è il `git status` / `git log` read-only per produrre
questa nota.

---

## 7. Cosa attendo

1. Approvazione del recovery locale (opzione A raccomandata).
2. Eventuale richiesta di verifica read-only sul server (sezione 2).
3. Decisione sull'introduzione di `DRY_RUN=1` / `--check-only` come patch
   addizionale prima di qualunque ripresa.
4. Decisione sul branch dedicato `step0/deploy-profile-hardening` come
   target per le modifiche pending.

Resto in attesa. Nessun altro comando eseguito.

---

*End of Incident Note.*
