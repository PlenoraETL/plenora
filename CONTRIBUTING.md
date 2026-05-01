# Contributing — URBY-ETL

Guida per contributor interni. Branch policy, commit format, PR rules,
test policy locale, review checklist.

---

## 1. Branch policy

- **`main`**: branch protetto. Solo via PR mergiata. CI verde
  obbligatoria.
- **Feature branches**: prefisso semantico:

  | Prefix | Per |
  |---|---|
  | `feat/...` | Nuova feature o miglioramento funzionale. |
  | `fix/...` | Bug fix. |
  | `chore/...` | Maintenance, deps update, config tweak, deprecation hygiene. |
  | `refactor/...` | Refactor senza behavior change. |
  | `test/...` | Test-only PR (modernization, coverage, harness). |
  | `docs/...` | Docs-only PR. |

  Esempi:
  - `feat/r25-b1-resolve-intermediate-mode`
  - `fix/security-cross-tenant-cascade`
  - `chore/be-compat-productization-cleanup`
  - `test/r04-n9-source-bound-modernization`
  - `docs/productization-wave-1-5`

- **Branch deletion**: dopo merge, branch remoto eliminato via
  `--delete-branch` di `gh pr merge`. Branch locali eliminabili a mano.
- **Force push su main**: vietato.
- **Lifetime branch**: target ~3-5 giorni. Se piu' lungo, valutare
  rebase su main intermedio o split.

---

## 2. Commit format

