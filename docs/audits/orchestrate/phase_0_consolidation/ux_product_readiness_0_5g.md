# Phase 0.5g — UX / Product Readiness

Status: **analysis only — no code change, no test, no benchmark.**
Scope: valutazione prodotto dal punto di vista dei flussi utente reali.
Per ogni flusso: percorso utente, frontend/backend coinvolti, stati,
messaggi, osservabilità, friction points, rischi product-grade,
quick wins, interventi più grandi, smoke suggeriti.

Read together with:
- `frontend_wide_audit_0_5b.md` (componenti FE evidence-deep)
- `hot_file_deep_dive_0_5a.md` (file BE)
- `deploy_ops_migration_design_0_5e.md` (ops)
- `performance_baseline_0_5f.md` (perf evidenza)
- `BUG.md` (incidenti chiusi recenti)

Distinzione operativa per ogni finding:
- **bug** = comportamento errato/incompleto rispetto al contratto
- **friction** = comportamento corretto ma sub-ottimale per l'utente
- **debt** = problema strutturale/manutenibilità che incide sul prodotto

---

## 0. Mappa flussi utente principali

| Flusso | Route principale | Pagina FE | Routers BE coinvolti |
| --- | --- | --- | --- |
| F1. Login + auth | `/login` | `LoginPage` | `auth/auth.py` (`/login`, `/logout`, `/me`, MFA) |
| F2. Dashboard / lista progetti | `/` | `ProjectList` | `projects`, `connections`, `features`, `files`, `schedules`, `datasources`, `eseguibili` |
| F3. Editor canvas (Estrai/Manipola/Arricchisci) | `/project/:id`, `/editor/manipola/:id`, `/arricchisci/:projectId` | `EstraiEditor`, `ManipolaEditor`, `ArricchisciEditor` | `projects/{id}/canvas`, `projects/{id}/schemas`, `manipola`, `estrai`, `arricchisci` |
| F4. Esecuzione pipeline | (button dentro F3) | `ResultNodeStandardView` + `ExecutionModal` | `orchestrator` (`pipeline_router.execute`, `tasks_router`), `arricchisci/enrich-async` |
| F5. Run history + intermediate data + compare | run history modal | `RunHistoryModal`, `PreviewModal` | `intermediate` (`/{canvas_id}`, `/{run_id}/nodes/{node_id}/data`, `/compare/l1`, `/compare/l2`, `/{run_id}/pin`) |
| F6. File manager / file sources | `/files` | `FileManager` | `files` (8 endpoint) |
| F7. API connection / connettori esterni | `/external-connectors` | `ApiConnectionManager` | `arricchisci/api-connections/*`, `templates`, `arricchisci/validate`, `arricchisci/estimate` |
| F8. Risultati / download | tasks page + result node | `ResultNode*View` | `tasks_router/{task_id}/download`, `pipeline_router/{project_id}/download` |
| F9. Eseguibili (esecutore) | `/eseguibili`, `/run` | `EseguibiliList`, `EsecutoreHome`, `EseguibileExecuteWizard` | `eseguibili` (analyze-upload, execute, download) |
| F10. Schedule + log | `/schedules`, `/logs` | `ScheduleList`, `LogViewer` | `scheduler`, `logs/runs_router` |
| F11. Connections (DB / ArcGIS) | `/connections` | `ConnectionManager` | `connections`, `features` (ArcGIS) |
| F12. DataSources (cataloghi) | `/datasources` | `DataSourceManager` | `datasources` |
| F13. Tickets | `/tickets`, `/tickets/:id` | `TicketsList`, `TicketDetail` | `tickets` |
| F14. Admin: users, backups, system-status | `/admin/users`, `/admin/backups`, `/admin/system-status` | `UserManager`, `AdminBackupsPage`, `AdminSystemStatusPage` | `auth/users`, `auth/groups`, `admin_backups`, `admin_system_status` |
| F15. IMDF publish flow | dentro F3 (arricchisci) | nodi `imdf/*`, modali `ImdfBundlesManager`, `ImdfManifest`, `ImdfPreview`, `ImdfValidatePublish`, `ImdfValidation` | `imdf` (full router) |

---

## 1. F1 — Login + auth

### 1.1 Percorso utente
1. Utente arriva su `/anything-protected` → `ProtectedRoute` redirige a `/login`.
2. Inserisce username + password (+ MFA TOTP se abilitato).
3. POST `/login` → JWT in localStorage → redirect a `/` (o role-based su `/run` per esecutore).

### 1.2 Componenti / endpoint
- FE: `LoginPage.jsx`, `ChangePasswordModal.jsx`, `AuthContext`
- BE: `auth/auth.py` — `/login` (688), `/logout` (756), `/me` (811), `/change-password` (820), `/mfa/*` (847–885)

### 1.3 Stati
- Happy: token + redirect.
- Loading: bottone "Accedi" disabilitato durante POST.
- Empty: n.a. (form sempre presente).
- Error: 401 → "Credenziali errate"; 403 user disabilitato; rate-limit per IP (login_rate_limit_per_ip test contract).

### 1.4 Messaggi utente
- "Credenziali non valide" (401)
- MFA: "Codice errato"
- Lockout post N tentativi: rate-limit risponde 429 (?). **Da verificare**: il messaggio comunica al user "riprova fra X secondi" oppure è generico?

### 1.5 Osservabilità
- `auth_enumeration_contract` test guard: nessun leak su user-not-exist vs password-wrong.
- `audit_action_coverage` test contract.
- Logger: `[GESTITO]` su login fail, `[CRASH]` su crash interno.

### 1.6 Friction
- **(friction)** Nessun "Password dimenticata / reset link" — solo admin può reset (`/users/{user_id}/reset-password`, 1042). User standard è bloccato out se l'admin non è raggiungibile.
- **(friction)** MFA enroll richiede QR rendering → da verificare che `mfa/enroll` ritorni stringa otpauth e che `LoginPage` la renderizzi correttamente.

### 1.7 Rischi product-grade
- **bug factor 1 ammesso** (vedi 0.5e, `incident_response.md`): se admin perde MFA, nessuno può sbloccare → recovery procedure manuale via DB.
- JWT in localStorage: vulnerabile a XSS (debt, mitigazione tramite CSP + cookie httpOnly è una proposta architetturale futura).

### 1.8 Quick wins
- **W1.1** Messaggio dedicato per rate-limit con countdown.
- **W1.2** Documentare in `incident_response.md` la procedura di recovery MFA admin.

### 1.9 Interventi più grandi
- **L1.1** Migrazione JWT → httpOnly cookie + CSRF token. Effort medio, sblocca security posture.
- **L1.2** Self-service password reset via email. Richiede SMTP config + token table.

### 1.10 Smoke
- Login ok / fail credenziali / 5 fail consecutivi (rate-limit) / login con MFA on-off / logout invalida token (jwt_logout_blacklist contract).

