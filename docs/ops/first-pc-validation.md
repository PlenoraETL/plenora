# First PC Validation

Stream: BE-GO-READY-INSTALL-016/017.

Use this document to collect and review evidence from a first clean machine
validation. The report becomes accepted only after the filled evidence is
reviewed against the checklist below.

## 1. Build Identity

Run and paste:

```bash
git rev-parse --short HEAD
git status --short
```

## 2. Non-Docker Path

Run and paste:

```bash
bash scripts/install.sh --check
PLENORA_INSTALL_ENVIRONMENT=development \
PLENORA_INSTALL_DATABASE_URL=postgresql://plenora:plenora@127.0.0.1:5432/plenora \
PLENORA_INSTALL_DB_SCHEMA=plenora \
PLENORA_INSTALL_BROKER_URL=amqp://guest:guest@127.0.0.1:5672 \
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

## 3. Docker Path

Docker remains a separate validation path until a real Linux host report is
green end-to-end.

Run and paste:

```bash
bash scripts/docker-validate.sh --dry-run
docker compose build backend
```

## 4. Stop Conditions

If any command fails, stop and paste:

- failing command;
- exit code;
- last 80 relevant log lines.

Do not infer success for commands that were not run.

## 5. Review Checklist

Intake:

- [ ] Commit and working tree output are present.
- [ ] Every requested command has output.
- [ ] Any stopped run includes exit code and log tail.

Non-Docker scoring:

- [ ] Preflight passed.
- [ ] Install passed.
- [ ] `/health` passed.
- [ ] `/ready` passed.
- [ ] `cleanroom.ping` reached `SUCCESS`.

Docker scoring:

- [ ] Dry-run passed.
- [ ] Backend image build passed.
- [ ] Docker remains marked not validated until the full real-host gate is
      green.

Outputs:

- [ ] Produce one intake summary line.
- [ ] Produce one backend task for each failing acceptance line.
- [ ] Produce doc-fix tasks for confusing or missing instructions.
