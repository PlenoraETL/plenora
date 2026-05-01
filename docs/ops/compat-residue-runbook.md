# Compat residue — Runbook esecuzione Q1-Q4

> Status: **RUNBOOK READY**, esecuzione **post-deadline manuale** in
> sessione DB autorizzata. Questo doc e' il punto di entrata operativo;
> la fonte autorevole per il contenuto delle query resta
> `orchestrate/phase_0_consolidation/compat_residue_evidence_0_5c.md §3`.

## 1. Sintesi

Le 4 query `Q1`-`Q4` raccolgono telemetry e DB metadata necessari per
decidere REMOVE o KEEP sui marker `[COMPAT-RESIDUE]` ancora presenti
nel codice. Sono **gia' documentate** in
`orchestrate/phase_0_consolidation/compat_residue_evidence_0_5c.md §3.1-3.4`
con il testo SQL completo.

Questo runbook **non duplica** le query (rischio drift). Tiene solo:
- precondizioni operatore
- safety guards di sessione
- procedura sicura step-by-step
- decision matrix per i risultati
- vincoli operativi assoluti

## 2. Pre-requisiti operatore

- **Client locale**: `psql` o DBeaver
- **NON via SSH** al server (regola in `runbook.md`): connessione
  diretta dal laptop dell'operatore tramite VPN o port-forward
  (vedi `runtime-prereqs.md` per endpoint produzione)
- **PG user con grant SELECT** su almeno:
  - `plenora.logs` (per Q1, Q2)
  - `plenora.api_connections` (per Q3)
  - `plenora.users` (per Q4)
- **Autorizzazione esplicita** del bus_factor_1 / sysadmin prima della
  sessione (annotare timestamp + chi esegue in `dr_drill_log.md`)

## 3. Safety guards (mandatory)

Prima di eseguire qualsiasi query, settare i seguenti guard nella
sessione corrente:

```sql
SET default_transaction_read_only = on;   -- aborti automatici scritture
SET statement_timeout = '60s';            -- aborti su query lunghe
SET lock_timeout = '5s';                  -- evita lock contention
```

Se uno qualsiasi di questi `SET` fallisce, **interrompere la sessione**
e diagnosticare prima di proseguire.

## 4. Procedura step-by-step

### 4.1 Connessione

```sh
# Sostituire <PG_HOST> e credenziali con valori del vault.
# NON committare credenziali. NON copiare in chat / canali condivisi.
psql -h <PG_HOST> -p <PG_PORT> -U <READONLY_USER> -d <DB_NAME>
```

### 4.2 Set guards

Incollare il blocco di Safety Guards (sezione 3 di questo doc) come
prima azione.

### 4.3 Esecuzione queries

Per ognuna delle 4 query (Q1, Q2, Q3, Q4):

1. Aprire la sezione corrispondente in
   `orchestrate/phase_0_consolidation/compat_residue_evidence_0_5c.md §3`
2. Copia-incollare il SQL dalla sezione `### 3.X` (testo SQL autorevole)
3. Eseguire
4. Salvare l'output **localmente** come `q<N>_run_<YYYY-MM-DD>.txt` o
   `.csv` — NON committare nel repo

### 4.4 Verbalizzazione

Creare nuova nota `compat_residue_run_<YYYY-MM-DD>.md` in
`orchestrate/` (workspace-level, **NON in `docs/`**) con:
- timestamp + operatore
- output sintetico per ogni query (count, decisione)
- eventuali progetti / utenti specifici emersi (anonimizzare se
  l'output viene condiviso oltre il team ops)

## 5. Decision matrix REMOVE/KEEP

| Query | Threshold | Decisione | Target da modificare (vedi evidence doc) |
|---|---|---|---|
| Q1 hit_count = 0 (30→60gg) | REMOVE marker E1 | manipola executor (filesystem fallback) |
| Q1 hit_count > 0 | KEEP + identificare canvas legacy | come sopra |
| Q2 hit_count = 0 (30→60gg) | REMOVE marker E2 | api_connector (filesystem fallback) |
| Q2 hit_count > 0 | KEEP + identificare project_id | come sopra |
| Q3 with_at_least_one_required = 0 | REMOVE branch "non-legacy" | enrichment service + validation preview |
| Q3 with_at_least_one_required > 0 | KEEP + riscrivere commento (non e' "legacy", e' design) | come sopra |
| Q4 bcrypt_users = 0 | REMOVE comment + rehash branch | auth |
| Q4 bcrypt_users > 0 | KEEP commento + branch (rehash progressivo attivo) | come sopra |

> Nota: i path/line precisi sono volutamente **non ripetuti** qui per
> evitare drift; la fonte autorevole e'
> `compat_residue_evidence_0_5c.md §2-§4` che mappa ogni marker al
> file:line corrente al momento del rilievo.

## 6. Sequenza post-execution (futura)

1. Per ogni REMOVE/KEEP decisione: aprire **PR mirata** scope ~30 LOC,
   con test-update se necessario (es. test che asserisce assenza
   marker)
2. Decidere se **4 PR piccole** (1 per marker) o **1 PR consolidata**
   in base a:
   - se i 4 marker toccano file disgiunti → 4 PR piccole
   - se condividono file → 1 PR consolidata
3. Closure issue tracking marker `[COMPAT-RESIDUE]` quando tutti i 4
   sono REMOVE'd o esplicitamente KEEP'd con commento riscritto

## 7. Vincoli operativi assoluti

- ❌ **Mai eseguire queste query da Claude Code** (richiede credenziali
  prod fuori scope agent)
- ❌ **Mai committare output query** nel repo (potrebbero contenere
  project_id reali, username, hash prefix bcrypt)
- ❌ **Mai bypassare i safety guards** in sezione 3
- ❌ **Mai eseguire query in finestra di alto traffico** senza
  comunicazione preventiva al team ops
- ✅ Tutto manualmente riproducibile seguendo questo runbook +
  `compat_residue_evidence_0_5c.md`

## 8. Out-of-scope

- ❌ Esecuzione automatica delle query (CI / cron)
- ❌ Refactor `[COMPAT-RESIDUE]` markers prima della sessione (le
  decisioni REMOVE/KEEP dipendono dai dati reali)
- ❌ Pipeline alerting / dashboard integration delle metriche compat
- ❌ Estensione finestra log retention oltre 30gg solo per questo audit
  (verificare `LOG_RETENTION_DAYS` corrente prima di pianificare 60gg)

## 9. Riferimenti

- **Fonte autorevole queries SQL**:
  `orchestrate/phase_0_consolidation/compat_residue_evidence_0_5c.md §3`
- **Mapping marker → file:line**: `compat_residue_evidence_0_5c.md §2-§4`
- **Endpoint produzione**: `docs/ops/runtime-prereqs.md`
- **Connection patterns + niente SSH al server**:
  `docs/ops/runbook.md`
- **Audit trail sessione**: `docs/ops/dr_drill_log.md`
