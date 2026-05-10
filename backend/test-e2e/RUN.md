# Plenora E2E — RUN log

Cronaca dei passi effettivi eseguiti per portare l'ambiente E2E
da zero (nessun container) a "12/12 PREP verdi + 6/6 contract verdi".
Ogni step ha comando, esito, problemi incontrati, fix applicato.

Versione di riferimento: prima sessione di setup.

---

## Stato finale

- 6 container app: postgres, redis, backend, worker, beat, frontend (root compose)
- 2 container fixture: plenora-e2e-postgres, plenora-e2e-fake-rest (E2E compose, profilo `core`)
- 1 container runner: plenora-e2e-runner (basato su `plenora-backend:scaffold` + pytest)
- 2 utenti DB: `admin_plenora` (superadmin, UI), `tester` (admin, runner)
- 2 network docker: `plenora_default` (root), `plenora-e2e` (cross-stack)
- 6/6 contract test verdi, 12/12 PREP test verdi

URL operativi:

| servizio | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend | http://localhost:5004 |
| Backend docs | http://localhost:5004/docs |
| Backend ready | http://localhost:5004/ready |
| Fake REST | http://localhost:8080 |
| Postgres root (host port) | localhost:5432 (via container `plenora-postgres-1`) |
| Postgres E2E (host port) | localhost:5433 (via container `plenora-e2e-postgres`) |

---

## Step 1 — Setup file locali

| sub-step | comando | esito |
|---|---|---|
| 1.1 | `cp .env.docker.example .env.docker` + sed dei due secret 64-char | ✅ |
| 1.2 | Creato `docker-compose.override.yml` per agganciare backend/worker/beat/redis/frontend alla network `plenora-e2e` | ✅ |
| 1.2-bis | Aggiunti `.env.docker` e `docker-compose.override.yml` a `.gitignore` | ✅ |
| 1.3 | `cp backend/test-e2e/docker/.env.example backend/test-e2e/docker/.env` | ✅ |

---

## Step 2 — Bootstrap

| sub-step | comando | esito |
|---|---|---|
| 2.1 | `docker network create plenora-e2e` | ✅ |
| 2.2 | `./backend/test-e2e/bootstrap.fish --skip-venv` | ⚠️ → ✅ dopo fix |

**Bug fixato**: `bootstrap.fish:170` usava `cat <<EOF` (bash heredoc),
ma è uno script `fish`. Sostituito con `printf '%s\n' \\` multilinea.

---

## Step 3 — Stack app Plenora

```fish
docker compose up -d --build
```

**Bug fixato**: prima build falliva con `can't stat
'backend/.pytest-run/...test_abs_joins_deep_nested_rel0'` perché il
`.dockerignore` aveva `backend/.pytest-run-*/` (con dash) ma la dir
reale è `backend/.pytest-run/` (senza dash). Aggiunto pattern corretto
+ rimosse le dir orphan locali.

Build durata: ~5 min al primo run (compilazione layer
scientific stack: numpy, pandas, oracledb, geopandas, pyarrow).

---

## Step 4 — Stack fixture E2E (profilo core)

```fish
cd backend/test-e2e/docker && docker compose --profile core up -d
```

**Bug fixato — pip install nel Dockerfile fake-rest**:
- Primo errore: SIGSEGV in `pip install` su `python:3.11-slim` (bug
  intermittente noto del pip 24.0 in alcune build slim).
- Secondo tentativo: `TypeError: fromisoformat: argument must be str`
  in pip 24.0 quando parsa `upload_time` del JSON simple index PyPI.
- Soluzione finale: cambiato base image da `python:3.11-slim` a
  `plenora-backend:scaffold` (ha già fastapi/uvicorn/pydantic, pip
  funzionante). Aggiunto solo `pyshp` via pip --index-url esplicito.

---

## Step 5 — Seed utente test

Lo script `backend/test-e2e/scripts/seed_test_user.sql` non era
allineato allo schema reale del DB:

| campo problematico | fix |
|---|---|
| `mfa_enabled` (NOT EXIST) | rimosso |
| `ON CONFLICT (email)` (email NOT UNIQUE) | cambiato in `ON CONFLICT (username)` con UPDATE |
| `<HASH_QUI>` placeholder | sostituito a runtime via `sed` con argon2 hash ottenuto da `app.shared.password_hashing` nel container backend |
| Tabelle senza schema prefix | aggiunto `plenora.users`, `plenora.groups` |

Comando applicato:

