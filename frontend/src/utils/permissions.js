/**
 * permissions.js — la tabella unica di "chi puo' fare cosa" (audit R10).
 *
 * Regole (vietano il ritorno del difetto: tests/shared/permissions.contract):
 *  1. Nessun confronto sul ruolo (`role ===`, `.role !==`, ...) fuori da
 *     questo file: i componenti usano `can.*` (azione) o `role.*` (categoria).
 *  2. Ogni azione distruttiva o amministrativa delle pagine passa da una
 *     capability `can.*`; il test lega ogni funzione API che modifica dati
 *     alla sua capability e verifica che chi la importa la usi.
 *  3. Un'azione non permessa NON si mostra (niente bottoni che portano a un
 *     403): il bottone esiste solo se la capability e' vera.
 *  4. Dove il server calcola il permesso sulla risorsa (`can_edit` nelle
 *     liste), quello e' la fonte: la capability lo legge invece di dedurlo.
 *
 * NOTA SICUREZZA: questo file e' solo UX. Il vero controllo sta nel backend
 * (Depends(require_*), owns_or_admin, compute_can_edit, ...).
 */

const ROLES = {
  ESECUTORE:  'esecutore',  // ruolo "kiosk": vede SOLO eseguibili, niente altro
  VIEWER:     'viewer',
  USER:       'user',
  ADMIN:      'admin',
  SUPERADMIN: 'superadmin',
};
// Esatti (= un solo ruolo)
const isEsecutore  = (u) => u?.role === ROLES.ESECUTORE;
const isViewer     = (u) => u?.role === ROLES.VIEWER;
const isUser       = (u) => u?.role === ROLES.USER;
const isAdminExact = (u) => u?.role === ROLES.ADMIN;
const isSuperadmin = (u) => u?.role === ROLES.SUPERADMIN;

// Cumulativi (= "almeno questo livello") — più comodi nel 99% dei casi
const isAdminOrAbove = (u) => u?.role === ROLES.ADMIN || u?.role === ROLES.SUPERADMIN;
const isUserOrAbove  = (u) => u?.role === ROLES.USER  || isAdminOrAbove(u);
// "Loggato e non kiosk" = qualunque ruolo della UI normale (esclude esecutore)
const isViewerOrAbove = (u) => isViewer(u) || isUserOrAbove(u);

export const role = {
  isEsecutore,              // ruolo kiosk-only
  isViewer,
  isUser,
  isAdmin: isAdminExact,    // ruolo "admin" esatto, NON include superadmin
  isSuperadmin,
  isAdminOrAbove,           // admin OR superadmin (caso più frequente)
  isUserOrAbove,            // user, admin, superadmin (esclude viewer ed esecutore)
  isViewerOrAbove,          // tutti i ruoli "normali" (esclude esecutore)
};

// Permesso calcolato dal server sulla risorsa (`can_edit` delle liste):
// assente = il server non lo dice, decide il ruolo minimo dell'azione.
const serverAllows = (resource) => resource?.can_edit !== false;

// Stessa regola di backend `ownership.owns_or_admin` (= `_check_ownership`
// delle dependency require_project_owner / require_schedule_owner /
// require_file_owner): admin e superadmin sempre, chiunque altro solo sulle
// risorse che ha creato. Senza `created_by` solo admin (come il backend). Il
// gruppo lo controlla il server: le liste portano solo risorse del gruppo.
const ownsOrAdmin = (u, resource) => isAdminOrAbove(u)
  || (!!u?.user_id && !!resource?.created_by && resource.created_by === u.user_id);

// Dove il server calcola il flag con la STESSA regola dell'azione (file:
// owns_or_admin), il flag decide; senza flag, la regola dell'azione.
const ownerFlagOrRule = (u, resource) => (typeof resource?.can_edit === 'boolean'
  ? resource.can_edit
  : ownsOrAdmin(u, resource));

const TICKET_CLOSED = ['resolved', 'closed'];

