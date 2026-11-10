# Dashboard Grafana degli Eseguibili — raccolta (11/11/2026)

Base: `60bd489a`. Richiesta dell'utente: una «vista dashboard Grafana» del risultato di un Eseguibile, una dashboard per Eseguibile, modificabile da chi può modificare l'Eseguibile, aperta in un iframe dalla finestra dei risultati con i dati della SOLA esecuzione corrente (max 30 minuti, max 50.000 righe), accesso a `/grafana` solo per utenti Plenora.

Metodo: raccolta completa **senza correggere** di ogni percorso che pubblica, ripubblica, elimina, esegue, scrive, legge o cancella l'output di un Eseguibile, più i percorsi nuovi (lasciapassare, forward_auth, sincronizzazione Grafana). Per ogni percorso: com'è oggi e cosa deve succedere. Ricerca: ogni chiamata di `app/core/eseguibili/storage.py` e `output_store.py` in `backend/app` e `workers`; ogni costruzione di `ExecutableOutputPersistence`; ogni `execution_subject` di tipo `eseguibile`; ogni endpoint di `eseguibili/router.py` e `orchestrator/router.py` che tocca un task di Eseguibile; il beat; il frontend (`api/tasks.js`, `EsecutoreHome.jsx`). Riferimento funzionante: lo spike `grafana-spike/setup_grafana.py` (`build_dashboard`, cartelle, team, permessi) e `mock_plenora.py` (HMAC, forward_auth).

Legenda dell'esito atteso: **C** = scrive la copia per la dashboard, **N** = nessuna copia, **K** = conserva la copia, **X** = cancella la copia, **L** = legge la copia (solo con lasciapassare).

## A. Pubblicazione, ripubblicazione, eliminazione

| # | Percorso | Oggi | Deve succedere |
|---|---|---|---|
| A1 | `POST /eseguibili` (`create_eseguibile`, `require_admin`) | crea riga + versione | campo nuovo `create_dashboard` (default false). Con `GRAFANA_ENABLED=false` → 400 `grafana_disabled` PRIMA di creare (messaggio chiaro). Con true: dopo la creazione genera la dashboard di partenza; se Grafana non risponde l'Eseguibile resta creato e la risposta porta un avviso in `dashboard_warnings` (si riprova con A5) |
| A2 | `POST /eseguibili/{id}/versions` (ripubblica) | nuova versione | non tocca la dashboard; il dettaglio restituito porta `dashboard_warnings` per le colonne usate dalla dashboard che non sono più tra le colonne attese |
| A3 | `POST /eseguibili/{id}/versions/{v}/activate` | cambia versione attiva | come A2 (stesso dettaglio) |
| A4 | `DELETE /eseguibili/{id}` | cancella la riga (CASCADE sulle versioni) | elimina anche la dashboard in Grafana, best effort: Grafana giù o errore → log, l'eliminazione non si blocca |
| A5 | nuovo: crea dashboard per un Eseguibile esistente | — | `require_admin` + `assert_can_modify`; 409 se c'è già; 400 se `GRAFANA_ENABLED=false`; 503 se Grafana non risponde (nessun `dashboard_uid` scritto) |
| A6 | nuovo: elimina dashboard | — | `require_admin` + `assert_can_modify`; Grafana 404 = già eliminata; Grafana giù → 503 e `dashboard_uid` resta (si riprova) |
| A7 | nuovo: «Modifica in Grafana» | — | stessa regola; sincronizza team/cartelle e restituisce il link di modifica |
| A8 | `PUT /eseguibili/{id}` (metadati) | aggiorna nome ecc. | non tocca la dashboard (il titolo in Grafana resta quello generato; si cambia in Grafana) |
| A9 | eliminazione di un gruppo (`db/group_cleanup`) | cancella le righe SQL degli Eseguibili del gruppo | nessuna chiamata a Grafana (dashboard, cartella e team del gruppo restano): **punto aperto** dichiarato |
| A10 | lista/dettaglio (`GET /eseguibili`, `GET /eseguibili/{id}`) | — | `has_dashboard` a tutti (bool), `dashboard_uid` solo a chi può modificare (`compute_can_edit`), `dashboard_warnings` solo nel dettaglio e solo a chi può modificare (richiede di leggere la dashboard da Grafana: la lista resta senza chiamate di rete); `dashboard_feature_enabled` = `GRAFANA_ENABLED` |

Colonne «attese» (per A1, A2, A3, A10): non c'è uno schema dell'output nella versione. La fonte è l'`output_contract` del progetto sorgente (`source_canvas_id`), cioè l'ultimo schema pubblicato dal progetto: è la stessa fonte per la dashboard di partenza e per gli avvisi. Senza contratto: dashboard con la sola tabella (colonne scoperte da Infinity) e statistica, nessun avviso (non si sa).

