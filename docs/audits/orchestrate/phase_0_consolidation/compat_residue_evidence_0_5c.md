# Phase 0.5c — COMPAT-RESIDUE Evidence

**Data:** 2026-04-28
**Stato:** **PENDING DB EVIDENCE** — analisi statica completata, query SQL
preparate; esecuzione contro DB produzione richiesta dall'operatore via
`psql` read-only.
**Vincoli rispettati:** read-only, nessun codice modificato, nessuna query
eseguita da questo passaggio, nessun micro-batch REMOVE aperto, nessun
test applicativo lanciato.

Phase di riferimento: `phase_0_consolidation/phase_0_4_test_coupling_evidence.md`
§3 (evidence plan E1-E5 originale, pre-storage). Questo documento aggiorna
l'evidence plan dopo i batch object storage / intermediate persistence /
A0 doc closure (`c426685`).

---

## 1. Sintesi call site `[COMPAT-RESIDUE]`

Grep `[COMPAT-RESIDUE]` su `backend/app/`:

| Categoria | Count | File |
|---|---|---|
| **Emit reale** (`logger.warning("[COMPAT-RESIDUE]…")`) | **2** | `api_connection_source_loading.py:340` (E2), `manipola/executor.py:417` (E1) |
| Riferimento docstring/commento | 6 | docstring/comment che cita il marker, non genera log |

Solo **E1** ed **E2** sono direttamente osservabili da `plenora.logs` via
PostgresLogHandler.

I marker docstring (CR1-CR6 in tabella sotto) non hanno azione separata:
si rimuovono naturalmente quando i 2 emit reali (E1/E2) vengono rimossi.

---

## 2. Tabella evidence — E1-E5 + A1/A4 + CR1-CR6

### 2.1 Sintesi compatta

| ID | File:line | Stato | Strategy |
|---|---|---|---|
| E1 | `manipola/executor.py:417` | path probabile dead post-Batch storage, ma non observable senza Q1 | DEFER + Q1 (30→60gg) |
| E2 | `api_connection_source_loading.py:340` | symmetric a E1 | DEFER + Q2 |
| E3 | `manipola/plugin_executor.py:540` (`PluginExecutorCompatibility` class) | dead by static analysis, branch unreachable | REMOVE-candidate, **non rimuovere ora** |
| E4a | `enrichment_service.py:263` | branch raggiungibile, decisione su Q3 | DEFER + Q3 (DB metadata) |
| E4b | `enrichment_validation_preview.py:154` | mirror di E4a | DEFER + Q3 |
| E5 | `auth/auth.py:299` | branch attivo se `bcrypt_users>0` | DEFER + Q4 |
| A1 | (consolidato di E3/E4a/E4b) | n/a | covered |
| A4 | `api_connection_rest_file.py:1092` | dead by static analysis (3 return point tutti `False`) | REMOVE-candidate, **non rimuovere ora** |
| CR1 | `api_connection_source_loading.py:46` | docstring header | rimosso con E2 |
| CR2 | `api_connection_source_loading.py:170` | docstring | rimosso con E2 |
| CR3 | `api_connection_source_loading.py:184` | docstring | rimosso con E2 |
| CR4 | `api_connection_source_loading.py:189` | docstring | rimosso con E2 |
| CR5 | `api_connection_source_loading.py:328` | docstring `_filesystem_fallback_load_project` | rimosso con E2 |
| CR6 | `api_connection_rest_file.py:250` | docstring | rimosso con E2 |

### 2.2 Dettaglio evidence per candidato

