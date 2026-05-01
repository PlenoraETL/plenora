# ADR-0017: Features vertical slice — status e disposizione dead symbols

**Status**: Accepted
**Date**: 2026-04-24
**Deciders**: @CloudIA

## Context

Il modulo `backend/app/core/features/` (ArcGIS Feature Service client + routes + service) e' un vertical slice live in produzione: 4.481 LOC (arcgis_client 2476 + features_service 1265 + routes 516 + models 197 + __init__ 27), 12 endpoint REST attivi (`/connections` CRUD + `/test` + `/services` + `/folders` + `/layers` + `/schema` + `/query/preview` + `/write` + `/create-service`), 11 funzioni client consumate dalla UI via `ConnectionManager`, `FeatureServicePanel`, `EstraiEditor`, `CaricanArcGISNode`, `FeatureLayerNode`, `ImdfPublisherNode`, `ProjectList`, `useEditorData`.

L'audit `orchestrate/phase_2_hardening/dead_code_and_oversized_modules_audit.md` (2026-04-22) ha flaggato un cluster interno al vertical che appariva come "design aspirazionale mai wired":

- **§2.4.1**: 6 classi Pydantic in `backend/app/core/features/models.py` sospette orfane — `ServiceInfo` (L105), `LayerInfo` (L113), `LayerSchema` (L136), `FeatureQueryRequest` (L151), `FeatureQueryResult` (L167), `FeatureWriteResult` (L191).
- **§3.1**: 3 client FE in `frontend/src/api/features.js` usati solo da `frontend/tests/api/features.contract.test.js` — `queryLayer` (L60), `writeFeatures` (L76), `createFeatureService` (L90).

La classificazione del batch §8 era **medio-alto rischio** con dichiarazione esplicita: *"Rimuovere = congelare la perdita di tipizzazione; il fix alternativo e' wirare (applicarli come `response_model`)"*.

Verifica puntuale 2026-04-24 pre-ADR ha sciolto l'ambiguita' reale:

1. **Gli endpoint `/query/preview`, `/write`, `/create-service` sono LIVE** sul backend. Non sono 501, non sono pianificati: sono in esercizio.
2. **Gli handler usano modelli DIVERSI da quelli di `models.py`**: `routes.py:311` definisce **inline** `WriteRequest`, `routes.py:324` definisce **inline** `CreateServiceRequest`, l'handler `/query/preview` usa `Query(...)` params. I 6 modelli di `models.py` hanno schema divergente (es. `FeatureQueryRequest` ha `service_url`+`layer_id`; l'handler ha `connection_id`+`layer_url`+`where`+`out_fields`+`max_features`).
3. **`response_model`** e' `dict` su 4/12 endpoint, assente sui restanti 8. Zero endpoint usa uno dei 6 Pydantic sospetti come `response_model=`.
4. **I 3 client FE sospetti CHIAMANO endpoint LIVE**: `queryLayer` → `/features/query/preview`, `writeFeatures` → `/features/write`, `createFeatureService` → `/features/create-service`. I test contract `frontend/tests/api/features.contract.test.js` proteggono la forma payload FE↔BE (tre scenari verificati con `httpMock.post`).
5. **La UI NON chiama nessuna delle 3 funzioni FE**: grep su `frontend/src/` conferma zero consumer UI (solo test). Le altre 11 funzioni di `features.js` sono wired da 8 consumer UI distinti.

La situazione effettiva:

- **Backend**: i 6 Pydantic di `models.py` sono **drafts aspirazionali** del design. Non e' "tipizzazione persa in attesa di essere wired": la tipizzazione live **esiste** ma vive inline in `routes.py`. `models.py` e' un residuo di un disegno alternativo mai adottato.
- **Frontend**: i 3 client sono **API-ready senza consumer UI**. Asimmetria rara ma non automaticamente morta: gli endpoint BE sono live, potenzialmente raggiungibili da integratori esterni (ICSC/RFI/Geosafe) che non vivono nel repo.

Due decisioni distinte, **indipendenti ma entrambe nel perimetro Batch B**:

- **Sub-A (backend)**: cosa fare dei 6 Pydantic non wired.
- **Sub-B (frontend)**: cosa fare dei 3 client senza UI.

Vincolo trasversale: deadline produzione 2026-05-05, focus su fix non feature (memoria progetto `project_deadline.md`).

## Decision

**Sub-A → remove** dei 6 Pydantic orfani di `models.py` (+1 cascata `FieldInfo` L125 che perde il suo unico user `LayerSchema.fields`).

