# Release 1.0 Readiness

This page answers one question: what must be true before Plenora can be
called a stable 1.0 product.

## Current Position

Plenora 1.0 is ready only when the documented install, runtime, and recovery
paths work from a clean checkout:

- backend, worker, beat, PostgreSQL, RabbitMQ, and frontend start cleanly;
- `/health` and `/ready` return success;
- a smoke pipeline can be created, executed, previewed, and downloaded;
- backup, restore, incident response, and deploy rollback docs are current;
- Windows native, Docker, and non-Docker installation paths are documented.

## 1.0 Must-Haves

| Area | Required state |
|---|---|
| Architecture | Horizontal engine boundaries are documented and enforced by tests where useful. |
| Auth and tenancy | No known mutable singleton tenant context in production paths. |
| Resources | Large artifact access is bounded; no unbounded stream joins in hot paths. |
| Execution | Canvas execution returns typed or clearly bounded contracts. |
| Frontend | Uses backend error/resource contracts without `detail`-only assumptions. |
| Installation | Windows native install validated end-to-end on Windows Server 2025; Linux non-Docker still needs a fresh-host report. |
| Docker | Compose path validated end-to-end, including frontend delivery. |
| Docs | Canonical docs describe current reality. |

## Current Blockers

| Blocker | Why it matters | Close condition |
|---|---|---|
| First clean Linux install not yet proven | The Linux non-Docker path is documented but still needs fresh-host evidence. | Completed report from a fresh machine with health, ready, worker, beat, and frontend checks. |
| Docker validation not yet evidenced | Compose is a release path only after it has been run end-to-end on Linux. | `install-docker.md` acceptance gate passes on a clean host. |
| Documentation drift | Operators need one trusted path during incidents and installs. | `docs/README.md` links only current product and operations docs. |

## Explicit Non-Blockers

These should not block 1.0 unless a concrete bug appears:

- future Go rewrite;
- perfect Docker/Kubernetes production hardening;
- full elimination of every large file if it is cohesive and tested.

## Architecture Bar

The 1.0 architecture bar is explicit data flow, clear service contracts,
bounded resource handling, tenant isolation without hidden request globals,
and documented runtime dependencies.

## Release Gate

Plenora can be tagged 1.0 when:

1. The latest `main` commit installs cleanly on Windows Server and has a
   fresh-host Linux validation report for the non-Docker path.
2. Docker compose runs the full product path or clearly documents the only
   excluded piece with a tested replacement.
3. A smoke pipeline can be created, executed, previewed, and downloaded.
4. Admin, auth, worker, beat, backup/readiness, and frontend smoke checks are
   documented with real output.
5. `docs/README.md` points to current docs only.
