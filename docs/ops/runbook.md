# Runbook — Procedure recovery operative

Procedure operative per scenari ricorrenti di incident / recovery.
Scope: scenarios concreti già osservati o noti, NON framework
generale incident response (per quello vedi `incident_response.md`).

Riferimenti incrociati:
- `incident_response.md` — incident framework, severity ladder, comms
- `deploy-checklist.md` — pre/post deploy + rollback
- `backup-restore.md` — backup + restore DB
- `jwt-incident-response.md` — JWT-specific incident handling
- `dr_drill_playbook.md` — DR drill formale
- `runtime-prereqs.md` — env baseline + dependencies

## 1. B-2 functional workflow

### 1.1 Cos'è

`.github/workflows/ci-functional.yml` — CI tier secondario per i 38
test marcati `requires_backend_http_service`. Spawnano un backend
uvicorn live + worker celery + PG/Redis services. Sono separati dalla
CI principale push/PR (che li deseleziona via marker).

### 1.2 Quando girare manuale

Pre-merge PR rilevanti che toccano:
- `backend/app/core/arricchisci/`
- `backend/app/core/scheduler/`
- `backend/app/db/`
- `backend/migrations/`
- `backend/app/main.py` o `app.config`

```sh
gh workflow run ci-functional.yml --ref <branch>
```

Per debug mirato:
```sh
gh workflow run ci-functional.yml --ref <branch> \
    -f pytest_extra_args="-k <test_name> -s -vv --log-cli-level=DEBUG"
```

### 1.3 Nightly automatico

Trigger `schedule: cron "0 2 * * *"` (02:00 UTC daily). Failure non
blocca PR (`continue-on-error: true` durante diagnostic phase).

### 1.4 Cosa fare se nightly fallisce

1. Aprire run dalla GitHub Actions UI (mail notification automatica)
2. `gh run view <run_id> --log-failed | head -200`
3. Identificare cluster pattern (errors/failures comuni)
4. Triage:
   - **Cluster nuovo**: investigare commit window (ultimi commit pre-failure), no panic — il tier è non-blocking per design
   - **Cluster già noto**: aprire issue di tracking se non esistente
   - **Cluster transient** (es. service flake, timeout race): registrare in incident note se ricorre 2+ volte

NON triggerare manualmente nuovo run "per controllare se passa": lascia il nightly stesso accumulare evidence.

### 1.5 Promozione a required check (futuro, B3b)

Quando 3-5 nightly consecutivi sono verdi senza intervento:
1. Rimuovere `continue-on-error: true` da `ci-functional.yml`
2. Valutare promozione a required check **solo per release branch** (non per ogni PR — l'opt-out via marker è già stato deciso in PR #12)

### 1.6 Riferimento

`orchestrate/phase_0_consolidation/ci_recovery_final_report.md` —
report completo Phase 0.5X CI recovery + Phase 1 B1 B-2 tier setup.

## 2. Recovery scenarios

### 2.1 MFA admin lockout

Scenario: admin ha perso il dispositivo MFA, non può accedere
all'UI per disabilitare il proprio MFA via self-service. Workaround
diretto via SQL (manuale, ammesso solo per admin role, audit
obbligatorio).

#### Pre-requisiti
- Accesso al PG prod (read+limited-write)
- Identità admin verificata fuori-banda (es. video call, conferma in
  team channel privato)

#### Procedura

1. Identificare user_id admin:
   ```sql
   SELECT id, username, role FROM plenora.users WHERE username = '<admin_username>';
   ```

2. Verificare che ruolo sia effettivamente admin:
   ```sql
   SELECT role FROM plenora.users WHERE id = '<user_id>';
   ```

3. Disabilitare MFA per l'utente:
   ```sql
   UPDATE plenora.user_mfa SET enabled = false WHERE user_id = '<user_id>';
   ```

4. Loggare l'azione manualmente (audit trail):
   ```sql
   INSERT INTO plenora.audit_actions (user_id, action_type, details, created_at)
   VALUES ('<user_id>', 'mfa_disabled_manual', 'MFA disabilitato via runbook §2.1', NOW());
   ```

5. Comunicare all'admin: deve riconfigurare MFA al prossimo login.

#### Note
- **NON usare** questa procedura per utenti non-admin. Self-service
  password reset (L1.2 backlog) coprirà il caso normale quando
  implementato.
- L'admin ricreato deve riabilitare MFA appena possibile (security
  posture).

