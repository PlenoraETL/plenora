# First-PC validation report template

`BE-GO-READY-INSTALL-015-FIRST-PC-EXECUTION-PACK`.

Marco fills this template after running the first-PC handoff
pack on the real Linux host. The output gets pasted back into
the audit thread; partial reports are fine -- stop where the
first failure occurs and paste what you have.

The two paths are NOT equivalent. The non-Docker path is the
**go-ready** first-PC test; the Docker path is the FIRST
SCAFFOLD validation, NOT yet validated, expected to surface
real bugs.

Companion docs:
- [`install-marco-dry-run.md`](install-marco-dry-run.md) -- the
  one-page operator pack (sections 1-7 single-machine + section
  5.4 Docker deltas).
- [`install-non-docker.md`](install-non-docker.md) -- full
  reference runbook.
- [`install-docker.md`](install-docker.md) -- Docker scaffold
  runbook (FIRST SCAFFOLD / NOT YET VALIDATED).
- [`first-pc-report-review-checklist.md`](first-pc-report-review-checklist.md)
  -- reviewer-side intake checklist that maps each acceptance
  line in this report to a pass/fail decision and a follow-up
  BE task title (used after the report is pasted back).

Replace every `<TBD>` with the real output. Leave the section
headings; they are the contract surface. Do NOT redact
secrets; `.env*` files are excluded from the paste.

---

## 0. Host snapshot

```
hostname:           <TBD>
distro:             <TBD>   (e.g. Ubuntu 24.04.1 LTS)
kernel:             <TBD>   (uname -r)
total RAM:          <TBD>   (free -h | head -2)
disk free /home:    <TBD>   (df -h /home | tail -1)
docker --version:   <TBD>   (or "not installed")
```

## 1. Clone + commit hash (start of run)

```
$ git clone <repo-url> ~/plenora-etl
$ cd ~/plenora-etl
$ git rev-parse --short HEAD
<TBD>
$ git rev-parse HEAD
<TBD>
$ git status --short
<TBD>   (must be empty for a clean run)
```

If `git status --short` is non-empty, STOP and paste the
status. Do not proceed with mixed working tree.

---

## 2. Non-Docker path (go-ready first-PC test)

Reference: [`install-marco-dry-run.md`](install-marco-dry-run.md)
sections 2.1-2.9 + the 8-item smoke harness in section 5.

### 2.1 host packages installed (dry-run section 2.1)

```
$ sudo apt update && sudo apt install -y python3.11 ...
<TBD>   (last 5 lines of apt output: 'X upgraded, Y newly installed' line is enough)
```

### 2.2 prereqs preflight (dry-run section 2.4)

```
$ bash scripts/install.sh --check
<TBD>   (paste full output)
```

Pass criterion: exits 0 and every required command + the
`gdal` shared library reports OK. STOP HERE if `--check`
fails -- paste the output and report which probe failed.

### 2.3 guided installer (dry-run section 2.5)

Run the full 7-var env shape verbatim (do NOT trim; missing
any of these forces the script to prompt and the run hangs):

```
$ PLENORA_INSTALL_ENVIRONMENT=development \
  PLENORA_INSTALL_DATABASE_URL='postgresql://plenora:plenora@127.0.0.1:5432/plenora' \
  PLENORA_INSTALL_DB_SCHEMA=plenora \
  PLENORA_INSTALL_REDIS_URL=redis://127.0.0.1:6379/0 \
  PLENORA_INSTALL_STORAGE_POLICY=db \
  PLENORA_INSTALL_BACKUP_BUCKET=plenora-pc-test-no-real-s3 \
  PLENORA_INSTALL_BACKEND_PORT=5004 \
  bash scripts/install.sh --non-interactive
<TBD>   (paste last ~20 lines: should end with [ok] Wrote backend/.env)
```

### 2.4 venv + Python deps (dry-run section 2.6)

```
$ cd backend && python3.11 -m venv venv
$ ./venv/bin/pip install --upgrade pip
$ ./venv/bin/pip install -r requirements.txt
<TBD>   (paste last 5 lines; full output is too long)
```

### 2.5 native dependency verification (dry-run section 3)

OS binaries (dry-run section 3.1):
```
$ python3.11 --version
<TBD>
$ pg_config --version
<TBD>
$ gdalinfo --version
<TBD>
... (paste all 10 probes from section 3.1)
```

Python imports inside the venv (dry-run section 3.2):
```
$ ./venv/bin/python - <<'PYEOF'
... (paste the heredoc from dry-run section 3.2)
PYEOF
<TBD>   (expected: "OK: 17 runtime modules import cleanly" or similar)
```

