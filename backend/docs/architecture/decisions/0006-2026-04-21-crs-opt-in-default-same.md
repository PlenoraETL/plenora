# ADR-0006: CRS opt-in sugli operatori spaziali — default `"same"` per retrocompat

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

Il modulo `app/core/manipola/transforms/spatial.py` espone operatori spaziali su GeoDataFrame pandas: `BUFFER`, `CENTROID`, `AREA`, `DISTANCE`, `REPROJECT`, `UNION`, `INTERSECTION`, ecc. Alcuni di questi richiedono un **calcolo metrico** (es. buffer in metri) ma accettano geometrie in qualsiasi CRS — incluso CRS geografico (lat/lon, gradi) dove "buffer 100" significa "100 gradi" (nonsenso).

Prima del refactor, l'operatore "risolveva" implicitamente: se il CRS era geografico, la libreria pandas/shapely lasciava passare il buffer in gradi senza warning. Risultato: dataset con risultati numericamente corretti ma semanticamente errati ("100 gradi" ~= 11 000 km).

Il fix concettuale e': per operatori metrici, riproietta a UTM appropriato, esegui il calcolo, riproietta indietro. Ma questo cambia il comportamento osservabile per canvas-ETL salvati **pre-refactor**: quelli che si affidano al comportamento vecchio devono continuare a funzionare.

Altra complicazione: alcuni clienti lavorano in CRS proiettato non-UTM (Lambert conformico, LAEA italiana EPSG:6875). Forzare UTM e' un overhead di riproiezione per nulla. Serve una via di controllo per l'utente.

## Decision

Introdurre due parametri opt-in sugli operatori spaziali:

- **`metric_crs: Optional[str]`** — CRS usato per il calcolo metrico interno.
  - `None` (default): **auto-UTM** via `get_utm_crs_for_geometry(gdf)` se l'input e' geografico; altrimenti **no reproject** (gia' metrico).
  - `"EPSG:xxxx"`: usa esplicitamente quel CRS (es. `"EPSG:6875"` per dataset multi-zona italiani LAEA).
- **`output_crs: str = "same"`** — CRS finale delle geometrie di output (solo operatori con output geometrico, es. BUFFER, UNION).
  - `"same"` (default): ritorna al CRS **dell'input** (retrocompat totale: roundtrip reproject → calcola → reproject indietro).
  - `"metric"`: ritorna nel `metric_used` scelto (evita roundtrip — utile se il caller sa gia' di voler lavorare in metrico downstream).
  - `"EPSG:xxxx"`: riproietta esplicitamente.

Codice: `app/core/manipola/transforms/spatial.py:146-178` — `_resolve_metric_crs`, `_resolve_output_geom_crs`. Ogni operatore metrico accetta i due param via signature.

## Rationale

- **Default `"same"` = retrocompat totale**. Omettere entrambi i param e' identico al comportamento pre-refactor — nessun canvas salvato si rompe al passaggio.
- **Opt-in esplicito**. L'utente che capisce il problema e vuole controllo lo ottiene via due argomenti ben documentati. L'utente che non sa nulla di CRS continua a ottenere output ragionevole (auto-UTM su geografico evita il bug "buffer in gradi").
- **Tre livelli di granularita'**: default stupido ma safe (`"same"` + auto-UTM), modalita' "avanzata ma comoda" (`metric_crs` fisso + `output_crs="same"`), modalita' "pipeline-aware" (`output_crs="metric"` evita roundtrip per catene di op metrici).
- **`_resolve_metric_crs` e `_resolve_output_geom_crs` isolate**. Due funzioni pure con un solo input ciascuna → test unit diretti. Il resto dell'operatore (buffer/centroid/etc.) non sa di questo problema.
- **No magic detection**. Non si tenta di indovinare l'intenzione dell'utente da pattern euristici sui dati (es. "se bbox span > 1 gradi allora uso UTM"). Esplicito > implicito per operatori geospaziali, dove gli errori costano giorni di debug.

## Alternatives considered

- **Hard default: sempre auto-UTM, `output_crs="same"` forzato** — **Rejected per retrocompat**. Chi aveva canvas con bug latenti (buffer in gradi) vede il risultato cambiare silenziosamente dopo l'upgrade. Nessun way per l'utente di disattivare il nuovo comportamento. Distruttivo di fiducia.
- **Breaking change documentato (changelog + major bump)** — **Rejected per Fase 0/1**. L'applicazione non e' versionata semanticamente come libreria; i clienti non fanno "pip upgrade" consapevole. Un deploy silenzioso che rompe output = incident.
- **Param unico `crs_strategy: str`** — **Rejected**. Tre valori strategy (`"auto"`, `"input"`, `"metric"`) farebbero esplodere il combinatorio quando si discute dove ritornare (input vs metric vs esplicito). Due param ortogonali (dove calcolo / dove ritorno) sono piu' chiari di uno con 6-9 valori.
- **Forzare l'utente a usare `REPROJECT` prima del buffer** — **Rejected**. Tecnicamente corretto ma operativamente crudele: costringe il canvas a un nodo in piu' per un problema che l'operatore sa risolvere da solo.

## Consequences

### Positive
- Zero regressione osservabile per canvas esistenti (verificato sui clienti ICSC, RFI pre-refactor).
- Utenti avanzati hanno controllo esplicito su CRS intermedio e output.
- Due helper puri (`_resolve_metric_crs`, `_resolve_output_geom_crs`) testabili in isolamento.
- Eliminato il bug "buffer in gradi" come default per nuovi canvas che usano auto-UTM.

### Negative
- Signature degli operatori cresciuta di 2 parametri. Per op singolo — accettabile. Per chi compone operatori in batch via API REST — deve leggere la doc.
- Auto-UTM richiede `get_utm_crs_for_geometry` che fa un `to_crs` e un `bounds` → piccolo overhead. Per dataset grandi irrisorio; per op iterativo su piccoli GeoDataFrame il roundtrip CRS pesa ~10-30ms.
- Caller che vogliono "metrico end-to-end" devono settare `output_crs="metric"` esplicitamente a ogni passo. Non c'e' (ancora) un context-manager o sticky-state a livello canvas.

### Neutral
- Operatori non-metrici (es. geometric union pura, spatial join) ignorano `metric_crs`. Accettato: il param e' documentato come "solo operatori metrici".
- `"same"` e' stringa magic ma e' letterale (non enum); evita dipendenza da un enum shared.

## References

- Source: `backend/app/core/manipola/transforms/spatial.py:100-178` — header comment con semantica CRS + `_resolve_metric_crs` + `_resolve_output_geom_crs`
- Source: `backend/app/core/manipola/transforms/spatial.py:246-316` — operatori che accettano i due param (BUFFER, DISTANCE, ecc.)
- Source: `backend/app/core/manipola/transforms/spatial.py:864+` — op aggregativi (UNION, INTERSECTION) con lo stesso pattern
- Related: ADR-0001 (validazione formula — altro refactor conservativo del modulo `manipola`)
- Invariants: `backend/docs/architecture/invariants.md` §manipola — retrocompat su canvas salvati
