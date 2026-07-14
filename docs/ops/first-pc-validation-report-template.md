# First-PC validation report template

Stream: BE-GO-READY-INSTALL-016-FIRST-PC-COMMAND-GUARD

Companion review checklist: `first-pc-report-review-checklist.md`.

## 1. Source revision

Run and paste the complete output:

```sh
git rev-parse --short HEAD
git status --short
```

Revision:

Working tree clean: YES / NO

## 2. Native Linux path

### 2.1 Preflight

```sh
bash scripts/install.sh --check
```

Result: PASS / FAIL

### 2.2 Guided install

```sh
bash scripts/install.sh --non-interactive
```

### 2.3 Full non-interactive environment

```sh
PLENORA_INSTALL_ENVIRONMENT=development \
PLENORA_INSTALL_DATABASE_URL=postgresql://plenora:change-me@127.0.0.1:5432/plenora \
PLENORA_INSTALL_DB_SCHEMA=plenora \
PLENORA_INSTALL_BROKER_URL=amqp://guest:guest@127.0.0.1:5672// \
PLENORA_INSTALL_STORAGE_POLICY=object_storage \
PLENORA_INSTALL_BACKUP_BUCKET=plenora-backups \
PLENORA_INSTALL_BACKEND_PORT=5004 \
bash scripts/install.sh --non-interactive
```

### 2.4 Health and readiness

```sh
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Health: PASS / FAIL

Ready: PASS / FAIL

### 2.5 Dramatiq cleanroom smoke

Run this from the backend environment. It enqueues `cleanroom.ping` through
RabbitMQ and polls the PostgreSQL task result store for at most 30 seconds.

```python
import time
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store

handle = dispatch_task_by_name("cleanroom.ping")
store = default_task_result_store()
deadline = time.monotonic() + 30
record = None
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in {"SUCCESS", "FAILURE"}:
        break
    time.sleep(0.25)
print(handle.id, record)
```

Result: PASS / FAIL

## 3. Docker path (FIRST SCAFFOLD, NOT YET VALIDATED)

Run the parser-only check before building:

```sh
bash scripts/docker-validate.sh --dry-run
```

Then run the real build:

```sh
docker compose build backend
docker compose up -d
```

Result: PASS / FAIL / STOPPED

## 4. Stop condition

Where execution stopped:

First failing command:

Complete error output:

## 5. Machine details

OS and version:

CPU / RAM:

Docker version, when applicable:

## 6. Evidence summary

Native path: PASS / FAIL / NOT RUN

Docker path: PASS / FAIL / NOT RUN

This file is only an evidence form. A completed report and its command output
are required before any deployment path can be promoted from candidate status.
