# Dependency Matrix — Domini Backend

Data baseline: 2026-08-04 (PR native pipeline adapter)
Strumento: `backend/scripts/dependency_graph.py`
Baseline versionata: `backend/tests2/shared/config/core_dependency_baseline.json`
Guardia "non peggiorare": `backend/tests2/shared/test_core_dependency_matrix.py`

Questo documento e' il contratto architetturale sugli import tra i domini di
`backend/app/core`. Nasce dalla roadmap accoppiamento (Epic A) e va aggiornato
ogni volta che la baseline viene rigenerata.

## Come rigenerare

Dalla root `backend/` (in locale o nel container test2):

```sh
# report leggibile (SCC, gradi, edge)
python scripts/dependency_graph.py

# consolidare la baseline dopo una riduzione di coupling
python scripts/dependency_graph.py --write-baseline \
    tests2/shared/config/core_dependency_baseline.json
```

Con la lane containerizzata:

```sh
docker compose -f docker-compose.test2.yml run --rm --entrypoint python test2 \
    scripts/dependency_graph.py
```

La guardia fallisce solo su coupling NUOVO (edge non censiti, nuove coppie
cicliche); il debito storico censito nella baseline non blocca lo sviluppo.
Rimuovere coupling non rompe mai il test: rigenerare la baseline nello stesso
PR per consolidare il progresso.

## Classificazione dei domini

| Classe | Domini | Ruolo |
|---|---|---|
| Verticali | `arricchisci`, `carica`, `estrai`, `manipola`, `features`, `imdf` | step applicativi della pipeline ETL |
| Engine orizzontali | `auth`, `connections`, `dataframes`, `orchestrator`, `resources`, `results`, `scheduler` | servizi trasversali consumati dai verticali |
| Applicativi centrali | `projects`, `files`, `datasources`, `eseguibili`, `artifacts`, `intermediate`, `output_contract`, `query_execution` | modelli e storage condivisi del prodotto |
| Foundation | `io`, `native_pipeline`, `security`, `task_runtime`, `runtime`, `lifecycle` | infrastruttura pura. `native_pipeline` dipende solo dalla capability tempdir di `io`; `lifecycle` ospita il registry del group cleanup (Epic E), con composizione dei default in `app/runtime/group_cleanup_wiring.py` |
| Admin & support | `admin_backups`, `admin_object_storage`, `admin_runtime_config`, `admin_system_status`, `dashboard`, `logs`, `tickets` | superfici operative |

`app.shared.*` non e' un dominio: e' la sede dei contratti strutturali
(es. `ColumnInfo` in `app.shared.execution.column_info`) ed e' importabile da
chiunque.

## Regole di import

1. **Tutti → `app.shared`**: sempre ammesso. I tipi usati come contratto tra
   domini vanno qui, non in un dominio applicativo.
2. **Verticali → engine / applicativi centrali / foundation**: ammesso.
3. **Engine → verticali**: vietato. Eccezioni legacy censite sotto, ognuna con
   percorso di rimozione.
4. **Engine ↔ applicativi centrali**: tollerato oggi (e' il grosso del ciclo a
   17), in riduzione progressiva tramite porte (Epic C/D/F/G).
5. **Foundation → core**: vietato (gia' blindato da
   `test_architecture_legacy_patterns.py` e dalle guardie boundary).
6. **Admin & support → verticali**: vietato; verso engine/foundation ammesso.
7. **Nessun nuovo edge** rispetto alla baseline senza motivazione qui dentro e
   rigenerazione consapevole della baseline (guardia
   `test_no_new_cross_domain_edges`).

La mappa completa degli shim di compatibilita' (Fase 6) e' in
[legacy-shims.md](legacy-shims.md).

## Eccezioni legacy censite (engine → verticali e coupling inverso)

