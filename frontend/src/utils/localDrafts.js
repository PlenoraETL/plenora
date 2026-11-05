/**
 * localDrafts — archivio delle copie di sicurezza nel browser (Fase 4, M7;
 * Blocco A, regola 8). Unico modulo che tocca le chiavi plenora:* del
 * localStorage; lo usano useLocalDraft, il ripristino di una versione,
 * l'eliminazione del progetto, l'uscita dall'account e l'avvio dell'app.
 *
 * REGOLA
 *  - Una copia e' di UN utente (user_id del login), su UN progetto, di UNA
 *    scheda: chiave plenora:draft:<user_id>:<progetto>:<scheda>. L'id di
 *    scheda vive in sessionStorage (stabile al ricaricamento della stessa
 *    scheda; una scheda duplicata se ne accorge e ne prende uno nuovo).
 *  - Ogni scheda scrive e cancella SOLO la propria copia. Una copia di una
 *    scheda chiusa (orfana) puo' essere adottata: chi la ripristina o la
 *    scarta ne diventa proprietario. Una copia di una scheda aperta non si
 *    cancella mai da fuori (si nasconde soltanto).
 *  - Cancellare una copia altrui (orfana o adottata) e' sempre "confronta e
 *    cancella": si toglie solo se e' ancora quella annunciata (stesso savedAt).
 *  - La copia registra la versione del server da cui parte (baseUpdatedAt,
 *    orologio del server): "salvato dopo" confronta due orari del server,
 *    mai l'orologio del browser con quello del server.
 *  - Solo JSON: la copia passa da JSON.stringify (niente funzioni).
 *  - Scade dopo MAX_AGE_MS; la pulizia e' GLOBALE all'avvio dell'app.
 *  - Ogni scrittura restituisce l'esito ({ ok, reason }); lo spazio pieno
 *    non cancella la copia precedente (ha la sua data) e l'editor lo dice.
 *  - Un progetto eliminato lascia una lapide: nessuna scheda puo' far
 *    rinascere la sua copia.
 *  - Uscita dall'account: la copia RESTA (e' di quell'utente e scade).
 *
 * TABELLA DELLE TRANSIZIONI (S = questa scheda, A = le altre schede/copie).
 * Stati di S: pulita | in sospeso (modifiche, pausa non ancora scaduta) |
 * copiata (modifiche gia' nella copia); ortogonali: offerta aperta, progetto
 * eliminato. Ogni riga ha un test in due schede in
 * tests/components/DraftTabs.contract.test.jsx (DRAFT_TRANSITIONS).
 *
 * | evento                     | effetto sulla copia di S                                   | effetto sulle copie di A                       |
 * |----------------------------|------------------------------------------------------------|------------------------------------------------|
 * | apertura                   | la copia propria (ricaricamento) diventa orfana; si offre   | nessuno (si leggono, istantanea in memoria)    |
 * |                            | l'istantanea di TUTTE le copie utente+progetto             |                                                |
 * | modifica                   | in sospeso (anche con l'offerta aperta)                    | nessuno                                        |
 * | pausa                      | scrive la propria copia con baseUpdatedAt; esito visibile  | nessuno                                        |
 * | spazio pieno               | scrittura fallita: avviso, la copia precedente resta       | nessuno                                        |
 * | salvataggio riuscito       | cancella la propria e quella adottata; base := versione    | restano; le schede aperte ricevono "salvato in |
 * |                            | restituita dal server (o sconosciuta); annuncia il salvato | un'altra scheda", la loro copia resta          |
 * | salvataggio fallito        | nessuno: resta in sospeso/copiata                          | nessuno                                        |
 * | ripristino                 | carica l'istantanea annunciata; cronologia: server (e      | copia di scheda aperta: mai toccata            |
 * |                            | modifiche correnti, previa domanda); la sorgente orfana     |                                                |
 * |                            | e' adottata e resta fino a salvataggio o scarto            |                                                |
 * | undo dopo ripristino       | come modifica: la copia propria segue, l'adottata resta    | nessuno                                        |
 * | scarta                     | copia propria/orfana: cancellata (confronta e cancella);   | copia di scheda aperta: solo nascosta          |
 * | uscita (pagehide/smontag.) | in sospeso -> scritta subito                               | nessuno                                        |
 * | 401 / uscita dall'account  | in sospeso -> scritta subito, PRIMA di togliere l'utente   | nessuno                                        |
 * | eliminazione progetto      | tutte le copie del progetto via + lapide                   | le schede aperte smettono di scrivere          |
 * | ripristino versione        | cancella la propria copia; annuncia il salvato             | restano; ricevono "salvato in un'altra scheda" |
 * | storage: salvato altrove   | resta; avviso; l'offerta aperta segna "salvato dopo"       | -                                              |
 * | storage: progetto eliminato| niente piu' scritture; offerta chiusa; avviso              | -                                              |
 * | conflitto: ricarica        | le modifiche diventano la copia propria (keepTabCopy),     | nessuno                                        |
 * |                            | poi si ricarica: all'apertura e' offerta "salvato dopo"    |                                                |
 *
 * Salvataggio condizionato alla versione (token canvas_version, 409 se
 * superata): utils/canvasVersion.js.
 */
