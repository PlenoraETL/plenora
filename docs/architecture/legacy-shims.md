# Mappa shim legacy e compatibilita' (Fase 6 roadmap accoppiamento)

Data censimento: 2026-06-12.

Ogni shim tollerato e' censito qui con motivo, test che lo protegge e
criterio di rimozione (Fase 6 roadmap: "rimuovere uno shim alla volta
quando non ci sono piu' import legacy"). Chi rimuove uno shim aggiorna
questa tabella e i test indicati nello stesso PR.

## Re-export di contratti migrati in shared (Epic B / B4)

| Shim | Sede | Motivo | Test che lo protegge | Criterio di rimozione |
|---|---|---|---|---|
| `ColumnInfo` re-export | `projects/canvas_models.py` | PR1: sede canonica `app.shared.execution.column_info`; ~30 file di test importano dal path storico | `test_import_hygiene_residual_contract` (sede canonica), `test_file_helpers_module_contract` (identita') | zero import di `ColumnInfo` da `canvas_models` in app+tests2 |
| `Column` re-export | `projects/canvas_models.py` | B4: sede canonica `app.shared.execution.canvas_column` | `test_canvas_column_shared_contract` (identita' + boundary output_contract) | zero import di `Column` da `canvas_models` fuori da projects |

## Re-export interni al dominio projects (Epic F)

| Shim | Sede | Motivo | Test che lo protegge | Criterio di rimozione |
|---|---|---|---|---|
| `RawProjectCanvas` re-export | `projects/service.py` | F3: la classe vive in `canvas_service` | `test_raw_project_canvas_reexport_identity` | consumer migrati a `canvas_service` |
| `ProjectDependencyPayload` re-export | `projects/service.py` | F5: la classe vive in `lifecycle_service` | `test_dependency_payload_reexport_identity` | consumer migrati a `lifecycle_service` |
| Facade di delega `ProjectService` | `projects/service.py` | Epic F: i 29 metodi storici delegano ai 4 sub-service; la facade preserva firma e patch surface (~28 file di test la referenziano) | `test_project_metadata_service_contract` (delega + patch surface + perimetri) | di fatto permanente finche' router e chiamanti non consumano i sub-service direttamente; nuove feature NON aggiungono metodi alla facade (perimetri chiusi dai test) |

## Wrapper di boundary (Epic E)

| Shim | Sede | Motivo | Test che lo protegge | Criterio di rimozione |
|---|---|---|---|---|
| `auth.cleanup_registry` wrapper | `auth/cleanup_registry.py` | Epic E: registry in `core/lifecycle`, composizione in `runtime/group_cleanup_wiring`; il wrapper preserva `GROUP_CLEANUP_HANDLERS` (PEP 562) e la superficie monkeypatch | `test_auth_delete_group_logging_contract`, `test_group_cleanup_policy_contract` (+ guardia E5) | chiamanti (`auth/group_cleanup.py`, test) migrati a lifecycle+wiring; poi tombstone |
| `delete_intermediate_runs_for_group_project` | `auth/cleanup_registry.py` | export storico per vecchi import auth (lazy verso `projects.cleanup`) | `test_group_cleanup_imports_intermediate_cleanup_boundary_only` | zero import del nome da auth in app+tests2 |

## Fallback per-chiamata sanzionati (Epic D, guardia D5)

Censiti in `_SANCTIONED_FALLBACKS` di
`tests2/shared/test_service_construction_ratchet_contract.py` — la guardia
li tiene a perimetro chiuso (non possono crescere):

| Shim | Sede | Motivo | Criterio di rimozione |
|---|---|---|---|
| Accessor `_get_*` (3 costruzioni) | `estrai/router.py` | wired-first via `ESTRAI_BINDING`; fallback per contesti senza wiring | tutti i contesti (test inclusi) passano dal binding |
| `_project_service()` (1) | `orchestrator/router.py` | wired-first via `init_orchestrator` | idem |
| Accessor `_get_*` (3) | `carica/dispatch.py` | wired-first via `CARICA_DISPATCH_BINDING`; i worker usano il fallback | wiring esplicito anche nel bootstrap worker |
| `ProjectMetadataAccess` per-call (1) | `projects/access_context.py` | adapter Epic C: costruzione per-chiamata per preservare i patch wholesale | conversione dei ~15 patch wholesale a DI (area D roadmap) |
| Singleton home (1) | `datasources/service.py` | strategia "wrap, don't move": `RuntimeServices` espone lo stesso oggetto per identita' | spostamento della costruzione nel composition root con migrazione dei patch |

## Default lazy nello scheduler (Epic G)

| Shim | Sede | Motivo | Test che lo protegge | Criterio di rimozione |
|---|---|---|---|---|
| `ConnectionService` lazy default | `scheduler/execution_manager.py` | G4: iniettabile, default lazy per il caller storico (`schedule_task`) | `test_execute_carica_fake_runner_contract` | `schedule_task` costruisce la manager con servizi espliciti |
| Default factory carica/orchestrator | `scheduler/execution_manager.py` | G3/G4: factory composition-root come default | idem (default = builder composition root) | n/a: e' gia' il design target |

## Engine -> verticale residuo

| Shim | Sede | Motivo | Test che lo protegge | Criterio di rimozione |
|---|---|---|---|---|
| `create_arcgis_provider` lazy import di `features.arcgis_client` | `connections/arcgis_provider_factory.py` | factory neutrale che nasconde il provider concreto ArcGIS (edge `connections -> features`, lazy-only) | baseline grafo (`test_core_dependency_matrix`: l'edge non puo' crescere) | registrazione del provider concreto al composition root (pattern `_register_multilayer_artifact_adapters`), poi l'edge cade e si rigenera la baseline |

## Singleton identity-preserving (pre-roadmap, censiti)

| Shim | Sede | Motivo | Test che lo protegge |
|---|---|---|---|
| `api_connection_service` | `arricchisci/api_connector.py` | singleton module-level storico; `RuntimeServices` lo espone per identita' (patch surface dei test preservata) | contract test identity (vedi docstring `RuntimeServices`) |
| `datasource_service` | `datasources/service.py` | idem | idem |