| ID | Trigger | Osservabilita' | Static analysis | Probe |
|---|---|---|---|---|
| **E1** | `materialized is None` E esiste file legacy in `results_base_dir/<source_project_id>` | ✅ direct log emit -> `plenora.logs` (level WARNING persistito) | Path raggiungibile per source pre-R#1 senza blob DB. Post-Batch storage 6/6 target sono regenerated → probabilmente dead in prod, ma canvas legacy non target potrebbero ancora colpirlo | Q1 |
| **E2** | last-resort tentativo 3 in `_load_source_project`: `materialized is None` → fallback filesystem | ✅ direct log emit | Symmetric a E1 lato arricchisci | Q2 |
| **E3** | n/a — riferimento "Fallback al vecchio sistema..." e' commento dentro la docstring della classe `PluginExecutorCompatibility` | ❌ NON osservabile (codice morto, non genera log) | `PluginExecutorCompatibility.dispatch` mai chiamata in `app/`. `manipola/executor.py:31` importa solo `get_executor`. Caller-zero verificato static su `app/` | grep esteso pre-REMOVE su `frontend/`, `tests2/`, `workers/`, `scripts/` |
| **E4a** | branch attivo se `params_def = api_config.get("parameters")` e' empty/missing → `_required_param_names = set()`, `_has_param_definitions = False` | ❌ silent branch, no log | Branch raggiungibile per costruzione difensiva. Decisione dipende dai dati reali: se `with_at_least_one_required = 0` → branch "non-legacy" e' dead | Q3 (DB metadata) |
| **E4b** | mirror di E4a: `_is_blocking` ritorna `True` se `_has_param_definitions == False` | ❌ silent branch | mirror di E4a | Q3 |
| **E5** | rehash progressivo da bcrypt a Argon2id al login successful | ❌ rehash non logga | branch attivo se ci sono utenti bcrypt; KEEP comment se >0, REMOVE se 0 | Q4 |
| **A4** | n/a — `_resolve_input_path_for_file_processor` ha 3 return point tutti `(_, False)`. Riga 832 (`if _owns_materialized_input and ...`) e' difensive code unreachable | ❌ branch strutturalmente irraggiungibile, niente da loggare | grep esaustivo `owns_disk_file = True \| owns = True \| owns_disk_file=True` su `backend/app/`: **0 hit**. Il `True` e' literal-mente assente. | grep esteso pre-REMOVE su `tests2/` per asserzioni shape `(_, True)` |

---

## 3. Query SQL — testo esatto pronto per esecuzione

**NB:** tutte read-only, da eseguire con utente DB con `SELECT` su
`plenora.logs`, `plenora.api_connections`, `plenora.users`. **NON via SSH**
al server: usare client `psql` diretto (vedi `runtime-prereqs.md` per
endpoint produzione).

### 3.1 Q1 — E1 telemetry (manipola filesystem fallback)

```sql
-- Q1.a: count + range temporale del marker E1 negli ultimi 30gg
SELECT
    COUNT(*)                       AS hit_count,
    MIN(created_at)                AS first_hit,
    MAX(created_at)                AS last_hit,
    COUNT(DISTINCT details->>'request_id') AS distinct_requests
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto%'
  AND created_at > NOW() - INTERVAL '30 days';

-- Q1.b: top source_project_id se vivo (per identificare canvas legacy specifici)
SELECT
    SUBSTRING(message FROM 'source_project=([^ ]+)') AS source_project_id,
    COUNT(*) AS hits,
    MAX(created_at) AS last_hit
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto%'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY 1
ORDER BY hits DESC
LIMIT 10;
```

**Decisione binaria:**
- `hit_count = 0` su 30gg → estendere finestra a 60gg → se sempre 0 → **REMOVE**.
- `hit_count > 0` → identificare i `source_project_id` colpevoli, decidere
  se riallinearli al blob DB o lasciare il fallback.

### 3.2 Q2 — E2 telemetry (api_connector filesystem fallback)

```sql
-- Q2.a: simmetrica a Q1 per E2
SELECT
    COUNT(*)        AS hit_count,
    MIN(created_at) AS first_hit,
    MAX(created_at) AS last_hit
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project%'
  AND created_at > NOW() - INTERVAL '30 days';

-- Q2.b: top project_id colpevoli
SELECT
    SUBSTRING(message FROM 'project_id=([^ ]+)') AS project_id,
    COUNT(*) AS hits,
    MAX(created_at) AS last_hit
FROM plenora.logs
WHERE message LIKE '%[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project%'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY 1
ORDER BY hits DESC
LIMIT 10;
```