const PREFIX = 'plenora:draft:';
const TAB_PREFIX = 'plenora:tab:';
const SAVED_PREFIX = 'plenora:saved:';
const DELETED_PREFIX = 'plenora:deleted:';
const TAB_ID_KEY = 'plenora:tab-id';

export const MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
const HEARTBEAT_MS = 10 * 1000;
const TAB_ALIVE_MS = 3 * HEARTBEAT_MS;
// Separatore delle copie orfane: <scheda>~<savedAt> non e' mai una scheda viva.
const ORPHAN_SEP = '~';

const storage = () => {
  try { return window.localStorage; } catch { return null; }
};
const session = () => {
  try { return window.sessionStorage; } catch { return null; }
};
const readJson = (ls, key) => {
  try { return JSON.parse(ls.getItem(key) || 'null'); } catch { return null; }
};
const keysWith = (ls, prefix) => {
  const out = [];
  try {
    for (let i = 0; i < ls.length; i += 1) {
      const k = ls.key(i);
      if (k && k.startsWith(prefix)) out.push(k);
    }
  } catch { /* storage non disponibile */ }
  return out;
};
const remove = (ls, key) => { try { ls.removeItem(key); } catch { /* niente */ } };

/** Utente della sessione: user_id salvato dal login (AuthContext). */
export const currentDraftUser = () => {
  try {
    const u = JSON.parse(session()?.getItem('urby_user') || 'null');
    return u?.user_id != null && u.user_id !== '' ? String(u.user_id) : null;
  } catch { return null; }
};

// ── Schede ───────────────────────────────────────────────────────────────

const newTabId = () => {
  try { if (window.crypto?.randomUUID) return window.crypto.randomUUID(); } catch { /* sotto */ }
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
};

const isTabAlive = (tabId, now = Date.now()) => {
  const ls = storage();
  if (!ls || !tabId || String(tabId).includes(ORPHAN_SEP)) return false;
  const at = Number(ls.getItem(`${TAB_PREFIX}${tabId}`));
  return Number.isFinite(at) && now - at < TAB_ALIVE_MS;
};

export const touchTab = (tabId) => {
  const ls = storage();
  if (ls && tabId) { try { ls.setItem(`${TAB_PREFIX}${tabId}`, String(Date.now())); } catch { /* pieno */ } }
};

const dropTab = (tabId) => { const ls = storage(); if (ls && tabId) remove(ls, `${TAB_PREFIX}${tabId}`); };

let pageTabId = null;
/**
 * Id di questa scheda, uno per caricamento di pagina. Stabile al
 * ricaricamento (sessionStorage; all'uscita la scheda toglie il proprio
 * segnale di vita). Una scheda duplicata copia il sessionStorage: trova il
 * segnale ancora vivo dell'originale e prende un id nuovo.
 */
export const currentTabId = () => {
  if (pageTabId) return pageTabId;
  const ss = session();
  let id = null;
  try { id = ss?.getItem(TAB_ID_KEY) || null; } catch { id = null; }
  if (!id || isTabAlive(id)) id = newTabId();
  try { ss?.setItem(TAB_ID_KEY, id); } catch { /* niente */ }
  pageTabId = id;
  return id;
};

// ── Copie ────────────────────────────────────────────────────────────────

export const draftKey = (user, projectId, tabId) => `${PREFIX}${user}:${projectId}:${tabId}`;
const projectPrefix = (user, projectId) => `${PREFIX}${user}:${projectId}:`;

const expired = (d, now) => !d?.savedAt || now - d.savedAt > MAX_AGE_MS;