// La tabella di policy: un'azione = un predicato. Ogni predicato e' la
// regola del backend dell'endpoint (CAPABILITY_BACKEND qui sotto, verificata
// da tests/shared/permissions.contract leggendo i Depends dei router).
export const can = {
  // ── Canvas e progetti ────────────────────────────────────────────────
  editCanvas:        isUserOrAbove,   // viewer/esecutore sono read-only
  createProject:     isUserOrAbove,
  // Rinomina e tag: PATCH /projects/{id} = user del gruppo (non solo owner).
  editProjectMeta:   (u) => isUserOrAbove(u),
  deleteProject:     (u, project) => ownsOrAdmin(u, project),
  // «Esegui di prova» con «Utente che esegue»: chi puo' modificare il
  // progetto (come il salvataggio del canvas), mai viewer ed esecutore.
  testRunProject:    isUserOrAbove,

  // ── File ─────────────────────────────────────────────────────────────
  uploadFile:        isUserOrAbove,
  editFile:          (u, file) => ownerFlagOrRule(u, file),

  // ── Connessioni e sorgenti (admin del gruppo, flag del server) ───────
  createConnection:    isAdminOrAbove,
  editConnection:      (u, connection) => isAdminOrAbove(u) && serverAllows(connection),
  createDataSource:    isAdminOrAbove,
  editDataSource:      (u, ds) => isAdminOrAbove(u) && serverAllows(ds),
  createApiConnection: isAdminOrAbove,
  editApiConnection:   (u, api) => isAdminOrAbove(u) && serverAllows(api),

  // ── Schedule: crea admin; esegui/attiva/modifica/elimina =
  //    require_schedule_owner (proprietario di qualsiasi ruolo, o admin).
  createSchedule:      isAdminOrAbove,
  manageSchedule:      (u, schedule) => ownsOrAdmin(u, schedule),

  // ── Eseguibili ───────────────────────────────────────────────────────
  createEseguibile:    isAdminOrAbove,
  manageEseguibile:    (u, item) => isAdminOrAbove(u) && serverAllows(item),
  // Tutti i ruoli loggati possono eseguire gli eseguibili visibili al
  // proprio gruppo (incluso esecutore, che è proprio il suo unico scopo).
  runEseguibile:     (u) => !!u?.role && Object.values(ROLES).includes(u.role),
  // Dashboard Grafana (11/11/2026): crea, elimina, «Modifica in Grafana» =
  // stessa regola di modifica dell'Eseguibile (require_admin +
  // assert_can_modify, flag can_edit del server).
  manageEseguibileDashboard: (u, item) => isAdminOrAbove(u) && serverAllows(item),
  // «Apri dashboard» nella finestra dei risultati: ogni ruolo che esegue
  // (il server da' il lasciapassare solo a chi ha lanciato l'esecuzione).
  openEseguibileDashboard: (u) => !!u?.role && Object.values(ROLES).includes(u.role),

  // ── Condivisioni tra gruppi ──────────────────────────────────────────
  shareResources:    isSuperadmin,

  // ── Ticket (backend tickets/router + service) ────────────────────────
  manageTickets:     isAdminOrAbove,  // stato, gravita', categoria, elimina, tipo
  reopenTicket:      (u, ticket) => !isAdminOrAbove(u) && !!ticket
    && ticket.created_by === u?.user_id && TICKET_CLOSED.includes(ticket.status),
  removeTicketAttachment: (u, attachment) => isAdminOrAbove(u)
    || (isViewerOrAbove(u) && !!attachment?.uploaded_by && attachment.uploaded_by === u?.user_id),

  // ── Utenti e gruppi ──────────────────────────────────────────────────
  manageUsers:       isAdminOrAbove,  // admin gestisce solo il proprio gruppo
  deleteUserKeepingHistory: isSuperadmin, // utente con storico: lapide (10/11/2026)
  manageGroups:      isSuperadmin,    // solo superadmin tocca i gruppi
  deleteGroup:       (u, group) => isSuperadmin(u) && !!group?.group_id && group.group_id !== 'default',

  // ── Pagine di sistema (solo superadmin) ──────────────────────────────
  manageObjectStorage: isSuperadmin,  // bonifica orfani, preview, oggetti
  manageTaskRuntime:   isSuperadmin,  // CARICA incerte: conferma / retry
  manageBackups:       isSuperadmin,
  seeTechnicalLogs:  isAdminOrAbove,  // tab "Log Tecnici" del LogViewer
  // Azioni offerte dalla finestra di un'esecuzione fallita (conferma UI
  // B-K6): l'esecutore non le ha (il backend risponde 403).
  readRunLog:        isViewerOrAbove, // «Apri il registro» (scheda Esecuzioni)
  createTicket:      isViewerOrAbove, // «Segnala il problema»
};

