# First PC Validation Report Template

Stream: BE-GO-READY-INSTALL-016-FIRST-PC-COMMAND-GUARD

Companion docs:
- first-pc-report-review-checklist.md

## 1. Build identity

Run and paste:

```bash
git rev-parse --short HEAD
git status --short
```

## 2. Non-Docker path

Run and paste:

```bash
bash scripts/install.sh --check
PLENORA_INSTALL_ENVIRONMENT=development \
PLENORA_INSTALL_DATABASE_URL=postgresql://plenora:plenora@127.0.0.1:5432/plenora \
PLENORA_INSTALL_DB_SCHEMA=plenora \
PLENORA_INSTALL_REDIS_URL=redis://127.0.0.1:6379/0 \
PLENORA_INSTALL_STORAGE_POLICY=db \
PLENORA_INSTALL_BACKUP_BUCKET= \
PLENORA_INSTALL_BACKEND_PORT=5004 \
bash scripts/install.sh --non-interactive
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
python - <<'PY'
import time
from app.core.task_runtime.dispatch import dispatch_task_by_name
from app.core.orchestrator.task_result_store import default_task_result_store
handle = dispatch_task_by_name("cleanroom.ping")
store = default_task_result_store()
deadline = time.monotonic() + 30
state = "PENDING"
while time.monotonic() < deadline:
    record = store.get(handle.id)
    if record is not None and record.state in ("SUCCESS", "FAILURE"):
        state = record.state
        break
    time.sleep(0.5)
print(state)
PY
```

## 3. Docker path - FIRST SCAFFOLD, NOT YET VALIDATED

Run and paste:

```bash
bash scripts/docker-validate.sh --dry-run
docker compose build backend
```

## 4. Stop conditions

If any command fails, stop and paste the failing command, exit code, and last
80 log lines.

## 5. What validated means

This template is only the report form. A path becomes validated only after a
filled report is reviewed and accepted.
