# Staging environment + P4 atomic frontend swap — Roadmap

> Status: **ROADMAP RECORDED** (2026-04-30). Implementazione **post-deadline**.
> Niente provisioning / deploy / DNS / DB live previsto in questa PR.

## 1. Stato attuale

### Cosa esiste oggi
- `deploy.sh` (~482 LOC) wrapper parametrico con `--profile=<name>`
- `deploy/profiles/prod.conf` — paths, systemd units, health URLs, retention
- `deploy/profiles/README.md` — template + selezione profilo
- `RETAIN_FRONTEND_VERSIONS="3"` già parametrizzato in `prod.conf`
- B-2 functional CI tier + nightly cron (PR #23)
- Ops doc baseline: `runbook.md`, `deploy-checklist.md`, `handoff.md` (PR #24)

### Cosa NON esiste
- `deploy/profiles/staging.conf` (solo placeholder `README.md`)
- EC2 / RDS / Redis / S3 / DNS staging
- SSM `/plenora/staging/*` namespace
- Atomic frontend swap (oggi `deploy_frontend()` e' pre-swap distruttivo:
  `rm -rf dist/* && scp -r`, vedi `deploy.sh:396-429`)
- GHA workflow `deploy.yml` con `--env` flag

### Riferimenti hot-file
- `orchestrate/phase_0_consolidation/deploy_ops_second_pass_0_5e.md §P4`
  (P4 atomic frontend swap)
- `orchestrate/phase_0_consolidation/deploy_ops_second_pass_0_5e.md §8`
  (Cosa serve per arrivare a GHA / staging)
- `orchestrate/phase_0_consolidation/phase_0_5x_final_synthesis.md` rank 4
  (DEPLOY-STEP0-B = P4) e rank 14 (STAGING-SETUP)

## 2. Prerequisiti B17 — Staging environment

### 2.1 Server
- **EC2 staging instance**: tipologia identica/simile a prod (es. t3.small/medium)
- **Security group**: SSH inbound da IP allowlist + outbound HTTP/HTTPS
- **systemd units**: `plenora-staging-backend`, `plenora-staging-celery`,
  `plenora-staging-beat` (o stessi nomi prod se isolato a livello di host)

### 2.2 DNS
- Sottodominio dedicato (es. `staging.<base-domain>`)
- TLS cert: Let's Encrypt via certbot, oppure ACM se ALB davanti

### 2.3 Database
Due opzioni, decisione esplicita richiesta (vedi §7):
- **Opzione A — RDS staging dedicata**: t3.micro 5GB, isolamento totale,
  costo incrementale ~$15/mese
- **Opzione B — Schema `plenora_staging` su RDS prod**: costo zero,
  rischio cross-contamination + lifecycle drift, richiede grant
  separati per il PG user staging

### 2.4 Object storage / S3
- Bucket dedicato (es. `<artifact-base>-staging-<region>`)
- Lifecycle aggressivo (es. delete dopo 7gg) per contenere costi
- IAM role staging con policy read+limited-write sul bucket staging

### 2.5 Cache / broker (Redis)
- Opzione A: Redis locale sull'EC2 staging (zero cost, gestione manuale)
- Opzione B: ElastiCache cache.t3.micro (~$12/mese, managed)

### 2.6 Secrets
- SSM Parameter Store namespace parallelo `/plenora/staging/*`
- `.env` staging generato al boot dal namespace (script esistente o
  template)
- Niente credenziali in chiaro nel repo. Niente file `.env` committato.

### 2.7 Deploy profile
- `deploy/profiles/staging.conf` con SERVER, paths, units, health URLs,
  ALLOWED_BRANCHES (es. `develop main`)
- Stesso shape di `prod.conf` (vedi `deploy/profiles/README.md`)

### 2.8 Out-of-scope B17 (rinviato a STAGING-SETUP rank 15)
- GHA OIDC role + `workflows/deploy.yml` parametrico
- Approval gate prod
- IaC esteso multi-environment

## 3. Roadmap

### 3.1 Fase B17a — Staging minimal (~3-4 giorni)

**Obiettivo**: ambiente staging utilizzabile da
`bash deploy.sh --profile=staging`, accesso solo VPN / operatori.

| Day | Attività | Effort |
|---|---|---|
| 1 | EC2 + Security group + DNS A record + Let's Encrypt cert | ~4-5h |
| 2 | DB staging (decisione A vs B) + Redis (decisione A vs B) | ~4h |
| 3 | SSM `/plenora/staging/*` + `.env` skeleton + IAM role staging + S3 bucket + lifecycle | ~4-5h |
| 4 | `deploy/profiles/staging.conf` + systemd units + first deploy + smoke `/health`+`/ready` | ~4h |

**Output**:
- `deploy/profiles/staging.conf` versionato
- `bash deploy.sh --profile=staging` end-to-end funzionante
- Smoke `/health` + `/ready` verde
- 1 PR test-only opzionale per `staging.conf` se non popolato durante
  setup live

### 3.2 Fase B17b — Staging profile + smoke validato (~1 giorno)

**Obiettivo**: contratto `--profile=staging` testato + procedura
promotion documentata.

- Deploy staging da `develop` o feature branch
- Smoke completo: login, 1 progetto, 1 esecuzione, 1 cascade error
  display
- Migration dry-run su staging
- Promotion path: tag staging → tag prod (no auto-promote)
- Aggiornamento `runbook.md` con sezione "Staging promotion"
- Aggiornamento `deploy-checklist.md` con fase staging-first

### 3.3 Fase P4 — Atomic frontend swap (~2h, dopo B17b)

**Obiettivo**: deploy frontend zero-downtime + rollback istantaneo.

#### Pre-condizione one-shot (sysadmin manuale)

> ⚠️ **NON ESEGUIRE SENZA CHANGE WINDOW**: prima del primo deploy
> con symlink swap, l'operatore deve fare manualmente sul server,
> directory `<remote-frontend-path>`:

```sh
# Pseudo-comando — non eseguire senza change window e backup.
mv dist dist-initial && ln -s dist-initial dist
```

Questo trasforma `dist/` directory in symlink → `dist-initial/` senza
perdere lo stato corrente. Da fare **prima** su staging poi su prod.

#### Pipeline P4

| Step | Azione | File toccato |
|---|---|---|
| 1 | Crea `dist-{NEW_SHA}/` su server | `deploy.sh` |
| 2 | `scp -r dist/*` nella nuova dir | `deploy.sh` |
| 3 | Atomic swap: `ln -sfn dist-{NEW_SHA} dist.tmp && mv -Tf dist.tmp dist` | `deploy.sh` |
| 4 | Cleanup retain N=3 (`RETAIN_FRONTEND_VERSIONS`) | `deploy.sh` |
| 5 | Aggiornamento `incident_response.md` rollback frontend | docs |

> ⚠️ Esempi `ln -sfn` / `mv -Tf` qui sono **pseudo-comandi**, non
> eseguire senza change window e senza staging dry-run.

#### Rollback istantaneo P4

```sh
# Pseudo-comando — non eseguire senza change window.
ln -sfn dist-{OLD_SHA} dist.tmp && mv -Tf dist.tmp dist
```

Senza ri-build, senza ri-upload bundle.

## 4. Dipendenze / gating

```
B17a (infra) → B17b (profile + smoke) → P4 (atomic swap, prima staging poi prod)
                                     └─→ STAGING-SETUP rank 15: GHA deploy.yml (separato)
```

- **B17a → B17b**: il profilo non e' utile senza l'infra a monte
- **B17b → P4**: P4 e' tecnicamente fattibile senza B17, ma richiede
  precondizione one-shot direttamente su prod senza safety net staging.
  Raccomandazione: B17 prima, P4 dopo
- **B17 → GHA deploy**: STAGING-SETUP rank 15 (GHA + OIDC + approval
  gate) e' progetto separato, fuori scope di questa roadmap

## 5. Costi stimati

| Voce | Effort | Costo infra mensile (USD) |
|---|---|---|
| B17a Staging minimal | 3-4gg | ~$30-50/mese |
| B17b Profile + smoke | 1gg | 0 incrementale |
| P4 atomic swap | ~2h | 0 incrementale |
| **Totale roadmap** | **~5-7gg** | **~$30-50/mese** |

Breakdown costo mensile (stima):
- EC2 t3.small: ~$15/mese
- RDS t3.micro o schema condiviso: $0-15/mese
- ElastiCache cache.t3.micro o Redis self-host: $0-12/mese
- S3 bucket staging + lifecycle: ~$1/mese
- Data transfer + CloudWatch logs: ~$2/mese

## 6. Cosa NON fare pre-deadline

> ❌ Vincoli pre-deadline (5 maggio 2026):

- ❌ **Aprire branch B17** — provisioning infra reale richiede 5-7gg
  + decisioni costo + accesso AWS prod
- ❌ **Aprire branch P4** — modifica `deploy.sh` core, richiede testing
  su staging che non esiste
- ❌ **Provisioning EC2/RDS/Redis** — fuori scope agent, richiede
  credenziali AWS prod
- ❌ **DNS / Route53 modifiche** — richiede AWS access fuori scope
- ❌ **Let's Encrypt cert emission** — richiede DNS attivo + server vivo
- ❌ **Sysadmin precondizione P4 su prod** senza staging-first
- ❌ **`deploy.sh` rewrite** anche minimo
- ❌ **`staging.conf` popolato con valori reali** — il file template
  arriva quando l'infra esiste

## 7. Checklist decisioni aperte

Decisioni che vanno prese **prima** dell'inizio dell'implementazione
B17. Nessuna richiede accesso prod; tutte sono decisioni di scope.

| Decisione | Opzioni | Default raccomandato | Trade-off |
|---|---|---|---|
| **DB staging** | A: RDS dedicata t3.micro <br> B: schema `<schema>_staging` su RDS prod | A (dedicata) | A: $15/mese, isolamento totale. B: $0, rischio cross-contamination + lifecycle drift |
| **Cache/broker** | A: Redis locale su EC2 staging <br> B: ElastiCache cache.t3.micro | A (locale) per economia | A: $0 + gestione manuale. B: $12/mese managed |
| **DNS** | A: sottodominio dedicato (es. `staging.<base>`) <br> B: path-based su prod (es. `<base>/staging`) | A | A: cleaner separation. B: complica nginx routing + cert |
| **TLS cert** | A: Let's Encrypt via certbot <br> B: ACM se ALB davanti | A | A: $0, manuale rinnovo (auto via cron). B: managed ma richiede ALB ($16/mese) |
| **S3 bucket staging** | nuovo bucket vs prefix dentro bucket prod | nuovo bucket | Bucket dedicato: lifecycle policy aggressiva safe; prefix: rischio mix con prod |
| **IAM role staging** | nuovo role vs riuso prod (sconsigliato) | nuovo role | Niente shared cred surface |
| **SSM namespace** | `/plenora/staging/*` parallelo a `/plenora/prod/*` | sì | Pattern allineato, audit trail coerente |
| **Branch deploy staging** | `main` vs `develop` vs feature branches | `develop main` (ALLOWED_BRANCHES) | Permette test feature branch staging-first |
| **Promotion path staging→prod** | manual tag vs auto-promote | manual | Manual = safety net; auto richiede gates GHA (out of scope B17) |
| **Webserver follow symlink (P4)** | nginx default vs config esplicita | default | Da confermare in `runtime-prereqs.md` prima del primo deploy P4 |

## 8. Riferimenti

- **Sorgenti analisi**:
  - `orchestrate/phase_0_consolidation/deploy_ops_second_pass_0_5e.md §P4, §8.1, §8.2`
  - `orchestrate/phase_0_consolidation/phase_0_5x_final_synthesis.md` rank 4 + rank 14
- **Doc operativi vivi**:
  - `backend/docs/ops/deploy-checklist.md` — pre/exec/post deploy + rollback
  - `backend/docs/ops/runbook.md` §1 (B-2 workflow), §2.4 (deploy.sh failure modes)
  - `backend/docs/ops/runtime-prereqs.md` — env baseline, dependencies (verificare nginx symlink follow)
  - `backend/docs/ops/incident_response.md` — incident framework + JWT-specific
  - `backend/docs/ops/handoff.md` — bus-factor-2 minimum, ownership matrix
  - `backend/docs/ops/observability-sink-decision.md` — Metabase A2 (parallel ops thread)
  - `backend/docs/ops/compat-residue-runbook.md` — Q1-Q4 sessione DB autorizzata
- **Codice riferimento (NON modificato in questa PR)**:
  - `deploy.sh:396-429` — `deploy_frontend()` pre-P4 distruttivo
  - `deploy/profiles/prod.conf` — template profile, `RETAIN_FRONTEND_VERSIONS=3` già presente
  - `deploy/profiles/README.md` — selezione profilo
