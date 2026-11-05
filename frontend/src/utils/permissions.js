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
 * (Depends(require_*), _check_ownership, compute_can_edit, ...).
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

// Stessa regola di backend `_check_ownership` (progetti, schedule, file):
// admin e superadmin sempre, gli altri solo sulle risorse che hanno creato.
// Se la risorsa non porta `created_by` decide il backend (403 motivato).
const ownsOrAdmin = (u, resource) => isAdminOrAbove(u)
  || (isUserOrAbove(u) && (!resource?.created_by || resource.created_by === u?.user_id));

const TICKET_CLOSED = ['resolved', 'closed'];

// La tabella di policy: un'azione = un predicato.
export const can = {
  // ── Canvas e progetti ────────────────────────────────────────────────
  editCanvas:        isUserOrAbove,   // viewer/esecutore sono read-only
  createProject:     isUserOrAbove,
  editProject:       (u, project) => ownsOrAdmin(u, project),
  deleteProject:     (u, project) => ownsOrAdmin(u, project),

  // ── File ─────────────────────────────────────────────────────────────
  uploadFile:        isUserOrAbove,
  editFile:          (u, file) => isUserOrAbove(u) && serverAllows(file),

  // ── Connessioni e sorgenti (admin del gruppo, flag del server) ───────
  createConnection:    isAdminOrAbove,
  editConnection:      (u, connection) => isAdminOrAbove(u) && serverAllows(connection),
  createDataSource:    isAdminOrAbove,
  editDataSource:      (u, ds) => isAdminOrAbove(u) && serverAllows(ds),
  createApiConnection: isAdminOrAbove,
  editApiConnection:   (u, api) => isAdminOrAbove(u) && serverAllows(api),

  // ── Schedule: crea admin; esegui/attiva/modifica/elimina come il
  //    backend (require_schedule_owner: proprietario o admin; gli schedule
  //    li crea solo un admin, quindi admin e superadmin).
  createSchedule:      isAdminOrAbove,
  manageSchedule:      (u, schedule) => isAdminOrAbove(u) || (isUserOrAbove(u)
    && !!schedule?.created_by && schedule.created_by === u?.user_id),

  // ── Eseguibili ───────────────────────────────────────────────────────
  createEseguibile:    isAdminOrAbove,
  manageEseguibile:    (u, item) => isAdminOrAbove(u) && serverAllows(item),
  // Tutti i ruoli loggati possono eseguire gli eseguibili visibili al
  // proprio gruppo (incluso esecutore, che è proprio il suo unico scopo).
  runEseguibile:     (u) => !!u?.role && Object.values(ROLES).includes(u.role),

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
  manageGroups:      isSuperadmin,    // solo superadmin tocca i gruppi
  deleteGroup:       (u, group) => isSuperadmin(u) && !!group?.group_id && group.group_id !== 'default',
  assignAdminRole:   isSuperadmin,    // solo superadmin può creare admin/superadmin
  createEsecutore:   isAdminOrAbove,  // sia superadmin che admin di gruppo

  // ── Pagine di sistema (solo superadmin) ──────────────────────────────
  manageObjectStorage: isSuperadmin,  // bonifica orfani, preview, oggetti
  manageTaskRuntime:   isSuperadmin,  // CARICA incerte: conferma / retry
  manageBackups:       isSuperadmin,
  seeTechnicalLogs:  isAdminOrAbove,  // tab "Log Tecnici" del LogViewer
};

export default { can, role, ROLES };
