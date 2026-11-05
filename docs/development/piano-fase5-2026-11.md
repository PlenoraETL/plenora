# Fase 5 — Resto dell'audit frontend (piano, 05/11/2026)

Raccolta completa: `docs/audits/fase5-raccolta-2026-11-05.md` (tre lettori indipendenti, ogni voce dell'audit del 03/11 verificata sul codice di `e4688ae6`).

## Stato di partenza

| Gruppo | Voci | Chiuse | Parziali | Aperte | N.p.a. | Nuovi difetti |
|---|---|---|---|---|---|---|
| R1–R4 (canvas, tipi, colonne, risorse) | 63 | 25 | 11 | 26 | 1 | 14 |
| R5–R8 (errori, modali, navigazione, form) | 48 | 6 | 9 | 33 | 0 | 14 |
| R9–R11, P, D, U, rimandati, residui A | 143 | 37 | 7 | 96 | 3 | 8 + 9 codice morto |
| **Totale** | **254** | **68** | **27** | **155** | **4** | **45** |

Canvas, tipi di nodo e colonne (R1, R2, R3 colonne) sono chiusi per costruzione dalle Fasi 1–4 e dal Blocco A; P1 (XSS), P2 (test finto) e i 12 rimandati sono chiusi. Restano soprattutto le **pagine di sistema** (utenti, ticket, log, admin, connessioni, data source, file, eseguibili, schedule, dashboard), mai toccate dal redesign, e l'infrastruttura comune che manca (risorse/task asincroni, errori, modali, navigazione, form).

## Regola di lavoro

Per ogni tranche: **correzione unica** (meccanismo comune + test di enumerazione che vieta il ritorno del difetto) → **un giro di conferma** indipendente → commit → deploy quando c'è abbastanza materiale → collaudo dal vivo. Tetto di 6 giri di lettura per tranche.

## Tranche (in ordine di rischio)

### T1 — Sicurezza, permessi, sessione
R10 (tutte: azioni visibili a chi non può, `role ===` grezzi, eliminazione gruppo che confronta l'id, Bonifica orfani globale, `window.prompt`, gruppo proprietario dal client, segreti degli header REST in `node.data`, devtools in produzione, allegati), R5.5 (logout che non azzera gli store), cache di modulo non svuotate al logout (R5.4, R1-N4, R5-N3), password minima 12 come il backend (R5-N1, N2), 401 su `/auth/login` (R5-N9), guardia XSS estesa a `bindTooltip(variabile)` e `divIcon({html})` (R9-N5).
**Meccanismo:** `can.*` unico con test che vieta `role ===` fuori da `permissions.js`; registro delle cache azzerate da un solo `resetSession()`; segreti mai nel canvas (riferimento alla connessione).

### T2 — Risorse e task asincroni, esecuzioni
R4 (tutte), R9 (stati, polling, download, busy, revoca, rollback ottimistico), anteprime con id di richiesta (R1-N2), race degli schemi (R1-N3), Annulla/progresso/chiusura dell'esecuzione in Estrai e Manipola (R4.11, R9.4, R9-N4), report errori (R9-N3), Eseguibili con la finestra unica «Prima di avviare» e la stima (residuo A, BA2).
**Meccanismo:** `useResource` (id di richiesta, stati loading/error/empty/ready, `<ListState>`) e `useTask` (polling con stop, 404/5xx/revoked, annulla); test di architettura che vieta `useEffect`+fetch a mano nelle pagine.

### T3 — Errori e messaggi
R5 restanti (401 tardivo e ripetuto, `getMe`, boundary con Sentry, normalizzazione unica: niente `err.message` grezzo nei toast), R5.9–R5.10.
**Meccanismo:** `requestErrorMessage` unico (già introdotto in F3) esteso a tutte le pagine; test che vieta `err.message` nei toast.

### T4 — Modali, wizard, navigazione
R6 (stack per Esc/overflow, id duplicati, salto di step senza validazione, doppio submit, overlay del Wizard, × attiva durante l'invio, Ctrl+Z/Y sotto le modali, `window.confirm`/prompt), R7 (`useBlocker` per tasto Indietro e Ctrl+K, LogViewer), R5-N10 (tre regole diverse per "finestra aperta"), N14 (focus).
**Meccanismo:** stack dei modali unico (`useDialog`) che possiede Esc, focus, scorciatoie e overlay; guard di navigazione unico.

### T5 — Form e input
R8 (ProjectInputNode, ConnectionSetsPanel, Pivot, cron/fusi orari, `parseInt||0`, cataloghi duplicati, `input_N`/`count_N`, ecc.), R5-N4 (Colonne attese che azzerano i tipi), N11–N13.
**Meccanismo:** form dichiarativi con buffer locale e un solo convertitore per tipo di campo; test di enumerazione dei campi.

### T6 — Canvas residui
R1.4 (upload paralleli di extra_files), R1.9 (stato locale stantio dopo undo), R2.9–R2.10, R3.12 (rinomina che riscrive i letterali nelle espressioni), R3.13 + R1-N14 (anteprima SQL di Estrai), R3.15–R3.21, R1/R3 parziali, N1 (form completi solo nel pannello), R9-N1 (chiavi legacy del Lookup riscritte come testo), R9-N2 (cambio operazione che lascia i binding), BA1 (tipi previsti del Lookup), BA3.

### T7 — Formati, pagine di sistema, accessibilita', prestazioni, codice morto
R11 (ora UTC come locale, 9 formattatori di dimensione, enum grezzi), U (Dashboard, pagine di sistema, badge, linee guida G1–G5), accessibilita' e prestazioni, P restanti non assorbite dalle tranche sopra, D e codice morto nuovo (con test che trova export e CSS senza uso).

## Esecuzione

T1 e T3 sono piccole e toccano file diversi: partono subito. T2 e T4 sono l'infrastruttura su cui poggiano le pagine: dopo T1/T3. T5–T7 a seguire. Deploy proposti: dopo T1+T3, dopo T2+T4, dopo T5–T7.

## T1+T3 — correzione dopo la conferma (05/11/2026)

Correzione unica dei 13 difetti trovati dal giro di conferma. Una regola per punto, ciascuna con il test che la enumera.

1. **Credenziali REST solo verso l'origine del connettore** (backend `carica/rest_auth.py`). Le intestazioni di un connettore partono solo se l'URL della scrittura ha la stessa origine (schema, host, porta) dell'URL del connettore (`endpoint.url`, poi `base_url`); chi usa il connettore deve poterlo usare (non esecutore; connettore visibile al SUO gruppo, oltre che al gruppo del progetto; superadmin solo il controllo sul progetto). Chi usa il connettore: l'utente che lancia (stato live al momento della scrittura); per le esecuzioni non presidiate (catene da schedule/cron, `user_id` assente) il proprietario del progetto (`created_by`), altrimenti rifiuto. Rifiuto = `RestAuthUnavailable` con `reason` dichiarato (`REST_AUTH_REASONS`), nessuna richiesta. Lo stesso controllo (senza decifrare) gira prima: POST /tasks e salvataggio del canvas (`orchestrator/carica_payload_validation.assert_rest_auth_usable`, 403 per i permessi, 400 per la configurazione, codice `carica_rest_auth_unavailable` con `details.reason`). Test: `tests2/carica/unit/test_carica_rest_auth_reference_contract.py`.
2. **Niente si toglie in silenzio** (`utils/restSecrets.js`). Le intestazioni segrete dei canvas vecchi restano in memoria e il nodo e' "da migrare": avviso sempre visibile nel nodo, sezione "Credenziali da migrare" in «Prima di avviare» (motivo `migrate`, Avvia spento), salvataggio bloccato nel punto unico di scrittura (`useCanvasStore.saveCanvasSerialized` -> `assertNoRestSecrets`, messaggio chiaro). Si sblocca scegliendo un connettore (sostituisce le righe segrete e lo dice) o rimuovendo le righe. La copia locale tiene la riga senza valore (`redacted: true`). Nomi di credenziale: elenco dichiarato `SECRET_HEADER_NAMES` + `*-token` + `secret: true` (X-Session-Id e simili non sono credenziali). Il backend con un canvas salvato non migrato scrive come prima. Test: `tests/shared/restSecrets.contract.test.jsx`, `tests2/.../test_carica_rest_auth_reference_contract.py::test_legacy_canvas_with_secret_headers_and_no_reference_writes_as_before`.
3. **Fail-closed sul tipo di autenticazione**: tabella `CREDENTIAL_HEADER_BY_AUTH_TYPE`; un connettore che non produce un'intestazione (none, api_key in query, arcgis_token, tipo sconosciuto) -> `auth_not_header`; credenziale vuota o token non ottenibile -> `credential_missing`. Il test enumera i tipi di `oauth.prepare_headers` (AST) e verifica che siano tutti in tabella.
4. **Storage della sessione svuotato in un solo punto**: `AuthContext.logout()`, dopo `emitBeforeLogout`; `api/http.handleUnauthorized` non tocca lo storage (deduplica con il token in chiusura). Test dall'interceptor reale: `tests/api/unauthorizedSessionClear.contract.test.jsx`.
5. **Ogni capability e' la regola del backend del suo endpoint**: tabella `CAPABILITY_BACKEND` in `utils/permissions.js` (capability -> funzioni dei router, dependency, regola `role`/`owner`/`service`, flag del server, eventuale `stricter` motivato). Il test legge i `Depends` delle funzioni e confronta ruolo per ruolo. `editProjectMeta` (rinomina/tag: user del gruppo) separata da `deleteProject` (proprietario o admin).
6. **Testo del login dal codice del backend** (`utils/loginErrors.js`, tabella codice -> testo). Un "messaggio" del backend che e' un codice snake_case non si mostra mai (`errorMessages.looksLikeCode`). Test: `tests/shared/loginErrors.contract.test.js`.
7. **Il catalogo dell'Object Storage mostra tutti gli orfani della bonifica**: risultati ed esecuzioni intermedie dalla stessa fonte (`results.orphan_cleanup.find_orphan_runs`, campo `intermediate_runs`); la bonifica con gli id mostrati pulisce entrambi. Test: `tests2/services/test_orphan_cleanup_scope.py`, `tests/pages/AdminObjectStoragePage.contract.test.jsx`.
8. **Lista = azione**: `ownership.owns_or_admin` e' la regola di `_check_ownership` e del `can_edit` dei file_source (prima `compute_can_edit`). Frontend: progetto senza `created_by` solo admin; `manageSchedule` = regola di `require_schedule_owner` (anche viewer proprietario). Test: `tests2/services/test_owner_rule_list_matches_action.py` + tabella del punto 5.
9. **Condivisioni bloccate senza proprietario noto**: 404 (risorsa non trovata) o risposta senza `owner_group_id` -> pannello bloccato con errore e «Riprova».
10. **Sink HTML**: l'argomento deve essere INTERAMENTE `escapeHtml(...)` o un nodo DOM creato nel file e mai riassegnato; sink aggiunti: openPopup/openTooltip, `L.popup/L.tooltip({content})`, `el['innerHTML']`, `srcdoc`/`srcDoc`, `setAttribute('srcdoc')`, `setHTMLUnsafe`. Casi positivi e negativi nel test.
11. **Password come Python**: lunghezza in code point (`Array.from`), nessuna regola speciale per gli spazi (come il backend).
12. **Set di connessioni condivisi per sessione** (`utils/connectionSets.js`): richiesta in volo condivisa, esito per la sessione, invalidato da chi modifica i set, svuotato da `resetSession`.
13. **Test della fine sessione con import statici** in testa al file.

## T2 — Risorse e task asincroni, esecuzioni (05/11/2026)

Correzione unica: un meccanismo per classe, ciascuno con il test che la enumera.

1. **Dati** — `hooks/useResource`: id di richiesta (una risposta superata non scrive), chiave che cambia = nuova richiesta (i dati della chiave di prima non si mostrano; `keepPreviousData` solo per la stessa risorsa con parametri nuovi), stati `idle/loading/error/empty/ready`, errore con il testo unico (T3) e `stale` sopra i dati gia' caricati, `initialLoading` solo al primo caricamento (`refreshing` dopo un'azione), `refreshInterval` un poll alla volta e fermo a scheda nascosta, `initialData` per le cache. `components/common/ListState` e `ResourceError`: errore mai come lista vuota, sempre con «Riprova». Pagine e componenti migrati: Ticket, Log/Run, Admin (stato, task runtime, backup, object storage, config), Connessioni, Data Source, API, File, Utenti, Eseguibili, Esecutore, Dashboard, Schedule, wizard dello schedule, nodi (Data Source, File, Progetto, Layer, Carica DB/ArcGIS/REST, Input di progetto, ApiEnrich), pannelli (servizi ArcGIS, set di connessioni, versioni, condivisioni, template, connettori), ColumnBuilder (traduzione dopo la pausa, SQL solo dei parametri attuali), `useEditorData` (progetto e connessioni in parallelo, catalogo e sorgenti non bloccanti con errore proprio), catalogo delle operazioni (`hooks/useTransformRegistry`, errore con Riprova anche nella palette).
2. **Task** — `utils/taskOutcome` (una fase per stato: running, success, failure, revoked, permission_revoked, lost; mai "completata" falsa), `utils/taskPoller` (404 ripetuti, errori ripetuti, 401/403 = esito `lost`; stop), `hooks/useTask` (id di richiesta, cambio di `scope` = reset, Annulla con 409 = gia' terminato). `useTaskPolling` rimosso. Sopra: `useExecution` (Annulla e progresso per i tre editor, finestra sempre chiudibile e riapribile dalla barra con "Mostra l'esecuzione"/"Vedi l'esito", task di Carica sullo stesso poller), `usePreview`/`useInlinePreview` (vince l'ultima anteprima, chiudere ferma il polling), Eseguibili (`useEseguibileRun`).
3. **Download** — `utils/downloadBlob.saveBlob/openBlob` (revoca ritardata, unico punto con createObjectURL), `hooks/useDownload` (errore mostrato, un clic per elemento), testo per famiglia del 410 in `errorMessages`.
4. **Azioni per elemento** — `hooks/useBusy` (test, eliminazioni, rinomine, clonazioni, risoluzioni, quick test, Valida/Anteprima); ottimistico per campo nella dashboard (`updateProjectOptimistic`: rollback al valore confermato solo se non c'e' una modifica piu' recente); conferma su Ripristina/Ripubblica.
5. **Eseguibili (BA2)** — la finestra unica «Prima di avviare» (`BeforeRunModal`, con `extra`/`extraInputs`/`extraComplete` per file e formato) e lo stesso esito (`ExecutionModal`, `backgroundable=false`); backend `POST /eseguibili/{id}/preflight` (stessi input dell'avvio, upload non consumati, file temporanei eliminati, stima per Arricchisci) e `preparation_fingerprint` su `/runs` (409 `preparation_changed`, upload non consumati). Pulizia dell'esecutore a ogni chiusura dopo la fine.
6. **Architettura** — `tests/shared/asyncArchitecture.contract.test.js`: nessun useEffect con chiamate al server fuori dagli hook comuni (eccezione dichiarata: verifica della sessione in AuthContext), nessun setInterval (orologio `hooks/useNow` per i contatori), polling dei task solo in taskPoller/useTask/useExecution, createObjectURL solo in downloadBlob, download dell'API solo dentro `run(`, niente busy a id singolo. Primitive: `tests/shared/asyncPrimitives.contract.test.jsx`.