### 3.3 Q3 — E4a/E4b (legacy required parameter handling)

```sql
-- Q3.a: shape distribution per `auth_config -> parameters`.
-- auth_config e' JSONB; "parameters" puo' essere a livello root o sotto endpoint.
SELECT
    COUNT(*)                                              AS total,
    COUNT(*) FILTER (
        WHERE jsonb_typeof(auth_config::jsonb -> 'parameters') = 'array'
          AND jsonb_array_length(auth_config::jsonb -> 'parameters') > 0
    )                                                      AS with_parameters_root,
    COUNT(*) FILTER (
        WHERE jsonb_typeof(auth_config::jsonb -> 'parameters') = 'array'
          AND jsonb_array_length(auth_config::jsonb -> 'parameters') = 0
    )                                                      AS empty_array_root,
    COUNT(*) FILTER (
        WHERE NOT (auth_config::jsonb ? 'parameters')
    )                                                      AS missing_root,
    COUNT(*) FILTER (
        WHERE auth_config::jsonb -> 'parameters' @> '[{"required": true}]'
    )                                                      AS with_at_least_one_required
FROM plenora.api_connections;

-- Q3.b: per quelle "with parameters", quante hanno almeno un required esplicito?
-- Se nessuna ha required esplicito, il branch "non-legacy" non si attiva mai
-- e i 2 commenti "legacy" sono fuorvianti (il branch "legacy" e' di fatto
-- l'unico path attivo).
SELECT id, name,
       jsonb_array_length(auth_config::jsonb -> 'parameters') AS n_params,
       (auth_config::jsonb -> 'parameters') @> '[{"required": true}]' AS has_any_required
FROM plenora.api_connections
WHERE jsonb_typeof(auth_config::jsonb -> 'parameters') = 'array'
  AND jsonb_array_length(auth_config::jsonb -> 'parameters') > 0
ORDER BY name
LIMIT 20;

-- Q3.c (sanity): sample shape `auth_config` su 3 connection per validare
-- l'assunzione "parameters" sta a livello root vs sub-key endpoint/endpoints[].
SELECT id, name, auth_config
FROM plenora.api_connections
LIMIT 3;
```

**Decisione:**
- `with_at_least_one_required = 0` → branch "non-legacy" e' dead → entrambi
  i commenti E4a/E4b sono obsoleti → semplificabile.
