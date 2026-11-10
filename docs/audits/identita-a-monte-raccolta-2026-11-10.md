# «Utente che esegue» negli Estrai a monte — raccolta (10/11/2026)

Base: `52de676f`. Richiesta dell'utente: quando l'Eseguibile è un Manipola (o un Arricchisci) che legge progetti Estrai a monte (nodi `project_source`), il filtro per utente deve stare **negli Estrai a monte** (nel database, veloce). Caso reale: Manipola «Archibus->v1GEO->Esri» legge 9 Estrai; Manipola «immobili attivi affittati/sfitti» legge 1 Estrai. Regola dell'utente: ogni istituto vede SOLO i propri dati.

Metodo: raccolta completa di **ogni punto che legge o ricalcola un progetto a monte** e di ogni percorso di esecuzione, prima di correggere. Ricerca: ogni uso di `project_source`, `LatestProjectArtifactRef`, `SourceRef.from_node_data("project_source")`, `materialize_latest`, `pipeline_dispatcher`, `_recompute_upstream_project`, `execute_pipeline` in `backend/app` e `backend/workers`; ogni chiamante di `RuntimeIdentity.*`, `resolve_identity_inputs`, `assert_identity_resolved`, `guard_upstream_recompute`; i percorsi API (`orchestrator/router`, `projects/router`, `arricchisci/router`, `eseguibili/router`), il worker (`pipeline_task`, `enrichment_task`, `carica_task`), lo scheduler.

## A. Come funziona oggi (prima della correzione)

