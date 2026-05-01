# Ledger - Carry-Over To Validate

Questo ledger contiene ipotesi di lavoro. Ogni voce va verificata nella seconda
run prima di diventare task.

## Architecture / Code Quality

- Retry consolidation:
  - enrichment per-row retry;
  - batch retry;
  - api_connector retry;
  - ArcGIS retry.
- Rate limiting:
  - valutare Redis/distributed limiter;
  - mantenere in-memory fallback se utile;
  - chiarire multi-worker behavior.
- Logger normalization:
  - nomi canonici espliciti;
  - warning/error contracts;
  - telemetry coerente.
- Compatibility wrappers:
  - distinguere back-compat reale da rumore storico.
- Dead code:
  - rimuovere solo con prova forte e test gate.

## Product / Ops

- Product grade target:
  - obiettivo esplicito: portare Plenora almeno a 9/10;
  - la run 0 -> 5 prepara il terreno;
  - `phase_6_product_grade/` decide il go/no-go prodotto.
- Remote/GitHub:
  - repo monorepo pubblicato: `https://github.com/PlenoraETL/plenora.git`;
  - branch `main` e tag `phase-5-complete` pubblicati post-Phase-5;
  - aggiornare deploy/server remote in uno step separato.
- Deploy:
  - verificare `deploy.sh`;
  - server branch/remote;
  - dry-run prima di produzione.

## Phase 5 Follow-Up

- Second pass sui moduli creati in Phase 5.
- Ridurre commenti/cutover diventati rumore.
- Verificare import morti.
- Consolidare duplicati tra `api_utils`, enrichment e api connector.
- Rafforzare test su failure modes.

## Behavior-Bound Test Debt

Da verificare nella nuova run:

- altri test che patchano logger module-level;
- test che ispezionano source fisico invece di comportamento;
- AST scan legati a path di file;
- patch su simboli facade che potrebbero non propagare;
- test che proteggono implementazione storica invece di contratto utente.

Regola: modernizzare prima, refactor dopo.

## Known Carry-Over From Phase 5

- Redis/distributed rate limiter: fuori scope Phase 5, da valutare nella nuova
  run.
- Retry consolidation: per-row, batch, API connector e ArcGIS hanno pattern
  simili ma non ancora unificati.
- Server/deploy remote: GitHub pulito e' pronto; resta da pianificare il
  riallineamento del server e il deploy controllato.
- API connector: da rivalutare dopo B3-B, con particolare attenzione a REST
  file, API generator/enricher, SSRF e source loading.
- Logger naming: uniformare dove il valore supera il rumore.
- Compatibility wrappers: mantenere solo quelli dimostrati necessari.

## Phase 0 Consolidation 2 Additions

Voci aggiunte dalla synthesis Phase 0.0/0.4. Restano ipotesi operative fino
alla fase indicata; non autorizzano refactor automatici.

- Formula eval security boundary: `eval` resta dietro AST whitelist, ma va
  trattato come audit ricorrente.
- Git local warning: `C:\Users\Marco/.config/git/ignore` genera
  `Permission denied`. Fix locale, non codice prodotto.
- Frontend large components: prossima superficie strutturale probabile
  (`ApiConnectionManager`, `ResponseConfigBuilder`, `ParameterInput`,
  `ApiEnrichNode`, `ResultNodeStandardView`, `FileManager`, `ProjectList`).
- `URBY_ALLOW_LOOPBACK_OUTBOUND`: alias retro-compat da deprecare con warning
  one-shot, mantenendo `PLENORA_ALLOW_LOOPBACK_OUTBOUND` come canonical.
- OAuth wrapper legacy in `shared/api_utils.py`: marcare come deprecated e
  mantenere finche' non ci sono zero caller esterni.
- Fallback legacy sospetti in arricchisci/manipola: verificare copertura e
  osservabilita' prima di rimuovere.
- REST_FILE `owns=True` backward-compat path: candidato solo dopo prova di non
  raggiungibilita'.
- `deploy.sh` auto-commit su working tree sporco: pattern rischioso; la
  direzione preferita e' fail-fast + review manuale.

## Phase 0 Closeout Decision

- Phase 0 non apre extraction.
- Phase 1 parte da stabilizzazioni piccole e reversibili:
  1. marker deprecation/warning su alias legacy;
  2. eventuale modernizzazione mirata dei 4 test logger-bound;
  3. design SSRF single-source senza cambio massivo immediato.
- Compat residue e dead code richiedono evidenza log/call graph prima di ogni
  rimozione.