/**
 * Capability -> regola del backend (Fase 5, correzione dopo la conferma).
 *
 * Per ogni capability: le funzioni dei router che la governano
 * (`file`: percorso da backend/app/core, `fns`: nomi delle funzioni) e la
 * regola:
 *   - `depends`: la dependency di autenticazione delle funzioni
 *     (require_user_or_above, require_admin, require_superadmin,
 *     require_not_esecutore, get_current_user, require_*_owner);
 *   - `rule`: 'role' (decide la dependency), 'owner' (regola
 *     owns_or_admin), 'service' (la regola fine sta nel service: il test
 *     controlla la dependency, i casi sono in "casi del backend");
 *   - `serverFlag`: la capability legge anche `can_edit` della risorsa
 *     ('exact' = il flag e' calcolato con la stessa regola dell'azione);
 *   - `stricter`: l'interfaccia e' DICHIARATAMENTE piu' restrittiva del
 *     backend, col motivo (nessuna azione nascosta senza motivo scritto).
 * Il test legge i Depends delle funzioni elencate e verifica che il
 * predicato di `can` dia, ruolo per ruolo, lo stesso esito della regola.
 */
export const CAPABILITY_BACKEND = Object.freeze({
  editCanvas: { file: 'projects/router.py', fns: ['update_project_canvas', 'create_canvas_version', 'restore_canvas_version'], depends: 'require_user_or_above', rule: 'role' },
  createProject: { file: 'projects/router.py', fns: ['create_project', 'clone_project'], depends: 'require_user_or_above', rule: 'role' },
  editProjectMeta: { file: 'projects/router.py', fns: ['update_project_details'], depends: 'require_user_or_above', rule: 'role' },
  deleteProject: { file: 'projects/router.py', fns: ['delete_project'], depends: 'require_project_owner', rule: 'owner' },
  testRunProject: { file: 'orchestrator/router.py', fns: ['launch_project_test_run'], depends: 'require_user_or_above', rule: 'role' },
  uploadFile: { file: 'files/router.py', fns: ['upload_file', 'upload_multi_layer'], depends: 'require_user_or_above', rule: 'role' },
  editFile: { file: 'files/router.py', fns: ['update_file', 'delete_file'], depends: 'require_file_owner', rule: 'owner', serverFlag: 'exact' },
  createConnection: { file: 'connections/router.py', fns: ['create_connection', 'create_connection_set'], depends: 'require_admin', rule: 'role' },
  editConnection: { file: 'connections/router.py', fns: ['update_connection', 'delete_connection', 'update_connection_set', 'delete_connection_set'], depends: 'require_admin', rule: 'role', serverFlag: true },
  createDataSource: { file: 'datasources/router.py', fns: ['create_datasource'], depends: 'require_admin', rule: 'role' },
  editDataSource: { file: 'datasources/router.py', fns: ['update_datasource', 'delete_datasource'], depends: 'require_admin', rule: 'role', serverFlag: true },
  createApiConnection: { file: 'arricchisci/router.py', fns: ['create_api_connection'], depends: 'require_admin', rule: 'role' },
  editApiConnection: { file: 'arricchisci/router.py', fns: ['update_api_connection', 'delete_api_connection'], depends: 'require_admin', rule: 'role', serverFlag: true },
  createSchedule: { file: 'scheduler/router.py', fns: ['create_schedule'], depends: 'require_admin', rule: 'role' },
  manageSchedule: { file: 'scheduler/router.py', fns: ['update_schedule', 'delete_schedule', 'toggle_schedule', 'run_schedule_now'], depends: 'require_schedule_owner', rule: 'owner' },
  createEseguibile: { file: 'eseguibili/router.py', fns: ['create_eseguibile', 'list_source_status'], depends: 'require_admin', rule: 'role' },
  manageEseguibile: { file: 'eseguibili/router.py', fns: ['update_eseguibile', 'delete_eseguibile', 'republish_eseguibile', 'activate_eseguibile_version', 'get_source_status'], depends: 'require_admin', rule: 'role', serverFlag: true },
  runEseguibile: { file: 'eseguibili/router.py', fns: ['execute_eseguibile', 'create_eseguibile_run'], depends: 'get_current_user', rule: 'role' },
  manageEseguibileDashboard: { file: 'eseguibili/dashboard_router.py', fns: ['create_eseguibile_dashboard', 'delete_eseguibile_dashboard', 'get_eseguibile_dashboard_edit_link'], depends: 'require_admin', rule: 'role', serverFlag: true },
  // `file::funzione` = funzione di un altro router. close_grafana_session
  // (logout) non ha dependency: toglie soltanto il cookie.
  openEseguibileDashboard: { file: 'eseguibili/dashboard_router.py', fns: ['issue_eseguibile_dashboard_pass', 'auth/grafana_router.py::open_grafana_session'], depends: 'get_current_user', rule: 'role' },
  shareResources: { file: 'auth/shares_router.py', fns: ['set_shares', 'add_share', 'remove_share'], depends: 'require_superadmin', rule: 'role' },
  manageTickets: { file: 'tickets/router.py', fns: ['update_ticket_endpoint', 'delete_ticket_endpoint'], depends: '_require_not_esecutore', rule: 'service' },
  reopenTicket: { file: 'tickets/router.py', fns: ['update_ticket_endpoint'], depends: '_require_not_esecutore', rule: 'service' },
  removeTicketAttachment: { file: 'tickets/router.py', fns: ['delete_attachment_endpoint'], depends: '_require_not_esecutore', rule: 'service' },
  manageUsers: { file: 'auth/users_router.py', fns: ['create_user', 'update_user', 'delete_user', 'reset_password'], depends: 'require_admin', rule: 'role' },
  deleteUserKeepingHistory: { file: 'auth/users_router.py', fns: ['delete_user_keeping_history'], depends: 'require_superadmin', rule: 'role' },
  manageGroups: { file: 'auth/groups_router.py', fns: ['create_group'], depends: 'require_superadmin', rule: 'role' },
  deleteGroup: { file: 'auth/groups_router.py', fns: ['delete_group'], depends: 'require_superadmin', rule: 'service' },
  manageObjectStorage: { file: 'admin_object_storage/router.py', fns: ['delete_all_previews', 'delete_object'], depends: 'require_superadmin', rule: 'role' },
  manageTaskRuntime: { file: 'admin_task_runtime/router.py', fns: ['resolve_uncertain_carica'], depends: 'require_superadmin', rule: 'role' },
  manageBackups: { file: 'admin_backups/router.py', fns: ['backup_trigger_endpoint'], depends: 'require_superadmin', rule: 'role' },
  seeTechnicalLogs: {
    file: 'logs/router.py', fns: ['get_logs'], depends: 'require_not_esecutore', rule: 'role',
    stricter: 'scelta di interfaccia: il tab "Log Tecnici" si mostra solo agli admin; il backend filtra comunque per gruppo',
  },
  readRunLog: { file: 'logs/runs_router.py', fns: ['get_runs', 'get_run'], depends: 'require_not_esecutore', rule: 'role' },
  createTicket: { file: 'tickets/router.py', fns: ['create_ticket_endpoint'], depends: '_require_not_esecutore', rule: 'role' },
});

export default { can, role, ROLES };