- `> 0` → i 2 branch coesistono → riscrivere il commento per non dire
  "legacy" (e' design corretto, non residuo).

### 3.4 Q4 — E5 (legacy bcrypt)

```sql
-- Q4.a: distribuzione formati password_hash users
SELECT
    COUNT(*) FILTER (WHERE password_hash LIKE '$2b$%')   AS bcrypt_2b,
    COUNT(*) FILTER (WHERE password_hash LIKE '$2a$%')   AS bcrypt_2a,
    COUNT(*) FILTER (WHERE password_hash LIKE '$2y$%')   AS bcrypt_2y,
    COUNT(*) FILTER (WHERE password_hash LIKE '$argon2%') AS argon2,
    COUNT(*) FILTER (
        WHERE password_hash NOT LIKE '$%' OR password_hash IS NULL
    )                                                     AS other_or_null,
    COUNT(*)                                              AS total
FROM plenora.users;

-- Q4.b: ultimo login per utenti bcrypt (sappiamo se sono attivi o dormienti)
SELECT id, username,
       SUBSTRING(password_hash FROM 1 FOR 4) AS hash_prefix,
       last_login,
       NOW() - last_login                  AS days_since_login
FROM plenora.users
WHERE password_hash LIKE '$2%'
ORDER BY last_login DESC NULLS LAST
LIMIT 30;
```

**Decisione:**
- `bcrypt_users = 0` → commento `auth.py:299` obsoleto → semplificabile +
  rehash branch rimovibile.
- `bcrypt_users > 0` → commento serve come hint del rehash progressivo →
  **KEEP**.
- Q4.b aggiunge contesto: se gli unici bcrypt sono dormienti
  (last_login > N mesi) si puo' valutare un force-rehash al prossimo
  accesso.

---

## 4. Classificazione finale per categoria

### 4.1 DEFER + telemetry window (richiedono finestra 30-60gg di osservazione)

| ID | Reason | Window | Decision after window |
|---|---|---|---|
| E1 | path probabile dead post-Batch storage; richiede log evidence per certezza | 30gg first, 60gg second | REMOVE se 0 hit; KEEP+root-cause altrimenti |
| E2 | symmetric a E1 | 30gg first, 60gg second | stesso pattern di E1 |

**Vincolo:** la finestra dipende da `LOG_RETENTION_DAYS` (default 30) e/o
da `LOG_ARCHIVE_ENABLED` per finestre piu' lunghe. Da verificare prima di
pianificare 60gg.

### 4.2 REMOVE-candidate static (dead by analysis, **non rimuovere ora**)

| ID | Reason | Verification gate before REMOVE |
|---|---|---|
| E3 | `PluginExecutorCompatibility.dispatch` mai chiamata in `app/`. Caller-zero verified static. | grep esteso `PluginExecutorCompatibility` su `frontend/`, `tests2/`, `workers/`, `scripts/`. Se anche li' caller-zero → REMOVE in micro-batch dedicato. |
| A4 | `_resolve_input_path_for_file_processor` ha 3 return point tutti `(_, False)`. Branch `True` strutturalmente irraggiungibile. | grep esteso `tests2/` per asserzioni shape `(_, True)` su quel return tuple. Se nessuna assertion → REMOVE difensivo guard riga 832 + docstring update. |

**Vincolo:** non rimuovere senza eseguire i 2 grep esterni e senza
pianificare il micro-batch dedicato. Phase 0.5c **non apre** il micro-batch.

### 4.3 DEFER + DB metadata queries (decisione immediata su Q3/Q4, non finestra)

| ID | Reason | Query | Decision after query |
|---|---|---|---|
| E4a/E4b | branch raggiungibile, decisione binaria su Q3 | Q3 | REMOVE branch non-legacy se `with_at_least_one_required = 0`; altrimenti riscrivi il commento e KEEP |
| E5 | branch attivo se `bcrypt_users > 0` | Q4 | KEEP commento + branch se >0; REMOVE se 0 |

---

## 5. Rischi delle query

| Q | Rischio | Mitigazione |
|---|---|---|
| Q1, Q2 | dipendono da retention `plenora.logs`. `LOG_RETENTION_DAYS=30` default → finestra max 30gg. Per 60gg serve estendere retention temporaneamente OR usare archive log (ADR-0014). | Verificare `LOG_ARCHIVE_ENABLED` + retention settings prima di pianificare la seconda finestra. |
| Q1.b, Q2.b | parsing `SUBSTRING (... FROM 'source_project=([^ ]+)')` assume formato esatto del messaggio. Se il messaggio cambia, regex non matcha. | Verificare formato attuale messaggio con `SELECT message FROM plenora.logs WHERE message LIKE '%COMPAT-RESIDUE%' LIMIT 3` prima. |
| Q3 | assume schema `auth_config -> parameters` a livello root. Se la shape e' nested (`endpoint.parameters`, `endpoints[].parameters`) la query miss-classifica. | Eseguire **Q3.c** prima per validare la shape su sample reali; aggiustare regex se necessario. |
| Q4 | copre solo prefix algoritmo (`$2b$`, `$argon2`). Hash custom (raro) finiscono in `other_or_null` → potenziale falso negativo se classificati come bcrypt manualmente. | Se `other_or_null > 0` ispezionare il contenuto manualmente. |
| All | utente DB richiede `SELECT` su `plenora.{logs, api_connections, users}`. | Usare ruolo DB read-only dedicato (no production credentials con grant write). |

---

## 6. Effort + ordering

| Step | Cosa | Chi | Effort | Pre-requisito |
|---|---|---|---|---|
| 1 | Verificare `LOG_RETENTION_DAYS` e `LOG_ARCHIVE_ENABLED` su prod | operatore | 5 min | accesso DB / .env prod |
| 2 | Eseguire **Q3.c** (sample auth_config shape) per validare assunzione root | operatore | 5 min | accesso DB |
| 3 | Eseguire **Q1, Q2, Q3.a/b, Q4.a/b** (4 query, 5 sub-query) contro DB prod | operatore | 20 min | accesso DB read-only |
| 4 | Incollare output in questo doc come §7 "Evidence Output" | review | 5 min | output Q1-Q4 |
| 5 | Interpretare + classificare ogni candidato (keep/remove/instrument) | review | 15 min | output disponibile |
| 6 | grep esteso `PluginExecutorCompatibility` fuori `app/` (E3 verification gate) | review | 5 min | n/a |
| 7 | grep esteso `(_, True)` shape in `tests2/` (A4 verification gate) | review | 5 min | n/a |
| 8 | Decidere se aprire micro-batch REMOVE per E3 + A4 (separati o unico commit doc+code) | review | decisione | step 6+7 done |
| 9 | Pianificare seconda finestra 60gg per E1/E2 se primo passaggio dice 0 hit | operatore | calendar entry | log retention check |

**Effort totale per chiusura completa di 0.5c (esclusa la finestra
60gg per E1/E2):** ~1 ora.

---

## 7. Evidence Output

**Status:** PENDING — query non ancora eseguite.

Quando l'operatore esegue le query, l'output va incollato qui sotto in
forma di tabella per ognuna delle 4 query, con timestamp + ambiente
(prod/staging) + utente DB usato.

```text
Q1.a output:
   hit_count | first_hit | last_hit | distinct_requests
   [PENDING]

Q1.b top 10:
   [PENDING]

Q2.a output:
   [PENDING]

Q2.b top 10:
   [PENDING]

Q3.c sample shape:
   [PENDING]

Q3.a totals:
   total | with_parameters_root | empty_array_root | missing_root | with_at_least_one_required
   [PENDING]

Q3.b detail:
   [PENDING]

Q4.a distribution:
   bcrypt_2b | bcrypt_2a | bcrypt_2y | argon2 | other_or_null | total
   [PENDING]

Q4.b last login per bcrypt user:
   [PENDING]
```

---

## 8. Decision matrix dopo evidence

Una volta popolata §7:

| Candidato | Output → Decisione |
|---|---|
| E1 | `Q1.a.hit_count = 0` → DEFER 60gg → REMOVE; `> 0` → identificare canvas + decidere migrazione |
| E2 | identico a E1 con Q2 |
| E3 | static = dead; se grep esteso (step 6) conferma 0 caller fuori `app/` → REMOVE-batch ammesso |
| E4a/b | `Q3.a.with_at_least_one_required = 0` → REMOVE non-legacy branch + comment update; `> 0` → KEEP + comment riscrittura |
| E5 | `Q4.a.bcrypt_users = 0` → REMOVE rehash branch + comment update; `> 0` → KEEP |
| A4 | static = dead; se grep esteso (step 7) conferma 0 assertion shape `(_, True)` in `tests2/` → REMOVE-batch ammesso |
| CR1-6 | rimossi naturalmente quando E1/E2 chiusi (no decisione separata) |

---

## 9. Stato e prossima azione

**Stato attuale:** PENDING DB EVIDENCE.

**Bloccato su:**
- esecuzione manuale Q1-Q4 da parte dell'operatore con `psql` read-only.
- 2 grep esterni (step 6+7) per E3 e A4 prima di considerarli REMOVE-ready.

**Sblocco previsto:** ~1h post-execution + interpretazione.

**Phase 0.5 next:** in attesa della review di questo doc, transizione a
**0.5e — deploy/ops migration design** come da brief.

**Vincoli rispettati per questo deliverable:**
- nessun codice modificato ✓
- E3/A4 non rimossi ✓
- query non eseguite ✓
- nessun micro-batch aperto ✓
- solo file markdown in `orchestrate/` ✓
