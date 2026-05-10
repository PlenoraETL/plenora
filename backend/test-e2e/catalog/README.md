# Catalogo test E2E — strutturato YAML

Questa cartella contiene la **fonte di verità** del catalogo dei test
end-to-end di Plenora. I file `<area>.yaml` sono machine-readable; il
catalogo prosa `test-e2e/test-e2e.md` viene **rigenerato** da qui via
`render.py`.

## Layout

```
test-e2e/catalog/
├── _schema.yaml          # template canonico di un test (riferimento)
├── _cross_cutting.yaml   # policy cross-cutting (audit/REQID/PII/...)
├── _yaml_lite.py         # parser YAML interno (vedi sotto)
├── README.md             # questo file
├── render.py             # YAML → test-e2e/test-e2e.md
├── query.py              # CLI per filtrare/listare test
├── prep.yaml             # area PREP — Preparatori (23 test)
├── api.yaml              # area API — CRUD endpoint (15 test)
├── ext.yaml              # area EXT — Estrai (30 test)
├── ...                   # ~52 file area in totale, 364 test
```

I file con prefisso `_` (`_schema.yaml`, `_cross_cutting.yaml`,
`_yaml_lite.py`) **non** sono caricati come catalogo da `render.py` né
da `query.py`. Servono come riferimento o supporto.

### Perché un parser YAML interno?

`_yaml_lite.py` implementa un sotto-insieme di YAML sufficiente al
nostro dialetto (mappe/sequenze/scalari quoted/literal blocks). PyYAML
6.0.3 + Python 3.14 mostra un bug interpreter sporadico (corruzione di
variabili locali in `yaml/scanner.py`) che causa fallimenti random.
Il parser interno è zero-dependency e deterministico per la nostra
forma fissa di emissione.

## Schema test

Ogni file area ha forma:

```yaml
area: <slug-lowercase>            # es. "prep", "ext", "intm"
description: <una riga libera>
tests:
  - id: PREP-004
    status: TODO                  # TODO | WIP | DONE | SKIP
    title: Crea connection PostgreSQL
    granularity: smoke            # smoke | integration | scenario
    priority: must                # must | nice-to-have
    tags: [setup, postgres]
    prereq:
      profile: core               # core | db | db-heavy | files | cloud | full
      tests: [PREP-003]           # ID di test che devono essere DONE prima
    references:
      - app/core/connections/router.py:42
    dataset: sql/postgres/01_seed.sql   # opzionale
    steps: |
      1. POST /api/connections con type=postgres, host=plenora-e2e-postgres
      2. Verifica response 201
    assertion: |
      201 + connection_id presente. Password cifrata at-rest.
```

Vedi `_schema.yaml` per il template completo con tutti i campi.

### Campi richiesti

- `id` — formato `<PREFIX>-NNN` (numerico zero-padded a 3 cifre)
- `status` — uno di `TODO`, `WIP`, `DONE`, `SKIP`
- `title` — short, in italiano coerente con il legacy
- `assertion` — cosa il test verifica end-to-end

### Campi opzionali

- `granularity` — default `smoke` per single-action, `integration` per
  multi-step, `scenario` per FLOW-*
- `priority` — uno di `must | nice | optional | meta`. Default
  raccomandato: `nice`. `must` riservato a smoke critici / gate merge
  (PREP-*, AUTH-001, SEC-001, INF-001..007/013, ecc.). `meta` usato dai
  test `status: META` (cross-cutting policy, non eseguibili autonomi).
- `tags` — derivati dall'area + keyword del titolo
- `prereq` — `{profile, tests}`
- `references` — paths nel codice produzione (con o senza linea)
- `dataset` — riferimento a fixture (`sql/`, `files/`, `fake-rest/`)
- `steps` — passi numerati, multi-line ammesso

## Workflow

### Aggiungere o modificare un test

1. Edita il file `<area>.yaml` corrispondente.
2. Verifica che il YAML sia valido:

   ```fish
   python3 -c "import yaml; yaml.safe_load(open('test-e2e/catalog/prep.yaml'))"
   ```

3. Rigenera il markdown:

   ```fish
   python3 test-e2e/catalog/render.py
   ```

4. Commit di **entrambi** YAML modificato + `test-e2e/test-e2e.md`
   rigenerato.

### Aggiungere una nuova area

1. Crea `test-e2e/catalog/<slug>.yaml` con almeno:

   ```yaml
   area: <slug>
   description: <una riga>
   tests: []
   ```