**Sub-B → keep** dei 3 client FE + test contract, con annotazione doc esplicita in `features.js` che dichiara l'asimmetria FE-UI.

Piano esecutivo post-ADR (4 commit dedicati):

| # | Commit prefix | Scope | File toccato |
|---|---|---|---|
| 1 | `[adr]` | questo documento | `docs/architecture/decisions/0017-2026-04-24-features-vertical-status.md` |
| 2 | `[p7-b1]` | rimozione 6+1 Pydantic orfani in un singolo commit atomico | `backend/app/core/features/models.py` |
| 3 | `[p7-b2]` | annotazione doc B.keep (commento intestazione sezione query/write/create-service) | `frontend/src/api/features.js` |
| 4 | `[invariants]` | breadcrumb §6 chiusura Batch B | `docs/architecture/invariants.md` |

## Rationale

### Sub-A: perche' remove (e non wire, non keep)

- **Zero perdita di tipizzazione**: la tipizzazione dei request e' gia' live inline (`WriteRequest`, `CreateServiceRequest`, `Query(...)` params). Rimuovere i 6 drafts non degrada nulla.
- **Schemi divergenti**: wire richiederebbe **riscrivere** i modelli per allinearli agli handler, non applicarli. Il costo e' un refactor contract-breaking, non un semplice `response_model=ServiceInfo`.
- **Sdoppiamento confuso**: tenerli (keep) lascia `models.py` come fonte apparente di verita' che NON combacia col codice live — il prossimo developer che legge il file viene sviato.
- **Meccanica Batch A**: rimozione atomica di 7 classi Pydantic senza effetti runtime, stesso standard metodologico del Batch A appena chiuso (0-caller, ast parse, pytest mirato).
- **Coerente con deadline**: pre-deadline si rimuove codice morto, non si apre refactor di tipizzazione.

### Sub-B: perche' keep (e non wire, non remove)

- **Wire** sarebbe feature work pre-deadline (aggiungere consumer UI in 3 component plausibili: `FeatureServicePanel` per preview, `ImdfPublisherNode`/`ResultNode` per publish, post-arricchimento per write). Non giustificato da deadline 2026-05-05.
- **Remove lato FE solo** e' cleanup asimmetrico: gli endpoint BE restano live. Se un integratore esterno li usa, la rimozione FE+test non gli interessa; se nessuno li usa, la rimozione vera include anche gli endpoint BE, scope grande (verifica integratori esterni + rimozione route + eventuale rimozione logica `features_service.py` a cascata).
- **Remove completo** (FE + BE) richiede conferma che nessun integratore esterno chiami `/features/write` o `/features/create-service` — non verificabile dal repo. Inoltre `ImdfPublisherNode` pubblica via path separato (`imdf_publish_task.py`), ma un'estensione futura del nodo potrebbe voler riusare `/features/create-service` direttamente. Rimozione = perdita di un'API potenzialmente utile senza costo reale di mantenerla.
- **Keep + annotation** chiude l'ambiguita' doc-only: il prossimo che legge `features.js` trova dichiarato che quelle 3 funzioni sono API-ready senza consumer UI, endpoint BE live, wiring UI scope post-launch. Zero aria architettonica residua.

### Decisioni di numerazione e perimetro

- **ADR-0017**: prossimo libero dopo ADR-0016 (IMDF boundaries, ultima di Phase 2).
- **Perimetro lock**: questo ADR tocca solo i simboli elencati in audit §2.4.1 + §3.1. Nessun tocco a `routes.py` inline models, a `features_service.py`, a `arcgis_client.py`, agli altri simboli live di `models.py` (`ArcGISConnection`, `ArcGISCreate`, `ArcGISUpdate`, `ArcGISList`, `ArcGISTestResult`, `FeatureWriteRequest`).
- **Piano esecutivo 4 commit**: ADR separato dalle rimozioni per rendere la decisione ratificabile prima dell'implementazione; `[p7-b1]` esegue il backend in un singolo commit (7 simboli, stesso file, stesso dominio — dentro la policy micro-batch definita durante Batch A); `[p7-b2]` e' commit doc-only FE; `[invariants]` chiude.

## Alternatives considered

### Sub-A alternatives