---

## 2. F2 — Dashboard / lista progetti (ProjectList)

### 2.1 Percorso utente
1. Utente arriva su `/` autenticato.
2. `loadData` Promise.all 8 endpoint (vedi `ProjectList.jsx:96`).
3. Vede 4 sezioni: Estrai, Manipola, Arricchisci, Eseguibili + stats row.
4. Click su card → editor; click "+" → modal nuovo progetto.

### 2.2 Componenti / endpoint
- FE: `ProjectList.jsx`, `ProjectSection`, `ProjectCard`, `EseguibileCard`, `PublishEseguibileModal`, `EseguibileExecuteWizard`, `AppSidebar`, `ModalWrapper`
- BE: `projects` (`GET ""`, `POST ""`, `DELETE /{id}`), `connections`, `features` (ArcGIS), `files`, `schedules`, `datasources`, `eseguibili`

### 2.3 Stati
- Happy: 4 sezioni popolate, stats row corretto.
- Loading: full-page "⬡ Caricamento...".
- Empty per sezione: empty state dedicato per ognuna (estrai/manipola/arricchisci/eseguibili).
- Error: `loadData` ha `.catch(() => default)` per ogni endpoint → 1 endpoint down NON rompe la dashboard. **Best practice del file** (vedi 0.5b §7).

### 2.4 Messaggi utente
- "Errore caricamento dati" (toast generico, line 130) — **friction**: non distingue quale endpoint è down.
- Cascade 409 su delete project: toast con dipendenze formattate (line 163, `formatCascadeDependencies`).
- 500 `project_cleanup_partial`: toast con messaggio operativo `Ritenta o contatta admin` (line 178).
- Validation modal: "Inserisci un nome", "Seleziona una connessione".

### 2.5 Osservabilità
- Backend: log strutturato per ogni endpoint chiamato.
- Frontend: nessuna telemetria (web vitals assenti, vedi 0.5f §4).

### 2.6 Friction
- **(friction)** TTI bloccato fino al più lento dei 8 endpoint paralleli (vedi 0.5b §7.4).
- **(friction)** `confirm(...)` browser native su delete (line 154) vs `useConfirmModal` di FileManager — UX mista cross-page.
- **(friction)** "Errore caricamento dati" non identifica l'endpoint failing → impossibile per l'utente capire se il backup non è raggiungibile vs se i progetti sono down.

### 2.7 Rischi product-grade
- **(debt)** Nessun pulsante "Refresh" dedicato visibile? C'è (line 304), ma il pattern è "Aggiorna" senza spinner durante load.
- **(bug latente)** `useEmptyHexCanvases` hook al re-render senza cleanup chiaro (linee 818–853, vedi 0.5b §7.4) — sospetto re-paint cyclic.
- **(friction)** Hash navigation `/#estrai` con setTimeout 50ms (line 86–90): brittle se DOM lento.

### 2.8 Quick wins
- **W2.1** Replace `confirm()` (line 154) con `useConfirmModal` per consistency (~1h, 0.5b suggested).
- **W2.2** Toast specifico per endpoint failing (es. "Schedule non disponibili, prosegue lettura progetti").
- **W2.3** Loading state granulare per sezione (skeleton cards).

### 2.9 Interventi più grandi
- **L2.1** Web vitals + Sentry/lighthouse su questa pagina come dashboard di partenza (vedi 0.5f §5.2).

### 2.10 Smoke
- Mount con utente esecutore (route → /run, non /). Mount con admin / superadmin / utente standard. Endpoint singolo failing → fallback graceful. Cascade 409 con dipendenze. 500 project_cleanup_partial.

---

## 3. F3 — Editor canvas (Estrai/Manipola/Arricchisci)

### 3.1 Percorso utente
1. Click su card progetto → route specifica per type.
2. Canvas ReactFlow render. Sidebar nodi disponibili. Pannello config a destra.
3. User collega nodi, edita parametri, salva (PUT canvas).
4. Click "Esegui" → trigger F4.

### 3.2 Componenti / endpoint
- FE: `EstraiEditor.jsx`, `ManipolaEditor.jsx`, `ArricchisciEditor.jsx`, `useCanvasStore`, `useCanvasCore`, `useCanvasKeyboard`, `useCanvasValidation`, `useEdgeHandlers`, `useNodeHandlers`, `useEditorData`, vasta serie di nodi (`api_enrich`, `result`, `manipola/*`, `estrai/*`)
- BE: `projects/{id}` (337), `projects/{id}/canvas` PUT (512), `projects/{id}/schemas` (761), `projects/{id}/validate-connection` (789), `projects/{id}/validate-all` (808)

### 3.3 Stati
- Happy: canvas caricato, save automatico via `handleSaveQuiet` quando isDirty.
- Loading: "⬡ Caricamento progetto..." finché GET project ritorna.
- Empty: canvas nuovo → ReactFlow vuoto + sidebar.
- Error: GET project 404 → redirect / toast; PUT canvas fail → toast "Errore salvataggio" (probabile, da verificare).

### 3.4 Messaggi utente
- Validazione canvas: la `useCanvasValidation` derivare invariants. Es. cycle detection (`canvasAccessControl`, `validateArricchisciCanvas`, `validateEstraiCanvas`, `validateManipolaCanvas`).
- Toast su error.
- Validation status nei nodi (es. `ApiEnrichNode validation` linee 343–398: `Sorgente non accessibile`, `Nessuna API configurata`, `Collega un File Source`, `Mappa: x, y, z`).

### 3.5 Osservabilità
- `isDirty` flag in store → previene PUT canvas inutili.
- Read-only mode: `isReadOnly = !can.editCanvas(user)` → store no-op su mutazioni.
- Logger backend per ogni POST canvas update.

### 3.6 Friction
- **(friction)** `confirm()` browser su delete nodo (probabile in alcuni handler) vs modale custom.
- **(friction)** `_advOpen` in `ApiEnrichNode` persiste nel canvas store → ogni canvas save include flag UI toggle (vedi 0.5b §4.5). Non bug ma rumore.
- **(friction)** Validazione cross-nodo (es. "manca file source") mostrata sul nodo invalido — ma l'utente potrebbe non vederla se il nodo è fuori viewport.
- **(friction)** Anti-loop pattern in `ApiEnrichNode` (linee 287–303): `JSON.stringify(normalize(...))` su grande payload → jank UI sospetto (vedi 0.5b §4.5, 0.5f §3.3).

### 3.7 Rischi product-grade
- **(bug latente sospetto)** `ResponseConfigBuilder.analyzeJsonStructure` ricorsivo senza depth cap su oggetti (vedi 0.5b §2.4) — payload API maligno = stack overflow / freeze.
- **(debt)** ReactFlow re-render frequenti su canvas con 50+ nodi: nessuna profilazione.
- **(debt)** Canvas serialization in `canvasSerializer.js` — versioning schema mai testato cross-version.
- **(friction)** `data._lastUpdate: Date.now()` usato come trigger render (linee multiple in `ApiEnrichNode`) — pattern fragile.