## B. Avvio (chi decide la copia)

| # | Percorso | Contratto | Deve succedere |
|---|---|---|---|
| B1 | `POST /eseguibili/{id}/runs` → `service.execute_versioned`, arricchisci | `execution_subject {kind: eseguibile}` → `create_enrichment_task` | il SERVER scrive `execution_subject.dashboard = true` se l'Eseguibile ha `dashboard_uid` e `GRAFANA_ENABLED`; mai dal client (il `runtime_context` del client non vale: `build_runtime_context` toglie la chiave e la rimette solo dal soggetto) |
| B2 | idem, estrai/manipola → `create_pipeline_task` | stesso soggetto | come B1 |
| B3 | `POST /eseguibili/{id}/execute` (v1) → `service.execute` | soggetto `{kind, id}` | come B1 (stessa funzione che costruisce il soggetto) |
| B4 | `POST /eseguibili/{id}/preflight` | contratto di stima, nessun task | N (nessuna esecuzione) |
| B5 | «Esegui di prova» dall'editor (`test_run`) | soggetto `test_run` | N (non è un Eseguibile) |
| B6 | Pianificazioni, catene, «Esegui» del progetto | nessun soggetto Eseguibile | N |

## C. Esecuzione: dove nasce l'output (per tipo di progetto e modalità)

Punto unico dei DataFrame: `ExecutableOutputPersistence.persist_dataframe_output` (`eseguibili/output_persistence.py`). Punto unico dello streaming: `ExecutableOutputPersistence.persist_streaming_query_to_storage`.