- **A.wire** — applicare i 6 model come `response_model=` sugli endpoint + migrare `WriteRequest`/`CreateServiceRequest` da inline a `models.py`. **Rejected**: schemi divergenti → wire = rewrite, non applicazione; il rewrite del contract degli handler e' refactor architetturale con rischio di rompere i test contract FE esistenti; pre-deadline fuori scope.
- **A.keep** — status quo con commento doc che dichiara "models.py sono drafts, non wire". **Rejected**: congela lo sdoppiamento draft↔live; costo zero a breve ma debito duraturo (ogni nuovo developer ripassa l'ambiguita').
- **A.remove** (scelta) — rimozione 6+1.

### Sub-B alternatives

- **B.wire** — aggiungere consumer UI nei 3 component plausibili. **Rejected**: feature work pre-deadline, non allineato con "focus on fixes not features".
- **B.remove (solo FE)** — rimuovere le 3 funzioni + test contract, lasciare endpoint BE live. **Rejected**: cleanup asimmetrico, lascia contract FE↔BE non protetto se un wiring UI arrivera' post-launch.
- **B.remove-full** (FE + BE + features_service) — rimozione verticale completa. **Rejected**: richiede verifica integratori esterni (fuori dal repo); scope grande; perde API potenzialmente utile per estensioni `ImdfPublisherNode` future.
- **B.keep** (scelta) — mantieni con annotation.

## Consequences

### Positive

- **Sdoppiamento backend risolto**: dopo `[p7-b1]`, `models.py` contiene solo i 5 `ArcGIS*` live + `FeatureWriteRequest` (import-usato in `routes.py:50`); zero draft aspirazionali.
- **Asimmetria frontend documentata**: dopo `[p7-b2]`, chiunque legga `features.js` vede l'annotation che dichiara "API-ready senza consumer UI, endpoint BE live, wiring UI scope post-launch".
- **Test contract preservati**: i 3 scenari in `features.contract.test.js` continuano a bloccare drift del payload FE↔BE. Se un wiring UI arrivera' post-launch, il contract gia' verificato sara' base di partenza.
- **Meccanica simmetrica a Batch A**: 7 simboli backend rimossi in un singolo commit atomico, standard 0-caller invariato.
- **Audit `dead_code_and_oversized_modules_audit.md §8 Batch B` chiuso** al pari di Batch A.

### Neutral

- **Endpoint BE `/query/preview`, `/write`, `/create-service`** restano live invariati. Nessuna garanzia aggiuntiva di utilizzo; nessun degrado.
- **`features_service.py` e `arcgis_client.py`** (4481 LOC insieme) non toccati — scope oversized modules e' Batch E-F territory, fuori Batch B.
- **Inline models `WriteRequest`/`CreateServiceRequest` in `routes.py`** restano inline. Migrazione a `models.py` e' refactor architetturale, non in scope Batch B.

### Negative

- **Perdita della possibilita' di "wire facile" futuro**: se qualcuno vorra' tipizzare i response degli endpoint Features, dovra' riscrivere i modelli da zero invece che partire dai 6 draft. Accettato perche' i draft avevano schemi divergenti (non erano base utile).
- **Asimmetria FE-UI dichiarata ma non risolta**: il gap tra client FE e consumer UI resta. Accettato perche' chiudere il gap e' feature work post-launch.

## Follow-up

Registrati come candidati post-launch, **non blocker** per chiusura Batch B:

- **Wiring UI delle 3 funzioni** (B.wire differito): scope post-launch, richiede design UX per preview/write/publish inside canvas ETL.
- **Migrazione inline models `WriteRequest`/`CreateServiceRequest` → `models.py`**: omogeneita' architetturale, non urgente. Scope Batch D o post-launch.
- **Decisione su endpoint BE `/query/preview`, `/write`, `/create-service`** se nessun consumer emerge post-launch: possibile rimozione verticale completa con verifica integratori esterni.
- **Tipizzazione response_model** per i 12 endpoint: upgrade da `dict` a modelli tipizzati specifici. Scope post-launch, richiede design nuovi model (i 6 rimossi qui non sono riutilizzabili).

## References

- Audit: `orchestrate/phase_2_hardening/dead_code_and_oversized_modules_audit.md §2.4.1 + §3.1 + §8 Batch B`
- Batch A chiuso (metodologia di riferimento): commit `c21b4a6` `[invariants]` + audit §8 Chiusura Batch A
- ADR precedente: ADR-0016 (IMDF boundaries, 2026-04-23)
- Invariants: `docs/architecture/invariants.md §6` (breadcrumb ratifica post piano esecutivo)
