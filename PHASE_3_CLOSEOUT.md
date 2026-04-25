# PHASE 3 AUDIT — Closeout

**Data chiusura**: 2026-04-25
**Stato**: CHIUSA
**Scope**: `orchestrate/phase_3_audit/TRIAGE.md`
**Mirror interno (tracking fase)**: `orchestrate/phase_3_audit/TRIAGE.md`
**Deadline**: 2026-05-05 (rispettata, chiusura anticipata 10 giorni)

Questo file e' la versione **git-tracked** del closeout di Phase 3.
Pattern simmetrico a `PHASE_1_CLOSEOUT.md` / `PHASE_2_CLOSEOUT.md`. Il
dettaglio operativo del triage e delle review batch-by-batch vive in
`orchestrate/phase_3_audit/TRIAGE.md` (cartella `orchestrate/` non sotto
version control). In caso di divergenza, questo file e' fonte di
verita' per lo stato formale della fase; il TRIAGE.md per le
motivazioni tecniche puntuali e per la cronaca delle review.

---

## 1. Sintesi

Phase 3 si chiude con:

- **8 T-* item chiusi** (CI gate, security audit/upload guards, ops
  runbook, SBOM, reconciliation smoke). 100% dei blocker pre-freeze
  identificati dal triage.
- **4 P1 tecnici chiusi** (audit trail mutating, NaN/Inf JSONB,
  unsafe append gate, parallel merge guard).
- **3 residui accettati esplicitamente** con mitigazione operativa
  documentata (P1-02, P1-03, 2 fail `test_n4_formula_validator_contract.py`).
- **0 P0 aperti**, **0 access violation Windows / crash di processo**
  sulla suite security default post-Batch B isolation.

Nessun fake closure. Quello che non e' stato chiuso e' classificato
esplicitamente come accepted residual (§4) con caveat operativo o
ticket Phase 4.

---

## 2. Commit Phase 3 (cronologia)

10 commit atomici, ognuno con review esplicita prima del merge:

| Commit | Batch | Tema |
|---|---|---|
| `1bb90b4` | A | T-01/T-02/T-03 — CI minimal + pytest.ini + test deps |
| `cd7ec76` | B | T-04 + P1-01 — upload size cap + audit_action su 26 endpoint |
| `d485bbe` | B (review) | DB-live test isolation (4 layer di stub conftest) |
| `727b794` | C | P1-06 — parallel merge index guard fail-loud |
| `1c704d5` | C | P1-04 — special floats JSONB sentinel object namespaced |
| `3fb7b8f` | C | P1-05 — unsafe append + per_batch combo gate |
| `edf618c` | D-light | T-06 — incident response playbook |
| `8ab8c10` | D-light | T-07 — DR drill playbook + RPO/RTO targets |
| `dd87eff` | D-light | T-05 — SBOM baseline + generator (107 components, 0 CVE) |
| `1e95dd9` | D2 | T-08 — PG reconciliation E2E smoke (10k rows, opt-in) |

---

## 3. Stato item TRIAGE

### T-* (blocker pre-freeze, 8/8 done)

| ID | Tema | Commit |
|---|---|---|
| T-01 | CI minima | `1bb90b4` |
| T-02 | pytest.ini path stale | `1bb90b4` |
| T-03 | test deps requirements-dev | `1bb90b4` |
| T-04 | upload size cap | `cd7ec76` |
| T-05 | SBOM + CVE baseline | `dd87eff` |
| T-06 | incident response playbook | `edf618c` |
| T-07 | DR drill + RPO/RTO | `8ab8c10` |
| T-08 | reconciliation E2E smoke | `1e95dd9` |

### P1 tecnici chiusi (4/6)

| ID | Tema | Commit |
|---|---|---|
| P1-01 | audit_action sui 26 endpoint mutating sensibili | `cd7ec76` |
| P1-04 | NaN/Inf JSONB sentinel object namespaced | `1c704d5` |
| P1-05 | unsafe append per-batch gate (`allow_unsafe_append`) | `3fb7b8f` |
| P1-06 | parallel merge index guard | `727b794` |

---

## 4. Accepted residuals before freeze

3 residui non chiusi runtime, accettati con mitigazione operativa
documentata. Dettaglio in `orchestrate/phase_3_audit/TRIAGE.md` sezione
"Accepted residuals before freeze".

