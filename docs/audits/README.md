# Audits

Report storici e snapshot di audit/triage cross-stack del prodotto
URBY-ETL. **Read-only**: questi documenti riflettono lo stato delle
analisi al momento della stesura, non vengono mantenuti aggiornati
quando il codebase evolve.

---

## Cosa vive qui

- **Audit reports** completi (snapshot di un momento specifico).
- **Triage notes** raccolti durante phase di stabilization / hardening
  / launch.
- **Run logs / decision trails** preservati per tracciabilita'
  decisionale (es. perche' una compat residue e' stata accettata,
  perche' un'invariante e' stata aggiunta).

## Cosa NON vive qui

- **ADR formali** → [`../architecture/decisions/`](../architecture/decisions/).
- **Runbook operativi attivi** → [`../ops/`](../ops/).
- **Roadmap residui** → [`../product/roadmap.md`](../product/roadmap.md).
- **Storia PR** → [`../development/changelog.md`](../development/changelog.md).

---

## Cartelle

### [`orchestrate/`](orchestrate/) — Phase 0-6 workspace snapshot

Snapshot read-only del workspace di triage usato dall'operatore
originale durante le Phase 0-6 (consolidation → product grade).
Importato 2026-05-01 come parte della centralizzazione documentazione
(Wave 1.7).

**Origine**: workspace locale `C:\Users\Marco\Desktop\int_efm\orchestrate\`,
58 file (55 markdown + 1 SQL schema dump + 1 Python helper script + 1
Docker pull log).

**Scopo dell'import**: preservare il contesto decisionale delle 6
phase. Senza questo import, un dev che lavora da un altro PC perde
la storia di triage / design notes / probe results che ha guidato le
ADR e i fix Phase 1.

**Stato**: snapshot append-only. Niente edit retroattivo. Per nuovi
audit reports, creare nuove cartelle (`audits/<slug>/`) o aggiungere
file in cartelle dedicate.

**Indice**: vedi [`orchestrate/README.md`](orchestrate/README.md) per
la mappa originale del workspace.

---

## Convenzioni

- Naming cartelle: `kebab-case` per audit reports nuovi. `orchestrate/`
  mantiene il naming storico originale del workspace.
- Niente migrazione di link interni: i path relativi nei doc
  importati (es. riferimenti a `orchestrate/phase_X/...md`) sono
  validi solo nel contesto interno della cartella stessa, non
  cross-doc verso il resto del repo.
- Eventuali "evidence file" non-md (SQL dumps, log, helper scripts)
  sono OK se passati da safety scan (no credentials, no PII, no
  high-entropy tokens). Vedi PR di import per il safety report
  associato.