### 3.8 Quick wins
- **W3.1** Banner top-canvas "il nodo X ha errori" con scroll-to-node click.
- **W3.2** Toast su PUT canvas success/fail (oggi silenzioso?).
- **W3.3** "Canvas non salvato" hint quando isDirty.

### 3.9 Interventi più grandi
- **L3.1** Versioning canvas schema con migration server-side. Effort medio, sblocca evoluzione futura.
- **L3.2** ReactFlow profiler in dev mode + ottimizzazioni mirate (vedi 0.5b raccomandazione `ApiEnrichNode` rank 5).
- **L3.3** Conflict detection multi-user (oggi: ultimo che salva vince — silently overwrite).

### 3.10 Smoke
- Apri canvas estrai con 5 nodi, edita, salva, refresh, rivedi.
- Apri canvas arricchisci con file_source upstream → connect api_enrich → param mapping.
- Read-only user (esecutore?) → tutti pulsanti edit disabilitati / nascosti.
- Validation: canvas con cycle → bloccato.
- Multi-user concurrent edit → conflict?

---

## 4. F4 — Esecuzione pipeline

### 4.1 Percorso utente
1. Utente clicca "Esegui" sul ResultNode (estrai → "Esegui Query"; manipola → "Pipeline"; arricchisci → "Arricchimento").
2. (Opzionale) `ExecutionModal` per opzioni.
3. POST `/api/pipeline/{project_id}/execute` (estrai/manipola) o `/api/arricchisci/{project_id}/enrich-async` (arricchisci).
4. Task Celery → polling su `tasks_router/{task_id}` (`useTaskPolling` hook).
5. Progress bar / toast aggiornati.
6. Su completion: result_id disponibile → download via `tasks_router/{task_id}/download`.

### 4.2 Componenti / endpoint
- FE: `ResultNodeStandardView` (button), `ExecutionModal`, `useExecution`, `useTaskPolling`
- BE: `pipeline_router/{id}/execute` (orchestrator/router.py:129), `pipeline_router/{id}/preview` (155), `pipeline_router/{id}/download` (185), `tasks_router` (300/363/424/447/621), `enrichment_router/{id}/enrich-async` (771)

### 4.3 Stati
- Happy: task PROGRESS via Redis → 100% → result_id → download.
- Loading: `update_task_progress` aggiorna `state="PROGRESS"` con `progress`, `message`. Pre-fix il task usava FAILURE→Celery interpretava come terminato; post-fix tutto resta PROGRESS con `is_error` flag.
- Empty: result vuoto valido (N14: 0 righe → output con header).
- Error: vari stati, vedi sotto.

### 4.4 Messaggi utente (osservati nel codice)
- "Inizializzazione..." (progress 0)
- "Caricamento dati..." (5)
- "API caricate, inizio elaborazione..." (10)
- "Caricamento sorgente dati..."
- "Esecuzione parallela di N nodi..."
- "Salvataggio risultati..." (90)
- "Completato!" (100)
- Su error: `"Errore: {error_message}"` con `is_error=True`.

### 4.5 Osservabilità
- `[PERF]` markers su snapshot (fingerprint_ms, db_ms, artifact_ms — vedi 0.5f §4.2).
- `open_run` / `close_run` su `intermediate.runs`.
- `append_step` per ogni step.
- `log_runs` filesystem audit trail.
- `run_id` propagato per idempotency (skipped_idempotent return).

### 4.6 Friction
- **(friction)** Polling `tasks_router/{task_id}` a intervalli fissi → carico crescente con N task paralleli aperti.
- **(friction)** Messaggio progress generico "Esecuzione parallela di N nodi" → utente non sa quale nodo è lento.
- **(friction)** Dopo completion success, l'utente deve cliccare manualmente per vedere il result — niente auto-redirect.
- **(friction)** N9 permission_revoked: se admin disabilita user mid-execution, task ritorna `permission_revoked` → frontend deve gestirlo. **Da verificare** che il messaggio sia user-friendly e non solo backend status.