| ID | Mitigazione pre-freeze | Fix vero |
|---|---|---|
| P1-03 RateLimiter arricchisci per-processo | doc/caveat: `celery --concurrency=1` per integrazioni rate-limit strict (Nominatim/IPA/Geoportale) + monitoring 429/503 | Redis shared token bucket post-launch (4-6h, riusa infra `app.shared.token_blacklist`) |
| P1-02 rate limiting endpoint costosi | worker concurrency hard-cap + monitoring `admin_system_status` + size cap T-04 gia' attivo | slowapi middleware post-launch (4-8h, 5 endpoint) |
| 2 fail `test_n4_formula_validator_contract.py` | nessuna (test bug, non isolation) | Phase 4 ticket dedicato (~1h: allineare expected message al validator AST reale) |

Owner Phase 4: TBD.

---

## 5. Gate / verifiche principali

- **CI minimal aggiunta** (`1bb90b4`): `.github/workflows/ci.yml`
  con job backend (pytest + redis service container) e frontend
  (vitest). Cancel-in-progress su PR. Niente coverage gate, niente
  DB matrix in questo step.
- **Security default no crash** (Batch B closeout review #1-#8): suite
  `pytest tests2/security` post-Batch B → **753 passed, 2 failed
  (N4 pre-esistenti), 12 skipped DB-live, 0 access violation, 0 crash
  di processo**. 4 layer di isolation in `tests2/conftest.py` +
  patch boundary corretto sui test che facevano DB reale nascosto.
- **DB-live opt-in disciplinato**: 13 test totali con marker
  `integration` + skipif `URBY_RUN_DB_LIVE_TESTS=1` (5 log_immutable
  + 7 mfa_totp pre-esistenti, 1 nuovo T-08 reconciliation smoke).
  Default CI path mai chiama `psycopg2.connect`.
- **Reconciliation smoke (T-08 / `1e95dd9`)**: 4-layer skip policy
  (marker integration → env opt-in → preflight TCP → preflight psycopg2
  in subprocess). Verifiche:
  - default no opt-in → skipped, no connect.
  - opt-in con PG ext live → 1 passed (verificato in ambiente Claude).
  - opt-in con psycopg2 non sano → skipped via subprocess probe (exit
    code 3221225477 catturato dal parent), no crash pytest runner
    (verificato in ambiente reviewer).
- **SBOM baseline (T-05 / `dd87eff`)**: CycloneDX 1.4 con 107 componenti,
  0 vulnerabilita' note al refresh. Cadenza manuale trimestrale, niente
  workflow scheduled, niente CI blocking.
- **DR target dichiarati (T-07 / `8ab8c10`)**: RPO ≤ 24h, RTO ≤ 2h.
  Playbook ripetibile + log append-only. Soglie alert: `RPO_measured > 36h`
  → P1; `RTO_measured > 2h` → P2.

---

## 6. Note operative

- `TRIAGE.md` completo (review batch-by-batch, ~440 righe) vive in
  `orchestrate/phase_3_audit/TRIAGE.md`, **fuori repo**. Non e'
  duplicato qui per mantenere il closeout sintetico, allineato al
  pattern Phase 1/2.
- I 3 residui accettati (§4) NON sono blocker go-live ma vanno
  promossi a Phase 4 con owner formale e deadline. Ciascuno ha fix
  vero stimato e mitigazione pre-freeze gia' applicabile.
- Phase 3 non ha aperto nuovi P0. Lo stato del repo a fine Phase 3 e'
  piu' robusto del baseline pre-Phase 3 su 5 dimensioni (CI gate,
  security guard, DB-live isolation, data correctness guards, ops
  runbook + reconciliation).

---

## 7. Carry-over verso Phase 4

- **P1-03 fix runtime** (Redis shared bucket): da promuovere a P1
  attivo Phase 4 mese 1 o al primo segnale reale di rate limit hit.
- **P1-02 fix runtime** (slowapi middleware): da promuovere se appare
  in monitoring un caso reale di abuso o saturazione worker single-user.
- **Test N4 message alignment**: ticket Phase 4 dedicato.
- **Item declassati P2** (vedi `TRIAGE.md`): file >2000 righe, ADR
  numbering, mutation testing, full observability stack, status page,
  maintenance window policy. Da rivalutare in kickoff Phase 4.
