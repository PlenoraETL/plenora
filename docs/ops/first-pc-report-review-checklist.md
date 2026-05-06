# First-PC report review / intake checklist

`BE-GO-READY-INSTALL-017-FIRST-PC-REPORT-INTAKE-CHECKLIST`.

Reviewer-side companion to
[`first-pc-validation-report-template.md`](first-pc-validation-report-template.md).
The template is what Marco fills out on the Linux PC. This page
is what the reviewer (anyone reading the pasted-back report)
runs through to decide:

  - which paths are green;
  - which are red and what to file as a follow-up BE task;
  - whether anything in the docs (template / dry-run pack /
    install-docker.md) needs to change before the next run.

> **Honesty contract.** This checklist does NOT validate the
> install on its own. A path is "validated" only when the
> filled-in report has every line in its acceptance summary
> marked YES on a real Linux PC. Until that happens, the
> non-Docker path remains the go-ready candidate (NOT yet
> verified on Marco's host) and the Docker path stays
> **FIRST SCAFFOLD / NOT YET VALIDATED**.

---

## 1. Intake -- did the report arrive in the expected shape?

Run the structural checks first. If any FAIL, ask Marco to
re-paste the relevant section before scoring; do NOT score on
a partial paste.

| # | Structural check | PASS criterion | If FAIL |
|---|---|---|---|
| 1.1 | All 7 top-level sections present (`0. Host snapshot` ... `6. Free-form notes`) | every header from the template is in the paste | ask for the missing section -- partial intake gets recorded as INCOMPLETE, not RED. |
| 1.2 | Section 0 host snapshot sane | distro is Ubuntu 22.04 / 24.04 / Debian 12; total RAM >= 2 GB; disk free /home >= 5 GB | flag as `BLOCKER-HOST` and stop; the host is below the minimum the dry-run pack assumes. |
| 1.3 | Section 1 commit hash is a valid `git` SHA visible on the repo | the short SHA + full SHA both exist in `git log` for the canonical branch | flag as `BLOCKER-COMMIT` -- the report is anchored to a commit the reviewer cannot inspect. |
| 1.4 | Section 1 `git status --short` is empty | empty paste under that line | if non-empty, flag as `WARN-DIRTY-TREE`; the run reflects a dirty tree, may not be reproducible. |
| 1.5 | No `.env*` content pasted by mistake | `chmod 600 .env.docker` etc. confirms file mode but does NOT echo `PLENORA_ENCRYPTION_KEY` / `JWT_SECRET_KEY` values | if a secret leaked into the paste, flag as `BLOCKER-SECRET-LEAK`; rotate the secret + edit the paste before any further work. |

If 1.1-1.5 are all PASS or only 1.4 is WARN, proceed to the
non-Docker scoring.

---

## 2. Non-Docker path scoring (go-ready candidate)

Look at the report's section 2.10 acceptance summary first
(seven lines marked YES / NO / PARTIAL). That is the canonical
verdict; the per-step sections (2.1..2.9) are where the reason
lives.

### 2.1 Top-line decision

| Section 2.10 line | Required for GREEN |
|---|---|
| `[2.2] --check passes:` | YES |
| `[2.5] all native deps import:` | YES |
| `[2.6] three foreground processes up:` | YES |
| `[2.7] /health 200:` | YES |
| `[2.7] /ready 200, 6 checks ok:` | YES |
| `[2.8] 5 memory caps printed:` | YES |
| `[2.9] cleanroom.ping returns 'pong':` | YES |

Verdict mapping:

  - **All 7 YES**: non-Docker path is GREEN. The go-ready
    first-PC test is satisfied on this host. Update the
    `INSTALL R-004` residual in
    `.agent-temp/BE_INSTALLATION_AUDIT.md` to "verified on
    `<host>` at commit `<short SHA>`"; do NOT yet flip
    install-docker.md FIRST SCAFFOLD markers (those are the
    Docker path).
  - **One or more NO / PARTIAL**: non-Docker path is RED. The
    Docker section is moot until this is fixed. File one BE
    task per failing line, using the per-step diagnostic
    pointers in section 2.2 below.
  - **Mixed YES + missing**: INCOMPLETE. Ask for the missing
    section before scoring.

### 2.2 Per-step diagnostic pointers (when RED)

| Failing line | Most likely root cause | BE task to file |
|---|---|---|
| `[2.2] --check passes` NO | host package missing (python3.11 / node / psql / redis-cli / curl / git or `gdal` shared lib) | `BE-GO-READY-INSTALL-XXX-HOST-PACKAGE-DRIFT` (the dry-run pack 2.1 apt one-liner needs an updated package name) |
| `[2.5] all native deps import` NO | venv built against wrong Python OR a binary wheel (psycopg2 / pyarrow / fiona) ABI mismatch | `BE-GO-READY-INSTALL-XXX-NATIVE-DEPS-DRIFT` -- include the failing import line from section 2.5 |
| `[2.6] three foreground processes up` NO | one of uvicorn / worker / beat crashes during boot. Re-read `[CRASH]` line from terminal 1/2/3 | `BE-GO-READY-INSTALL-XXX-FOREGROUND-BOOT-FAIL` -- name the failing service in the title |
| `[2.7] /health 200` NO | postgres or redis check failing. Inspect `/health` JSON body for the `ok=false` key | `BE-GO-READY-INSTALL-XXX-HEALTH-CHECK-FAIL` -- include the false key (postgres / redis) |
| `[2.7] /ready 200, 6 checks ok` NO | one of the 6 readiness checks failed. The body lists which | `BE-GO-READY-INSTALL-XXX-READY-CHECK-FAIL` -- include the failing check name |
| `[2.8] 5 memory caps printed` NO | venv import-error before the heredoc returns; usually downstream of [2.5] | bundle with the [2.5] task; do NOT file separately |
| `[2.9] cleanroom.ping returns 'pong'` NO | broker URL wrong, Redis down, OR worker not consuming. The dry-run pack section 5.3 lists the three failure modes | `BE-GO-READY-INSTALL-XXX-CLEANROOM-PING-REGRESSION` -- this is a real regression on a path that used to be green; high priority |

Each task title should include `<host>` and `<short SHA>` so
later reviewers can tie the failure to a specific run.

---

## 3. Docker path scoring (FIRST SCAFFOLD, NOT YET VALIDATED)

Score section 3.10 acceptance summary using the same
shape, but the bar is intentionally lower: the Docker path is
expected to surface real bugs because the scaffold has not
been validated end-to-end on a real Linux host yet.

### 3.1 Top-line decision

| Section 3.10 line | Meaning of YES | Meaning of NO |
|---|---|---|
| `[3.1] docker compose config parses` | compose file is syntactically valid on the host | scaffold YAML defect; high priority |
| `[3.3] docker-validate.sh --dry-run exits 0` | the validation pack itself parses on this host | scaffold script defect; high priority |
| `[3.4] docker compose build backend` | image builds (most fragile step; libgdal-dev package name has moved on Debian bookworm) | flag as `WARN-SCAFFOLD-BUILD` -- expected hazard, not a blocker |
| `[3.4] backend (healthy)` | health probe passes inside the container | dependent on the previous step |
| `[3.6] /health 200` | host-side curl from the outside reaches the containerised app | dependent on the previous |
| `[3.6] /ready 200` | 6 readiness checks all green | dependent |
| `[3.7] cleanroom.ping in worker -> pong` | container-internal Celery round-trip works | regression vs the cleanroom CI lane |
| `[3.8] 6 memory caps printed inside cont.` | runtime caps visible inside the container | dependent on backend boot |
| `[3.9] docker compose stop preserves vols` | non-destructive stop works | scaffold compose defect |

Verdict mapping:

  - **All 9 YES**: Docker scaffold has been **executed** end
    to end on this host AT THIS COMMIT. That is NOT the same
    as "validated"; it is the prerequisite. The reviewer's
    next BE task should be to flip the FIRST SCAFFOLD markers
    in `install-docker.md` and `install-marco-dry-run.md`,
    with explicit reference to the commit SHA + host signature
    from section 1.
  - **Any NO**: Docker scaffold is RED. Open one BE task per
    failing line. The non-Docker path remains the go-ready
    signal; do NOT block on Docker.
  - **Any PARTIAL**: Docker scaffold is YELLOW. Treat the
    PARTIAL as a NO for scoring purposes (do not promote a
    "best-effort" run to validation), but cross-link the
    PARTIAL note in the BE task body so the next operator
    knows what was already attempted.

### 3.2 Per-step diagnostic pointers (when NO)

| Failing line | Most likely root cause | BE task to file |
|---|---|---|
| `[3.1] docker compose config parses` NO | compose YAML defect on the runner host (env interpolation, file mode, etc.) | `BE-GO-READY-INSTALL-XXX-COMPOSE-CONFIG-FAIL` |
| `[3.3] docker-validate.sh --dry-run` NO | validation script bug surfaced on a real Linux host (most likely a path / line-ending issue per `test_docker_validate_script_guard`) | `BE-GO-READY-INSTALL-XXX-VALIDATE-SCRIPT-DRYRUN-FAIL` |
| `[3.4] docker compose build backend` NO | apt package name drift (libgdal-dev) OR base image change | `BE-GO-READY-INSTALL-XXX-DOCKERFILE-BUILD-FAIL` -- include the failing apt or pip line |
| `[3.4] backend (healthy)` NO | image built but container fails the HEALTHCHECK. Same boot-gate failures as the non-Docker path, but inside the container | `BE-GO-READY-INSTALL-XXX-CONTAINER-HEALTHCHECK-FAIL` |
| `[3.6] /health 200` NO | port mapping or proxy issue between host and container | `BE-GO-READY-INSTALL-XXX-CONTAINER-PORT-MAPPING-FAIL` |
| `[3.6] /ready 200` NO | one readiness gate is failing inside the container; same triage as `[2.7]` but with `docker compose logs` | bundle with the `(healthy)` task |
| `[3.7] cleanroom.ping` NO | broker URL inside the container is wrong, OR worker container is not joined to the redis network | `BE-GO-READY-INSTALL-XXX-CONTAINER-BROKER-NETWORK` |
| `[3.8] memory caps` NO | image lacks an env var that the runtime expects; check `.env.docker` is being injected correctly | `BE-GO-READY-INSTALL-XXX-CONTAINER-ENV-DRIFT` |
| `[3.9] stop preserves vols` NO | compose volume name drift; this is a destructive defect (the next stop wipes data) | `BE-GO-READY-INSTALL-XXX-COMPOSE-VOLUME-DRIFT` -- high priority |

---

## 4. Stop-condition handling (sections 4-6 of the report)

Marco may stop at any point. The reviewer must:

  - read section 4 ("Where I stopped (if not green)") FIRST
    when any acceptance line is missing -- it tells the
    reviewer where to focus the diagnostic;
  - read section 5 ("Time spent") to size the next task --
    if Marco spent > 4h on a single section, that is a doc
    gap, not just a regression; flag the doc page in the BE
    task title;
  - read section 6 ("Free-form notes") for surprise / gotcha /
    ambiguous instruction -- those drive the next round of
    doc fixes (template / dry-run pack / install-docker.md).

A "where I stopped" paste with a useful failure log is more
valuable than a partially-green acceptance summary. Treat any
report with a populated section 4 as a credible-failure-mode
artefact even if the acceptance summary lines below it are
empty.

---

## 5. Outputs of the intake review

After scoring, the reviewer produces three artefacts:

  1. **Intake summary line** in
     `.agent-temp/BE_INSTALLATION_AUDIT.md` of the form:
     ```
     `<host>` `<short SHA>` non-Docker: GREEN/RED/INCOMPLETE; Docker: GREEN/RED/INCOMPLETE/SKIPPED
     ```
     plus a short two-line summary of the failing acceptance
     lines (none if both green).
  2. **One BE task per failing line**, titled per the
     diagnostic pointers in sections 2.2 and 3.2.
  3. **Doc-fix tasks** (if section 6 raised any), titled
     `BE-GO-READY-INSTALL-XXX-DOC-DRIFT-<page>` and pointing
     at the specific page that was ambiguous.

The intake summary line is the smallest unit of "we have
empirical evidence about this commit on this host"; it is what
later reviewers grep for to find the most recent validated
run. The BE task list is the work-queue.

---

## 6. What this checklist does NOT do

Mirrored from the template's own honesty section:

  - It does NOT flip `FIRST SCAFFOLD` / `NOT YET VALIDATED`
    markers anywhere. Only a follow-up BE stream that
    explicitly cites a green report can do that.
  - It does NOT promote partial acceptance to "validated".
    A PARTIAL is treated as a NO for scoring purposes.
  - It does NOT replace the existing dry-run pack guards
    (`test_install_marco_dry_run_doc_guard.py`,
    `test_install_smoke_harness_doc_guard.py`,
    `test_install_execution_pack_doc_guard.py`,
    `test_first_pc_validation_report_template_guard.py`,
    `test_docker_scaffold_guard.py`,
    `test_docker_validate_script_guard.py`). Those locks
    enforce the doc shape; this checklist enforces the
    review process.

---

## Companion docs

- [`first-pc-validation-report-template.md`](first-pc-validation-report-template.md)
  -- the operator template (input to this checklist).
- [`install-marco-dry-run.md`](install-marco-dry-run.md) --
  the one-page operator pack the report follows.
- [`install-non-docker.md`](install-non-docker.md) -- full
  reference runbook (section 13.2 cleanroom acceptance bar).
- [`install-docker.md`](install-docker.md) -- Docker scaffold
  runbook (FIRST SCAFFOLD / NOT YET VALIDATED).