export function readDraft(user, projectId, tabId) {
  const ls = storage();
  if (!ls || !user || !projectId || !tabId) return null;
  const key = draftKey(user, projectId, tabId);
  const d = readJson(ls, key);
  if (!d?.nodes) return null;
  if (expired(d, Date.now())) { remove(ls, key); return null; }
  return d;
}

/** Tutte le copie di un utente su un progetto (ogni scheda), piu' recenti prima. */
export function listDrafts(user, projectId) {
  const ls = storage();
  if (!ls || !user || !projectId) return [];
  const prefix = projectPrefix(user, projectId);
  return keysWith(ls, prefix)
    .map(k => readDraft(user, projectId, k.slice(prefix.length)))
    .filter(Boolean)
    .sort((a, b) => b.savedAt - a.savedAt);
}

export const isProjectDeleted = (projectId) => {
  const ls = storage();
  return Boolean(ls && projectId && ls.getItem(`${DELETED_PREFIX}${projectId}`));
};

/**
 * Scrive la copia di QUESTA scheda. Solo JSON (le funzioni non passano).
 * @returns {{ok: boolean, reason?: 'nouser'|'unavailable'|'deleted'|'full'}}
 */
export function writeDraft(user, projectId, tabId, draft) {
  const ls = storage();
  if (!user || !projectId || !tabId) return { ok: false, reason: 'nouser' };
  if (!ls) return { ok: false, reason: 'unavailable' };
  if (isProjectDeleted(projectId)) return { ok: false, reason: 'deleted' };
  const record = { v: 2, ...draft, userId: user, projectId, tabId };
  try {
    ls.setItem(draftKey(user, projectId, tabId), JSON.stringify(record));
    return { ok: true };
  } catch {
    // Spazio pieno: la copia precedente resta (con la sua data); l'editor
    // avvisa che non e' aggiornata.
    return { ok: false, reason: 'full' };
  }
}

/** Cancella la copia di QUESTA scheda. */
export const removeDraft = (user, projectId, tabId) => {
  const ls = storage();
  if (ls && user && projectId && tabId) remove(ls, draftKey(user, projectId, tabId));
};

/** Confronta e cancella: toglie la copia solo se e' ancora quella annunciata. */
export const removeDraftIf = (user, projectId, tabId, savedAt) => {
  const d = readDraft(user, projectId, tabId);
  if (d && d.savedAt === savedAt) removeDraft(user, projectId, tabId);
};

/**
 * All'apertura: la copia propria (lasciata da questa scheda prima di
 * ricaricare) diventa una copia orfana, cosi' il lavoro nuovo ha una copia
 * separata da quella offerta.
 */
export function retireOwnDraft(user, projectId, tabId) {
  const ls = storage();
  const d = readDraft(user, projectId, tabId);
  if (!ls || !d) return;
  const orphanId = `${tabId}${ORPHAN_SEP}${d.savedAt}`;
  try {
    ls.setItem(draftKey(user, projectId, orphanId), JSON.stringify({ ...d, tabId: orphanId }));
    remove(ls, draftKey(user, projectId, tabId));
  } catch { /* pieno: la copia resta dov'e' */ }
}

/** Provenienza di una copia vista da `myTab`: 'this' | 'open' | 'closed'. */
export const draftOrigin = (copyTabId, myTab) => {
  if (copyTabId === myTab || String(copyTabId).startsWith(`${myTab}${ORPHAN_SEP}`)) return 'this';
  return isTabAlive(copyTabId) ? 'open' : 'closed';
};

/**
 * La versione del server e' andata oltre quella da cui parte la copia?
 * Due orari del SERVER; null se non si puo' dire (copia senza base).
 */
export const serverMovedOn = (baseUpdatedAt, serverUpdatedAt) => {
  const base = baseUpdatedAt ? Date.parse(baseUpdatedAt) : NaN;
  const server = serverUpdatedAt ? Date.parse(serverUpdatedAt) : NaN;
  if (!Number.isFinite(base) || !Number.isFinite(server)) return null;
  return server > base;
};

// ── Annunci tra schede (evento storage) ──────────────────────────────────

/** Il canvas del server e' cambiato da questa scheda (salvataggio, versione). */
export const announceSaved = (projectId, { tabId, updatedAt = null } = {}) => {
  const ls = storage();
  if (!ls || !projectId) return;
  try { ls.setItem(`${SAVED_PREFIX}${projectId}`, JSON.stringify({ tabId, updatedAt, at: Date.now() })); } catch { /* pieno */ }
};

