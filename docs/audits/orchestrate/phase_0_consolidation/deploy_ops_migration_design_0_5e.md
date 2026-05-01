# Phase 0.5e — Deploy / Ops Migration Design

Date: 2026-04-28
Status: **analysis only — no implementation, no Step 0/1 opened**
Scope: re-design path for deploy/ops boundary in Plenora-ETL
        (URBY-ETL repo) post object_storage / intermediate batches.
Read together with:
- `probe_0_2_deploy.md` (initial deploy probe)
- `delta_post_storage_2026_04_28.md` (D7 deploy alignment finding)
- `wide_audit_2026_04_28.md` (Phase 0 wide audit map)

---

## 0. Codex Review Note — Pre-Step 1 Verification

**Note (from Codex review on 0.5e draft):**

> Prima di proporre Step 1 come code change, verifica sul codice attuale
> se `validate_storage_config` già fa fail-fast quando
> `STORAGE_POLICY=object_storage` e mancano `OBJECT_STORAGE_BUCKET` /
> `PLENORA_ARTIFACT_BUCKET`. Potrebbe essere già chiuso dal batch A1.

**Verifica eseguita 2026-04-28** sul file
`backend/app/shared/cloud_backends.py` (head `b926616`):

- funzione `validate_storage_config()` definita a riga 713;
- ramo `policy == "object_storage"` (righe 800–814) esegue:
  - `runtime_bucket = os.getenv("OBJECT_STORAGE_BUCKET") or os.getenv("PLENORA_ARTIFACT_BUCKET")`;
  - `if not runtime_bucket: _fail(...)` con messaggio esplicito che
    impone `OBJECT_STORAGE_BUCKET` o `PLENORA_ARTIFACT_BUCKET` e nega
    fallback su `BACKUP_BUCKET` in policy `object_storage`;
- `validate_storage_config()` viene chiamata da `app/main.py:47` al boot
  (fail-fast effettivo);
- contract test in `backend/tests2/utils/test_storage_policy.py`
  copre il caso (vedi righe 71, 77, 95, 108, 120, 128, 140, 152).

**Conclusione**: Step 1 (code change "fail-fast su bucket mancante in
object_storage policy") **è gia' chiuso dal batch A1** (commit
`2a80970` referenziato in `invariants.md:83`).

**Implicazione su questo documento**: Step 1 della sequenza di
migrazione **non è più un code change**. Resta solo come
**deploy-side checklist** (verificare che le env effettive in produzione
contengano i bucket richiesti, e che `validate_storage_config` venga
effettivamente eseguita al boot dei systemd unit). Sezioni successive
del documento riflettono questa correzione.

---

## 1. Stato Attuale Deploy / Ops

### 1.1 deploy.sh

File: `URBY-ETL/deploy.sh` (97 righe).

Pattern attuale:

```
1. git add -A && git commit -m "deploy: <date>"   # auto-commit locale
2. git push origin main
3. cd frontend && npm run build
4. ssh -i <PEM> ubuntu@15.160.219.93 \
     "cd /home/ubuntu/plenora-etl && \
      git pull origin main && \
      cd backend && pip install -q -r requirements.txt && \
      sudo systemctl restart plenora-backend plenora-celery plenora-beat"
5. ssh ... "rm -rf /home/.../frontend/dist/*"     # destructive pre-swap
6. scp -r frontend/dist/* ubuntu@<IP>:/.../dist/
```

Hardcoded:
- `PEM=~/OneDrive-Marco/.../plenora.pem` (R2: bus factor 1, path
  personale macchina sviluppatore).
- `SERVER=ubuntu@15.160.219.93` (R3: nessuno staging, target unico).
- Nessuna variabile per `STAGING` / `PROD`.

Mancano:
- health check post-restart (R4);
- atomic frontend swap (R5);
- rollback path automatico;
- env diff check pre-deploy (R6, R7);
- secrets sync trigger su SSM.

### 1.2 systemd units

Path: `URBY-ETL/backend/deploy/systemd/`.

7 unit files inspezionati:

| Unit | Type | Detail |
| --- | --- | --- |
| `plenora-backend.service` | service | uvicorn 127.0.0.1:5004, 2 workers, EnvironmentFile=`backend/.env` |
| `plenora-celery.service` | service | celery worker concurrency=4, EnvironmentFile=.env |
| `plenora-beat.service` | service | PlenoraBeatScheduler (custom), EnvironmentFile=.env |
| `plenora-backup.service` | oneshot | `python load_secrets.py backup_db.py`, env inline (AWS_REGION + BACKUP_BUCKET), no .env |
| `plenora-backup.timer` | timer | OnCalendar=daily 02:00 UTC, RandomizedDelaySec=5m |
| `plenora-backup-test.service` | oneshot | restore drill, weekly |
| `plenora-backup-test.timer` | timer | OnCalendar=Sun 03:00 UTC |

