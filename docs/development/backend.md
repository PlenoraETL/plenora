# URBY-ETL Backend

FastAPI + Celery + PostgreSQL backend per la piattaforma URBY-ETL
(Estrai / Manipola / Arricchisci / Carica / Scheduler / IMDF).

Rebrand: il package storico era `PLENORA-ETL`, oggi `URBY-ETL`. Production
endpoint: `lab.plenora.it` (15.160.219.93:5004).

---

## Setup dev

```bash
# Python 3.12 venv
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Env vars minime (vedi docs/ops/runtime-prereqs.md per la lista completa)
export PLENORA_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://dataflow:dataflow@localhost:5432/dataflow_test"
export REDIS_URL="redis://localhost:6379/0"

# Run dev server
uvicorn app.main:app --reload --port 5004

# Run worker Celery (in altra shell)
celery -A workers.celery_app worker --loglevel=info

# Run beat scheduler (in altra shell)
celery -A workers.celery_app beat --loglevel=info
```

---

## Test policy

**Niente full suite locale per default.** La full regression la fa GitHub
CI. Budget locale: ~10-15 minuti.

Per ogni PR:

1. **Run mirato obbligatorio** sui file modificati:
   ```bash
   pytest tests2/<dominio>/<file>.py -v
   ```

2. **Wider di dominio (max 1)**:
   ```bash
   pytest tests2/security/ tests2/orchestrator/    # esempio cross-domain
   ```

3. **Full regression**: lasciata a GitHub CI (`.github/workflows/ci.yml`).

Eccezioni in cui estendere il run locale:

- prod code core toccato
- auth/security runtime
- conftest / helper globale modificato
- scheduler/runtime
- release candidate

Test markers disponibili (`backend/tests2/pytest.ini`):
`unit`, `functional`, `security`, `contract`, `regression`, `scheduler`,
`arricchisci`, `carica`, `features`, `manipola`, `orchestrator`,
`intermediate`, `eseguibili`, `estrai`, `shared`, `integration`.

---

## Stato moduli (post Phase 1, 2026-05-01)

| Modulo | Stato |
|---|---|
| Estrai | LIVE — pipeline SQL multi-dialect (PG/MySQL/Oracle/MSSQL/SQLite) |
| Manipola | LIVE — 33+ trasformazioni, multi-join, geo ops |
| Arricchisci | LIVE — REST API + REST File processor + executable |
| Carica | LIVE — DB writer (replace/append/upsert) + ArcGIS Feature Service |
| Scheduler | LIVE — Celery beat con leader election + heartbeat |
| IMDF | LIVE — bundle persist + ArcGIS publish |
| Intermediate | LIVE — `INTERMEDIATE_PERSISTENCE_MODE` con per-call override (ADR-0019) |
| Auth | LIVE — JWT HS256 8h (ADR-0004) + MFA TOTP opt-in (ADR-0018) |

---

## Compatibility & deprecation

Alias legacy con `DeprecationWarning` emit-once + `logger.warning` attivi
(R-21/R-22/R-23, 2026-05-01). Removal target: **2026-Q4**.

| Alias | Tipo | Migrate to | Removal |
|---|---|---|---|
| `URBY_ALLOW_LOOPBACK_OUTBOUND` env var | env | `PLENORA_ALLOW_LOOPBACK_OUTBOUND` | 2026-Q4 |
| `api_utils.get_oauth2_token` | wrapper Python | `api_utils.get_cached_oauth2_token(auth['type']='oauth2_password')` | 2026-Q4 |
| `api_utils.get_oauth2_client_credentials_token` | wrapper Python | `api_utils.get_cached_oauth2_token(auth['type']='oauth2_client_credentials')` | 2026-Q4 |
| `cloud_backends.get_object_storage` | wrapper Python | `get_runtime_object_storage()` o `get_backup_object_storage()` esplicito | 2026-Q4 |

I `DeprecationWarning` sono filtrati di default in produzione (Python
`default` filter); ops vede solo il `logger.warning` (cache LRU emit-once).
Test contract: `backend/tests2/shared/test_deprecation_warnings_contract.py`.

Compat residue rimosse pre-launch (registrate in
`../ops/compat-residue-runbook.md`):
- `URBY_ENCRYPTION_KEY`, `CONN_ENCRYPT_KEY` env aliases (2026-04-23, ADR-0003).

---

## Riferimenti chiave

### Architecture Decision Records

`../architecture/decisions/` — registro Nygard.