| # | Fatto | Dove |
|---|---|---|
| A1 | Gli input «Utente che esegue» si risolvono solo sul canvas del progetto eseguito (manifest dell'Eseguibile o canvas del progetto). I progetti a monte non vengono mai risolti. | `eseguibili/runtime.resolve_runtime_inputs`, `orchestrator/runtime_inputs` |
| A2 | Un Eseguibile Manipola/Arricchisci parte con `force_recalculate=True`: **ogni** progetto a monte si ricalcola con `orchestrator.execute_pipeline(id, force_recalculate=True)` = esecuzione normale del progetto a monte (riga `results`, `output_contract`, run `cascade` in `log_runs`, run intermedia, e i suoi Carica). | `eseguibili/service.execute_versioned`, `manipola/project_source_runtime.load_project_sources`, `arricchisci/api_connection_source_loading._recompute_upstream_project` |
| A3 | Un progetto a monte con «Utente che esegue» ricalcolato così si **blocca** in `assert_identity_resolved` (nessuna identità): l'Eseguibile fallisce con «Errore caricamento sorgente upstream». | `orchestrator/service._execute_loaded_pipeline` |
| A4 | Senza ricalcolo forzato (Esegui del progetto, Pianificazione, anteprima, prova) si legge l'ultimo risultato salvato, se «aggiornato» (`results.created_at >= projects.updated_at`). Un Estrai con «Utente che esegue» non può più produrre un risultato salvato (Esegui, Pianificazioni e catene si bloccano; prova ed Eseguibile sono isolati), quindi il suo ultimo risultato è **di prima del filtro** (tutti gli istituti) e oggi è quasi sempre «non aggiornato» perché aggiungere il nodo ha cambiato il canvas. **Ma** la protezione dipende dalle date, non dalla regola. | `projects/source_freshness` |
| A5 | Buco reale: un Manipola M2 calcolato IERI leggendo l'Estrai E non filtrato, quando OGGI E riceve «Utente che esegue», resta «aggiornato» (il canvas di M2 non è cambiato). Chi legge M2 (Manipola a valle, Eseguibile, valori distinti) legge dati di tutti gli istituti senza alcun avviso. | `source_freshness` confronta solo il canvas del progetto letto |
| A6 | Valori distinti / stima del Manipola (`CanvasDataPreparation`), sorgente REST_FILE di Arricchisci, caricamento della richiesta di stima di Arricchisci leggono **sempre** l'ultimo risultato salvato, senza controllo di data né di identità. | `manipola/canvas_data_preparation`, `arricchisci/api_connection_rest_file`, `arricchisci/source_request_loader` |
| A7 | L'esecuzione di prova di un Manipola SENZA un proprio nodo «Utente che esegue» non esiste (`ProjectTestRunNotApplicable`): dall'editor si può solo «Esegui», che legge il risultato salvato dell'Estrai (A4/A5). | `orchestrator/runtime_inputs.apply_test_run_inputs`, frontend `PipelineRunner` (decide la prova dai nodi del canvas) |
| A8 | Il manifest pubblicato è uno snapshot del Manipola; gli Estrai a monte sono vivi (si legge sempre il loro stato attuale). L'avviso N1 confronta solo il canvas del Manipola. Se a monte si **toglie** «Utente che esegue», l'Eseguibile allarga i dati senza alcun avviso. | `eseguibili/service.source_status`, `source_changed` |
| A9 | Nuovo Estrai con «Utente che esegue» mai eseguito prima: non ha `output_contract` (lo scrive solo un'esecuzione che salva) e quindi un Manipola non può usarlo come sorgente (schema delle colonne). Difetto preesistente, fuori da questa correzione (punto aperto). | `projects/schema_inference._infer_project_source_schema` |

## B. Percorsi di esecuzione: cosa deve succedere

Legenda dell'esito atteso: **I** = gli Estrai a monte con identità si ricalcolano isolati con la stessa identità; **S** = come oggi (risultato salvato o ricalcolo normale), solo se a monte non c'è identità; **B** = blocco con un messaggio chiaro se a monte (anche indirettamente) c'è «Utente che esegue»; **—** = non legge progetti a monte.

| # | Percorso | Identità | Oggi con un Estrai a monte con identità | Atteso |
|---|---|---|---|---|
| B1 | `POST /eseguibili/{id}/runs` (avvio Eseguibile Manipola) | `authenticated` | ricalcolo normale → blocco A3 | **I** (+ snapshot U7) |
| B2 | `POST /eseguibili/{id}/preflight` (stima, impronta) | `authenticated` | impronta senza gli input a monte | **I** pianificato (stessi errori dell'avvio), impronta con i valori a monte |
| B3 | `POST /eseguibili/{id}/runs` Arricchisci che legge un Estrai con identità | `authenticated` | blocco A3 | **B** dichiarato (ricalcolo isolato non supportato in Arricchisci) |
| B4 | Eseguibile Estrai | `authenticated` | — | — |
| B5 | `POST /pipeline/{id}/test-run` Manipola con proprio nodo identità | `test_run` | legge il salvato (A4) o si ferma | **I** con lo username di prova |
| B6 | `POST /pipeline/{id}/test-run` Manipola SENZA proprio nodo, Estrai a monte con identità | `test_run` | 400 «non applicabile» | **I** con lo username di prova degli Estrai (uno solo) |
| B7 | `/test-run/enrich` (prova di Arricchisci) | `test_run` | legge il salvato | **B** |
| B8 | `GET /pipeline/{id}/preview` (anteprima Manipola) | `preview` | legge il salvato o «non aggiornato» | **I** con lo username di prova (mai per un risultato salvato) |
| B9 | `POST /pipeline/{id}/execute` (Esegui del progetto Manipola) | `project_run` | ricalcolo/salvato, poi A3/A4 | **B** subito all'avvio (400), e di nuovo nel worker |
| B10 | Pianificazione (scheduler → `execute_pipeline`) | nessuna | come B9 | **B** nel worker |
| B11 | Catena: ricalcolo a monte di un Manipola (`pipeline_dispatcher`) | nessuna | come B9 | **B** |
| B12 | Carica (`carica_task` ricalcola la pipeline dal contratto) | nessuna | come B9 | **B** |
| B13 | Worker senza contratto (`POST /tasks`) | nessuna | come B9 | **B** |
| B14 | Arricchisci: esecuzione, `enrich-async`, preflight con `project_source` | `project_run` | ricalcolo o salvato | **B** |
| B15 | Valori distinti / stima del Manipola (`CanvasDataPreparation`) | — | legge il salvato (A6) | **B** come «non disponibile» (nessun dato non filtrato) |
| B16 | Arricchisci REST_FILE con `project_source` | — | legge il salvato (A6) | **B** |
| B17 | Richiesta di validazione/stima di Arricchisci con `project_id` | — | legge il salvato (A6) | **B** |
| B18 | Valida del progetto (`/validate`) | `preview_of` | non legge dati a monte | — (invariato) |
| B19 | Carica del progetto (legge il PROPRIO ultimo risultato), pubblicazione Feature Service (`features/routes`) | — | proprio progetto | — (invariato: non è un progetto a monte) |
| B20 | Manipola a valle di un Manipola che legge un Estrai con identità (transitivo, A5) | qualsiasi | salvato «aggiornato» non filtrato | come la riga del percorso: **I** se c'è identità, **B** altrimenti |
| B21 | Estrai a monte SENZA identità | qualsiasi | salvato / ricalcolo | **S** (invariato, anche il ricalcolo forzato dell'Eseguibile A2) |
| B22 | Dentro un ricalcolo isolato, le altre sorgenti del Manipola a monte | — | — | risultato salvato; mai un ricalcolo che scrive (se manca o non è aggiornato: si ferma con «Esegui prima il progetto a monte») |
| B23 | Arricchisci a monte con identità (anche indiretta) dentro I | — | — | **B** dichiarato (non ricalcolato isolato) |
| B24 | Controllo dei conflitti di Carica con `project_id` (`carica/conflict_request_loader`) | — | legge il salvato del progetto | **B** come risposta «nessun controllo» con il motivo (trovato dal test di enumerazione, non dalla prima ricerca) |

## C. Manifest, impronta, avvisi

| # | Punto | Oggi | Atteso |
|---|---|---|---|
| C1 | Form dell'Eseguibile («Prima di avviare») | conosce solo gli input del manifest | riceve gli input a monte VIVI dal dettaglio (`upstream_identity_inputs`): riga in sola lettura, campo facoltativo per l'amministratore se l'input a monte ha `admin_override` |
| C2 | Valore scelto dall'admin per un input a monte | non esiste | chiave `"<id progetto a monte>::<chiave input>"`, stessa regola `may_choose` (admin/superadmin dello stesso gruppo dell'Eseguibile + `admin_override` del nodo a monte), stesso audit |
| C3 | Impronta della stima (`preparation_fingerprint`) | input + identità dell'Eseguibile | anche i valori degli input a monte e i canvas risolti a monte (un Estrai cambiato tra stima e avvio → 409) |
| C4 | Snapshot alla pubblicazione | nessuno | `manifest.upstream_identity`: gli input a monte con identità al momento della pubblicazione |
| C5 | Input a monte tolto dopo la pubblicazione | allarga i dati in silenzio (A8) | avvio e stima si **bloccano** finché non si ripubblica; N1 (`source_status`) dice «cambiato» con la riga `upstream_identity_removed` |
| C6 | Input a monte aggiunto dopo la pubblicazione | — | si applica subito (restringe soltanto); nessun avviso |
| C7 | Altre modifiche di un Estrai a monte | lette vive | lette vive (come il risultato salvato di oggi): N1 non le confronta, perché l'Eseguibile non ha mai uno snapshot degli Estrai a monte |
| C8 | Editor del Manipola: quando «Esegui» diventa la prova | solo con un proprio nodo identità | anche con un Estrai a monte con identità (annotazione transitoria `upstream_identity` sul nodo `project_source`, come `source_freshness`) |

## D. Prestazioni

- Solo i progetti a monte con identità (diretta o indiretta) si rieseguono; gli altri si leggono dal risultato salvato (o dal ricalcolo normale, come oggi).
- La chiusura transitiva legge una volta il canvas di ogni progetto a monte (una query per progetto, con memoria per esecuzione). Il ricalcolo isolato tiene il risultato **in memoria** (nessun file nello storage, nessuna riga): con il filtro per istituto il risultato è piccolo.

## E. Verdetto della raccolta

Disegno fattibile senza cambiare il modello dati (nessuna migrazione): campo nuovo del contratto d'esecuzione (`isolated_upstreams`), campo nuovo del manifest (`upstream_identity`), una porta unica nel modulo `runtime_identity`. Rischi dichiarati e ridotti: (1) Arricchisci come lettore o come progetto a monte con identità resta bloccato (B3, B7, B14, B23); (2) il ricalcolo isolato tiene il risultato in memoria (limite: memoria del worker, come il caricamento del risultato salvato); (3) A9 resta aperto. Si procede con la correzione unica.
