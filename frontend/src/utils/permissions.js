/**
 * permissions.js — Policy table dei ruoli URBY-ETL
 *
 * Centralizza in un unico file tutta la logica "chi può fare cosa".
 * I componenti importano `can.*` (capability) o `role.*` (predicati di ruolo)
 * invece di scrivere direttamente `user?.role === 'viewer'`.
 *
 * Vantaggi:
 *  - Le regole sono leggibili come una tabella di verità in un solo posto
 *  - Cambiare i permessi di un ruolo = modifica una funzione, non grep+sed
 *  - Test super semplici (puro JS, niente mock di /me)
 *  - Autocomplete dell'IDE sui nomi delle capability
 *
 * NOTA SICUREZZA: questo file è SOLO UX (mostra/nascondi pulsanti). Il vero
 * enforcement sta nel backend (Depends(require_*), assert_group_access, ecc.).
 * Anche se uno bypassa i check JS via DevTools, il backend rifiuta.
 *
 * Convenzione:
 *  - `can.X(user)`  → usare quando esiste una capability semantica precisa
 *                     ("createSchedule", "shareResources"). Preferito.
 *  - `role.X(user)` → usare per check di "categoria di ruolo" generici
 *                     ("isAdminOrAbove" per mostrare una sezione admin).
 *
 * Se aggiungi un nuovo ruolo o una nuova capability, aggiornare SOLO questo
 * file e poi importare la nuova funzione dove serve.
 */

// ── Ruoli supportati ─────────────────────────────────────────────────────
// `ROLES` e' interno al modulo + esposto via default export `{ can, role,
// ROLES }`. Il named `export const ROLES` precedente non aveva consumer
// (audit FE-PHASE2-STRUCTURAL-CLEANUP-001). L'enum resta semantico nel
// default export per chi vorra' leggerlo come tabella di ruoli.
const ROLES = {
  ESECUTORE:  'esecutore',  // ruolo "kiosk": vede SOLO eseguibili, niente altro
  VIEWER:     'viewer',
  USER:       'user',
  ADMIN:      'admin',
  SUPERADMIN: 'superadmin',
};

// ── Predicati di ruolo "puri" ────────────────────────────────────────────
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

// ── Capability semantiche ────────────────────────────────────────────────
// Mappare ogni "azione" a un predicato. Questa è la tabella di policy.
//
// Se domani decidi che "editCanvas" deve essere permesso anche ai viewer,
// editi UN punto qui sotto, non 15 file sparsi.
//
// NOTA: per la maggior parte delle capability `esecutore` ritorna FALSE
// perché vive in una UI separata (kiosk) che non ha quei pulsanti. Le
// uniche capability vere dell'esecutore sono `runEseguibile` e
// `changeOwnPassword` (di fatto sempre vere quando è loggato).
export const can = {
  // ── Canvas / progetti ──────────────────────────────────────────────
  editCanvas:        isUserOrAbove,   // viewer/esecutore sono read-only
  createProject:     isUserOrAbove,
  deleteOwnProject:  isUserOrAbove,

  // ── File ───────────────────────────────────────────────────────────
  uploadFile:        isUserOrAbove,
  deleteFile:        isUserOrAbove,

  // ── Risorse tecniche (solo admin/superadmin del proprio gruppo) ────
  createConnection:    isAdminOrAbove,
  createDataSource:    isAdminOrAbove,
  createApiConnection: isAdminOrAbove,
  createSchedule:      isAdminOrAbove,
  createEseguibile:    isAdminOrAbove,

  // ── Sharing cross-gruppo (solo superadmin) ─────────────────────────
  shareResources:    isSuperadmin,

  // ── User & Group management ────────────────────────────────────────
  manageUsers:       isAdminOrAbove,  // admin gestisce solo il proprio gruppo
  manageGroups:      isSuperadmin,    // solo superadmin tocca i gruppi
  assignAdminRole:   isSuperadmin,    // solo superadmin può creare admin/superadmin
  createEsecutore:   isAdminOrAbove,  // sia superadmin che admin di gruppo

  // ── Log viewer ─────────────────────────────────────────────────────
  seeTechnicalLogs:  isAdminOrAbove,  // tab "Log Tecnici" del LogViewer

  // ── Eseguibili (kiosk-friendly) ────────────────────────────────────
  // Tutti i ruoli loggati possono eseguire gli eseguibili visibili al
  // proprio gruppo (incluso esecutore, che è proprio il suo unico scopo).
  runEseguibile:     (u) => !!u?.role && Object.values(ROLES).includes(u.role),
};

export default { can, role, ROLES };