**Conventional Commits** (gia' lo schema usato dal repo).

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type ammessi

`feat` `fix` `chore` `refactor` `test` `docs` `perf` `build` `ci` `style`.

### Scope tipici

`auth`, `intermediate`, `arricchisci`, `manipola`, `estrai`, `carica`,
`scheduler`, `imdf`, `security`, `compat`, `cleanup`, `ux`, `ops`,
`p1-b`, `p1-c`, `r25`, `r04`, ecc.

### Subject

- Imperativo presente, lowercase, no punto finale.
- Max ~72 char ideale, max ~100 char hard.

### Body

- 1-N paragrafi che spiegano **perche'** (non cosa: il diff lo dice).
- Mention residui chiusi (R-XX), evidence path, decisioni.
- Max ~70-80 col per riga.

### Footer

- Reference a issue/PR (`#123`), breaking change (`BREAKING CHANGE: ...`).
- **NIENTE `Co-Authored-By` trailer** salvo richiesta esplicita
  utente (memory rule).

### Esempio

```
feat(intermediate): R-25 completion — B2.b manipola/arricchisci + B3 endpoint dual-auth + B4 ADR/runbook

Chiude il debito R-25 (audit gap INTERMEDIATE_PERSISTENCE_MODE per-call
override). Estende propagation kwarg a manipola/arricchisci e
introduce endpoint HTTP superadmin-gated. Frontend (B5) escluso:
opzionale, non blocca capability backend.

[...body con sections B2.b / B3 / B4...]

Reference: #41
```

---

## 3. Test policy locale

**Niente full suite locale per default.** GitHub CI fa la full
regression.

**Budget locale target**: 2-5 min, max 10 min.

| Tipo PR | Test locale richiesto |
|---|---|
| **docs / compat / test-only** | mirati sui file modificati. **Niente wider massivi locali.** |
| **prod runtime / security core** | mirati + max 1 wider di dominio. Target 2-5 min, max 10 min. |
| **auth / scheduler / conftest globale** | estendere il run con giudizio (es. wider su tests2/security/ + tests2/scheduler/). |
| **release candidate** | full backend + frontend in locale prima del merge. |

**Comandi tipici**:

```bash
# Backend mirato (file specifico)
cd backend
pytest tests2/<dominio>/<file>.py -v

# Backend wider (1 dominio)
pytest tests2/security/

# Backend wider cross-domain (max 1 in PR normale)
pytest tests2/security/ tests2/orchestrator/

# Frontend mirato
cd frontend
npx vitest run tests/<area>/<file>.test.{js,jsx}

# Frontend full (CI lo fa, locale solo se serve)
npm run test
```

**Eccezioni dove estendere il run locale e' giustificato**:

- prod code core toccato
- auth/security runtime
- conftest / helper globale modificato
- scheduler/runtime
- release candidate

**Cosa NON fare**:

- ❌ Lanciare `pytest tests2/` (full backend) per default.
- ❌ Rilanciare varianti della full suite in foreground o background.
- ❌ Aspettare che la full passi localmente: lascia fare a CI.

---

## 4. PR rules

### Quando aprire una PR

- Sempre, anche per docs/compat. Niente push diretto su `main`.
- Branch dedicato. Mai merge da `main` a `main` locale + push.

### Scope coerente

Una PR = un tema. Se durante l'implementazione emerge scope creep:
- splittare in PR atomiche, oppure
- riportarlo nel body PR + considerare follow-up dedicato.

### LOC budget (campanello, non stop rigido)

| Tipo PR | LOC target | Stop rigido |
|---|---|---|
| Test-only | +400 | +800 |
| Compat / docs | +600 | +1500 |
| Feature backend | +400 prod / +800 test | +1000 |
| Feature full-stack | concordato | concordato |

Se sfori il campanello ma il batch e' coerente: documentalo nel body
PR. Se sfori lo stop rigido: split.

### Vincoli duri sempre

- ❌ NO mischiare FE + BE nella stessa PR salvo feature full-stack
  esplicitamente dichiarata.
- ❌ NO deploy/DB/infra live (modifica deploy.sh, migrations, env prod)
  senza window dedicata.
- ❌ NO nuove librerie senza motivazione esplicita nel body.
- ❌ NO cambio default silenzioso (settings, env vars, behavior).
- ❌ NO `--no-verify`, `--no-gpg-sign`, `--force` salvo richiesta
  utente.

---

## 5. Review checklist

Prima di merge, verifica:

### Scope

- [ ] Diff coerente con titolo + body PR?
- [ ] Niente file fuori scope dichiarato?
- [ ] Niente FE in PR backend / niente BE in PR frontend?

### Behavior

- [ ] Default invariato (env vars, settings, API public)?
- [ ] Backward compat: caller esistenti non rompono?
- [ ] Rollback: la PR e' reversibile via revert + re-deploy?

### Test

- [ ] Mirati sui file modificati? (Esito documentato in body PR.)
- [ ] Wider 1 dominio (se applicable)? (Esito documentato.)
- [ ] CI verde su GitHub? (Backend pytest + Frontend vitest.)
- [ ] 0 nuovi `pytest.skip` / `@xfail` (a meno che giustificati)?

### Security / runtime

- [ ] Auth runtime invariato (rate limiter, JWT, MFA)?
- [ ] Tenant isolation preservata (cross-group access bloccato)?
- [ ] Audit trail invariato o esteso (mai ridotto)?

### Docs

- [ ] Se cambia behavior pubblico: aggiornato README o ADR?
- [ ] Se introduce alias/deprecation: documentato in
      `backend/README.md` compatibility table + DeprecationWarning?
- [ ] Se chiude un residuo audit: aggiornato `docs/ROADMAP.md`?

### Mergeable

- [ ] `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`?
- [ ] Niente conflict con `main`?
- [ ] CI checks tutti SUCCESS?

---

## 6. Window-gated changes

Le modifiche sotto richiedono **finestra di lavoro dichiarata** (no
hot-fix sotto traffico):

- `backend/migrations/*.sql` — schema DB.
- `deploy.sh`, `deploy/profiles/prod.conf`, `.github/workflows/*.yml`.
- Modifica `app/core/auth/auth.py` su rate-limiter / JWT secret /
  password hashing.
- Modifica `app/shared/crypto.py` (Fernet key chain).
- Modifica scheduler beat config (cron / TTL leader / heartbeat).

Pattern: aprire PR draft, far review, merge in finestra di lavoro
concordata, monitorare deploy.sh dry-run + post-deploy health gate.

Vedi [`backend/docs/ops/deploy-checklist.md`](backend/docs/ops/deploy-checklist.md).

---

## 7. ADR — quando scriverne uno

Apri un ADR (Architecture Decision Record) quando:

- Scegli un'opzione architetturale con trade-off non ovvi.
- Decidi di rifiutare un pattern o una libreria con ragione strutturale.
- Documenti un compat residue accettato consapevolmente.
- Chiudi un ramo decisionale aperto da un audit / threat model.

Format Nygard, append-only. Vedi
[`backend/docs/architecture/decisions/README.md`](backend/docs/architecture/decisions/README.md)
per template + indice.

Non aprire ADR per:

- Bug fix puntuali.
- Refactor senza scelta architetturale.
- Configurazione runtime (va in `runtime-prereqs.md` o `.env.example`).

---

## 8. Issue tracking & residual register

Residui audit aperti vivono in [`docs/ROADMAP.md`](docs/ROADMAP.md).
Quando chiudi un residuo:

1. Sposta voce da NEXT/LATER a CLOSED.
2. Cita PR# / commit hash come evidence.
3. Mantieni la voce nel doc (append-only) per tracciabilita'.

Quando apri un nuovo residuo (audit o segnalazione):

1. Aggiungi voce in sezione appropriata di ROADMAP.
2. Format: ID + descrizione 1-line + evidence file:line + status NEXT/LATER + effort qualitativo (XS/S/M/L/XL) + note.

---

## 9. Mantra operativi

- **Fix the root, not the symptom.** Se un test fallisce, capisci
  perche'; non aggiungere `xfail`.
- **Default invariato e' sacro.** Cambiare un default in produzione
  richiede ADR + comunicazione ops.
- **Behavior osservabile > source string.** I test devono catturare
  rotture vere, non rinames cosmetici (vedi R-04 / R-04b).
- **Niente full suite locale.** Lascia fare a CI; il tuo tempo vale
  piu' della duplicazione.
- **Documentare come si chiude un residuo, non solo che si chiude.**
  Future-you lo apprezzera'.