/**
 * Ripristino di una versione da questa scheda: la copia di questa scheda
 * non vale piu'; le altre restano e vengono avvisate.
 */
export const serverCanvasReplaced = (projectId, user = currentDraftUser(), tabId = currentTabId()) => {
  removeDraft(user, projectId, tabId);
  announceSaved(projectId, { tabId });
};

/**
 * Conflitto di versione, scelta "ricarica la versione del server": le
 * modifiche di questa scheda diventano la sua copia di sicurezza (stessa
 * chiave utente:progetto:scheda, base = versione del server da cui
 * partivano). Al ricaricamento la scheda la ritrova nell'offerta ("Questa
 * scheda, prima di ricaricare", segnata "salvato dopo").
 */
export const keepTabCopy = (projectId, { nodes, edges, baseUpdatedAt = null }, user = currentDraftUser(), tabId = currentTabId()) => {
  // Solo il contenuto: selezione e misure non contano (come useLocalDraft).
  // eslint-disable-next-line no-unused-vars
  const cleanNodes = (nodes || []).map(({ selected, dragging, measured, ...n }) => n);
  // eslint-disable-next-line no-unused-vars
  const cleanEdges = (edges || []).map(({ selected, ...e }) => e);
  return writeDraft(user, projectId, tabId, { savedAt: Date.now(), baseUpdatedAt, nodes: cleanNodes, edges: cleanEdges });
};

/** Progetto eliminato: nessuna copia per nessun utente, e una lapide. */
export const markProjectDeleted = (projectId) => {
  const ls = storage();
  if (!ls || !projectId) return;
  keysWith(ls, PREFIX)
    .filter(k => k.slice(PREFIX.length).split(':')[1] === String(projectId))
    .forEach(k => remove(ls, k));
  try { ls.setItem(`${DELETED_PREFIX}${projectId}`, String(Date.now())); } catch { /* pieno */ }
};

/** Legge un evento storage: { type: 'saved'|'deleted', projectId, ... } o null. */
export const parseDraftEvent = (e) => {
  const key = e?.key;
  if (!key || e.newValue == null) return null;
  if (key.startsWith(SAVED_PREFIX)) {
    try {
      const v = JSON.parse(e.newValue);
      return { type: 'saved', projectId: key.slice(SAVED_PREFIX.length), tabId: v?.tabId || null, at: v?.at || null, updatedAt: v?.updatedAt || null };
    } catch { return null; }
  }
  if (key.startsWith(DELETED_PREFIX)) return { type: 'deleted', projectId: key.slice(DELETED_PREFIX.length) };
  return null;
};

// ── Pulizia globale e segnale di vita ────────────────────────────────────

/** Toglie copie scadute o illeggibili, annunci e lapidi vecchi, segnali morti. */
export function pruneDrafts(now = Date.now()) {
  const ls = storage();
  if (!ls) return;
  keysWith(ls, PREFIX).forEach(k => { const d = readJson(ls, k); if (!d?.nodes || expired(d, now)) remove(ls, k); });
  keysWith(ls, SAVED_PREFIX).forEach(k => { const d = readJson(ls, k); if (!d?.at || now - d.at > MAX_AGE_MS) remove(ls, k); });
  keysWith(ls, DELETED_PREFIX).forEach(k => { const at = Number(ls.getItem(k)); if (!Number.isFinite(at) || now - at > MAX_AGE_MS) remove(ls, k); });
  keysWith(ls, TAB_PREFIX).forEach(k => { const at = Number(ls.getItem(k)); if (!Number.isFinite(at) || now - at > MAX_AGE_MS) remove(ls, k); });
}

let housekeeping = false;
/** All'avvio dell'app (index.jsx): pulizia globale e segnale di vita della scheda. */
export function installDraftHousekeeping() {
  if (housekeeping || typeof window === 'undefined') return;
  housekeeping = true;
  pruneDrafts();
  const tabId = currentTabId();
  touchTab(tabId);
  setInterval(() => touchTab(tabId), HEARTBEAT_MS);
  // In uscita la scheda smette di essere viva: al ricaricamento tiene il suo id.
  window.addEventListener('pagehide', () => dropTab(tabId));
  window.addEventListener('pageshow', () => touchTab(tabId));
}
