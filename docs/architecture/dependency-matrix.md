# Dependency Matrix — Domini Backend

Data baseline: 2026-07-14 (branch `main`)
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
| Foundation | `io`, `security`, `task_runtime`, `runtime`, `lifecycle` | infrastruttura pura, zero import verso altri domini core (verificato: out-degree 0/1). `lifecycle` ospita il registry del group cleanup (Epic E); la composizione dei default e' in `app/runtime/group_cleanup_wiring.py` |
| Admin & support | `admin_backups`, `admin_object_storage`, `admin_runtime_config`, `admin_system_status`, `logs`, `tickets` | superfici operative |

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
| `projects -> manipola` | schema inference riusa l'ordinamento deterministico degli input transform | Estrarre l'helper di ordering in shared execution per spezzare il ciclo |

## Stato misurato (baseline 2026-07-20)

- **SCC maggiore: 16 domini** — con B4 (`Column` in
  `app.shared.execution.canvas_column` + schema inference iniettata dai
  chiamanti projects) il 2-ciclo `projects <-> output_contract` e' spezzato
  e **`output_contract` esce dal ciclo** (secondo dominio liberato dopo
  `dataframes`). Guardia dedicata:
  `test_canvas_column_shared_contract.py`.
- **33 domini**, **149 edge cross-domain**. `admin_task_runtime` resta fuori
  dalle SCC; `admin_object_storage` espone anche la read model progetti/risultati.
  Il maggiore SCC sale a **17 domini** per `projects -> manipola`, mentre
  `io -> resources` crea una SCC separata di 2 domini. Entrambi i percorsi di
  rimozione sono registrati nella tabella sopra. `auth` resta a out-degree 5.

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