### 2.2 JWT secret rotation

Vedi `jwt-incident-response.md` per procedura completa.

Riassunto operativo:
1. Generare nuovo `JWT_SECRET_KEY` (es. `openssl rand -base64 32`)
2. Aggiornare env produzione (sistema secrets management — NON nel repo)
3. Restart `plenora-backend` (i token esistenti diventeranno invalidi)
4. Comunicare logout forzato a tutti gli utenti

### 2.3 DB schema mismatch dopo deploy

Sintomo: errori `psycopg2.errors.UndefinedTable` o `UndefinedColumn`
nei log post-deploy.

Causa probabile: migration applicate parzialmente o file `migrations/*.sql`
con dipendenza implicita non rispettata.

#### Procedura diagnostic

1. Verificare migration applicate:
   ```sql
   SELECT filename, applied_at FROM plenora.schema_migrations ORDER BY filename;
   ```

2. Confrontare con file presenti in `backend/migrations/`:
   ```sh
   ls backend/migrations/*.sql | xargs -n1 basename
   ```

3. Identificare gap:
   - File presente nel filesystem ma assente in `schema_migrations`:
     non è stato applicato
   - File assente nel filesystem ma presente in `schema_migrations`:
     applicato in passato e poi rimosso (rare)

#### Procedura recovery

1. Se gap è **non bloccante** (es. migration legacy che non esiste più):
   - Lasciare stare, non c'è cleanup automatico
2. Se gap è **bloccante** (errore runtime):
   - Identificare il file `.sql` mancante
   - Verificare manualmente con `psql` su prod che le tabelle/colonne attese esistano (read-only)
   - Se mancano: applicare manualmente il file `.sql` mancante via `psql -f`
   - Inserire row in `schema_migrations` per registrare:
     ```sql
     INSERT INTO plenora.schema_migrations (filename) VALUES ('<filename.sql>')
     ON CONFLICT (filename) DO NOTHING;
     ```
   - Restart `plenora-backend`

### 2.4 deploy.sh failure modes

#### 2.4.1 Pre-flight SSH fail

Sintomo: `Cannot connect to <server>` o timeout SSH.