2. Aggiungi lo slug a `AREA_ORDER` in `render.py` per posizionarlo nel
   markdown finale (altrimenti finisce in coda alphabetical).
3. Aggiungi tag impliciti (se servono) in `migrate_legacy.py` non è più
   usato, ma se vuoi tagging automatico per la nuova area, gestiscilo a
   mano nei singoli test.
4. Esegui `render.py`.

### Marcare un test come DONE

Modifica `status: TODO` → `status: DONE` nel YAML, poi `render.py`. Le
statistiche in testa al markdown si aggiornano automaticamente.

## Query CLI

```fish
# Tutti i must da fare per primi
python3 test-e2e/catalog/query.py --status TODO --priority must

# Lista test di un'area, full JSON
python3 test-e2e/catalog/query.py --area prep --json

# Test postgres già completati
python3 test-e2e/catalog/query.py --tag postgres --status DONE

# Tutti gli scenari (FLOW-*)
python3 test-e2e/catalog/query.py --granularity scenario

# Quanti test must rimangono
python3 test-e2e/catalog/query.py --priority must --status TODO --count-only

# Filtro su prefix arbitrario
python3 test-e2e/catalog/query.py --id-prefix EXT-

# JSON full-record per pipeline esterne
python3 test-e2e/catalog/query.py --status DONE --json | jq '.[].id'
```

Filtri combinabili (AND): `--status`, `--area`, `--priority`,
`--granularity`, `--tag`, `--id-prefix`. Output: una riga per ID, o JSON
con `--json`, o solo conteggio con `--count-only`.

## Convenzioni di nomenclatura

- **Area slug**: lowercase, mappato 1:1 al prefisso ID (`PREP-NNN` →
  `prep.yaml`). Eccezione: `FILE-PREV-NNN` e `FILE-MULTI-NNN` vivono in
  `files.yaml`.
- **ID**: sempre `<PREFIX>-<NNN>` con NNN zero-padded a 3 cifre. Per ID
  legacy senza padding (es. `EXT-1`), allargare a `EXT-001`.
- **Tags**: lowercase, kebab-case (`rate-limit`, non `RateLimit`).
- **Status**: maiuscolo (`TODO`, non `todo`).
- **Priority/granularity**: lowercase con eventuali trattini.

## Validazione (lint)

Per verificare che tutto il catalogo sia valido:

```fish
python3 test-e2e/catalog/render.py --check
```

Output atteso: `Loaded N areas, M tests.` con `exit 0`. Errori di parsing
YAML emergono qui prima del commit.

## File legacy

`test-e2e/test-e2e_LEGACY.md` è il monolite originale (~364 test in
prosa) preservato come backup pre-migrazione. **Non modificare**: ogni
modifica va fatta nei YAML.

## Distribuzione priorità (auto-aggiornata da render.py)

I valori sotto rispecchiano la classificazione `priority` corrente nel
catalogo. La tabella viene anche emessa in cima a
`test-e2e/test-e2e.md` ad ogni `render.py`.

| priority | count |     % |
|----------|------:|------:|
| must     |    50 | 13.5% |
| nice     |   227 | 61.4% |
| optional |    79 | 21.4% |
| meta     |    14 |  3.8% |
| total    |   370 | 100.0% |

Criteri di classificazione:

- **must** — gate merge: tutti i `PREP-*`, smoke happy-path per area
  (`EXT-001`, `MAN-002`, `ENR-001`, `LOAD-001`), security gates
  (`SEC-001`, `SEC-005`, `SSRF-001..003`), infra healthcheck
  (`INF-001..007`, `INF-013`), API contract base (`API-001/002/009/014`),
  scheduler core (`SCH-001`, `BEAT-003`), idempotenza delete
  (`IDEM-005`), auth (`AUTH-001/004`), happy-path completo (`FLOW-001`).
- **nice** — CI nightly: cross-dialect non-postgres, formati file,
  paginazione/auth styles, edge dataset, permission matrix, ESEG/TKT,
  ArcGIS, ADM/BCK/RECON/RET, contract tests (CT-*), webhook (HOOK-*).
- **optional** — edge case secondari: PERF-*, SIZE-*, CONC-*, MIG-*,
  SYS-*, LOC-*, MFA non-001, TENANT non-001, FLOW-002..005, SP error
  paths (SP-002..006), SFTP non-001, CLOUD-002+, CORS non-meta, LOG
  non-meta, dialetti DB heavy duplicati (Oracle, MySQL spaziale).
- **meta** — i 14 test `status: META` (cross-cutting policy autouse),
  non eseguibili autonomi.
