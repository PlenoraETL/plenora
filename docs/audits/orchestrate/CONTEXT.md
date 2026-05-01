# Context - Working Baseline

Questo file e' una mappa iniziale, non un perimetro chiuso.

Usare questa conoscenza per orientarsi, ma non assumerla corretta senza
verifica. Ogni fase deve riconfermare con lettura codice, grep, call graph e
test.

## Product

Plenora / URBY-ETL e' un ETL canvas geospaziale con:
- backend FastAPI + Celery + PostgreSQL;
- frontend React/Vite;
- moduli principali per trasformazioni, caricamento DB, arricchimento API,
  file sources, IMDF/ArcGIS, auth/security e workers.

## Domain Map

- `manipola`: trasformazioni tabellari/geospaziali.
- `carica`: scrittura database, transazioni, replace strategies, batch write.
- `arricchisci`: enrichment HTTP/API, API connector, rate limit, retry,
  REST_FILE, API generator/enricher.
- `imdf`: upload/publish ArcGIS, service discovery/lifecycle/publish ops.
- `auth/security`: crypto, JWT, MFA, blacklist, secret handling.
- `workers`: esecuzione async Celery.
- `frontend`: canvas UI, API client, workflow utente.

## Phase 5 Knowledge To Verify

- Moduli standalone/facade sono stati usati per funzioni pure.
- Mixin via MRO sono stati usati per classi stateful.
- Orchestrator core sono stati lasciati nei file principali quando spostarli
  avrebbe peggiorato coesione o patchability.
- Alcuni test source-location-bound sono stati modernizzati a behavior-bound.
- Alcuni debiti sono stati rimandati apposta per non mischiare refactor
  strutturale e cambio comportamento.

Questa sezione non deve vincolare l'audit. Serve a non ripartire ciechi, ma la
seconda run deve poter smentire ogni punto se il codice lo dimostra.

Principio:

> Treat Phase 5 closeouts as hypotheses, not as authority.

In italiano:

> Tratta i closeout Phase 5 come ipotesi operative, non come verita'.

## Known Risk Patterns

- Patch su `module.logger` o `patch("...logger")`.
- `inspect.getsource(module)` o AST scan su file fisico.
- Monkeypatch su facade che non propaga nei moduli sibling.
- Retry/429/timeout semantics.
- Crypto decrypt strict vs best-effort.
- Transaction boundaries in `carica`.
- SSRF e tenant isolation in `api_connector`.
- Source loading fallback filesystem.

## First Run Heritage

La nuova run 0 -> 5 deve ripetere il ciclo, non diventare una Phase 6.

- Phase 0: consolidation, path canonici, fallback, feature flag, invarianti.
- Phase 1: stabilization, fix mirati, regressioni, test fragili.
- Phase 2: hardening, failure modes, security, performance, observability.
- Phase 3: audit indipendente su architettura, security, ops, regression.
- Phase 4: launch/publication/deploy readiness.
- Phase 5: structural quality pass.

La prima run ha reso il sistema governabile. La seconda run deve usare questa
governabilita' per fare una passata piu' profonda.

## What Not To Assume

Non assumere che:

- un file piccolo sia automaticamente sano;
- un residuo dichiarato intenzionale sia intoccabile;
- un re-export sia ancora necessario;
- un fallback sia ancora autorizzato;
- un test verde protegga il comportamento giusto;
- un closeout precedente abbia visto tutto.

Ogni affermazione deve poter essere riconfermata con codice e test.

## Remote Note

Remote GitHub pulito pubblicato post-Phase-5:

`https://github.com/PlenoraETL/plenora.git`

Checkpoint:
- branch `main` tracking `origin/main`;
- tag `phase-5-complete` pubblicato;
- vecchio remote locale `ultra` lasciato come riferimento storico.

Il server non e' ancora riallineato. Quando si passa al server, usare GitHub
come sorgente pulita e fare deploy controllato, non patch manuali.