Service probes (dry-run section 3.3):
```
$ psql ... -c "SELECT current_user, current_database(), version();"
<TBD>
$ psql ... -c "SELECT has_database_privilege('plenora', 'CREATE');"
<TBD>   (must return 't')
$ redis-cli -u redis://127.0.0.1:6379/0 PING
<TBD>   (must return PONG)
```

### 2.6 foreground smoke (dry-run section 2.7)

Three terminals, each pasted separately:

Terminal 1 -- uvicorn (paste first 30 lines and any [CRASH] / [ERROR]):
```
<TBD>
```

Terminal 2 -- celery worker (paste first 20 lines + any error):
```
<TBD>
```

Terminal 3 -- celery beat (paste first 15 lines + tick log every ~60s):
```
<TBD>
```

### 2.7 /health and /ready (dry-run section 2.8)

```
$ curl -fsS http://127.0.0.1:5004/health
<TBD>   (expected HTTP 200 + JSON with postgres.ok=true and redis.ok=true)

$ curl -fsS http://127.0.0.1:5004/ready
<TBD>   (expected HTTP 200 + 6 ready checks all ok)
```

If `/ready` returns 503, paste the response body verbatim
(it lists which check failed) and STOP HERE. The
non-Docker path is not green; the Docker validation below
is moot until this path is green.

### 2.8 memory caps visibility (dry-run section 5.2)

```
$ ./venv/bin/python - <<'PYEOF'
from app.config import settings
for k in ("LOAD_FILE_MAX_BYTES","WORKER_RSS_MAX_BYTES","RESOURCE_MAX_BYTES","CROSS_JOIN_MAX_OUTPUT_ROWS","MULTI_JOIN_MAX_OUTPUT_ROWS"):
    print(f"{k}={settings.get(k)}")
PYEOF
<TBD>   (paste the 5 lines)
```

### 2.9 tiny ETL smoke (dry-run section 5.3)

```
$ ./venv/bin/python - <<'PYEOF'
from workers.celery_app import celery_app
ar = celery_app.send_task("cleanroom.ping")
print(ar.get(timeout=30))
PYEOF
<TBD>   (expected: pong)
```

### 2.10 acceptance summary (non-Docker)

Mark each line YES / NO / PARTIAL:

```
[2.2] --check passes:                  <TBD>
[2.5] all native deps import:          <TBD>
[2.6] three foreground processes up:   <TBD>
[2.7] /health 200:                     <TBD>
[2.7] /ready 200, 6 checks ok:         <TBD>
[2.8] 5 memory caps printed:           <TBD>
[2.9] cleanroom.ping returns 'pong':   <TBD>
```

If all 7 are YES, the non-Docker path is GREEN. The
go-ready first-PC test is complete. The Docker validation
below is OPTIONAL but useful.

---

## 3. Docker validation path (FIRST SCAFFOLD, NOT YET VALIDATED)

Reference: [`install-docker.md`](install-docker.md) sections
1-7. **Expected to surface real bugs.** Paste verbatim
output even on failure -- that is the whole point of this
section.

### 3.1 docker prereqs (install-docker.md section 1)

```
$ docker --version
<TBD>
$ docker compose version
<TBD>
$ docker compose config 2>&1 | head -20
<TBD>   (compose YAML must parse without error)
```

If `docker compose config` fails, STOP and paste the
error. The scaffold YAML may have a defect; that is the
scaffold validation point.

### 3.2 env decisions (install-docker.md section 2)

```
$ cp .env.docker.example .env.docker
$ python3 -c "import secrets; print('PLENORA_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
$ python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
$ chmod 600 .env.docker
$ ls -l .env.docker
<TBD>   (must be -rw------- 1 marco marco)
```

Do NOT paste the `.env.docker` content (it has secrets).
Just confirm `chmod 600` is applied.

### 3.3 docker-validate.sh dry-run (preflight, no docker calls)

Before any real `docker compose build`, run the dry-run to
confirm the validation pack itself parses on this host:

```
$ bash scripts/docker-validate.sh --dry-run 2>&1 | tail -30
<TBD>   (expected: every step prints "(dry-run: not executing)";
       script exits 0)
```

If the dry-run exits non-zero, STOP HERE and paste the full
output: the validation pack itself has a defect on this
host. Do NOT proceed to the real run.

### 3.4 build + bring up (install-docker.md section 3)

Either via the validation pack wrapper:

```
$ bash scripts/docker-validate.sh 2>&1 | tee /tmp/docker-validate.log | tail -40
<TBD>
```

OR via the raw compose commands (same shape, useful when
isolating a single step):

