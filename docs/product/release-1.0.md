# Release 1.0 Readiness

Status reviewed: 2026-05-07.

This page answers one question: what must be true before Plenora can be
called a stable 1.0 product.

## Current Position

Plenora is moving from vertical project modules toward horizontal engines.
The direction is correct, but the release line must stay honest:

- backend architecture is significantly cleaner than the legacy shape;
- frontend is mostly stable and in standby unless backend contracts change;
- single-machine installation has a documented path but still needs a real
  clean-host validation report;
- Docker backend scaffold exists, but full Docker product packaging is not
  complete until frontend delivery is included and validated;
- documentation is being reduced from audit history into product guidance.

## 1.0 Must-Haves

| Area | Required state |
|---|---|
| Architecture | Horizontal engine boundaries are documented and enforced by tests where useful. |
| Auth and tenancy | No known mutable singleton tenant context in production paths. |
| Resources | Large artifact access is bounded; no unbounded stream joins in hot paths. |
| Execution | Canvas execution returns typed or clearly bounded contracts. |
| Frontend | Uses backend error/resource contracts without legacy `detail` assumptions. |
| Installation | Non-Docker install validated on a fresh Linux PC from a clean clone. |
| Docker | Compose path validated end-to-end, including frontend delivery. |
| Docs | Canonical docs describe current reality; audits remain historical. |

## Current Blockers

| Blocker | Why it matters | Close condition |
|---|---|---|
| First clean Linux install not yet proven | A product that cannot be installed cleanly is not 1.0. | Completed report from a fresh machine with health, ready, worker, beat, and frontend checks. |
| Docker frontend gap | Compose is not a full product stack without UI delivery. | Frontend Docker image or documented static delivery path in compose, validated end-to-end. |
| Remaining vertical execution seams | Manipola, Arricchisci, and IMDF still carry legacy shape in places. | Clear contract boundaries and no hidden global context. |
| Documentation split still settling | Old audit material can confuse new developers. | Canonical docs are short, current, and linked from `docs/README.md`. |

## Explicit Non-Blockers

These should not block 1.0 unless a concrete bug appears:

- historical ADRs mentioning the old brand;
- archived audit reports;
- future Go rewrite;
- perfect Docker/Kubernetes production hardening;
- full elimination of every large file if it is cohesive and tested.

## Go-Ready Meaning

In this project, "go-ready" means the code can later be rewritten in Go
without rediscovering the architecture:

- explicit data passed through function boundaries;
- small contracts instead of broad facades;
- provider protocols instead of direct vertical imports;
- no magic request context for tenant state;
- resource references instead of moving large data through generic payloads.

It does not mean rewriting the current backend in Go before 1.0.

## Release Gate

Plenora can be tagged 1.0 when:

1. The latest `main` commit installs cleanly on a fresh Linux PC.
2. Docker compose runs the full product path or clearly documents the only
   excluded piece with a tested replacement.
3. A smoke pipeline can be created, executed, previewed, and downloaded.
4. Admin, auth, worker, beat, backup/readiness, and frontend smoke checks are
   documented with real output.
5. `docs/README.md` points to current docs only.