Verifiche:
- VPN attiva (se richiesta dall'environment)?
- PEM path corretto in `PLENORA_DEPLOY_PEM` env?
- PEM permessi `600`?
- Server raggiungibile via altri tool (`ping`, `curl <app-url>/health`)?
- IP whitelisted (security group AWS/GCP)?

#### 2.4.2 init_all_dbs error

Sintomo: post-deploy uvicorn esce con `psycopg2.errors.*` durante
`init_all_dbs`.

Verifiche:
- PG raggiungibile dal server applicativo?
- Schema `plenora` esiste? (SELECT FROM `information_schema.schemata` WHERE schema_name = 'plenora')
- User applicativo ha permessi CREATE TABLE su schema `plenora`?
- `schema_migrations` esiste o `migrations_runner.py` riesce a crearla?

Recovery: riferirsi a §2.3 (DB schema mismatch).

#### 2.4.3 Health gate timeout

Sintomo: `deploy.sh` esce con errore "Health gate failed dopo N tentativi".

Verifiche:
- `journalctl -u plenora-backend -n 100` per stack trace
- `systemctl status plenora-backend` — è realmente in `active (running)`?
- PG pool warm-up: aspettare ~60s e riprovare manualmente
- `/ready` body include errori specifici (db, redis)?

Recovery: se servizio è `failed`, indagare causa via journalctl;
se solo lento, attendere e re-check manuale `/ready`.

## 3. Compat residue (Q1-Q4 SQL)

Le query Q1-Q4 in `orchestrate/phase_0_consolidation/compat_residue_evidence_0_5c.md §3`
sono pronte per esecuzione su prod DB ma non ancora eseguite.

Decisione su rimozione marker COMPAT-RESIDUE è bloccata da Q1-Q4
queries — finché non eseguite, niente removal autorizzato.

Quando si decide di eseguirle: vedi sezione dedicata in `phase_0_5x_final_synthesis.md §9.1`.

## 4. Audit run con override `intermediate_mode`

### 4.1 Cos'è

R-25 (ADR-0019, 2026-05-01): override per-call della `INTERMEDIATE_PERSISTENCE_MODE` globale per audit / debug di una singola run, senza modificare env e restartare backend.

### 4.2 Quando usarlo

- Audit forte su una run specifica: serve confronto riga-per-riga col baseline → override `full_pg`.
- Debug di una run: vedere le prime 500 righe nel modal preview senza materializzare l'intero result set → override `preview_only`.
- Test del path `full_artifact` (Parquet + manifest su object storage) per una run pilota → override `full_artifact` (richiede `OBJECT_STORAGE_ENABLED=true` già configurato a startup).

### 4.3 Come usarlo

Solo `superadmin` (qualunque altro ruolo riceve `403`). Il param e' query-string `intermediate_mode`.

```sh
# Pipeline (estrai / manipola)
TOKEN=$(curl -s -X POST https://lab.plenora.it/api/auth/login \
    -d 'username=...&password=...&mfa_code=...' | jq -r .access_token)

curl -X POST "https://lab.plenora.it/api/projects/{project_id}/execute?intermediate_mode=full_pg" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"force_recalculate": false}'

# Arricchimento async
curl -X POST "https://lab.plenora.it/api/projects/{project_id}/enrich-async?intermediate_mode=preview_only" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"format": "csv"}'
```

Mode ammessi: `metadata_only` | `preview_only` | `full_pg` | `full_artifact`.

### 4.4 Codici risposta

| Status | Causa |
|---|---|
| 200 | Override applicato. `task_id` ritornato; il task riceve il kwarg. |
| 403 | Ruolo non `superadmin` con override richiesto. Il base-flow `/execute` (senza param) resta aperto agli altri ruoli. |
| 422 | Mode non in enum (Pydantic Literal). Body include la lista valori ammessi. |

### 4.5 Cosa controllare dopo

- `intermediate.runs` per il `run_id` della task: ultimo record deve avere `status='completed'`.
- `intermediate.node_meta`:
  - `persistence_mode` riflette l'override (es. `full_pg`).
  - `full_artifact`: `artifact_uri`, `artifact_status='complete'`, `artifact_format='parquet'` valorizzati.
- Logs backend: cercare `[PERF] op=record_final_snapshot ... persistence_mode=<override>`.

### 4.6 Failure mode noti

- **Override invalido al record-time** (es. il chain produce qualcosa di non in enum): `IntermediateSettingsError` propagata, catturata dal try/except non-bloccante in `query_executor` / `manipola_executor` / `enrichment_task`. La query stessa procede (snapshot best-effort), ma viene loggato `Intermediate write fallita (non bloccante): ...`. **Niente fallback al default.**
- **`full_artifact` con object storage spento**: validazione startup-side dovrebbe averlo già intercettato. Se sfugge, lo writer fail-closed al record-time con `artifact_status='failed'`.

### 4.7 Rollback

L'override e' per-call: la run successiva senza param ritorna al default globale (`metadata_only`). Niente cleanup operativo richiesto.

## 5. Out-of-scope (rinvio a doc dedicati)

| Scenario | Doc dedicato |
| --- | --- |
| Backup strategy + restore | `backup-restore.md` |
| DR drill formale | `dr_drill_playbook.md` |
| Trusted proxy / IP whitelist | `trusted-proxies.md` |
| Ticket triage / sev levels | `tickets.md` |
| Incident framework generale | `incident_response.md` |
| JWT incident specifico | `jwt-incident-response.md` |
| Runtime env prerequisites | `runtime-prereqs.md` |
| DR drill log storico | `dr_drill_log.md` |