Highlights:
- [ADR-0003](../architecture/decisions/0003-2026-04-21-fernet-crypto-at-rest.md) — Fernet crypto at-rest per credenziali PG-stored.
- [ADR-0004](../architecture/decisions/0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md) — JWT HS256, TTL 8h, no refresh token.
- [ADR-0009](../architecture/decisions/0009-2026-04-21-jwt-blacklist-redis-fail-open-read-fail-loud-write.md) — JWT blacklist Redis: fail-open READ + fail-loud WRITE.
- [ADR-0015](../architecture/decisions/0015-2026-04-22-minio-object-storage-provider.md) — MinIO object storage provider (capability cross-platform).
- [ADR-0018](../architecture/decisions/0018-2026-04-30-mfa-required-disclosure-tradeoff.md) — MFA enrollment-state disclosure tradeoff (`403 mfa_required` vs `401`).
- [ADR-0019](../architecture/decisions/0019-2026-05-01-intermediate-mode-per-call-override.md) — `INTERMEDIATE_PERSISTENCE_MODE` per-call override + endpoint dual-auth superadmin.
- [ADR-0020](../architecture/decisions/0020-2026-05-01-source-bound-test-keep-list.md) — Source-bound test KEEP list (post R-04/R-04b modernization).

Indice completo: [`../architecture/decisions/README.md`](../architecture/decisions/README.md).

### Ops runbook

`../ops/` — runbook operativi.

- [runbook.md](../ops/runbook.md) — recovery procedures + `intermediate_mode` audit override (§4).
- [deploy-checklist.md](../ops/deploy-checklist.md) — pre/post deploy + rollback.
- [backup-restore.md](../ops/backup-restore.md) — backup S3 + restore DB.
- [dr_drill_playbook.md](../ops/dr_drill_playbook.md) — DR drill end-to-end.
- [incident_response.md](../ops/incident_response.md) — severity matrix + comms.
- [jwt-incident-response.md](../ops/jwt-incident-response.md) — JWT compromise playbook.
- [runtime-prereqs.md](../ops/runtime-prereqs.md) — env vars + dependencies + IAM.
- [trusted-proxies.md](../ops/trusted-proxies.md) — anti-spoofing allowlist.
- [observability-sink-decision.md](../ops/observability-sink-decision.md) — observability decision (deferred post-deadline).
- [compat-residue-runbook.md](../ops/compat-residue-runbook.md) — post-launch cleanup runbook.

### Compliance

`../compliance/` — registro compliance.

- [secrets-management.md](../compliance/secrets-management.md) — SSM Parameter Store + rotation.
- [sbom.md](../compliance/sbom.md) — CycloneDX SBOM (regen via `backend/scripts/generate_sbom.py`).
- [soa-iso-27001.md](../compliance/soa-iso-27001.md) — Statement of Applicability.
- [registro-trattamenti-gdpr.md](../compliance/registro-trattamenti-gdpr.md) — registro GDPR.

---

## Roadmap (post-launch)

Note: roadmap, **non commitment** di implementazione.

- **Cluster-safe rate limiter** (R-01): rate-limiter `auth.py` da
  in-memory threading.Lock a Redis-backed sliding-window. Single-node
  deploy attuale rende il gap dormiente; necessario per multi-replica.
- **Compliance CI automation** (R-28/29/35): SBOM CI hook +
  Bandit/Trivy scanner + SBOM frontend.
- **DB snapshot pre-deploy** (R-30): `pg_dump`->S3 step in
  `deploy.sh:pre_deploy_check`.
- **DR drill execution** (R-33): primo drill su playbook gia' scritto.
- **Structural splits** (R-19/R-20): `arcgis_client.py` 2476 LOC e
  `enrichment_task.py` 1430 LOC — XL refactor multi-PR.
- **Core vs capability separation**: roadmap di lungo termine per
  modularizzazione `app/core/<modulo>` con interfacce piu' nette
  (out-of-scope corrente).

---

## CI

`.github/workflows/ci.yml` — Backend pytest + Frontend vitest su push/PR
verso `main`.
`.github/workflows/ci-functional.yml` — B-2 functional tier (HTTP-live
tests) nightly cron + manuale, **non** blocking PR principale.

Niente coverage gate / mutation / DB matrix integration in CI principale:
i test che richiedono DB live (oracle/mysql/mssql) si auto-skip via
`_<dialect>_reachable()` guard interno (vedi `tests2/integration/`).