```fish
HASH=$(docker exec plenora-backend-1 python -c \
  "from app.shared.password_hashing import hash_password; \
   print(hash_password('plenora_test_pwd'))")
sed "s|<HASH_QUI>|$HASH|" backend/test-e2e/scripts/seed_test_user.sql \
  | docker exec -i plenora-postgres-1 psql -U plenora -d plenora
```

Utente creato: `tester` / `plenora_test_pwd` (role `user`, group
`e2e-test-group`). Successivamente upgrade a `admin` per i contract test.

In aggiunta è stato creato manualmente `admin_plenora`
/ `admin_plenora_2026` (role `superadmin`) per accesso UI.

---

## Step 6 — Container runner

`python:3.11` puro continua a SIGFPE/`fromisoformat`-error su pip
install. Stessa workaround dello step 4: base image
`plenora-backend:scaffold`. Aggiunti solo `pytest pytest-timeout
pyyaml` come root.

```fish
docker run -d --name plenora-e2e-runner \
  --network plenora_default \
  -e PLENORA_API_URL=http://backend:5004 \
  -e PLENORA_E2E_USER=tester \
  -e PLENORA_E2E_PASSWORD=plenora_test_pwd \
  -e PYTHONPATH=/repo/backend/test-e2e \
  -v /home/marcob/Scrivania/plenora:/repo \
  -w /repo/backend/test-e2e/runner \
  --entrypoint tail \
  plenora-backend:scaffold -f /dev/null
docker exec -u root plenora-e2e-runner \
  pip install --no-cache-dir --quiet pytest pytest-timeout pyyaml
docker network connect plenora-e2e plenora-e2e-runner
```

**Mount path**: `/home/marcob/Scrivania/plenora:/repo`. Il `conftest.py`
del runner usa `Path(__file__).resolve().parents[3]` per trovare il
REPO_ROOT, quindi serve mountare il repo a un livello che permetta i
3 `..` da `runner/conftest.py`.

---

## Step 7 — Pre-Fase 1: 6 contract test

Esito iniziale: 4/6 verdi, 2 falliti.

| test | problema | fix |
|---|---|---|
| CT-002 POST connection duplicate | backend ritorna 201 invece di 4xx | aggiunto `ConnectionDuplicateNameError` in `app/core/connections/errors.py`, pre-check `(name, group_id)` in service, mapping a HTTP 409 nel router |
| CT-006 project execute | Python ternaria malposizionata: `(A or B or C) if cond else None` invece di `A or B or (C if cond else None)` | rifatto con if/else esplicito; aggiunto anche `task_id` a `_maybe_id` |

Esito finale: **6/6 verdi**.

---

## Step 8 — Fase 1 PREP

Esito iniziale: 9/12 falliti dopo `--force-recreate backend`. Tre cause:

### Causa A — DNS collision `postgres`

Sintomo: `password authentication failed for user "plenora"`.
Diagnosi: `postgres` come service name è in **entrambi** i compose
(root + E2E), e dopo aver agganciato backend a `plenora-e2e` il DNS
risolve a uno dei due (round-robin).

Fix in `docker-compose.override.yml`:
- Postgres root tenuto SOLO sulla network `default`, con alias dedicato `plenora-db`.
- `DATABASE_URL` overridden a `postgresql://plenora:plenora@plenora-db:5432/plenora` per backend/worker/beat.
- Backend/worker/beat/redis/frontend agganciati a `plenora-e2e` per parlare al fake-rest.

### Causa B — fixture `api_conn_apikey_id` payload errato

Sintomo: `URL non configurato`.
Diagnosi: la fixture passava `base_url`/`auth_type`/`api_key`
flat, ma il backend (`api_connection_management.create_api_connection`)
si aspetta `url` top-level (mappato in `endpoint.url`) e `auth`
annidato `{type, location, key_name, key_value}`.

### Causa C — SSRF guard blocca IP privati

Sintomo: `URL bloccato: l'host risolve a un IP privato/interno (172.18.0.8)`.
Diagnosi: `plenora-e2e-fake-rest` ha IP 172.18.x (subnet docker
plenora-e2e). Il guard SSRF in `app/core/security/outbound_url.py`
blocca tutti gli IP `is_private` per default — atteso in prod, ma
impedisce E2E in container locali.

Fix: opt-in `PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND=1` (env var).
Quando settata + `ENVIRONMENT != production/prod/staging/stage`,
il guard permette `is_private`. Settata SOLO in
`docker-compose.override.yml` (mai in produzione).

### Causa D — `--force-recreate` non riapplica env override

