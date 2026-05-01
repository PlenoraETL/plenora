# Contributing — URBY-ETL

Il contenuto pieno e' stato spostato in
[`docs/development/contributing.md`](docs/development/contributing.md)
nel quadro della centralizzazione documentazione (Wave 1.6).

Questo file root resta per convenzione GitHub (sidebar Insights, PR
template auto-discovery).

---

Branch policy, commit format, test policy locale, PR rules, review
checklist, window-gated changes, ADR scrivere quando, mantra
operativi:

→ [docs/development/contributing.md](docs/development/contributing.md)

---

## Quick reference

- **Branch prefix**: `feat/` `fix/` `chore/` `refactor/` `test/` `docs/`.
- **Commit**: Conventional Commits (`<type>(<scope>): <subject>`).
- **Test locale**: mirati + max 1 wider di dominio. Full a GitHub CI.
- **No FE+BE mischiati** in stessa PR salvo full-stack esplicita.
- **No deploy/DB/infra live** senza window dichiarata.
- **No nuove librerie** senza motivazione.
- **No cambio default silenzioso**.
