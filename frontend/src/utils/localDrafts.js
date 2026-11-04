/**
 * localDrafts — archivio delle copie di sicurezza nel browser (Fase 4, M7).
 *
 * Regole (una sola tabella, usata da useLocalDraft, logout, eliminazione
 * progetto e ripristino versione):
 *  - la copia e' di UN utente su UN progetto: chiave plenora:draft:<utente>:<progetto>;
 *  - scade dopo MAX_AGE_MS (contenuti come SQL e mappature non restano per sempre);
 *  - si cancella quando il canvas del server cambia dall'editor (salvataggio,
 *    ripristino di una versione) e quando il progetto viene eliminato.
 *    All'uscita dall'account resta (l'uscita chiede gia' conferma se ci
 *    sono modifiche non salvate; la copia e' di quell'utente e scade).
 */
const PREFIX = 'plenora:draft:';
export const MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;

const storage = () => {
  try { return window.localStorage; } catch { return null; }
};

/** Utente della sessione (stesso dato di AuthContext). */
export const currentDraftUser = () => {
  try {
    const u = JSON.parse(window.sessionStorage.getItem('urby_user') || 'null');
    return u?.id || u?.username || null;
  } catch { return null; }
};

export const draftKey = (user, projectId) => `${PREFIX}${user}:${projectId}`;

export function readDraft(user, projectId) {
  const ls = storage();
  if (!ls || !user || !projectId) return null;
  try {
    const d = JSON.parse(ls.getItem(draftKey(user, projectId)) || 'null');
    if (!d?.nodes) return null;
    if (!d.savedAt || Date.now() - d.savedAt > MAX_AGE_MS) { ls.removeItem(draftKey(user, projectId)); return null; }
    return d;
  } catch { return null; }
}

/** @returns {boolean} false se non si e' potuto scrivere (la copia vecchia viene tolta). */
export function writeDraft(user, projectId, draft) {
  const ls = storage();
  if (!ls || !user || !projectId) return false;
  const key = draftKey(user, projectId);
  try {
    ls.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    // Storage pieno: meglio nessuna copia che una copia vecchia offerta
    // come se fosse l'ultima.
    try { ls.removeItem(key); } catch { /* niente */ }
    return false;
  }
}

const removeWhere = (match) => {
  const ls = storage();
  if (!ls) return;
  try {
    const keys = [];
    for (let i = 0; i < ls.length; i += 1) {
      const k = ls.key(i);
      if (k && k.startsWith(PREFIX) && match(k.slice(PREFIX.length))) keys.push(k);
    }
    keys.forEach(k => ls.removeItem(k));
  } catch { /* storage non disponibile */ }
};

/** Il canvas del server e' cambiato (salvataggio, versione ripristinata). */
export const clearDraft = (projectId, user = currentDraftUser()) => {
  if (user) removeWhere(rest => rest === `${user}:${projectId}`);
};

/** Progetto eliminato: nessuna copia per nessun utente di questo browser. */
export const clearProjectDrafts = (projectId) => removeWhere(rest => rest.endsWith(`:${projectId}`));
