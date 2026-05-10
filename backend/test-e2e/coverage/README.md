# Plenora E2E - Coverage Matrix

Strumenti per generare e mantenere la **matrice di coverage** dei test
end-to-end. Risponde alla domanda: "tutti i (source x target x auth x
format) sono coperti?".

## Cosa c'e' qui

```
test-e2e/coverage/
|- matrices.py          # definizione delle 5 matrici (rows x cols)
|- coverage_parser.py   # legge il catalogo, classifica i test in celle
|- render.py            # CLI: produce matrix.md / .html / .json
|- check.py             # gate CI sulla soglia di coverage
|- matrix.md            # output ultima rigenerazione (committato)
|- matrix.html          # idem
|- matrix.json          # idem (machine-readable, per badge / CI)
+- README.md            # questo file
```

## Le 5 matrici

| name          | rows                     | cols                      | descrizione |
|---------------|--------------------------|---------------------------|-------------|
| `extraction`  | source (DB, file, REST)  | dataset_block (base/edge/null/volume/streaming) | Per ogni sorgente, quali tipi di dato estraiamo. |
| `load`        | source canvas            | target di scrittura       | DB->DB cross-dialect, DB->file, file->DB, REST sink. |
| `auth`        | protocollo (rest/arcgis/ftp/...) | auth pattern (api_key/bearer/oauth2/...) | Quali coppie protocollo+auth sono testate. |
| `pagination`  | shape della response REST | stile di paginazione     | object/array/nested x offset/cursor/link_header/... |
| `error_paths` | sorgente                 | classe di errore (401/422/timeout/...) | Negative tests: cosa succede quando la sorgente fallisce. |

Le matrici `extraction`, `load`, `auth`, `error_paths` sono **critiche**:
le celle scoperte qui contano nel "Top 5 gap" del summary.

## Come si interpretano le celle

| simbolo (md) | classe (html) | significato |
|--------------|---------------|-------------|
| `[x] N`      | `done`        | almeno N=1 test in stato DONE copre la cella. |
| `[.] N`      | `todo`        | la cella e' catalogata ma nessun test e' DONE (TODO/WIP). |
| `[~] N`      | `skip`        | tutti i test in cella sono SKIP (gap noto, non bug). |
| `[ ] 0`      | `empty`       | nessun test catalogato. |

**Una cella vuota non e' sempre un bug.** Esistono coppie "impossibili"
(es. `ftp x oauth2_cc`) che restano vuote per design. Le elenchiamo
comunque per renderle ispezionabili: se domani la cella diventa
implementabile, saltera' all'occhio.

## Uso

### Rigenerare la matrice

```fish
cd test-e2e
python3 coverage/render.py --format markdown --output coverage/matrix.md
python3 coverage/render.py --format html     --output coverage/matrix.html
python3 coverage/render.py --format json     --output coverage/matrix.json
```

Output deterministico: stesso input -> stesso output (no timestamp,
ordinamento esplicito su righe/colonne/celle/test ID).

### Gate CI

```fish
python3 coverage/check.py --min-coverage 60
python3 coverage/check.py --min-coverage 30 --metric catalogued
```

Exit code:

- `0` -> coverage >= soglia
- `1` -> coverage < soglia (gate fallito: blocca il PR)
- `2` -> errore di parsing del catalogo

Metriche:

- `--metric done`: coverage_pct = celle con >=1 test DONE.
- `--metric catalogued`: catalogued_pct = celle con >=1 test (anche TODO/WIP).

Quest'ultima e' utile in fase di set-up (la maggioranza dei test e' TODO):
controlla che almeno **abbiamo pensato** a quella combinazione.

## Sorgente del catalogo

Il parser legge in ordine:

1. **YAML canonico** (`test-e2e/catalog/*.yaml`, escluso `_schema.yaml`)
   se presente. Schema in `test-e2e/catalog/_schema.yaml`.
2. **Markdown legacy** (`test-e2e/test-e2e.md`) come fallback.

Il fallback markdown e' temporaneo, valido finche' il catalogo YAML
non copre tutti i test. Documenta che la classificazione da markdown
e' meno precisa (i tag espliciti mancano, si usa solo l'euristica).

## Come si classifica un test in una cella

Due strategie, applicate nell'ordine:

1. **Tag-based**: se il YAML del test ha `tags: [postgres, base, extraction]`,
   il test entra direttamente in `(postgres, base)` di EXTRACTION.
2. **Heuristic-based**: parsing di `title + assertion + dataset + steps`
   per parole chiave (vocabolario in `coverage_parser.py`):
   - `"PostGIS"` / `"PostgreSQL"` -> source `postgres`
   - `"10k rows"` / `"100k"` / `"volume"` -> col `volume`
   - `"NULL"` -> col `null`
   - `"Unicode"` / `"O'Brien"` / `"BOM"` / `"emoji"` -> col `edge`
   - ecc.

L'euristica e' la rete di sicurezza per i test che non hanno tag espliciti.
**Conviene comunque taggare** quando si scrive il test, per evitare falsi
negativi/positivi.

## Aggiungere una nuova matrice

1. Aggiungi una nuova istanza di `Matrix` in `matrices.py`.
2. Inseriscila nella tupla `ALL_MATRICES`.
3. In `coverage_parser.classify()` aggiungi la sezione che popola le
   celle della nuova matrice (puoi riusare i `_detect_*` helper).
4. Rigenera `matrix.md` e committa.

Esempio:

```python
GEOMETRY = Matrix(
    name="geometry",
    title="Geometry types coverage",
    rows=("postgres", "mysql", "oracle", "sqlserver"),
    cols=("point", "linestring", "polygon", "multipolygon", "3d"),
    description="Per ogni dialetto SQL, quali tipi geometrici testiamo.",
    critical=False,
)
```

## Integrazione CI

Esempio di step in pipeline (pseudo-yaml):

```yaml
- name: Coverage matrix gate
  run: |
    cd test-e2e
    python3 coverage/render.py --format markdown --output coverage/matrix.md
    python3 coverage/check.py --min-coverage 30 --metric catalogued
```

Per generare un badge:

```fish
python3 coverage/render.py --format json --output coverage/matrix.json
jq -r '.totals.coverage_pct | floor | tostring + "%"' coverage/matrix.json
```

## Limitazioni note

- Il classificatore e' best-effort: se un test ha titolo ambiguo
  ("Estrai dati") e nessun tag, finisce in molteplici celle o in
  nessuna. La cura e': **taggare nel YAML**.
- Le matrici sono pensate per "data plane" (sorgenti, target, auth).
  I test trasversali (concurrency, security, audit) non hanno una
  matrice dedicata: appaiono solo in quelle in cui toccano una
  sorgente/target.
- Il fallback markdown perde i tag espliciti: porta a coverage stimata
  in difetto. Da rimuovere quando l'YAML copre tutto.