Asimmetria importante (R6):
- backend / celery / beat → `EnvironmentFile=.env` (file su disco);
- backup → inline env + `load_secrets.py` (SSM Parameter Store).

Implicazione: due fonti di verità per env, mai diff'ate, mai validate
end-to-end.

### 1.3 Env files

File: `backend/.env.example` (314 righe).

Stato: **2 vars uncommented** (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`),
**54 vars commented** templates incluse storage section
(`OBJECT_STORAGE_*`, `PLENORA_ARTIFACT_BUCKET`, `BACKUP_BUCKET`,
`LOG_ARCHIVE_BUCKET`).

Implicazione (R6, R7):
- `.env` reale in produzione **non e' versionato** (corretto), ma
  l'allineamento `.env.example` ↔ runtime non e' verificato in CI;
- non esiste un dry-run "quali env mancherebbero al boot di
  plenora-backend con questa .env?";
- `validate_storage_config` cattura il problema solo a boot effettivo,
  non in fase di deploy (cioe' restart fallisce e backend e' down).

### 1.4 Secret management

Stack misto:
- **AWS SSM Parameter Store** per `DATABASE_URL`,
  `PLENORA_ENCRYPTION_KEY`, `JWT_SECRET_KEY` (whitelist in
  `migrate_env_to_ssm.py`);
- **`backend/scripts/load_secrets.py`** wrapper: fetcha SSM, esegue
  `os.execv` sul target script;
- **plenora-backup.service** usa `load_secrets.py` (SSM-backed);
- **plenora-backend / celery / beat** usano `.env` (file-backed).

IAM:
- `backend/deploy/iam/plenora-backup-ssm-read.json` policy minima
  read-only su SSM `/plenora/*`;
- README in `backend/deploy/iam/` documenta il modello.

R8 (asimmetria SSM): backup ha SSM, runtime no. Se ruotiamo
`PLENORA_ENCRYPTION_KEY` su SSM, dobbiamo **anche** aggiornare il `.env`
del backend manualmente. Drift garantito.

### 1.5 IaC (parziale)

Path: `URBY-ETL/backend/deploy/terraform/`.

In Terraform:
- `lifecycle.tf` — S3 bucket lifecycle rules (transition to IA, expiry).
- bootstrap state config.

**NOT in IaC** (R12):
- bucket S3 stesso (esiste in console AWS, non in TF);
- IAM role / policy per EC2 e per backup;
- KMS keys (encryption at rest);
- SSM parameters (creati manualmente);
- EC2 instance (15.160.219.93);
- RDS instance / PG endpoint;
- VPC / SG / Route53;
- CloudWatch alarms / dashboards.

### 1.6 CI/CD

File: `.github/workflows/ci.yml`.

Stato:
- jobs: `backend-tests` (pytest, redis sidecar, STORAGE_POLICY=db) +
  `frontend-tests` (vitest);
- trigger: push + PR su `main`;
- **nessun job di deploy**;
- **nessun job di env validation**;
- **nessun job che esegue `validate_storage_config()` con env staging**.

R9 (CI gate gap): merge su `main` non implica niente sul deploy. Deploy
e' completamente manuale dal laptop di Marco.

### 1.7 Ops docs

Path: `URBY-ETL/backend/docs/ops/` (1778 righe totali).

Documenti presenti:
- `runtime-prereqs.md` — requisiti host;
- `backup-restore.md` — procedure backup;
- `dr_drill_playbook.md` — playbook drill DR;
- `dr_drill_log.md` — log drill eseguiti;
- `incident_response.md` — risposta incidenti (nota: "Bus factor 1 —
  Marco Bonamente only");
- `jwt-incident-response.md` — incident response specifico JWT;
- `trusted-proxies.md` — config proxy fidati;
- `tickets.md` — ticket tracking ops.

Punti di forza: documentazione operations e' in atto, drill DR
periodici. Punti deboli: "Bus factor 1" ammesso esplicitamente.

### 1.8 Migrations history

Path: `URBY-ETL/backend/deploy/migrations/`.

File presenti:
- `2026-04-18_rename-urby-to-plenora.sh` — cutover rebrand
  URBY → PLENORA, eseguito (script con guard idempotency).

Pattern: migrations come bash script, non versionate in DB. Per ora
accettabile (pochi cutover storici), ma da formalizzare se cresce.

---

## 2. Rischi R1–R15

Severità: **C** (critical, prod-impacting), **H** (high, latente),
**M** (medium, hygiene), **L** (low, futuro).

| ID | Categoria | Descrizione | Severità |
| --- | --- | --- | --- |
| R1 | deploy.sh | Auto-commit `git add -A && git commit -m "deploy: <date>"` mescola code review e deploy: ogni deploy diventa un commit "rumore", niente review | **H** |
| R2 | secret | PEM hardcoded in path OneDrive personale (Marco), bus factor 1 | **C** |
| R3 | infra | Server hardcoded, nessuno staging, deploy diretto su prod | **C** |
| R4 | deploy | SSH+restart in single concat command, no health gate post-restart, no rollback automatico | **H** |
| R5 | frontend | Pre-swap distruttivo (`rm -rf dist/*`) prima di scp: finestra utente vede 404/500 | **H** |
| R6 | env | `.env` file vs SSM duplica fonti di verità senza diff/sync | **H** |
| R7 | storage | ~~`OBJECT_STORAGE_BUCKET` runtime senza fail-fast pre-deploy~~ **CHIUSO da batch A1** (vedi sezione 0). Resta solo deploy-side checklist | **M** (downgrade da H) |
| R8 | secret | Asimmetria SSM (solo backup) vs .env (runtime): rotazione chiave non sincronizzata | **H** |
| R9 | CI | Nessun deploy gate da CI; merge non implica deploy verificato | **H** |
| R10 | frontend | Nessun versioning artifact frontend (dist/) → impossibile rollback senza rebuild | **M** |
| R11 | logging | Logs systemd journal non shippati out-of-host: in incident, accesso solo SSH | **M** |
| R12 | IaC | Bucket S3, IAM, KMS, SSM, EC2, RDS, VPC tutti out-of-IaC: nessun disaster rebuild ricostruibile da repo | **C** |
| R13 | infra | Nessun monitoring/alerting attivo (no CloudWatch, no Sentry/Datadog, no uptime check) | **H** |
| R14 | deploy | Nessuna release notes/version tag → debug post-deploy guesswork | **L** |
| R15 | bus factor | Tutti questi rischi convergono su "Marco Bonamente only" (vedi `incident_response.md`) | **C** |

Critical (C) totale: 4 (R2, R3, R12, R15).
High (H): 7 (R1, R4, R5, R6, R8, R9, R13).

---

## 3. Target Design

### 3.1 Principi

1. **Source of truth unica per secrets**: SSM Parameter Store per
   *tutti* i secret (runtime + backup), `.env` solo per non-secret
   config (DEBUG, LOG_LEVEL, FEATURE FLAGS).
2. **Boot validation come gate, non come trap post-mortem**: deploy
   gira `validate_storage_config()` (e analoghi) **prima** di
   restart-are i unit, non dopo.
3. **Atomic operations su prod**: frontend swap atomic (symlink), DB
   migrations idempotenti, restart con health gate.
4. **CI come autorita' deploy**: deploy parte da CI green, non dal
   laptop. Ramo `staging` esiste e ha un suo target.
5. **IaC come disaster recovery**: bucket, IAM, KMS, SSM, EC2/RDS in
   Terraform → ricreare l'ambiente in <1 ora se l'account AWS muore.
6. **Bus factor 2**: documentazione + accesso replicato → secondo
   operatore puo' gestire incident base.

### 3.2 Architettura target (ASCII)

```
┌──────────────┐     ┌────────────┐     ┌──────────────────────┐
│  Developer   │ →   │  GitHub PR │ →   │  CI (workflows/ci.yml) │
│  laptop      │     │   main     │     │  - tests (BE+FE)      │
└──────────────┘     └────────────┘     │  - env validation     │
                                         │  - validate_storage_*  │
                                         │  - lint/type/coverage  │
                                         └─────────┬─────────────┘
                                                   │ green
                                                   ↓
                                         ┌──────────────────────┐
                                         │  Staging deploy      │
                                         │  workflows/deploy.yml│
                                         │  - target: staging   │
                                         │  - smoke + e2e       │
                                         └─────────┬─────────────┘
                                                   │ approved
                                                   ↓
                                         ┌──────────────────────┐
                                         │  Prod deploy         │
                                         │  - target: prod      │
                                         │  - manual approval   │
                                         │  - health gate       │
                                         │  - atomic swap FE    │
                                         │  - rollback path     │
                                         └──────────────────────┘

Secrets:
   AWS SSM Parameter Store (canonical)
        ↑
        │  (read-only IAM per role)
        │
   ┌────┴──────┬──────────┬────────────┐
   │ backend   │ celery   │ beat       │
   │ (load_*) │ (load_*) │ (load_*)   │
   └───────────┴──────────┴────────────┘

Infra:
   Terraform (URBY-ETL/backend/deploy/terraform/)
   - S3 buckets (object_storage, backup, log_archive)
   - IAM roles + policies
   - KMS keys
   - SSM parameters (skeleton, valori manual)
   - EC2 instance (staging + prod)
   - RDS instance
   - CloudWatch alarms
   - Route53 (lab.plenora.it, staging.plenora.it)
```

### 3.3 Required runtime env (post object_storage, prod)

Reference: `backend/app/config.py`, `backend/app/shared/cloud_backends.py:validate_storage_config`.

Mandatory (validate fail-fast):
- `STORAGE_POLICY=object_storage`
- `OBJECT_STORAGE_ENABLED=true`
- `OBJECT_STORAGE_BUCKET` (o `PLENORA_ARTIFACT_BUCKET`)
- `BACKUP_BUCKET` (separato, IAM boundary distinto)
- `DATABASE_URL`
- `PLENORA_ENCRYPTION_KEY` (canonical da 2026-04-21)
- `JWT_SECRET_KEY`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

Optional (validate warn):
- `LOG_ARCHIVE_BUCKET`
- `OBJECT_STORAGE_PROVIDER` (default aws)
- `OBJECT_STORAGE_REGION` (default eu-south-1)

Mai in `.env`:
- `DATABASE_URL`, `PLENORA_ENCRYPTION_KEY`, `JWT_SECRET_KEY`
  (sempre via SSM).

---

## 4. Sequenza Migrazione — Step 0–9

| Step | Cosa | Effort | Rischio | Test gate |
| --- | --- | --- | --- | --- |
| **0** | Hardening minimo `deploy.sh`: rimuovi auto-commit, aggiungi pre-deploy check, separa code-deploy da frontend-swap | XS (1–2h) | basso | smoke deploy su prod (con consenso) |
| **1** | ~~Code change validate fail-fast su OBJECT_STORAGE_BUCKET~~ → **CHIUSO da A1**. Deploy-side checklist: aggiungi a deploy.sh un `ssh ... "python -c 'from app.shared.cloud_backends import validate_storage_config; validate_storage_config()'"` con env produzione **prima** del restart | XS (1h) | basso | failure simulato (unset bucket) deve fermare deploy |
| **2** | Setup ambiente staging (EC2 nuova, dominio `staging.plenora.it`, RDS separato o schema separato, SSM namespace `/plenora/staging/*`) | M (2–3 giorni) | medio | e2e su staging deve girare verde |
| **3** | GitHub Actions deploy job: `workflows/deploy.yml` con target staging + prod, OIDC role per AWS (no static creds), trigger su tag `vX.Y.Z` per prod, su push main per staging | M (2–3 giorni) | medio | tag dispatch testato su staging |
| **4** | Atomic frontend swap: `dist/` come symlink → `dist-{commit}/`, swap symlink atomico, retain N=3 versions per rollback istantaneo | S (1 giorno) | basso | rollback test (swap symlink avanti/indietro) |
| **5** | Uniforma SSM per runtime: backend/celery/beat passano via `load_secrets.py` come backup; rimuovi secret da `.env`, lasciare solo non-secret config | S (1 giorno) | medio | restart con SSM, verifica boot OK |
| **6** | Estendi IaC: bucket S3, IAM role/policy, KMS keys, SSM skeleton (no values), EC2+RDS+VPC. Import `terraform import` per resources esistenti | L (5–7 giorni) | basso (read-only) | `terraform plan` su account = no diff |
| **7** | CI deploy gate: `validate_storage_config` con env staging in CI, deploy job depende da `tests + validation green` | S (1 giorno) | basso | rosso/verde test |
| **8** | DR drill esteso: include rebuild infra da Terraform su account separato, restore DB da backup, smoke test app rebuilt | M (3 giorni) | alto solo durante drill | drill log in `dr_drill_log.md` |
| **9** | Bus factor 2: secondo operatore con accesso PEM (in vault condiviso, non OneDrive personale), accesso AWS (read+limited write), runbook letto+firmato. Aggiorna `incident_response.md` | S (1–2 giorni) | basso | tabletop exercise con secondo operatore |

Total stimato: ~3–4 settimane developer-time, ~6–8 settimane elapsed (perche' staging costa mesi su AWS billing + drill richiedono finestre).

---

## 5. Cosa Fare Subito vs Dopo

### 5.1 Subito (pre-produzione 5 maggio 2026)

| Step | Motivo | Status |
| --- | --- | --- |
| Step 0 | deploy.sh hardening | **proposto, non aperto** |
| Step 1 (deploy checklist) | ridurre R7 residuo (validate è già in code, basta runarla pre-restart) | **proposto, non aperto** |
| Step 4 | atomic FE swap (R5) → utente non vede 404 | **proposto, non aperto** |
| Step 9 (parziale) | sposta PEM da OneDrive personale a vault condiviso → R2 immediato | **proposto, non aperto** |

Effort totale subito: ~2–3 giorni developer-time.

### 5.2 Dopo (post-produzione, fase di stabilizzazione)

| Step | Motivo |
| --- | --- |
| Step 2 | staging serve dopo prod, non prima — testiamo prod live con utenti pilota |
| Step 3 | CI deploy job: dopo che il deploy manuale e' rodato e safe |
| Step 5 | uniforma SSM: post-prod stabile, senza pressione |
| Step 6 | IaC completo: dopo MVP, prima di scalare |
| Step 7 | CI gate validato: dopo Step 3 |
| Step 8 | DR drill esteso: trimestrale, post-IaC |
| Step 9 (completo) | bus factor 2 piano: post-prod stabile |

### 5.3 Out-of-scope per Phase 0 / Phase 1

- multi-tenant deploy (1 cliente = 1 stack);
- blue-green / canary deployment (overkill per MVP);
- Kubernetes (1 EC2 e' sufficiente per ora);
- service mesh / observability avanzata (Datadog/Sentry self-hosted o
  paid);
- ChatOps / Slack-bot deploy.

---

## 6. Decision Matrix — Critical Path

Ordinato per (impatto su prod) × (semplicità).

| # | Decisione | Quando | Owner | Note |
| --- | --- | --- | --- | --- |
| 1 | Sposta PEM da OneDrive Marco a vault (1Password / AWS Secrets Manager) | subito | Marco | R2 critical |
| 2 | Aggiungi pre-restart `validate_storage_config` step a `deploy.sh` | subito | Marco | R7 → diventa M |
| 3 | Atomic frontend swap | subito | Marco | R5 visibile a utente |
| 4 | Setup staging env | post-prod | Marco | R3 alto, ma rinviato perche' richiede settimane |
| 5 | GH Actions deploy job (OIDC) | post-staging | Marco | R9 dipende da Step 2 |
| 6 | Uniforma secrets SSM | post-staging | Marco | R6, R8 |
| 7 | IaC completo | post-Step 6 | Marco | R12 critical-but-rebuild-able |

Nota: tutti gli step convergono su "Marco only" → R15 = bus factor.
Questo va affrontato in parallelo con qualsiasi step (anche solo
"share-with-second-operator") per ridurre il rischio incident.

---

## 7. Stato Phase 0.5e

**Status: analysis only. Nessuno step aperto. Nessun codice modificato.**

Output prodotti:
- questo documento (`deploy_ops_migration_design_0_5e.md`);
- nota Codex: Step 1 chiuso da batch A1 (verificato 2026-04-28),
  resta solo deploy-side checklist;
- mappatura R1–R15;
- sequenza Step 0–9 con effort/rischio;
- decision matrix critical path 1–7.

Pending (Phase 0 wide audit):
- 0.5a — hot-file deep dive (arcgis_client, spatial, file_service,
  enrichment_task, execution_manager);
- 0.5b — frontend wide audit;
- 0.5c — Q1–Q4 SQL queries esecuzione su prod DB
  (vedi `compat_residue_evidence_0_5c.md`);
- 0.5d — test coupling roadmap;
- 0.5f — perf baseline;
- 0.5g — UX / product readiness.

Nota stretta: questo documento NON apre Step 0/1. Sono proposte,
non roadmap commitments. La decisione su quando aprirli e' di Marco.

---

*End of Phase 0.5e — Deploy/Ops Migration Design.*