| # | Percorso | Come scrive | Deve succedere |
|---|---|---|---|
| C1 | Estrai, query in streaming (csv/parquet/xlsx senza geometria) — `query_executor.py` → `persist_streaming_query_to_storage` → `query_streaming.execute_streaming_to_writer` | cursore → writer a byte, nessun DataFrame | C con «tee» sul cursore: le prime 50.000 righe passano anche al collettore (le righe dopo il limite si contano soltanto) |
| C2 | Estrai, non streaming (geojson/gpkg/shp/json o con geometria) → `persist_dataframe_result` → `persist_dataframe_output` | DataFrame | C (prime 50.000 righe del DataFrame) |
| C3 | Manipola → `manipola/executor.py` → `output_persistence.persist_dataframe_output` | DataFrame | C |
| C4 | Features (ArcGIS) in un Eseguibile → `features_service.py` → `persist_dataframe_output` | DataFrame | C |
| C5 | Arricchisci (DataFrame) → `enrichment_run_mode.EseguibileRunMode.persist_dataframe_result` → `ExecutableOutputPersistence(...).persist_dataframe_output` | DataFrame | C: la run-mode riceve il flag dal contesto del contratto |
| C6 | Arricchisci con `rest_file` finale (zip) → `enrichment_task.py` `eseg_storage.save_bytes` + artifact interno `__internal__rest-file__…` (`api_connection_rest_file.py`) | bytes del connettore | N: nessuna copia, «Apri dashboard» non disponibile (il dettaglio ha già `output_is_file_based`; il lasciapassare risponde 410) |
| C7 | Estrai a monte ricalcolato in isolamento (`MemoryOutputPersistence`) | in memoria | N (non è l'output dell'Eseguibile) |
| C8 | `pipeline_task._persist_executable_output` (compatibilità: risultato con `result_id` copiato dal result store) | copia di byte | N (percorso dei risultati prodotti prima dell'adattatore diretto; le esecuzioni nuove non ci passano) |
| C9 | Esecuzione fallita o annullata | nessun output | N; un writer abortito non lascia copia (la copia si scrive dopo il `finalize` dell'output) |
| C10 | DataFrame vuoto | output con solo intestazione | C con array vuoto `[]` (la dashboard mostra 0 righe) |

Formato della copia: JSON, array di oggetti `{colonna: valore}` così come lo serve l'endpoint dati (nessuna rielaborazione alla lettura), valori resi JSON (date ISO, NaN → null, Decimal → numero o testo, geometrie → testo WKT tronco o omesse: la colonna geometrica si omette). Limite: 50.000 righe (`DASHBOARD_DATA_MAX_ROWS`) e un limite in byte (`DASHBOARD_DATA_MAX_BYTES`) oltre il quale le righe si fermano.

## D. Storage del task: chi legge e chi cancella

Cartella del task: `tenants/<g>/eseguibili-outputs/<task_id>/`. Oggi contiene l'output, `__owner.json` e, per rest_file, l'artifact interno.

| # | Percorso | Oggi | Deve succedere |
|---|---|---|---|
| D1 | download `GET /eseguibili/{id}/download/{task}` → `build_consume_once_response` → `delete_task_outputs(retain_owner=True)` | cancella TUTTO tranne `__owner.json` (anche gli artifact interni) | K: cancella l'output e gli artifact interni di lavoro, conserva owner e copia per la dashboard |
| D2 | download `GET /tasks/{id}/download` (ramo Eseguibile) → stesso `build_consume_once_response` | come D1 | K (stessa funzione) |
| D3 | `DELETE /tasks/{id}/full-cleanup` → `task_cleanup.purge_terminal_task` → `delete_task_outputs()` | cancella tutto, owner compreso | X |
| D4 | «scarto» del frontend alla chiusura della finestra / uscita dalla pagina (`api/tasks.js` `releaseTaskOnPageExit`, `cleanupTaskCompletely`) | la stessa `DELETE /tasks/{id}/full-cleanup` (anche `sendBeacon`/keepalive) | X (stesso percorso D3) |
| D5 | pulizia richiesta su un task in corso (`finish_requested_cleanup`, worker o beat sui worker morti) | `purge_terminal_task` | X (stesso percorso) |
| D6 | `find_output_keys` / `select_output_key` (download) | esclude owner e artifact interni | la copia è un artifact interno: mai «Output ambiguo» |
| D7 | `execute`/`runs` falliti all'avvio → `delete_task_owner` | toglie solo l'owner | nessuna copia esiste ancora |
| D8 | beat `_cleanup_eseguibili_outputs` (ogni 30 min) | solo residui locali + lease di download | X delle copie SCADUTE (oltre 30 min) nello storage, per ogni gruppo: il limite di 30 minuti vale anche fisicamente, non solo all'accesso |
| D9 | lifecycle del bucket (`plenora-retention=ephemeral`, 7 giorni) | rimuove gli oggetti effimeri | la copia è taggata effimera come l'output (ultima rete) |
| D10 | `storage.delete_by_task`, `storage.open_stream` | nessun chiamante fuori dallo storage / owner | invariati; il test di enumerazione dichiara ogni chiamante |
| D11 | `api_connection_rest_file.py` legge l'artifact interno rest_file (`open_task_stream`) | lettura interna | invariato; non deve mai leggere la copia della dashboard |

Scelta (vincolo A della richiesta): **artifact interno nella cartella del task** (`storage.internal_artifact_filename("dashboard", "dati", "scade-<epoch>.json")`), non un dominio storage nuovo: eredita tenant, tag effimero, cancellazione di `full-cleanup` e l'esclusione dal download senza toccare `storage_layout`. La scadenza (epoch, = scrittura + 30 min) sta nel nome: nessun orologio dello storage, nessun secondo oggetto.

## E. Lasciapassare

| # | Punto | Deve succedere |
|---|---|---|
| E1 | emissione `POST /eseguibili/{id}/dashboard/pass {task_id}` | solo chi ha lanciato l'esecuzione (`owner_meta.user_id`, superadmin compreso come oggi per il download), Eseguibile visibile, con dashboard, `GRAFANA_ENABLED`; la copia deve esistere e non essere scaduta (410 altrimenti) |
| E2 | contenuto | HMAC-SHA256 con chiave SEPARATA `DASHBOARD_PASS_SECRET` (mai `JWT_SECRET_KEY`): `typ`, task_id, eseguibile_id, user_id, `exp` |
| E3 | scadenza | min(adesso + 30 min, scadenza della copia); rinnovabile richiamando E1 finché la finestra è aperta (non oltre la scadenza della copia) |
| E4 | non è un login | un lasciapassare non decodifica come JWT di login; un JWT di login non vale come lasciapassare; il lasciapassare non vale come cookie di sessione Grafana e viceversa (separazione di dominio nella firma + `typ`) |
| E5 | iframe | `/grafana/d/<uid>?orgId=1&kiosk&var-exec=<task_id>&var-pass=<pass>` |

## F. Endpoint dati per Infinity

| # | Punto | Deve succedere |
|---|---|---|
| F1 | `GET /api/dash/exec/{task_id}/rows?pass=…` (prefisso reale: le route stanno sotto `/api`, `app_factory.include_router(api_router, prefix="/api")`; porta 5004 nativa) | chiamato da Grafana lato server; nessun Bearer |
| F2 | intestazione `X-Grafana-Secret` | obbligatoria (`GRAFANA_DATA_SECRET`), confronto a tempo costante; assente/errata → 401. NON decide l'accesso: decide SOLO il lasciapassare |
| F3 | lasciapassare | firma, `typ`, scadenza, `task_id` = quello del path; utente ancora attivo e non eliminato; owner del task ancora uguale; altrimenti 401 |
| F4 | path | `task_id` solo `[A-Za-z0-9_-]`, rifiuta `..` → 400 |
| F5 | dati | lettura NON distruttiva; JSON array di oggetti; max 50.000 righe; 410 se la copia non c'è più (download non la tocca, full-cleanup sì, scaduta sì) |
| F6 | frequenza | limite semplice per task (`DASHBOARD_DATA_RATE_PER_MINUTE`) → 429 |
| F7 | datasource | uid fisso `plenora-infinity` (lo crea l'installer), `allowedHosts` = `http://127.0.0.1:5004/api/dash/` (percorso completo con `/` finale) |

## G. Accesso a `/grafana` (Caddy `forward_auth`)

| # | Punto | Deve succedere |
|---|---|---|
| G1 | `POST /api/grafana/session` (Bearer) | cookie dedicato HttpOnly, SameSite=Strict, Path=/grafana, firmato (non il JWT), durata breve (`GRAFANA_SESSION_TTL_SECONDS`) rinnovabile richiamando l'endpoint; `Secure` se la richiesta arriva in https |
| G2 | `GET /api/grafana/auth` (forward_auth) | legge il cookie: 200 + `X-WEBAUTH-USER: <username>`; superadmin anche `X-WEBAUTH-ROLE: Admin`; gli altri NESSUN ruolo (Grafana li crea Viewer); 401 se cookie assente, malformato, scaduto, utente disattivato o eliminato (lapide: `get_user` non la vede) |
| G3 | `DELETE /api/grafana/session` | toglie il cookie (logout) |
| G4 | ruolo che cambia | il nome e il ruolo si rileggono dal DB a ogni richiesta di G2: niente ruolo «congelato» nel cookie |

## H. Sincronizzazione Grafana (team e cartelle)

| # | Punto | Deve succedere |
|---|---|---|
| H1 | cartella per gruppo proprietario | `plenora-g-<gruppo>`: Viewer = View, team `admin-<group_id>` = Edit |
| H2 | stessa regola di modifica dell'Eseguibile | un admin del gruppo NON modifica un Eseguibile creato da un superadmin (`assert_can_modify`): la sua dashboard va in una seconda cartella del gruppo `plenora-g-<gruppo>-riservata` (Viewer = View, nessun team). La cartella si decide con `compute_can_edit` per un admin del gruppo: la stessa funzione, nessuna regola copiata |
| H3 | membri del team | admin attivi (non eliminati) del gruppo; si aggiungono i mancanti, si tolgono gli altri (utente che cambia ruolo o gruppo, disattivato o eliminato: tolto alla sincronizzazione successiva) |
| H4 | utente mai entrato in Grafana | Grafana non lo conosce ancora: la sincronizzazione lo crea con una richiesta auth-proxy (`X-WEBAUTH-USER`) prima di aggiungerlo al team |
| H5 | superadmin retrocesso | Grafana conserva il ruolo Admin (senza intestazione di ruolo non lo cambia): la sincronizzazione riporta a Viewer chi in Plenora non è più superadmin |
| H6 | quando | creazione della dashboard (A1, A5) e «Modifica in Grafana» (A7); idempotente |
| H7 | `GRAFANA_ENABLED=false` | nessuna chiamata a Grafana in nessun percorso (creazione rifiutata, eliminazione dell'Eseguibile senza chiamate, lasciapassare e link rifiutati) |

## I. Schema, configurazione, dipendenze

| # | Punto | Deve succedere |
|---|---|---|
| I1 | DB | migrazione additiva `2026-11-11_eseguibili_dashboard.sql` (`dashboard_uid TEXT`, nullable); `EseguibileRow`/`EseguibileSummaryRow`, SELECT della lista, `sql_safety` |
| I2 | segreti | `.env`: `DASHBOARD_PASS_SECRET`, `GRAFANA_DATA_SECRET`, `GRAFANA_SERVICE_TOKEN` (letti con `get_env`, nessun default) |
| I3 | resto | `config.py` DEFAULTS: `GRAFANA_ENABLED` (false), `GRAFANA_URL`, percorso pubblico, URL dei dati visto da Grafana, uid del datasource, timeout, TTL sessione, TTL copia/lasciapassare, limiti righe/byte, frequenza |
| I4 | moduli | `app/core/grafana/` foglia (client, generatore, lasciapassare e sessione firmati, sincronizzazione): non importa domini del grande ciclo, così `eseguibili → grafana` e `auth → grafana` non creano cicli; gli endpoint stanno in `eseguibili/dashboard_router.py` e `auth/grafana_router.py` |
