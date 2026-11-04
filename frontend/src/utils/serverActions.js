/**
 * serverActions — UNICO punto da cui il canvas dell'editor arriva al server
 * (Fase 4, M7, salvataggio esplicito).
 *
 * Quello che gira (Scheduler, Eseguibili) e' l'ultima versione SALVATA,
 * quindi il canvas si salva solo quando l'utente lo chiede:
 *   - saveNow()        "💾 Salva" e Ctrl+S;
 *   - saveForRun()     "▶ Esegui" (il pulsante dice "Salva, controlla ed esegui");
 *   - saveBeforeServerRead(azione)  Valida, Anteprima, "Dati dopo…",
 *                      "Aggiorna": leggono la versione salvata; con modifiche
 *                      non salvate CHIEDONO "Salva e continua" (o lo dice gia'
 *                      il pulsante: { explicit: true }). Annulla = niente.
 *   - restoreVersion(id)  ripristino di una versione (finestra Versioni).
 * Ogni scrittura e' condizionata alla versione della scheda (Blocco A,
 * regola 8, utils/canvasVersion): su un conflitto l'utente sceglie
 *   - reloadServerVersion()  ricarica la versione del server, tenendo le
 *                            modifiche come copia di sicurezza della scheda;
 *   - overwriteServer()      sovrascrive consapevolmente (senza base).
 * Nessun altro modulo chiama handleSaveQuiet, restoreVersionQuiet o
 * overwriteAfterConflict (test di enumerazione).
 */
import useCanvasStore from '../store/useCanvasStore';
import { hasUnsavedChanges } from '../store/saveState';
import { keepTabCopy, serverCanvasReplaced } from './localDrafts';

// Ricarica dell'editor (sostituibile nei test).
export const pageReload = { run: () => window.location.reload() };

const save = () => useCanvasStore.getState().handleSaveQuiet();

export const saveNow = () => save();

export const saveForRun = () => save();

/**
 * @param {string} action  es. "La validazione", "L'anteprima"
 * @param {{explicit?: boolean}} [opts] explicit: il pulsante premuto diceva gia' "Salva e …"
 * @returns {Promise<boolean>} false = l'utente ha annullato
 */
export async function saveBeforeServerRead(action, { explicit = false } = {}) {
  const st = useCanvasStore.getState();
  if (!hasUnsavedChanges(st)) return true;
  if (!explicit) {
    const ask = st.getEditorAction?.('confirmSaveBeforeRead');
    // Senza chi chiede (fuori dall'editor) non si salva di nascosto: si
    // legge la versione salvata.
    if (!ask) return true;
    if (!(await ask(action))) return false;
  }
  await save();
  return true;
}

/** Prima di un'anteprima (usePreview): stessa regola, funzione stabile. */
export const beforePreview = () => saveBeforeServerRead("L'anteprima");

/**
 * Il canvas del server e' stato sostituito da questa scheda (ripristino di
 * una versione): la copia di questa scheda non vale piu', le altre schede
 * vengono avvisate, l'editor si ricarica dal percorso normale di apertura.
 */
function afterServerReplaced(projectId) {
  useCanvasStore.getState().markClean();
  serverCanvasReplaced(projectId);
  setTimeout(() => pageReload.run(), 600);
}

/** Ripristino di una versione: scrittura condizionata, poi si ricarica. */
export async function restoreVersion(versionId) {
  const st = useCanvasStore.getState();
  const projectId = st.projectId;
  const response = await st.restoreVersionQuiet(versionId);
  if (response) afterServerReplaced(projectId);
  return response;
}

/** Conflitto di versione: "Sovrascrivi" = la scrittura rifiutata senza base. */
export async function overwriteServer() {
  const st = useCanvasStore.getState();
  const projectId = st.projectId;
  const done = await st.overwriteAfterConflict();
  if (done?.kind === 'restore' && done.response) afterServerReplaced(projectId);
  return done;
}

/**
 * Conflitto di versione: "Ricarica la versione del server". Le modifiche
 * della scheda restano come sua copia di sicurezza (offerta al ricaricamento).
 * @returns {{ok: boolean, reason?: string}} esito della copia: senza copia
 *   non si ricarica (le modifiche andrebbero perse).
 */
export function reloadServerVersion() {
  const st = useCanvasStore.getState();
  if (hasUnsavedChanges(st)) {
    const res = keepTabCopy(st.projectId, { nodes: st.nodes, edges: st.edges, baseUpdatedAt: st.lastSavedServerAt || null });
    if (!res.ok) return res;
  }
  // La copia e' scritta: uscire non deve piu' chiedere delle modifiche.
  st.markClean();
  pageReload.run();
  return { ok: true };
}