Sintomo: dopo modifica `docker-compose.override.yml`, env nel
container restano vecchie.
Workaround: `docker compose down + up -d` (preserva volumi/DB) +
runtime `docker network connect plenora-e2e <container>` per i
container che non vengono auto-attaccati alla seconda network.

Esito finale: **12/12 PREP verdi**, 11 skip giustificati (profili
`db`/`db-heavy`/`files`/`cloud` non attivi, `--run-slow` non passato,
PREP-023 richiede superadmin che lo smoke flow non ha).

---

## File modificati in tree

| file | tipo | scopo |
|---|---|---|
| `.dockerignore` | infra | pattern corretto `backend/.pytest-run/` (era `pytest-run-*/` con dash) |
| `.gitignore` | infra | aggiunti `.env.docker`, `docker-compose.override.yml` |
| `backend/test-e2e/bootstrap.fish` | fix | sostituito bash heredoc `cat <<EOF` con `printf '%s\\n'` fish-friendly |
| `backend/test-e2e/fake-rest/Dockerfile` | fix | base `plenora-backend:scaffold` per evitare SIGSEGV/fromisoformat di pip 24 su `python:3.11-slim` |
| `backend/test-e2e/scripts/seed_test_user.sql` | fix | allineato allo schema reale (no `mfa_enabled`, `ON CONFLICT (username)`) |
| `backend/app/core/connections/errors.py` | feature | nuovo `CONNECTION_DUPLICATE_NAME` + classe exception |
| `backend/app/core/connections/service.py` | feature | pre-check duplicate `(name, group_id)` in `create_connection` |
| `backend/app/core/connections/router.py` | feature | mapping `ConnectionDuplicateNameError` → HTTP 409 |
| `backend/app/core/security/outbound_url.py` | feature | opt-in `PLENORA_ALLOW_PRIVATE_NETWORK_OUTBOUND=1` per E2E |
| `backend/test-e2e/runner/conftest.py` | fix | payload `api_conn_apikey_id` con `url` + `auth` annidato |
| `backend/test-e2e/runner/tests/test_contract.py` | fix | bug ternaria CT-006 + `task_id` in `_maybe_id` |

File non tracciati (gitignored):
- `.env.docker` (secret 64-char)
- `docker-compose.override.yml` (network plumbing E2E)
- `backend/test-e2e/docker/.env` (compose E2E env)

---

## Lessons learned operative

1. **Sempre creare `.env.docker` PRIMA del primo `up`**. Senza i 2 secret, il backend boot fallisce.
2. **Network `plenora-e2e` non auto-creata**. Va creata con `docker network create plenora-e2e` prima del primo up dei due stack.
3. **`docker compose --force-recreate` può non applicare env override**. Se l'env nel container non riflette `compose config`, fare `down + up -d` (preserva volumi).
4. **Dopo down/up, riconnettere a network secondaria a runtime**: bug docker compose con override + multiple networks. Comando: `docker network connect plenora-e2e <container>` per backend/worker/beat/redis/frontend.
5. **`python:3.11-slim` ha pip 24 instabile**. Per i container ad-hoc usare base `plenora-backend:scaffold` se possibile (pip già a posto + scientific stack già installato).
6. **DNS collision se 2 servizi hanno stesso nome in network condivise**: usare alias o evitare la doppia connessione.
7. **`tester` smoke user ha role `user` di default**, ma molti test (CRUD connections, projects) richiedono `admin`. Aggiornare il seed o upgrade a runtime.

---

## Comandi rapidi per restart pulito

```fish
# Stop tutto preservando dati
cd backend/test-e2e/docker && docker compose --profile core down
cd ~/Scrivania/plenora           && docker compose down

# Restart
cd ~/Scrivania/plenora                          && docker compose up -d
cd backend/test-e2e/docker                       && docker compose --profile core up -d
for c in plenora-backend-1 plenora-worker-1 plenora-beat-1 \
         plenora-redis-1   plenora-frontend-1; do
    docker network connect plenora-e2e $c 2>/dev/null
done

# Run contract test
docker exec plenora-e2e-runner pytest -m contract -v

# Run PREP
docker exec plenora-e2e-runner pytest -k prep_ -v

# Wipe cosmetic logs (richiede bypass trigger append-only)
docker exec plenora-postgres-1 psql -U plenora -d plenora -c \
  "BEGIN; SET LOCAL plenora.retention_cleanup = 'true'; \
   DELETE FROM plenora.logs; COMMIT;"

# Tear-down totale (DROP volumi → DB pulito al prossimo up)
cd backend/test-e2e/docker && docker compose --profile core down -v
cd ~/Scrivania/plenora    && docker compose down -v
docker network rm plenora-e2e
```
