# Eliminare un utente mantenendo lo storico — raccolta (10/11/2026)

Base: `5ca2700a`. Richiesta: poter eliminare davvero un utente (caso reale: l'esecutore di prova «E0071», 1 riga in `eseguibile_runs`) **mantenendo lo storico leggibile**; solo il superadmin; lo storico non si cancella mai.

Metodo: raccolta completa di **ogni punto che legge la tabella `users` o mostra un utente** (backend e frontend), poi una correzione unica per costruzione. Ricerca: ogni SQL con `FROM/JOIN/UPDATE/INTO users` in `backend/app`, `workers`, `scripts`; ogni chiamante delle funzioni utente di `app/core/auth/db.py`; ogni percorso di autenticazione (`decode_token`, `get_current_user`, login, MFA, worker); ogni campo con l'id o il nome di un utente nelle risposte; nel frontend ogni uso di `username`, `created_by`, `author_*`, `uploaded_by*`, `tested_by`, `triggered_by` e delle API utenti.

Legenda del tipo (è la tabella dichiarativa `app/core/auth/deleted_users.USERS_SQL_SITES`): **O** = operativa (una lapide non deve esistere), **S** = storica col nome (deve dire «utente eliminato (<username>)»), **R** = storica sulla riga (serve la lapide), **T** = tutte le righe (vincolo), **W** = scrittura.

## A. SQL sulla tabella `users` (backend)

| # | Punto | Uso | Tipo | Prima della correzione |
|---|---|---|---|---|
| A1 | `auth/db.get_user` | ogni autenticazione con token (`dependencies.get_current_user`), `/auth/me`, cambio password, ricontrollo nei worker (`async_user_recheck`: progetti, Eseguibili, `load_live_user` di carica REST), gestione utenti (PUT, reset, DELETE) | O | nessun filtro |
| A2 | `auth/db.get_user_by_username` | login, `smoke_seed` | O | nessun filtro |
| A3 | `auth/db.list_users` | lista utenti dell'admin (proprio gruppo) | O | nessun filtro |
| A4 | `auth/db.list_all_users` | lista utenti del superadmin | O | nessun filtro |
| A5 | `auth/db.find_users_by_normalized_username` | unicità dello username in creazione | T | leggeva solo id/username e poi `get_user` |
| A6 | `auth/db.get_username_index_status` | collisioni per l'indice unico (stato di sistema) | T | — |
| A7 | `auth/db.create_user` | INSERT | W | — |
| A8 | `auth/db.update_user` | PUT utente | O | nessun filtro |
| A9 | `auth/db.update_user_password` | reset, cambio, rehash al login | O | nessun filtro |
| A10 | `auth/db.delete_user` | cancellazione fisica | O | nessun filtro |
| A11 | `auth/db.get_group_summary` | conteggio «Utenti» nel riepilogo d'impatto del gruppo | O | contava tutti |
| A12 | `db/group_cleanup._DELETE_GROUP_ROWS_SQL["users"]` | eliminazione del gruppo | T | — |
| A13 | `tickets/db.list_comments` | `author_username` dei commenti | S | `u.username` |
| A14 | `tickets/db.list_attachments` | `uploaded_by_username` degli allegati | S | `u.username` |
| A15 | `shared/connection_tests/results._LOAD_SQL` | nome e gruppo di chi ha testato una connessione | S | nome registrato al test, gruppo attuale |

## B. Chi usa le funzioni utente (Python)

| # | Punto | Lettura | Note |
|---|---|---|---|
| B1 | `dependencies.get_current_user` | A1 | ogni endpoint autenticato; un token già emesso dopo l'eliminazione deve dare 401 |
| B2 | `auth.login` | A2 + `is_active` + MFA | |
| B3 | `auth.get_me`, `auth.change_password` | A1 | |
| B4 | `async_user_recheck.recheck_project_authorized`, `recheck_eseguibile_authorized`, `load_live_user` | A1 | task in coda avviati dall'utente, connettore REST (le Pianificazioni partono senza utente, `user_id=None`) |
| B5 | `ownership.can_modify`, `assert_can_modify` | A1 sul creatore | regola «risorsa creata da un superadmin: l'admin non la modifica». Con A1 filtrato la protezione sparirebbe quando il superadmin creatore viene eliminato → serve la lettura **R** |
| B6 | `users_router` list/create/update/reset/delete | A1–A4, A8–A10 | |
| B7 | `groups_router.get_group_summary` | A11 | |
| B8 | `admin_system_status` | A6 | |
| B9 | `smoke_seed` | A2 | |
| B10 | `auth.logout`, `auth_middleware` | nessuna (solo il token) | il logout non legge l'utente; il middleware lo usa solo per i log |
| B11 | MFA (`/auth/mfa/*`, `mfa_store`) | via B1; tabelle `user_mfa`, `user_mfa_used_codes` (ON DELETE CASCADE) | con la riga che resta il CASCADE non scatta: vanno cancellate a mano |

Non esistono refresh token, chiavi API utente né sessioni lato server: i JWT si validano sempre con B1.

## C. Dati che referenziano un utente per id

| # | Colonna | Vincolo | Come si mostra oggi |
|---|---|---|---|
| C1 | le 14 chiavi di `USER_REFERENCES` (`eseguibile_runs.user_id`, `projects/eseguibili/eseguibile_versions/schedules/connections/api_connections/arcgis_connections/datasources/file_sources.created_by`, `tickets.created_by/assignee`, `ticket_comments.author_id`, `ticket_attachments.uploaded_by`) | FK senza ON DELETE: bloccano la cancellazione | id nelle risposte, usato per i permessi (ownership, `reopenTicket`, `removeTicketAttachment`); nomi solo per C1 commenti/allegati (A13, A14) |
| C2 | `user_mfa.user_id`, `user_mfa_used_codes.user_id` | FK CASCADE | — (segreti) |
| C3 | `connection_sets.created_by` | FK SET NULL | — |
| C4 | `project_canvas_versions.created_by` | nessuna FK | **l'id grezzo** nel modale «Versioni» (`CanvasVersionsModal`) |
| C5 | `connection_test_results.tested_by` (+ `tested_by_name`) | nessuna FK | nome registrato, visibile a superadmin o stesso gruppo (A15) |
| C6 | `audit_actions.user_id`, metadati dei task `owner_user_id` | nessuna FK | non mostrati (archivio) |
| C7 | `tickets.assignee` in scrittura | FK | `POST/PATCH /tickets`: qualunque id; un id inesistente era 500 (FK), una lapide passerebbe la FK |

## D. Testi registrati con lo username (istantanee)

`connection_test_results.tested_by_name`, `log_runs.triggered_by` (`test_run:<nome di prova>`, nome fittizio dell'editor), `overridden_by` nel riepilogo delle esecuzioni («valore scelto dagli amministratori»), messaggi di log e audit. Sono testo scritto al momento del fatto, non letture di `users`: restano lo username originale, che è leggibile. Non si riscrivono (lo storico non si riscrive).

## E. Frontend

| # | Punto | Cosa mostra |
|---|---|---|
| E1 | `pages/UserManager.jsx` | lista utenti (`listUsers`), modale di eliminazione con «Disattiva» al 409 `user_has_history` |
| E2 | `pages/UserManager.jsx`, `GROUP_IMPACT` | conteggio «Utenti» del gruppo (A11) |
| E3 | `pages/TicketDetail.jsx` | `author_username`, `uploaded_by_username` (A13, A14) |
| E4 | `components/modals/CanvasVersionsModal.jsx` | `created_by` (id grezzo, C4) |
| E5 | `utils/connectionTestStatus.js` (ConnectionManager, ApiConnectionManager, DataSourceManager, ProjectList) | `tested_by` (A15) |
| E6 | `context/AuthContext`, `AppSidebar`, `EditorSidebar`, `EstraiEditor`, `EsecutoreHome`, `BeforeRunModal` | l'utente corrente (`/auth/me`, login): una lapide non arriva mai qui (B1, B2) |
| E7 | `components/logviewer/RunsTab.jsx` | `triggered_by` (istantanea, D) |
| E8 | `utils/permissions.js` (`can`, `CAPABILITY_BACKEND`), `utils/errorMessages.js` | permesso `manageUsers`, testo di `user_has_history` |
| E9 | assegnatario dei ticket | nessun selettore nell'interfaccia (solo API, C7) |

## F. Regole esistenti verificate

- **Non sé stessi**: `self_delete_denied` (DELETE).
- **Ultimo superadmin**: non esiste un controllo esplicito. Con l'eliminazione riservata al superadmin e il divieto su sé stessi, chi elimina è un superadmin attivo diverso dal bersaglio: l'ultimo superadmin non si può eliminare per costruzione.
- **Gruppo**: `users.group_id` è NOT NULL con FK; l'appartenenza conta solo per liste (A3), conteggio (A11) e visibilità del nome di chi ha testato (A15). Con A3 e A11 filtrati non conta più per nulla di operativo; resta per A15 e per l'eliminazione del gruppo (A12).

## Esito

Difetti/punti da chiudere: A1–A4, A8–A11 (lettura operativa senza filtro), A5 (unicità che deve vedere la lapide), A13–A15 e C4/E4 (nome nello storico), B5 (protezione del superadmin creatore), B11 (segreti MFA), C7 (assegnatario), E1 (offerta al superadmin). Correzione unica: `docs/development/piano-fase5-2026-11.md`, sezione «Eliminare un utente mantenendo lo storico (10/11/2026)».
