# ADR-0021: Env / Settings / runtime_policy.yaml config boundary

**Status**: Accepted
**Date**: 2026-05-03
**Deciders**: @CloudIA

## Context

L'audit `BE-CONFIG-001` (Phase 2 data-plane) ha mappato l'uso di
configurazione nel backend:

- **60 occorrenze** `os.getenv | os.environ.get | os.environ[...]` in
  `backend/app` (31 file) + 1 in `backend/workers`;
- **64 chiavi** in `app.config.Settings.DEFAULTS`;
- **~50 chiavi** documentate in `backend/.env.example`;
- **15 chiavi** lette via `settings.get(...)`.

Le env mescolano sette nature diverse: secret, infra di deploy, policy
applicativa, tuning ops, feature flag, test-only, alias legacy. Mancava
una regola scritta che dicesse "questa va in env", "questa va in
`Settings`", "questa va in un file versionato". L'invariant
`section 1.5 Config loading` documenta il principio ("entry point
canonico = `Settings`") con "eccezioni documentate" elencate come lista,
ma non una classificazione esaustiva ne' un criterio per decidere il
bucket di una nuova chiave.

Effetto pratico: ogni nuova feature aggiunge un `os.getenv` "nel posto
piu' vicino al consumer". Quel pattern, oggi consentito da `section 1.5`,
ha prodotto 17 letture dirette di valori che sono, di fatto,
`OPERATIONAL_TUNING` o `APP_POLICY` (login security, threshold dispatch,
retention TTL, JWT tuning). Audit/review sono piu' lenti perche' la
stessa chiave puo' essere letta in 1 a 4 file con default inline ripetuti
(`50 * 1024 * 1024`, `1800`, `10`, ecc.).

## Decision

Adottiamo una **classificazione esplicita in 7 bucket** e una regola di
routing per bucket. La regola e' append-only: una nuova env va nel bucket
corretto in PR, non promossa a posteriori.

### Bucket di classificazione

| Bucket | Contenuto | Vive in |
|---|---|---|
| **SECRET_INFRA** | Chiavi crypto, password, token, DSN con credenziali | Env solo. Mai in `Settings.DEFAULTS` (no default sensato). Mai in YAML versionato. |
| **DEPLOY_INFRA** | Endpoint/host/path/bucket/region per-deploy. Non sono secret ma sono per-host. | Env solo. Esposti in `Settings.DEFAULTS` solo come `None` quando il default `None` ha significato (es. `OBJECT_STORAGE_BUCKET`). |
| **APP_POLICY** | Decisione di prodotto: storage policy, retention, threshold dispatch, formato default, security policy login, intermediate persistence mode. | Oggi `Settings.DEFAULTS` con override env. Phase 3+: migrazione a `runtime_policy.yaml` checked-in. |
| **OPERATIONAL_TUNING** | Timeout, batch size, pool size, RAM cap. Tunabili da ops senza decisione di prodotto. | `Settings.DEFAULTS` con override env. Restano in DEFAULTS anche post Phase 3. |
| **FEATURE_FLAG** | Kill-switch / opt-in di funzionalita'. | `Settings.DEFAULTS` + invariants section 3. Owner + data introduzione + condizione di rimozione obbligatori. |
| **TEST_ONLY** | Opt-in dev/test (loopback, default key, ecc.). | Env solo. Direct read autorizzato vicino al consumer (security guard pattern). |
| **LEGACY_COMPAT** | Alias di chiavi rinominate. | Env solo, lettura come fallback. Removal criteria documentati nell'ADR di rinomina. |

### Target model

```text
SECRET_INFRA       -> env (systemd / SSM / Secret Manager)
DEPLOY_INFRA       -> env
APP_POLICY         -> Settings.DEFAULTS (Phase 2) -> runtime_policy.yaml (Phase 3+)
OPERATIONAL_TUNING -> Settings.DEFAULTS
FEATURE_FLAG       -> Settings.DEFAULTS + invariants.md section 3
TEST_ONLY          -> env (direct read, scope ristretto)
LEGACY_COMPAT      -> env (fallback solo) + criteri rimozione
```

`Settings` resta il **typed boundary** unico per APP_POLICY,
OPERATIONAL_TUNING e FEATURE_FLAG: type coercion e default centralizzati
in `app/config.py`. Direct `os.getenv(key)` su una chiave che vive in
`Settings.DEFAULTS` e' una **violazione**: va sostituito con
`settings.get(key)`.

DB tenant/project-level config resta fuori da questo ADR (usage diverso:
per-tenant, non per-deploy).

### Regole di routing

Quando si introduce una nuova env key, la PR deve:

1. dichiarare il bucket nella PR description;
2. se SECRET_INFRA / DEPLOY_INFRA / TEST_ONLY: direct env read ammesso
   (con default-less o default-`None` per DEPLOY_INFRA);
3. se APP_POLICY / OPERATIONAL_TUNING: entry in `Settings.DEFAULTS` con
   default tipato + lettura via `settings.get(key)`;
4. se FEATURE_FLAG: entry in `Settings.DEFAULTS` + registrazione in
   `invariants.md` section 3 (owner, data, scopo, rimozione attesa);
5. se LEGACY_COMPAT: ADR di rinomina con criterio di rimozione (zero hit
   log per N giorni, deadline calendar, ecc.).

### Direct env read autorizzati

Restano direct read **solo** in:

- bootstrap pre-`Settings` (`app/config.py:35`, `app/main.py:70`,
  `app/db/pg_connection.py:111`): la sequenza di import non puo'
  passare da `settings`;
- secret bootstrap (`app/shared/crypto.py:_get_raw_key`,
  `app/shared/jwt_token.py:47` per `JWT_SECRET_KEY`): fail-fast prod e'
  codice, non config;
- security guard pattern (`app/shared/api_utils.py:155-165` per
  `PLENORA_ALLOW_LOOPBACK_OUTBOUND` + alias): il pattern
  "ENVIRONMENT-aware fail-closed" deve restare nel modulo di validazione;
- helper lru-cached (`app/shared/request_utils.py:70` per
  `TRUSTED_PROXIES`): la cache e' su raw env value;
- script `backend/scripts/*` (backup unit oneshot): fuori dal process app.

### Removal criteria per LEGACY_COMPAT

| Alias | Canonico | Criterio rimozione |
|---|---|---|
| `BACKUP_S3_BUCKET` | `BACKUP_BUCKET` | Zero hit log prod per 30 giorni post-prod-flip + grep `.env` operativi. |
| `BACKUP_S3_REGION` | `AWS_REGION` | Come sopra. |
| `URBY_ALLOW_LOOPBACK_OUTBOUND` | `PLENORA_ALLOW_LOOPBACK_OUTBOUND` | Gia' deprecated 2026-04-23; rimozione target Q4 2026 (allineato con `R-21/R-22/R-23`). |
| `LOG_DB_FILE` / `LOG_DB_MAX_ROWS` / `LOG_DB_CONNECT_TIMEOUT` / `LOG_DB_BUSY_TIMEOUT` | n/a (stale post-PG migration) | Comment in `config.py:79-82` gia' lo segnala stale; rimozione safe quando `.env` operativi non li valorizzano piu'. |

## Rationale

- **L'invariant section 1.5 fissa il principio ma non e' stato sufficiente
  a tenere ordinata la pratica**: 17 chiavi sono state introdotte come
  direct read pur avendo natura di policy/tuning. La classificazione
  esplicita rimuove l'ambiguita' sul bucket "feature flag locale" che
  oggi si confonde con OPERATIONAL_TUNING e APP_POLICY (es.
  `LOGIN_MAX_ATTEMPTS` non e' un flag, e' policy).
- **Settings come typed boundary** e' Python-idiomatic e gia' presente
  nel codice. Non introduciamo un nuovo loader (es. pydantic-settings)
  in Phase 2: il costo di refactor non e' giustificato finche' la
  classificazione + dedup non e' completa. Phase 3+ rivaluta.
- **`runtime_policy.yaml` non viene introdotto in Phase 2**: serve un
  loader tipato + strategia diff/audit + commit pipeline. Senza, un YAML
  libero diventa un secondo `os.getenv` mascherato. Il pattern proposto
  (Phase 3+) e' un loader con dataclass-based schema + fallback a env
  override per ops emergency.
- **DB-level config (per-tenant)** resta fuori scope: e' un altro problema
  (ACL, multi-tenant, write-through cache). Mescolarlo a questa decisione
  bloccherebbe il progresso senza beneficio.
- **Removal criteria espliciti** chiudono il loop "alias rimangono per
  sempre". Se non c'e' criterio, l'alias non si rimuove mai.

## Alternatives considered

- **pydantic-settings come boundary tipato**: rejected per Phase 2.
  Soluzione tecnicamente migliore ma cambia tipo del singleton `settings`
  rompendo decine di consumer (`settings.get(...)`, `settings.PROPERTY`).
  Refactor multi-PR; non e' lo scope di `BE-CONFIG-001`. Riconsiderare in
  Phase 3 quando si introduce il loader YAML.
- **Tutto in YAML, env solo per secret**: rejected. Operatori ops gia'
  tunano via `.env`/systemd EnvironmentFile; rimuovere quel canale rompe
  deploy esistenti. Il YAML e' un'aggiunta, non una sostituzione.
- **Tutto in env, niente Settings**: rejected. Perderebbe type coercion,
  default centralizzati e `print_config` per debug. Settings e' un valore
  netto positivo.
- **Mantenere classificazione informale (status quo)**: rejected. L'audit
  ha dimostrato che senza regola scritta il drift continua. 17 direct read
  in policy/tuning sono il sintomo.

## Consequences

### Positive

- Ogni env key ha un bucket. Audit di security/compliance puo' filtrare per
  bucket (SECRET_INFRA per scan secret, DEPLOY_INFRA per audit per-host).
- 17 chiavi OPERATIONAL_TUNING / APP_POLICY direct-read sono candidate
  naturali per `BE-CONFIG-001B/C/D` (migrate meccanico bit-per-bit a
  `Settings.DEFAULTS`).
- Removal criteria per LEGACY_COMPAT chiudono i loop aperti
  (`BACKUP_S3_BUCKET`, `URBY_*` alias).
- Un nuovo developer ha una regola operativa per "dove metto questo env":
  leggi il bucket, applica la regola.

### Negative

- Ridondanza in Phase 2: lo stesso valore puo' essere letto via
  `settings.get(key)` + `os.getenv(key)` durante la migrazione (i batch
  B/C/D la chiudono). Documentato nei commit message.
- L'enforcement della regola e' code-review based; nessun test statico
  blocca l'introduzione di un nuovo `os.getenv` non motivato. Phase 3 puo'
  aggiungere un linter/ruff rule (`no-direct-os-getenv-outside-allowlist`).

### Neutral

- `.env.example` resta autoritativo per ops. Le chiavi
  APP_POLICY/OPERATIONAL_TUNING che non sono "operatore deve toccarle"
  (es. tutte le `INTERMEDIATE_*`) restano implicit-only: documentate in
  `Settings.DEFAULTS` + commenti, ma non esposte in `.env.example`. Un
  blocco di commento nel template rinvia all'ADR.
- `invariants.md` section 1.5 resta il vincolo di codice (no nuovi
  `os.getenv` senza giustificazione); questo ADR e' la classificazione,
  non un nuovo vincolo.

## References

- Audit: `.agent-temp/BE.md` `BE-CONFIG-001` (audit Phase 2 temporary,
  contenuti consolidati qui per la parte stabile).
- Settings entry point: `backend/app/config.py:64-690`.
- Env template: `backend/.env.example`.
- Invariants: `docs/architecture/invariants.md` section 1.5 (principio
  config loading), section 3 (registro feature flag), section 2 (registro
  fallback).
- Related ADR: `ADR-0011` (file_sources S3 threshold), `ADR-0014` (log
  archive pipeline), `ADR-0015` (MinIO provider).
- Related residuals (in `docs/product/roadmap.md` / `residual-register.md`):
  none - questa decisione consolida la pratica gia' osservata.