| Edge | Natura | Percorso di rimozione |
|---|---|---|
| ~~`auth -> arricchisci`~~ | ~~cleanup registry importa i cleanup di 7 domini~~ | **FATTO (Epic E, 2026-06-12)**: registry neutro in `core/lifecycle/group_cleanup.py`, composizione in `app/runtime/group_cleanup_wiring.py`, wrapper compat in auth. Rimossi anche `auth -> connections` e `auth -> datasources`. Nuovo edge dichiarato: `auth -> lifecycle` (one-way, lifecycle ha out-degree 0: nessun ciclo). |
| `carica -> arricchisci` | `carica/rest_auth.py`: le credenziali CARICA REST si leggono per riferimento al connettore API (cifrato) di `arricchisci/api_connector` (audit frontend R10.8, Fase 5 T1, 05/11/2026) | spostare il servizio dei connettori API in `connections` |
| `scheduler -> carica` | import diretto executor in `execution_manager.py` | G4 (PR 5): port dal composition root |
| `orchestrator -> carica` | lazy, dispatch pipeline | Epic G: `PipelineDispatchPort` |
| `orchestrator -> estrai` | dispatch nodo estrazione | Epic G |
| `connections -> features` | wrapper compatibilita' legacy | Fase 6: rimozione shim |
| `projects -> arricchisci` | lazy, dependency check | Epic F: porta dependency checks |
| `projects -> scheduler` | lazy, cleanup/dipendenze | Epic F |
| `auth -> projects` / `auth -> files` | `resource_access` istanzia servizi | Epic C (PR 2): `ProjectMetadataReader` / `FileSourceReader` |
| `orchestrator -> io` | preview payload object-storage e spool bounded | Intenzionale: `io` e' foundation con out-degree 0 |
| `projects -> orchestrator` | verifica lazy dei task attivi prima del lifecycle delete | Estrarre `ActiveTaskProbe` condiviso quando si riduce il ciclo projects/orchestrator |
| `tickets -> io` | lettura prefisso e streaming allegati senza materializzare BLOB | Intenzionale: support domain verso foundation |
| `tickets -> security` | upload-size guard sugli allegati | Intenzionale: support domain verso foundation |
| `admin_object_storage -> auth` | explorer read-only riservato ai superadmin | Intenzionale: RBAC al boundary HTTP |
| `admin_object_storage -> io` | catalogo paginato e download streaming del bucket runtime | Intenzionale: support domain verso foundation con out-degree 0 |
| `admin_object_storage -> projects` / `results` | catalogo operativo correla oggetti runtime con metadati progetto e risultati | Estrarre una read-model port se il catalogo cresce oltre l'ispezione admin |
| `admin_task_runtime -> auth` / `task_runtime` | endpoint admin protetto espone le lease del runtime task | Intenzionale: adapter operativo read-only sul dominio runtime |
| `datasources -> security` | upload datasource applica il limite centralizzato | Intenzionale: `security` e' una policy foundation |
| `io -> resources` | bounded range reader usa l'errore canonico di limite memoria | Spostare l'eccezione in shared quando si separa il ciclo `io/resources` |
| `dashboard -> arricchisci` / `datasources` / `scheduler` / `auth` | `GET /dashboard/counts` (UI-1, 06/11/2026) conta con le route delle liste di connettori API, Data Source e schedule (visibilita' identica) e l'RBAC di `auth` | Intenzionale: read-model della dashboard; estrarre porte di conteggio nei tre domini se crescono |
| `projects -> manipola` | schema inference riusa l'ordinamento deterministico degli input transform | Estrarre l'helper di ordering in shared execution per spezzare il ciclo |
| `eseguibili -> connections` | il servizio eseguibili espone i connection set selezionabili per gli input runtime | Intenzionale nel read model corrente; estrarre una porta shared se il catalogo cresce |
| `orchestrator -> files` | cleanup fail-closed degli upload runtime rifiutati dal percorso progetto | Intenzionale e lazy: evita file orfani finche' il lifecycle upload non viene portato nel composition root |
| `native_pipeline -> io` | workspace privato creato dalla capability filesystem dichiarata, senza accesso diretto fuori dal boundary I/O | Intenzionale, aciclico: `native_pipeline` e' foundation adapter e `io` non importa `native_pipeline` |

## Stato misurato (baseline 2026-08-04)

- **34 domini**, **152 edge cross-domain**.
- **SCC maggiore: 17 domini** — `arricchisci`, `artifacts`, `auth`, `carica`,
  `connections`, `datasources`, `eseguibili`, `estrai`, `features`, `files`,
  `intermediate`, `manipola`, `orchestrator`, `projects`, `query_execution`,
  `results`, `scheduler`.
- `io <-> resources` resta una SCC separata di 2 domini.
- `native_pipeline` e' un singoletto aciclico con un solo edge uscente verso
  `io`; il nuovo adapter non amplia alcuna SCC.
- Il baseline consolida anche gli edge aciclici preesistenti
  `eseguibili -> connections` e `orchestrator -> files`, motivati sopra.

### Snapshot precedente (post Epic E)

### Snapshot precedente (2026-06-11)

- **30 domini**, **141 edge cross-domain**.
- **SCC maggiore: 17 domini** — `arricchisci`, `artifacts`, `auth`, `carica`,
  `connections`, `datasources`, `eseguibili`, `estrai`, `features`, `files`,
  `intermediate`, `orchestrator`, `output_contract`, `projects`,
  `query_execution`, `results`, `scheduler`.
  - Rispetto allo snapshot 2026-06-04 (18 domini) **`dataframes` e' uscito dal
    ciclo** (PR 1 roadmap: `ColumnInfo` in `app.shared.execution`, guardia
    `test_dataframes_engine_does_not_import_projects_domain`). Milestone 2
    raggiunta.
- **13 singoletti**: tutti gli admin, `dataframes`, `imdf`, `io`, `logs`,
  `manipola`, `resources`, `runtime`, `security`, `task_runtime`, `tickets`.
  Notevole: `manipola` (out=9, in=0) e `imdf` sono verticali puri consumatori,
  gia' fuori dal ciclo.
- **Hub piu' centrali** (out/in): `auth` 7/19, `projects` 7/13,
  `dataframes` 2/15, `resources` 1/11, `io` 0/11.
- **Foundation confermata dai numeri**: `io`, `security`, `task_runtime` hanno
  out-degree 0.

Target roadmap: SCC sotto 12, poi sotto 8, poi nessuna componente grande tra
verticali ed engine. Ricalcolare a ogni PR di riduzione e aggiornare questa
sezione.

## Aggiornamento del documento

Quando un PR riduce coupling:

1. rigenerare la baseline (`--write-baseline`);
2. aggiornare "Stato misurato" (dimensione SCC, edge count);
3. spuntare/rimuovere l'eccezione legacy corrispondente dalla tabella.

Quando un PR introduce coupling intenzionale (raro): aggiungere la riga di
motivazione nelle eccezioni PRIMA di rigenerare la baseline.


## Dipendenze di inferenza (Blocco A, fase A2, 04/11/2026)

Integrazione backend al documento generale `docs/architecture/dependency-matrix.md` del repository. Il perimetro A2 consente scritture soltanto in backend.

Le seguenti dipendenze sono intenzionali e richieste dalla regola «l'esecuzione è la verità»:

| Arco | Motivo |
|---|---|
| `projects -> estrai` | L'inferenza usa il renderer SELECT e la visita/ordinamento del grafo effettivi, evitando una seconda implementazione della proiezione. |
| `projects -> query_execution` | Riutilizza la normalizzazione dei nomi restituiti dal provider e la disambiguazione dei duplicati. Non esegue query sul database sorgente. |
| `projects -> connections` | Legge dal bridge DB il tipo della connessione corrente; non costruisce servizi, decifra credenziali o apre connessioni al database sorgente. |

La lettura della configurazione API corrente riusa il bridge DB di Arricchisci, su un arco già esistente. Il contratto viene consumato tramite il suo boundary pubblico. Non sono aumentati i limiti per import locali, annotazioni mancanti o costruzioni dirette dei servizi.

Baseline rigenerata con `python scripts/dependency_graph.py --write-baseline tests2/shared/config/core_dependency_baseline.json`. Il diff aggiunge questi tre archi; non aggiunge coppie di domini in ciclo. La rigenerazione riallinea anche conteggi storici già cambiati in HEAD e rimuove l'arco storico non più presente `orchestrator -> files`.
