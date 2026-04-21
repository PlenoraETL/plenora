# Architecture Decision Records (ADR)

**Scope**: registro formale delle decisioni architetturali del backend URBY-ETL, in formato Nygard.

**Rationale**: ISO 27001 A.18.2 e prassi di auditing compliance richiedono un registro esplicito delle decisioni con rationale + alternative valutate. Prima di Fase 1 queste decisioni vivevano in commenti sparsi, commit message e documenti trasversali (`VALUTAZIONE_URBY_ETL.md`, `invariants.md`). Questo registro le consolida.

**Relazione con `../invariants.md`**: gli ADR **citano** le invarianti applicabili, non le duplicano. Un'invariante dichiara il **principio vincolante** (es. "write DB passa per `DestinationWriter`"); l'ADR spiega **perche'** quel principio e' stato scelto e quali alternative sono state rifiutate. Un cambio di invariante richiede PR dedicata `[invariants]`; un nuovo ADR si aggiunge in append-only.

## Convenzioni

### Naming
- File: `NNNN-YYYY-MM-DD-slug-kebab.md`
- `NNNN` numerazione progressiva a 4 cifre (0001, 0002, ...)
- `YYYY-MM-DD` data della decisione (non del write)
- `slug-kebab` titolo breve in kebab-case ascii
- Esempio: `0001-2026-04-17-eval-whitelist-over-asteval.md`

### Format (template Nygard)
```markdown
# ADR-NNNN: <titolo decisione>

**Status**: Accepted | Superseded by ADR-XXXX | Deprecated
**Date**: YYYY-MM-DD
**Deciders**: @<handle>

## Context
Problema concreto, vincoli, tradeoff.

## Decision
Cosa e' stato deciso, in 3-5 righe.

## Rationale
Perche' questa scelta, pattern usato.

## Alternatives considered
- **Alt 1**: <nome> — rejected because <reason>
- **Alt 2**: <nome> — rejected because <reason>

## Consequences
- **Positive**: cosa migliora
- **Negative**: tradeoff accettato
- **Neutral**: side-effect

## References
- Source file: `app/shared/xxx.py:line`
- Commit: <hash>
- Related: ADR-NNNN
- Invariants: `invariants.md §X.Y` (se applicabile)
```

### Stati ADR
- **Accepted** — decisione in vigore, codice la riflette
- **Superseded by ADR-XXXX** — decisione rimpiazzata, mantenuta per tracciabilita' storica
- **Deprecated** — decisione ancora formalmente valida ma in via di dismissione (indicare data di rimozione attesa)

Non cancellare mai un ADR: il registro e' append-only. Un errore in un ADR si rettifica con un nuovo ADR che lo supersede.

## Indice

### Security / Crypto
- [ADR-0001](0001-2026-04-17-eval-whitelist-over-asteval.md) — Validazione formula utente via whitelist AST anziche' libreria `asteval`
- [ADR-0002](0002-2026-04-21-safe-number-identifier-vs-bindparam.md) — f-string SQL via `_safe_number` / `_safe_identifier` vs SQLAlchemy `bindparam`
- [ADR-0003](0003-2026-04-21-fernet-crypto-at-rest.md) — Crypto at-rest via `cryptography.Fernet` per `encrypt_dict_fields`
- [ADR-0004](0004-2026-04-21-jwt-hs256-ttl-8h-no-refresh.md) — JWT HS256 vs RS256, TTL 8h, no refresh token
- [ADR-0009](0009-2026-04-21-jwt-blacklist-redis-fail-open-read-fail-loud-write.md) — JWT JTI blacklist Redis: policy fail-open READ + fail-loud WRITE
- [ADR-0010](0010-2026-04-21-trusted-proxies-allowlist.md) — Trusted proxies anti-spoofing: allowlist-based vs trust-all

### Architecture patterns
- [ADR-0005](0005-2026-04-21-async-user-recheck-helper.md) — Helper condiviso `recheck_user_authorized` (DRY su 4 task Celery)
- [ADR-0006](0006-2026-04-21-crs-opt-in-default-same.md) — CRS opt-in metric: default `"same"` retrocompat
- [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md) — `Protocol` + `typing.runtime_checkable` per storage, Python-idiomatic vs `ABC`
- [ADR-0008](0008-2026-04-21-backup-observability-db-side.md) — Observability backup DB-side (`plenora.backup_runs`/`verify_runs`) vs systemctl/journalctl
- [ADR-0011](0011-2026-04-21-file-sources-dispatch-s3-threshold.md) — `FileSourcesDispatch` threshold S3 50MB, pattern FS-4 simmetrico
- [ADR-0012](0012-2026-04-21-preview-execute-policy.md) — Policy preview vs execute per `estrai / manipola / arricchisci` (ratifica D6, post S26)

## Workflow

1. **Scrivi l'ADR** con naming convention sopra
2. **Aggiorna l'indice** in questa README
3. **Cita riferimenti** concreti (source file:linea, commit hash, invariants)
4. **Commit atomico** con prefisso `[stream-acn] adr-NNNN: <slug>` (per lo stream Fase 1; successivamente `[adr-NNNN]`)

Un ADR non deve essere auto-celebrativo. Le alternative valutate devono essere onestamente raccontate, con il motivo reale del rifiuto (costo, complessita', maturita' libreria, lock-in, ecc.).