```
$ docker compose build backend 2>&1 | tail -20
<TBD>
$ docker compose up -d postgres redis 2>&1 | tail -10
<TBD>
$ docker compose up -d --wait backend 2>&1 | tail -10
<TBD>
$ docker compose up -d worker beat 2>&1 | tail -10
<TBD>
```

Most likely failure: `docker compose build backend` failing
on `libgdal-dev` (Debian bookworm package name moved). If
that happens, paste the apt error. The scaffold flags this
risk in install-docker.md "Known scaffold limitations".

### 3.5 docker compose ps + healthchecks

```
$ docker compose ps
<TBD>   (paste full table; backend should be (healthy))
```

### 3.6 /health + /ready from host (install-docker.md section 4)

```
$ curl -fsS http://127.0.0.1:5004/health
<TBD>
$ curl -fsS http://127.0.0.1:5004/ready
<TBD>
```

If 503, also paste:

```
$ docker compose logs --tail 100 backend
<TBD>
$ docker compose logs --tail 50 worker
<TBD>
$ docker compose logs --tail 50 beat
<TBD>
```

### 3.7 Celery check via `docker compose exec` (install-docker.md section 5)

```
$ docker compose exec worker python -c \
    "from workers.celery_app import celery_app; \
     print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
<TBD>   (expected: pong)
```

### 3.8 memory caps visibility inside container (install-docker.md section 6)

```
$ docker compose exec backend python -c "
from app.config import settings
for k in ('LOAD_FILE_MAX_BYTES','WORKER_RSS_MAX_BYTES','RESOURCE_MAX_BYTES','ARTIFACT_PREVIEW_MAX_BYTES','CROSS_JOIN_MAX_OUTPUT_ROWS','MULTI_JOIN_MAX_OUTPUT_ROWS'):
    print(f'{k}={settings.get(k)}')
"
<TBD>   (paste the 6 lines)
```

### 3.9 non-destructive stop (install-docker.md section 7)

```
$ docker compose stop
<TBD>
$ docker compose ps
<TBD>   (all services Exited; volumes preserved)
```

### 3.10 acceptance summary (Docker scaffold)

Mark each line YES / NO / PARTIAL:

```
[3.1] docker compose config parses:        <TBD>
[3.3] docker-validate.sh --dry-run exits 0: <TBD>
[3.4] docker compose build backend:        <TBD>
[3.4] backend (healthy):                   <TBD>
[3.6] /health 200:                         <TBD>
[3.6] /ready 200:                          <TBD>
[3.7] cleanroom.ping in worker -> pong:    <TBD>
[3.8] 6 memory caps printed inside cont.:  <TBD>
[3.9] docker compose stop preserves vols:  <TBD>
```

If any are NO, paste the failing command output and STOP.
Do NOT cherry-pick a "best-effort" partial run. The
scaffold needs honest failure reports more than partial
green claims.

---

## 4. Where I stopped (if not green)

```
last successful section:    <TBD>   (e.g. "2.7 /ready returned 503")
first failing command:      <TBD>
paste of failure output:    <TBD>
my hypothesis (optional):   <TBD>
```

## 5. Time spent

```
non-Docker path total:      <TBD>   (rough hours)
Docker validation path:     <TBD>
total wall-clock:           <TBD>
```

## 6. Free-form notes

Any surprise, gotcha, doc gap, ambiguous instruction Marco
wants to flag. This section drives the next round of doc
fixes.

```
<TBD>
```

---

## Stop conditions (fast reference)

- `--check` fails on a host package -> stop at 2.2; paste
  output; do not proceed.
- `/ready` returns 503 on the non-Docker path -> stop at
  2.7; paste body; the Docker section is moot.
- `docker compose build backend` fails -> stop at 3.3;
  paste error; the rest of section 3 is moot but the
  non-Docker section above remains the go-ready signal.
- `cleanroom.ping` does not return `pong` -> stop at 2.9
  or 3.6; that is a live regression.
- Anything that looks like data loss, OOM kill, or a
  `[CRASH]` log line not visible in the foreground
  terminals -> stop, snapshot logs, do not retry.

## What "validated install" means

The non-Docker path is "validated" when sections 2.1-2.10
are all YES on this host. The Docker path is "validated"
when sections 3.1-3.9 are all YES on this host AND the
report has been reviewed and merged.

Until this report comes back green for the non-Docker
path, the production claim "Plenora-ETL installs cleanly
on a fresh Ubuntu 24.04 PC" remains an assumption, not a
verified fact. Until this report comes back green for the
Docker path, install-docker.md keeps the FIRST SCAFFOLD /
NOT YET VALIDATED markers.
