# Backend Development

FastAPI, Dramatiq, PostgreSQL, and RabbitMQ backend for Plenora-ETL.

Main runtime domains:

- Estrai
- Manipola
- Arricchisci
- Carica
- Scheduler
- IMDF
- Auth
- Resources/storage
- Observability/install

---

## Local Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

export PLENORA_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql://plenora:plenora@localhost:5432/plenora"
export DRAMATIQ_BROKER_URL="amqp://guest:guest@localhost:5672/"

uvicorn app.main:app --reload --port 5004
python -m dramatiq workers.dramatiq_app --processes 1 --threads 8
python -m workers.beat
```

See [runtime-prereqs.md](../ops/runtime-prereqs.md) for the full runtime
configuration contract.

---

## Test Policy

Default local policy: targeted tests first, one wider domain run when the
change touches shared contracts or cross-domain behavior. Full regression is
left to CI unless preparing a release candidate.

Examples:

```bash
python -m pytest -p no:cacheprovider tests2/shared/test_shared_engine_boundary_guard.py -q
python -m pytest -p no:cacheprovider tests2/orchestrator/unit/test_execution_result_contract.py -q
```

Run broader suites when touching:

- auth/security;
- runtime/bootstrap/install;
- shared execution/resource/connection contracts;
- scheduler/Dramatiq;
- database migrations or persistence contracts.

---

## Static Analysis

Run the Python hygiene gate before opening a PR that touches backend code:

```bash
python -m ruff check backend/app backend/workers backend/scripts backend/tests2 scripts --select F401,F403,F405,F811,F821,F841
python -m pyflakes backend/app backend/workers backend/scripts backend/tests2 scripts
```

Use Vulture only as an audit aid, not as an automatic deletion rule:

```bash
python -m vulture backend/app backend/workers backend/scripts scripts --min-confidence 60
```

Expected interpretation:

- Ruff and Pyflakes findings are actionable unless a line is deliberately
  excluded with a narrow reason.
- Vulture has many false positives in this backend: FastAPI route handlers,
  Dramatiq actors, Pydantic model fields, middleware hooks, registry methods and
  reflection-based call sites often look unused statically.
- Keep imports at module top by default. Local imports are acceptable for
  circular-import breaks, optional/heavy providers, bootstrap boundaries, or
  test monkeypatch isolation.

---

## Current Architecture Direction

Backend work is converging from vertical feature modules toward horizontal
engine boundaries:

- Auth / security
- Connections / external providers
- Execution / orchestration
- Transformation
- Resources / storage
- Persistence / DB
- Observability / install

Current guardrails:

- `app.shared.execution.*`, `app.shared.resources.*`,
  `app.shared.connections.*`, and `app.shared.contracts.*` are leaf packages.
- `CanvasNode` and `CanvasGraph` are structural only; node-type business
  semantics belong in the owning engine.
- `ExecutionResult` is the typed execution boundary; heavy payloads such as
  DataFrames, bytes, and streams are forbidden.
- ArcGIS is treated as a connection provider boundary, not an IMDF subdomain.
- `app.shared` root is allowlisted and should be drained into subpackages.

See:

- [horizontal-engines.md](../architecture/horizontal-engines.md)
- [macro-components.md](../architecture/macro-components.md)
- [execution-engine-seam.md](../architecture/execution-engine-seam.md)

---

## Architecture References

- [Architecture overview](../architecture/overview.md)
- [Horizontal engines](../architecture/horizontal-engines.md)
- [Invariants](../architecture/invariants.md)

---

## Operational References

- [Clean install, non-Docker](../ops/install-non-docker.md)
- [Clean install, Docker](../ops/install-docker.md)
- [Runbook](../ops/runbook.md)
- [Backup and restore](../ops/backup-restore.md)
- [Incident response](../ops/incident_response.md)
- [Runtime prerequisites](../ops/runtime-prereqs.md)
