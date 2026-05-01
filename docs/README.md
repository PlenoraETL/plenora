# URBY-ETL Documentation

Indice navigabile della documentazione URBY-ETL. Tutta la doc
sostanziale vive sotto `docs/`. Il [README root](../README.md) rimane
entry point GitHub con quickstart minimale + link qui.

---

## Struttura

```
docs/
├── README.md                       # questo indice
├── product/                        # prodotto: overview + roadmap
│   ├── overview.md
│   └── roadmap.md
├── architecture/                   # design + ADR + invariants
│   ├── overview.md
│   ├── invariants.md
│   ├── inventory_report.md
│   └── decisions/                  # ADR registry (Nygard, append-only)
├── development/                    # dev workflow + setup BE/FE
│   ├── onboarding.md               # setup nuovo dev
│   ├── backend.md                  # setup + test policy + ADR refs
│   ├── frontend.md                 # setup + folder structure + test
│   ├── contributing.md             # branch policy + PR rules
│   ├── changelog.md                # PR raggruppate per tema
│   └── code-map.md                 # mappa puntuale moduli (high-density)
├── ops/                            # runbook operativi
├── compliance/                     # SBOM + GDPR + ISO 27001 SoA + secrets
├── audits/                         # audit reports (read-only snapshot)
│   └── orchestrate/                # Phase 0-6 workspace snapshot
└── support/                        # support / FAQ (placeholder)
```

---

## Per dominio

### Product

- [overview](product/overview.md) — capabilities live + posizionamento prodotto.
- [roadmap](product/roadmap.md) — residui audit, Phase 2/3/4+, capability services come direzione futura.

### Architecture

- [overview](architecture/overview.md) — core platform, BE/FE boundaries, execution flow, persistence, auth, observability.
- [invariants](architecture/invariants.md) — invarianti vincolanti del codebase (es. write DB via DestinationWriter).
- [inventory_report](architecture/inventory_report.md) — mappa endpoint REST + Celery tasks + tabelle DB.
- [decisions/](architecture/decisions/) — ADR registry (formato Nygard, append-only). Indice in [`decisions/README.md`](architecture/decisions/README.md).

### Development

- [onboarding](development/onboarding.md) — setup completo nuovo dev.
- [backend](development/backend.md) — setup BE + test policy + compatibility table + ADR refs.
- [frontend](development/frontend.md) — setup FE + folder structure + API client conventions + test patterns.
- [contributing](development/contributing.md) — branch policy + commit format + PR rules + review checklist.
- [changelog](development/changelog.md) — PR raggruppate per tema (Phase 1 hardening, R-25, R-04/R-04b, FE-22, compat/productization).
- [code-map](development/code-map.md) — mappa puntuale moduli (~71KB, riferimento high-density).

### Ops

Runbook recovery + incident response + deploy checklist:

- [runbook](ops/runbook.md) — recovery procedures + audit `intermediate_mode` override (§4).
- [deploy-checklist](ops/deploy-checklist.md) — pre/post deploy + rollback (26 checkpoint).
- [backup-restore](ops/backup-restore.md) — backup S3 + restore DB (7 scenari A-F).
- [dr_drill_playbook](ops/dr_drill_playbook.md) — DR drill formale.
- [dr_drill_log](ops/dr_drill_log.md) — log esecuzioni drill (template).
- [incident_response](ops/incident_response.md) — severity matrix + comms.
- [jwt-incident-response](ops/jwt-incident-response.md) — JWT compromise playbook.
- [runtime-prereqs](ops/runtime-prereqs.md) — env vars + dependencies + IAM.
- [trusted-proxies](ops/trusted-proxies.md) — anti-spoofing allowlist.
- [observability-sink-decision](ops/observability-sink-decision.md) — observability decision (deferred post-deadline).
- [compat-residue-runbook](ops/compat-residue-runbook.md) — post-launch cleanup runbook.
- [staging-roadmap](ops/staging-roadmap.md) — staging + atomic frontend swap.
- [tickets](ops/tickets.md) — ticket triage / sev levels.
- [handoff](ops/handoff.md) — operator onboarding checklist.

### Compliance

- [secrets-management](compliance/secrets-management.md) — SSM Parameter Store + rotation.
- [sbom](compliance/sbom.md) — CycloneDX SBOM (regen via `backend/scripts/generate_sbom.py`).
- [sbom.json](compliance/sbom.json) — SBOM CycloneDX 1.4 JSON.
- [soa-iso-27001](compliance/soa-iso-27001.md) — Statement of Applicability.
- [registro-trattamenti-gdpr](compliance/registro-trattamenti-gdpr.md) — registro GDPR.

### Audits

Audit reports e snapshot di triage storici, read-only.

- [`audits/`](audits/) — indice con convenzioni e separation
  audits/ vs ops/ vs ADR.
- [`audits/orchestrate/`](audits/orchestrate/) — snapshot del
  workspace Phase 0-6 dell'operatore originale (consolidation,
  stabilization, hardening, audit, launch, structural quality,
  product grade). Importato 2026-05-01.

Phase 1 audit **findings** (residui ancora azionabili) sono tracciati
in [`product/roadmap.md`](product/roadmap.md).

### Support

Placeholder per FAQ utente / runbook supporto. Vedi [ops/](ops/) per
runbook tecnici operativi.

---

## Convenzioni

### File markdown

- Naming: `kebab-case.md` per file nuovi (eccezione: ADR mantengono il
  prefix numerico storico `NNNN-YYYY-MM-DD-slug.md`).
- Append-only per docs di tracciabilita': ADR registry, changelog,
  audit reports, drill logs.
- I doc product/roadmap restano editabili con append/move tra status,
  ma niente delete retroattivo.

### Link relativi

Tutti i link interni sono **relativi** al file che li contiene. I path
target sono dentro `docs/` o relative al repo root (`../backend/...`,
`../frontend/...` per riferire codice).

### Indice ADR

Indice in [`architecture/decisions/README.md`](architecture/decisions/README.md).
Workflow: scrivi nuovo ADR → aggiorna l'indice. Naming `NNNN-YYYY-MM-DD-slug.md`.