### 4.7 Rischi product-grade
- **(bug latente, RB.#14 chiuso)** Se cardinality mismatch in batch parallel → `_merge_batch_parallel_node_results` solleva RuntimeError → task fallito visibile invece di success silenzioso con dati persi (chiuso, vedi `enrichment_task.py:46–107`).
- **(bug latente)** Long-running task (>1h) potrebbe scadere broker Redis → task lost. Nessuna recovery doc per user.
- **(friction)** Cancel task: `tasks_router.delete /{task_id}` (424) — ma l'utente nel codice può vederlo? Da verificare UX di cancel.
- **(debt)** Idempotency guard su run_id (linea 159 `enrichment_task.py`): retry manuali con stesso run_id ritornano `skipped_idempotent` — ma l'utente non vede questo status nel UI? Da verificare.

### 4.8 Quick wins
- **W4.1** Mostra current step name nel progress (`"Esecuzione nodo: 'geocoding'..."` invece di generic).
- **W4.2** Auto-scroll/highlight result node quando task completa.
- **W4.3** Pulsante "Annulla" visibile durante esecuzione.
- **W4.4** Mostra explicit messaging per `skipped_idempotent` (es. "Run già completato, mostro risultato cached").
- **W4.5** Banner permission_revoked dedicato (non toast generico).

### 4.9 Interventi più grandi
- **L4.1** WebSocket / SSE per progress real-time invece di polling.
- **L4.2** Task cancellation UX (cancel + cleanup partial output).
- **L4.3** Pre-execution estimate (`enrichment_router/estimate` esiste, non chiaro se usato). Mostrare "Stimato: ~32s, ~7000 righe" prima di click run.

### 4.10 Smoke
- Run estrai con 1 connection. Run manipola con 5 source. Run arricchisci con API geocoding 100 righe. Run con error handler (api 500). Run idempotent (stesso run_id 2 volte). Run cancellato a metà. User disabilitato during run.

---

## 5. F5 — Run history / intermediate data / compare L1-L2

### 5.1 Percorso utente
1. Click "🕐 Cronologia run" su result node o api_enrich node → `RunHistoryModal`.
2. Lista run per `canvas_id` (con pin, success/failure, timestamp).
3. Click su run → vede metadata; click su nodo → vede dati intermedi.
4. Compare di 2 run via `/compare/l1` (hash) o `/compare/l2` (rows diff).

### 5.2 Componenti / endpoint
- FE: `RunHistoryModal.jsx`, `PreviewModal.jsx`, `PreviewTable.jsx`
- BE: `intermediate/{canvas_id}` (131), `intermediate/{run_id}/nodes/{node_id}/data` (155), `intermediate/compare/l1` (306), `intermediate/compare/l2` (356), `intermediate/{run_id}/pin` (611)

### 5.3 Stati
- Happy: run elencate, dati visibili (preview_only) o non disponibili (metadata_only).
- Loading: spinner.
- Empty: nessuna run → "Nessuna esecuzione".
- Error / 422 / 501: vari, vedi sotto.

### 5.4 Messaggi utente (osservati nel codice)
- `metadata_only` su nodo: 200 con `rows: []` + `Dati intermedi non salvati per questo nodo (persistence_mode=metadata_only)…` (vedi BUG.md riga 38).
- `compare/l2` su `metadata_only`: 422 con `{diff_available: false, reason: "metadata_only_no_rows", message: "..."}` (BUG.md riga 40).
- `compare/l2` su mix mode (full_pg vs preview_only): 422 conservativo (`router.py:498`).
- `compare/l2` su `full_artifact`: 501 `full_artifact_diff_not_implemented` (BUG.md backlog).
- `preview_only` con preview troncato: 200 con `partial=true, reason=preview_only`.

### 5.5 Osservabilità
- `intermediate_run_report.py` script per drift detection (`persisted_rows_count != COUNT(node_rows)`).
- `node_meta` table con `persistence_mode`, `node_hash`, `artifact_status`.

### 5.6 Friction
- **(friction reale)** `metadata_only` (default produzione) → nessun dato visibile in compare. L'utente "perde" la feature compare/diff per default. Trade-off documentato (DB pressure ↓), ma l'utente non lo sa.
- **(friction)** 422 con `diff_available: false reason: metadata_only_no_rows` — messaggio backend è chiaro, ma il frontend deve renderizzarlo bene (banner / spiegazione invece di "errore generico").
- **(friction)** 501 `full_artifact_diff_not_implemented`: il modal compare deve dire "Diff per full_artifact non ancora supportato".
- **(friction)** Nessun suggerimento "abilita preview_only / full_artifact su questo run" per ottenere diff.

### 5.7 Rischi product-grade
- **(debt)** Compare cross-run è feature avanzata documentata, ma con default `metadata_only` di fatto è disponibile solo se l'operatore configura policy explicitly.
- **(bug latente)** Drift `persisted_rows_count != actual` rilevabile solo via script manuale → nessun alert.

### 5.8 Quick wins
- **W5.1** Modal compare con messaging dedicato per ogni reason (`metadata_only_no_rows`, `mode_mismatch`, `full_artifact_diff_not_implemented`, `preview_only`).
- **W5.2** Tooltip `?` su persistence_mode con spiegazione del trade-off.
- **W5.3** Auto-eseguire `intermediate_run_report.py` periodicamente e mostrare drift in admin/system-status.

### 5.9 Interventi più grandi
- **L5.1** Implementare diff L2 per `full_artifact` (DuckDB su Parquet — backlog BUG.md).
- **L5.2** Auto-toggle persistence_mode da UI (es. "Esegui in modalità debug → preview_only").

### 5.10 Smoke
- Apri run history su canvas con 5 run. Compare L1 (hash). Compare L2 con preview_only ↔ preview_only. Compare L2 con metadata_only (422). Compare L2 con full_artifact (501). Pin run + visualizza nella lista.

---

## 6. F6 — File Manager / file sources

### 6.1 Percorso utente
1. `/files` → lista file con format icon, columns count, sharing button (super).
2. Drag & drop o click → `UploadModal` 3-step wizard.
3. Step 1: file + nome + descrizione. Step 2: columns selection (single/multi-layer/multi-sheet). Step 3: confirm.
4. Upload → POST `/files/upload` o `/files/upload-multi-layer`.
5. Lista refresh; preview via `getFilePreview`.

### 6.2 Componenti / endpoint
- FE: `FileManager.jsx`, `FileCard`, `UploadModal`, `LayerModal`, `PreviewModal`, `useConfirmModal`
- BE: `files/preview-temp` (143), `files/upload` (213), `files/upload-multi-layer` (351), `files` GET (442), `files/{id}` GET (486), `files/{id}` DELETE (495), `files/{id}/layers` (514), `files/{id}/preview` (532)

### 6.3 Stati
- Happy: upload OK → card visibile.
- Loading: drop zone + step columns con "⏳ Lettura colonne...".
- Empty: nessun file → drop-zone CTA.
- Error: MIME bloccato, ZIP slip, multi-layer detection failure.

### 6.4 Messaggi utente
- "Lettura colonne..." (multi-layer)
- "File non trovato: potrebbe essere stato eliminato. Aggiorno la lista..." (RB.5 race condition fix, line 330)
- "Errore durante l'upload"
- "Seleziona almeno una colonna"
- "Eliminazione confermata?"

### 6.5 Osservabilità
- `[PERF] load_dataframe op=parse fmt=` su parse (utils/dataframe.py:164)
- N1 cross-tenant fail-closed nel service
- MIME defense in depth nei 3 entry methods (defense_in_depth contract test)

### 6.6 Friction
- **(friction RB.5 chiuso)** 404 race-condition su preview di file deleted in altra tab → toast + auto-reload (già risolto).
- **(friction)** Multi-layer wizard ha 5 useState collegati (`checkedLayers`, `layerColumns`, `layerSelectedCols`, `activeLayerTab`, `loadingLayerCols`) — UX pesante per file con 10+ layer.
- **(friction)** Drop zone aperta sotto la modal: doppio click apre modal ripetuto.
- **(friction)** SRID detection: se non auto-detect, default `4326` (line 267) — ma l'utente non sa che lo ha hardcoded.
- **(friction)** ZIP binary path: salta lo step columns (linea 270) → upload diretto SENZA preview. Niente conferma a utente "questo è un file binario, ok?"

### 6.7 Rischi product-grade
- **(debt)** Test coverage solo 1 e2e test (vedi 0.5b §5.5).
- **(bug latente)** Cleanup tmp file su upload fail: già coperto da `_rmtree_safe` + `_temp_file_for` (security/temp_lifecycle_core).
- **(friction)** Sharing modal solo super: utente standard non vede chi ha accesso.
- **(friction)** Nessuna lista cronologica upload (timestamp, user) — utile per audit ma assente.

### 6.8 Quick wins
- **W6.1** Tooltip "?" su SRID con spiegazione "EPSG:4326 = WGS84 lat/lng standard".
- **W6.2** Banner conferma "File binario senza parsing colonne — ok?" prima di upload diretto.
- **W6.3** Mostra owner+timestamp+last-used in card (oggi solo in metadata).
- **W6.4** Replace `confirm()` con `useConfirmModal` (già usato qui).

### 6.9 Interventi più grandi
- **L6.1** Multi-layer wizard custom hook (`useFileUploadWizard`, vedi 0.5b §5.7).
- **L6.2** File version history (file replaced N times).
- **L6.3** Quotas per gruppo (file size, count).

### 6.10 Smoke
- Upload CSV / XLSX multi-sheet / GPKG multi-layer / GDB / Shapefile ZIP / DXF binary.
- 404 race: cancellare file in tab A, click preview tab B.
- N1 cross-tenant: user gruppo A tenta load file gruppo B → 403 fail-closed.
- MIME defense: rinominare `.exe` → `.csv`, upload bloccato.
- ZIP slip: zip con `../evil.txt` → bloccato.
- Multi-layer: GPKG con 5 layer, selezionare 2.

---

## 7. F7 — API connection / connettori esterni

### 7.1 Percorso utente
1. `/external-connectors` → lista connettori con status (active/warning).
2. Click "+ Nuovo Connettore" → 3 path: Template / OpenAPI / Manuale.
3. Wizard 6 step (Tipo, Info, Parametri, Auth, Batch, Test).
4. Step Test → POST `/api-connections/test` con payload draft.
5. Save → POST `/api-connections` o PUT.

### 7.2 Componenti / endpoint
- FE: `ApiConnectionManager.jsx`, `TemplateLibraryModal`, `OpenAPIImportModal`, `DataSourceConfigModal`, `ResponseConfigBuilder`, `AllowedGroupsSelector`
- BE: `arricchisci/api-connections/*` (435/464/479/506/543/660/695), `arricchisci/api-connections/fetch-openapi-schema` (160), `templates` (744)

### 7.3 Stati
- Happy: card list, wizard step, test successful → schema fields auto-mappati.
- Loading: `loading=true` durante list, `testing=true` durante POST test.
- Empty: nessun connettore → empty state con CTA.
- Error: cascade 409, test fail con error in panel.

### 7.4 Messaggi utente
- "Nessun connettore configurato" (empty state)
- "Errore salvataggio" (handleSave catch generic)
- Cascade 409 + dipendenze
- Test result panel: success+available_fields o error message
- Quick test panel: idem

### 7.5 Osservabilità
- `[PERF] op=enrichment` non ancora aggregato (vedi 0.5f §4.2).
- C12 token-bearer contract: token non in params, retry 401/498/499, retry 429.

### 7.6 Friction
- **(friction)** Wizard 6 step monolitico — re-render dell'intero blocco a ogni keystroke (vedi 0.5b §1.5).
- **(friction)** "Errore salvataggio" generico → l'utente non sa se l'auth è sbagliato, l'URL malformed, o un campo obbligatorio manca.
- **(friction)** Auth section condizionale per 6 type (apikey, basic, oauth2_pwd, oauth2_clientcred, arcgis_token, none) — campi diversi appaiono/scompaiono. Cambiare type non pulisce sempre i campi precedenti → invalid payload silenzioso.
- **(friction)** Test in batch mode wrappa `{[inputKey]: batchItems}` (line 268) — se il backend rifiuta il batch, errore criptico.
- **(friction)** Sanitize masked auth: se utente lascia `***` come password, viene cancellata. UX: "lasciato come prima"? Non comunicato.

### 7.7 Rischi product-grade
- **(bug latente sospetto, MERGE pattern)** Pre-fix con fallback-OR perdeva campi mancanti (linea 198–202). Già risolto, blindato da test contract. Ma se un nuovo campo nested viene aggiunto (es. `options.timeout`), va aggiunto a EMPTY_FORM altrimenti regression.
- **(debt)** OpenAPI import seriale senza chunking → freeze UI su import grandi (vedi 0.5b §1.5).
- **(friction)** Sharing per superadmin → utente standard non vede stato sharing.
- **(debt)** ResponseConfigBuilder pure functions non unit-tested (vedi 0.5b §2.5).

### 7.8 Quick wins
- **W7.1** Toast specifico per error type ("URL malformed", "Auth: client_id mancante", ecc.) invece di "Errore salvataggio" generico.
- **W7.2** Tooltip "?" sulla password masked: "Lascia *** per mantenere il valore esistente".
- **W7.3** Validation client-side prima di Save: form schema Zod/Yup.
- **W7.4** Banner "Test fallito" inline accanto al pulsante save (oggi: utente può salvare anche test fail).

### 7.9 Interventi più grandi
- **L7.1** Wizard step extraction in 6 sub-component (vedi 0.5b §1.7).
- **L7.2** OpenAPI bulk import con progress + chunking.
- **L7.3** Connection health check periodico in background → status dinamico in card.

### 7.10 Smoke
- Crea via Template POI search. Edit di un connettore esistente con tutti campi. Test connection success / fail. Cascade 409 su delete connettore in uso da un canvas. ArcGIS token auth con 3 client mode (requestip/referer/ip). OAuth2 client_credentials. Batch mode.

---

## 8. F8 — Risultati / download

### 8.1 Percorso utente
1. Task completato → result node UI mostra "Scarica" o link.
2. Click → GET `/api/tasks/{task_id}/download` o `/pipeline/{project_id}/download`.
3. Browser download del file.

### 8.2 Componenti / endpoint
- FE: result node + tasks polling integration
- BE: `tasks_router/{task_id}/download` (621), `pipeline_router/{project_id}/download` (185), `eseguibili/{id}/download/{task_id}` (487)

### 8.3 Stati
- Happy: file scaricato.
- Error: 410 Gone se result_id stale, 404 se task non trovato.

### 8.4 Messaggi utente
- 410 Gone: "Risultato non più disponibile" (presunto, da verificare UX).
- Format: csv/xlsx/geojson/gpkg/shp dipende da result.

### 8.5 Osservabilità
- `[PERF] s3_download` + `get_result_data backend=object_storage` markers (vedi 0.5f §4.2).
- BUG.md A1 batch: backend log distingue object_storage vs db_legacy.

### 8.6 Friction
- **(friction)** result_id propagation in task result (vedi 0.5a §4.5: 8 invariants tenant_binding) — pre-fix senza result_id, download cadeva su 410.
- **(friction)** Download grandi file (es. 100 MB GeoJSON): nessuna progress bar lato client. Browser nativo gestisce.
- **(friction)** GPKG/SHP path: file binario → nessun preview prima di download.

### 8.7 Rischi product-grade
- **(debt 0.5f)** `get_result_data` materializza in memoria via `b"".join(chunks)` — peak memory == size del result. Streaming già esiste in `orchestrator/router.py:758` ma `get_result_data` no.
- **(friction)** Eseguibile download endpoint elimina file dopo invio → se browser interrompe, file perso (`/eseguibili/{id}/download/{task_id}`).

### 8.8 Quick wins
- **W8.1** Mostra size file accanto al pulsante download.
- **W8.2** Resume-on-fail per eseguibili: temporaneamente non eliminare file dopo invio (cleanup separato).

### 8.9 Interventi più grandi
- **L8.1** Streaming `get_result_data` per file > soglia.

### 8.10 Smoke
- Download CSV piccolo. Download GPKG grande. Download eseguibile (eliminato post-download). Download con result_id stale (410 path).

---

## 9. F9 — Eseguibili (esecutore)

### 9.1 Percorso utente (esecutore)
1. Login come role esecutore → redirect a `/run` (kiosk mode).
2. Vede lista eseguibili disponibili (admin pubblicati).
3. Click su eseguibile → `EseguibileExecuteWizard`.
4. Upload file input richiesto → POST `analyze-upload` (validate columns).
5. POST `execute` → polling task → download.

### 9.2 Componenti / endpoint
- FE: `EsecutoreHome.jsx`, `EseguibiliList.jsx`, `EseguibileExecuteWizard.jsx`, `PublishEseguibileModal.jsx`
- BE: `eseguibili` GET (73), POST (99), GET/{id} (135), PUT (151), DELETE (177), `analyze-upload` (300), `execute` (409), `download/{task_id}` (487)

### 9.3 Stati
- Happy: upload → analyze OK → execute → download.
- Loading: progress bar.
- Empty: nessun eseguibile pubblicato → empty state.
- Error: analyze 422 (colonne mismatch), execute fail.

### 9.4 Messaggi utente
- `eseguibili_size_cap_contract`: limite size enforce con messaggio.
- Colonne attese: visualizzazione colonne richieste vs colonne uploaded.
- Output format pre-configurato dall'admin.

### 9.5 Osservabilità
- Task lifecycle separato (no `intermediate.runs` per eseguibili).
- `is_eseguibile=True` flag in task.
- Cleanup file_source temp post-execute.

### 9.6 Friction
- **(friction)** Esecutore vede solo eseguibili pubblicati — nessuna ricerca/filter.
- **(friction)** `analyze-upload` ritorna colonne attese ma il messaggio "manca colonna X" potrebbe essere asciutto.
- **(friction)** Cleanup periodico (celery beat) rimuove file orfani — se l'utente è lento a scaricare, file potrebbe sparire.

### 9.7 Rischi product-grade
- **(debt)** Token eseguibile separato dall'auth standard? Da verificare in `eseguibili/router.py`.
- **(bug latente)** Idempotency su execute: 2 click veloci → 2 task? Vedi `enrichment_task` idempotency guard.

### 9.8 Quick wins
- **W9.1** Mostra preview colonne richieste prima dell'upload.
- **W9.2** Spinner + disable button su click execute (anti double-click).
- **W9.3** Documentare retention temporale download eseguibile.

### 9.9 Interventi più grandi
- **L9.1** Esecutore self-service download history (oggi non esiste).
- **L9.2** Notifiche push al completamento.

### 9.10 Smoke
- Login esecutore → lista eseguibili. Upload file con colonne corrette / mismatch. Execute happy / fail. Download. Doppio click execute. Cancellazione admin durante execution.

---

## 10. F10 — Schedule + log

### 10.1 Percorso utente
1. `/schedules` → lista schedule (cron + manuale + catchup).
2. Crea / edita schedule (cron expression, target connection, retry policy).
3. Toggle on/off.
4. View logs filesystem + DB.

### 10.2 Componenti / endpoint
- FE: `ScheduleList.jsx`, `ScheduleWizard.jsx`, `LogViewer.jsx`
- BE: `scheduler` 12 endpoint (62, 123, 143, 197, 218, 252, 270, 298, 326, 376, 469, 535), `logs/runs_router`

### 10.3 Stati
- Happy: schedule run → log entry success.
- Loading: list fetch.
- Empty: nessuno schedule.
- Error: cron syntax invalid, target connection unreachable (pre-check fail).

### 10.4 Messaggi utente
- Status schedule: running, completed, partial, failed, skipped, orphan.
- "Schedule diventa orphan: progetto non più esistente" — messaggio dedicato.
- Lock_lost warning (vedi 0.5a §5.4): se heartbeat thread rileva lock perso → flag in result + log warning.
- N15: scheduled_for catchup tick logico per audit lineage.

### 10.5 Osservabilità
- `log_runs` filesystem + `intermediate.runs` DB (con `triggered_by`, `scheduled_for`).
- Heartbeat thread + retry budget (N=3, T=10s) sul release lock.
- `err_msg` sanitization: no leak `str(e)` user-facing (audit batch 7.c-bis).

### 10.6 Friction
- **(friction)** Cron expression: nessuna validation client-side esplicita prima di save.
- **(friction)** Catchup vs cron normale: stesso UI ma semantica diversa — l'utente potrebbe non capire `scheduled_for`.
- **(friction)** Status `partial` (rows_skipped > 0): l'utente vede "completato ma con righe skipped" senza spiegazione.
- **(friction)** Orphan detection: schedule continua a fallire ogni tick finché admin non lo disabilita. **Già fix**: `mark_as_orphan` su project_meta None (orphan_and_release contract).
- **(friction)** Lock lost: messaggio nel result dict ma frontend deve renderizzarlo con banner dedicato.

### 10.7 Rischi product-grade
- **(debt)** PlenoraBeatScheduler custom (non Celery beat standard) → bug factor 1 sul codice.
- **(debt)** No central UI per "schedule che falliscono ricorrentemente" → admin scopre solo via incident_response.

### 10.8 Quick wins
- **W10.1** Cron expression preview ("Prossime 3 esecuzioni: 2026-...") prima di save.
- **W10.2** Banner lock_lost dedicato nel run history.
- **W10.3** Filtro "schedule con errori ultimi 7 giorni" in lista.

### 10.9 Interventi più grandi
- **L10.1** Migrazione a Celery beat standard.
- **L10.2** Alerting (email/Slack) su N fallimenti consecutivi.

### 10.10 Smoke
- Crea schedule cron `0 2 * * *` su connection valida. Toggle off/on. Esegui manualmente. Pre-check fail (DB destinazione down). Concorrenza: 2 worker stesso schedule. Catchup run. Project deleted → orphan.

---

## 11. F11–F15 — flussi minori

### 11.1 F11 Connections (DB / ArcGIS)
- FE: `ConnectionManager.jsx`. BE: `connections`, `features`.
- **friction**: test connection diff per type (DB vs ArcGIS) — UX divergente. Smoke: crea connection PG, MySQL, MSSQL, ArcGIS Online, ArcGIS Enterprise, test ognuna.

### 11.2 F12 DataSources
- FE: `DataSourceManager.jsx`. BE: `datasources`.
- **friction**: integrazione con ApiConnectionManager via DataSourceConfigModal — flusso doppio (qui CRUD, là embedding).

### 11.3 F13 Tickets
- FE: `TicketsList`, `TicketDetail`, `TicketCreateModal`. BE: `tickets`.
- Sistema bug-tracking interno. Già stable (test coverage `tickets_*_contract`).
- **friction**: attachments via file_sources reuse — se file eliminato, ticket attachment rotto?

### 11.4 F14 Admin
- FE: `UserManager`, `AdminBackupsPage`, `AdminSystemStatusPage`. BE: `auth/users`, `auth/groups`, `admin_backups`, `admin_system_status`.
- `admin_backups`: GET (28), GET /status (78), GET /schedule (86), POST /trigger (94).
- `admin_system_status`: GET aggregato (16) — 1 endpoint che dovrebbe dare overview.
- **friction**: il dashboard system-status è solo 36 LOC backend → da verificare che esponga i dati realmente utili (DB connection count, queue depth, S3 reachability, ecc.).
- **rischio**: backup trigger via UI, ma `admin_backups` test esistono solo ad alto livello — la UX di "ho lanciato backup, sta girando?" non chiara.

### 11.5 F15 IMDF publish
- FE: `BundleSlotCard`, `ImdfBundlesManagerModal`, `ImdfManifestModal`, `ImdfPreviewModal`, `ImdfValidatePublishModal`, `ImdfValidationModal`, `ImdfPublisherNode`. BE: `imdf` router (full).
- Flusso specializzato: rest_file processor → IMDF zip → publish ad ArcGIS.
- Test contract estesi (5 imdf test). Già stabile.
- **friction**: `output_kind="imdf"` selezionato manualmente (`ApiEnrichNode`); auto-detection dal connector type sarebbe più friendly.

---

## 12. Friction trasversali (cross-flow)

### 12.1 Inconsistenze UI

| Inconsistenza | Dove | Risoluzione (quick win) |
| --- | --- | --- |
| `confirm()` browser native | `ApiConnectionManager:228`, `ProjectList:154` | Replace con `useConfirmModal` (~1h totale) |
| Toast "Errore X" generico | tutti i page | Replace con error type-specific |
| Theme toggle replicato in 3+ page | ApiConnectionManager, FileManager, ProjectList | Estrarre `<ThemeToggle />` shared (vedi 0.5b) |
| Empty state design diff | ProjectList vs FileManager | Componente `<EmptyState>` shared |
| Drop zone design diff | FileManager + ApiEnrichNode ExtraFileSlot | OK: contesti diversi, scelta deliberata |
| `useHexCanvas` background canvas | 4 page | OK: branding consistency |

### 12.2 Error handling

| Pattern | Dove | Stato |
| --- | --- | --- |
| Cascade 409 | `api/errors.handleCascade409` + `formatCascadeDependencies` | Riusato in ProjectList, ApiConnectionManager. **Best practice.** |
| 500 `project_cleanup_partial` | ProjectList | Banner specifico "Ritenta o contatta admin" |
| 410 Gone (result stale) | tasks/download | Da verificare UX |
| 404 race-condition (file deleted) | FileManager.handlePreview | RB.5 fix in atto: toast + reload |
| 422 `metadata_only_no_rows` (compare L2) | intermediate router | Backend chiaro, FE deve renderizzare bene |
| 501 `full_artifact_diff_not_implemented` | intermediate router | FE deve dire "non ancora supportato" |
| 401/498/499 (ArcGIS token expired) | arcgis_client | Refresh + retry interno, transparent al user |
| `permission_revoked` (N9 user disabled mid-task) | enrichment_task | Backend ritorna status; FE deve mostrare banner dedicato |

### 12.3 Empty states

Praticamente tutti i page hanno empty states dedicati con CTA (verificato in ProjectList, FileManager, ApiConnectionManager). **Buona base.** Da rivedere caso per caso se la CTA è quella giusta (es. "Crea il tuo primo connettore" → manda al wizard manuale, non al template library, scelta forse non ottimale per onboarding).

### 12.4 Loading states

Mix di:
- Full-page `⬡ Caricamento...` (ProjectList, FileManager).
- Inline spinner buttons (Wizard test, upload).
- Tab-level loading (ResponseConfigBuilder preview).

**friction**: nessun pattern univoco. Skeleton screens completamente assenti.

### 12.5 Accessibility (a11y)

Nessuna evidenza di:
- ARIA labels su drag & drop (`onDragEnter`/`onDragLeave` HTML5 native).
- Focus trap nei modal.
- Keyboard navigation completa nei wizard (TAB navigation testata?).
- Screen reader support.

**(debt)** A11y è completamente assente come priorità. **Non è bug**, ma per prodotto B2B in IT con clienti pubblici (Comune, Stazioni FF.SS.) potrebbe diventare requisito normativo (AgID, WCAG 2.1 AA).

---

## 13. Osservabilità / Operability del prodotto

### 13.1 Cosa l'admin vede oggi
- `AdminSystemStatusPage` (1 endpoint aggregato, 36 LOC BE) — incompleto in termini di metric significative.
- `LogViewer` legge `logs.details` JSONB — ricerca + filter base.
- `ScheduleList` mostra status per schedule.
- `AdminBackupsPage` lista backup + trigger manuale.

### 13.2 Cosa NON vede oggi
- Queue depth Celery
- Throughput task / RPS
- p50/p95 endpoint API (vedi 0.5f §4.1)
- Storage usage per gruppo / per progetto
- Top slow query DB
- Slow runs (run > 5min)
- Lock contention scheduler
- Drift `node_rows` vs `persisted_rows_count` (solo via script manuale)

### 13.3 Operability gap
- **(debt)** Bus factor 1 ammesso (vedi 0.5e §1.7).
- **(debt)** Nessun runbook per: "scheduler beat down", "redis down", "S3 unreachable", "DB pool exhausted", "task hung > 1h".
- **(friction)** "Mostra log di X task" → c'è LogViewer ma il task_id deve essere passato a mano.

---

## 14. Sintesi: cosa è product-grade vs cosa no

### 14.1 Già product-grade

- **F1 Login**: solido, audit-coverage, MFA, rate-limit. Manca self-service password reset.
- **F6 File Manager**: defense-in-depth (MIME, ZIP slip, N1), 404 race fix, multi-layer wizard. Manca quotas + history.
- **F7 ApiConnectionManager** (parte test): C12 token bearer, MERGE pattern, sanitize masked auth. Manca validation client-side + error type-specific.
- **F2 ProjectList**: defensive `loadData` con `.catch(() => default)`. Manca toast specifico per endpoint failing.
- **F4 Esecuzione pipeline (parte fail-hard)**: `_merge_batch_parallel_node_results` fail-hard, idempotency, N9 re-check, lock_lost propagation. Manca real-time progress + cancel UX.
- **F10 Schedule (parte invariants)**: orphan detection, lock_lost flag, retry budget, scheduled_for lineage. Manca Celery beat standard + alerting.

### 14.2 Sotto product-grade

- **F5 Run history / compare** — feature avanzata ma con default `metadata_only` di fatto disponibile solo se operatore configura policy explicitly. UX dei 422/501 da rivedere.
- **F3 Editor canvas** — editor potente ma anti-loop pattern, conflict detection mancante, validation cross-nodo non sempre visibile.
- **F4 Esecuzione (parte UX)** — progress generico, niente cancel button visibile, niente preview estimate.
- **F8 Download** — peak memory full-buffer su `get_result_data`, no streaming end-to-end per result grandi.
- **F9 Eseguibili** — kiosk mode esecutore funzionale, ma niente self-service download history.
- **F11 Connections** — UX divergente DB vs ArcGIS.
- **F14 Admin** — `admin_system_status` aggregato 36 LOC BE → da espandere.
- **F12, F13, F15** — funzionali, no blocking issue.

### 14.3 Operability sotto product-grade

- Observability: marker `[PERF]` esistono ma non aggregati (vedi 0.5f §5).
- Bus factor: 1 (admin Marco only).
- Runbook incidents: parziali.
- Self-service user (password reset, MFA recovery): assente.
- A11y: assente.

---

## 15. Top quick wins (ordinati per ratio payoff/effort)

| Rank | ID | Flusso | Descrizione | Effort stimato |
| ---:| --- | --- | --- | ---:|
| 1 | W2.1 / W6.4 | F2, F6 | `confirm()` → `useConfirmModal` cross-page (UX consistency) | 1h |
| 2 | W4.4 | F4 | Mostra messaging dedicato per `skipped_idempotent` | 1h |
| 3 | W5.1 | F5 | Compare modal con messaging per ogni `reason` (`metadata_only_no_rows`, `mode_mismatch`, `full_artifact_diff_not_implemented`) | 2h |
| 4 | W7.1 | F7 | Toast error type-specific in ApiConnectionManager save/test | 2h |
| 5 | W4.1 | F4 | Current step name nel progress (no "esecuzione N nodi" generic) | 2h |
| 6 | W4.5 | F4 | Banner permission_revoked dedicato | 1h |
| 7 | W10.1 | F10 | Cron preview "Prossime 3 esecuzioni" | 2h |
| 8 | W2.2 | F2 | Toast specifico per endpoint failing in loadData | 1h |
| 9 | W4.3 | F4 | Pulsante "Annulla" visibile durante esecuzione | 3h |
| 10 | W6.1 | F6 | Tooltip SRID + spiegazione default 4326 | 0.5h |

Tot: ~15h effort per chiudere gap UX di base cross-flow.

---

## 16. Top interventi più grandi (ordinati per impatto)

| Rank | ID | Flusso | Descrizione | Impatto |
| ---:| --- | --- | --- | --- |
| 1 | L4.1 | F4 | WebSocket / SSE per progress real-time | Sblocca UX per task lunghi |
| 2 | L5.1 | F5 | Diff L2 da `full_artifact` Parquet (DuckDB) | Chiude backlog BUG.md, sblocca compare avanzato |
| 3 | L8.1 | F8 | Streaming `get_result_data` per file > soglia | Riduce peak memory worker, sblocca result grandi |
| 4 | L1.2 | F1 | Self-service password reset (SMTP) | Sblocca onboarding senza admin |
| 5 | L10.1 | F10 | Migrazione a Celery beat standard | Chiude debt PlenoraBeatScheduler custom |
| 6 | L3.1 | F3 | Versioning canvas schema + migration | Sblocca evoluzione nodi futura senza rotture |
| 7 | L1.1 | F1 | JWT → httpOnly cookie + CSRF | Sblocca security posture |
| 8 | L4.3 | F4 | Pre-execution estimate + show cost | UX produttività |
| 9 | L10.2 | F10 | Alerting su fallimenti ricorrenti | Operability |
| 10 | L3.3 | F3 | Conflict detection multi-user editing | Sblocca multi-utente concorrente |

---

## 17. Smoke / test scenarios cross-flow consigliati

Lista ad alto livello — niente codice, solo "cosa cliccare a mano":

1. **End-to-end Estrai**: login → new estrai project → connect PG → drag query node → param → result node csv → execute → download. ~3 min.
2. **End-to-end Manipola**: login → new manipola → 2 source projects → join → execute → run history → compare L1 hash. ~5 min.
3. **End-to-end Arricchisci**: login → new arricchisci → file_source upstream → api_enrich (geocoding) → param mapping → execute → download. ~5 min.
4. **End-to-end IMDF**: arricchisci con rest_file IMDF → output_kind=imdf → publish ad ArcGIS. ~10 min.
5. **End-to-end Eseguibile**: admin pubblica eseguibile → esecutore login → upload file input → execute → download. ~5 min.
6. **Schedule cron**: crea schedule daily → wait next tick → run history → log filesystem visible. ~2 min (+ 1 tick wait).
7. **Schedule manual + catchup**: schedule disable → re-enable → run catchup. ~3 min.
8. **Concurrent edit conflict** (2 browser tab): verifica what happens. ~2 min.
9. **404 race FileManager**: tab A delete file, tab B click preview. ~1 min.
10. **Cascade 409**: try delete project con dipendenze (eseguibili, schedules). ~1 min.
11. **Permission_revoked mid-task**: admin disable user durante long-running task → check status. ~5 min.
12. **Token expiry ArcGIS mid-batch**: simulate token expiry tra 2 batch write (richiede ArcGIS Online). ~10 min.
13. **N1 cross-tenant**: user gruppo A tenta load file gruppo B → 403. ~1 min.
14. **MFA enroll + recovery**: enroll → verify → disable e re-enroll. ~5 min.

Totale smoke completo: ~1h tempo manuale per round.

---

## 18. Stato Phase 0.5g

**Status: analysis only. No code change. No test. No deadline.**

Output prodotti:
- 15 flussi utente (F1–F15) mappati con percorso, componenti FE, endpoint BE, stati, messaggi, osservabilità, friction, rischi, quick wins, interventi grandi, smoke
- Friction trasversali (UI consistency, error handling, empty/loading states, a11y)
- Observability gap dal punto di vista admin
- Sintesi product-grade vs sotto product-grade per flusso
- 10 quick wins ordinati per ratio (~15h totali)
- 10 interventi grandi ordinati per impatto
- 14 smoke scenarios cross-flow

Distinzione mantenuta cross-flow:
- **bug** (es. RB.5 race, RB.#14 fail-hard) → la maggioranza chiusi in BUG.md;
- **friction** (UX) → quick wins applicabili a basso effort;
- **debt** (struttura/operability/a11y) → interventi più grandi.

Pending Phase 0:
- 0.5c — Q1–Q4 SQL queries esecuzione su prod DB (`compat_residue_evidence_0_5c.md`, operatore manuale).

Phase 0.5 sub-stream completati: 0.5a, 0.5b, 0.5c (analisi), 0.5d, 0.5e, 0.5f, 0.5g.

---

*End of Phase 0.5g — UX / Product Readiness.*
